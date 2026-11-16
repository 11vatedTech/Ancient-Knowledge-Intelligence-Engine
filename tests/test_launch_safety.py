"""Relocation, rights-gated Library export, failure reporting and API rights checks."""
from __future__ import annotations

import json

import pytest

from aki.archive_backup import BackupError, backup_archive, latest_verified_backup, verify_backup
from aki.cli_room import _problems, preflight
from aki.db import Database, initialize
from aki.home import RelocationError, is_cloud_synced, relocate, resolve_home
from aki.ingest import ingest_pdf
from aki.library_export import export_library

pymupdf = pytest.importorskip("pymupdf")

from test_reading_room import ALL_OPERATIONS, _pdf, _seed  # noqa: E402


@pytest.fixture()
def home(tmp_path):
    root = tmp_path / "project" / ".aki"
    database = Database(root / "aki.sqlite3")
    initialize(database.path)
    _seed(database, "e1", "Book One", url="https://theholyword.net/bible/b1")
    ingest_pdf(database, root / "blobs", _pdf(tmp_path / "f.pdf"), edition_id="e1", dpi=72)
    # register the original as an asset with a source record so export can name it
    snapshot = tmp_path / "books.json"
    snapshot.write_text(json.dumps([{"id": "b1", "title": "Book: One?", "category": "Bibles",
                                     "author": "A"}, {"id": "b9", "title": "Denied"}]))
    return database, root, snapshot


# ------------------------------------------------------------------ relocation
def test_relocate_copies_verifies_and_switches(home, tmp_path, monkeypatch):
    database, root, _ = home
    monkeypatch.delenv("AKI_HOME", raising=False)
    project = root.parent
    (root / "backups").mkdir()
    (root / "backups" / "old.sqlite3").write_bytes(b"x")
    (root / "derived").mkdir()
    (root / "derived" / "thumb.jpg").write_bytes(b"y")
    target = tmp_path / "local" / "AKI"
    with pytest.raises(RelocationError, match="no verified archive backup"):
        relocate(root, target, project_root=project)
    backup_archive(database, root / "blobs", tmp_path / "backup drive" / "AKI backup")
    report = relocate(root, target, project_root=project)
    assert report["database"]["integrity"]["quick_check"] == "ok"
    source_files = sorted(p.relative_to(root / "blobs") for p in (root / "blobs").rglob("*") if p.is_file())
    target_files = sorted(p.relative_to(target / "blobs") for p in (target / "blobs").rglob("*") if p.is_file())
    assert source_files == target_files
    assert (target / "backups" / "old.sqlite3").is_file()
    assert not (target / "derived").exists()  # derived caches are rebuilt, not copied
    assert (root / "aki.sqlite3").is_file() and (root / "MOVED.txt").is_file()  # nothing deleted
    assert resolve_home(project) == target
    with Database(target / "aki.sqlite3").connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='archive_relocated'").fetchone()[0] == 1


def test_relocate_refuses_corrupt_vault_file(home, tmp_path):
    _, root, _ = home
    victim = next(p for p in (root / "blobs").rglob("*") if p.is_file())
    victim.write_bytes(b"tampered")
    with pytest.raises(RelocationError, match="failed verification"):
        relocate(root, tmp_path / "AKI", project_root=root.parent, require_backup=False)
    assert not (root.parent / "aki.config.json").exists()  # still pointing at the old home
    assert not (tmp_path / "AKI" / "aki.sqlite3").exists()  # database is copied last


def test_relocate_refuses_cloud_and_nested_targets(home, tmp_path):
    _, root, _ = home
    with pytest.raises(RelocationError, match="cloud"):
        relocate(root, tmp_path / "OneDrive" / "AKI", project_root=root.parent)
    with pytest.raises(RelocationError, match="outside"):
        relocate(root, root / "inner", project_root=root.parent)
    assert is_cloud_synced("C:/Users/x/OneDrive/Desktop/p")
    assert not is_cloud_synced(tmp_path / "AKI")


def test_relocate_refuses_existing_database(home, tmp_path):
    _, root, _ = home
    target = tmp_path / "AKI"
    target.mkdir()
    (target / "aki.sqlite3").write_bytes(b"older copy")
    with pytest.raises(RelocationError, match="already exists"):
        relocate(root, target, project_root=root.parent, require_backup=False)


def test_env_overrides_config(tmp_path, monkeypatch):
    (tmp_path / "aki.config.json").write_text(json.dumps({"home": str(tmp_path / "A")}))
    monkeypatch.delenv("AKI_HOME", raising=False)
    assert resolve_home(tmp_path) == tmp_path / "A"
    monkeypatch.setenv("AKI_HOME", str(tmp_path / "B"))
    assert resolve_home(tmp_path) == tmp_path / "B"


# ------------------------------------------------------------------ library export
def test_library_export_is_gated_idempotent_and_audited(home, tmp_path):
    database, root, snapshot = home
    _seed(database, "e9", "Denied", url="https://theholyword.net/bible/b9", grant=False)
    with database.transaction() as connection:
        asset = connection.execute("SELECT * FROM assets WHERE edition_id='e1'").fetchone()
        connection.execute(
            "INSERT INTO assets (id, edition_id, rights_decision_id, storage_key, sha256, byte_size,"
            " detected_mime, asset_role, preflight_status, created_at)"
            " SELECT 'a9', 'e9', 'rd-e9', storage_key, sha256, byte_size, detected_mime,"
            " 'original_scan', 'passed', created_at FROM assets WHERE id = ?", (asset["id"],))
    out = tmp_path / "Library"
    first = export_library(database, root / "blobs", out, snapshot=snapshot)
    assert first["copied_now"] == 1 and first["skipped_without_store_original"] == ["Denied"]
    assert (out / "Bibles" / "Book One.pdf").is_file()
    second = export_library(database, root / "blobs", out, snapshot=snapshot)
    assert (second["copied_now"], second["already_present"]) == (0, 1)
    assert "Denied" not in (out / "_catalog.csv").read_text(encoding="utf-8")
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='library_export'").fetchone()[0] == 2


def test_library_export_replaces_a_damaged_copy(home, tmp_path):
    database, root, snapshot = home
    out = tmp_path / "Library"
    export_library(database, root / "blobs", out, snapshot=snapshot)
    copy = next(p for p in out.rglob("*.pdf"))
    size = copy.stat().st_size
    copy.write_bytes(b"x" * size)  # same size, wrong content
    report = export_library(database, root / "blobs", out, snapshot=snapshot)
    assert report["copied_now"] == 1


# ------------------------------------------------------------------ finish failure reporting
def test_finish_reports_worker_and_stage_failures():
    healthy = {"ingest": {"exit_codes": [0, 0]}, "media": {"results": [{"pages": 1}]},
               "semantic": {"provider": "lsa"},
               "integrity": {"quick_check": "ok", "foreign_key_violations": 0}}
    assert _problems(healthy) == []
    broken = {**healthy, "ingest": {"exit_codes": [0, 3]},
              "media": {"results": [{"error": "OSError: locked"}]},
              "semantic": {"error": "no permitted passages"},
              "integrity": {"quick_check": "corrupt", "foreign_key_violations": 0}}
    problems = _problems(broken)
    assert len(problems) == 4 and "workers [1]" in problems[0]


# ------------------------------------------------------------------ API rights
def test_api_enforces_render_and_research_rights(home, tmp_path):
    from fastapi.testclient import TestClient

    from aki.app.api import create_app

    database, root, _ = home
    narrowed = [op for op in ALL_OPERATIONS if op not in ("render_pages", "internal_research")]
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO rights_decisions (id, edition_id, state, scope, notes, created_at)"
            " VALUES ('rd-narrow', 'e1', 'cleared_limited', ?, 'narrowed', '2999-01-01T00:00:00Z')",
            (json.dumps({"permitted_operations": narrowed}),))
    client = TestClient(create_app(database.path, root / "blobs", root))
    page_id = client.get("/api/books/e1").json()["page_list"][0]["id"]
    assert client.get(f"/api/pages/{page_id}/thumb").status_code == 404
    research = client.post("/api/research", json={"question": "What did Azazel teach?"}).json()
    assert research["findings"] == []


# ------------------------------------------------------------------ revision 3
def test_interrupted_relocation_resumes(home, tmp_path):
    database, root, _ = home
    target = tmp_path / "AKI home"
    blobs = sorted(p for p in (root / "blobs").rglob("*") if p.is_file())
    # leave the state an interrupted run would: one good copy, one truncated, a stray .part
    good, cut = blobs[0], blobs[1]
    for path, data in ((good, good.read_bytes()), (cut, cut.read_bytes()[:10])):
        dest = target / "blobs" / path.relative_to(root / "blobs")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    (dest.with_name(dest.name + ".part")).write_bytes(b"half")
    (target / "aki.sqlite3.relocating").write_bytes(b"half a database")
    report = relocate(root, target, project_root=root.parent, require_backup=False)
    assert report["vault_files_already_present"] == 1
    for path in blobs:
        copy = target / "blobs" / path.relative_to(root / "blobs")
        assert copy.read_bytes() == path.read_bytes()
    assert Database(target / "aki.sqlite3").integrity_check()["quick_check"] == "ok"


def test_archive_backup_restore_test_and_refusals(home, tmp_path):
    database, root, _ = home
    with pytest.raises(BackupError, match="outside"):
        backup_archive(database, root / "blobs", root / "inside")
    destination = tmp_path / "E drive" / "AKI backup"
    report = backup_archive(database, root / "blobs", destination)
    assert report["restore_test"]["ok"] and report["restore_test"]["originals_verified"] == 1
    assert latest_verified_backup(database)["path"] == str(destination.resolve())
    with pytest.raises(BackupError, match="already holds"):
        backup_archive(database, root / "blobs", destination)
    original = next(p for p in (destination / "blobs").rglob("*") if p.is_file())
    original.write_bytes(b"bit rot")
    result = verify_backup(destination)
    assert not result["ok"] and "does not match" in result["problems"][0]


def test_library_sync_reconciles_by_hash_and_rights(home, tmp_path):
    from aki.library_sync import sync_library

    database, root, snapshot = home
    _seed(database, "e2", "Book Two", url="https://theholyword.net/bible/b2")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO assets (id, edition_id, rights_decision_id, storage_key, sha256, byte_size,"
            " detected_mime, asset_role, preflight_status, created_at)"
            " SELECT 'a2', 'e2', 'rd-e2', storage_key, sha256, byte_size, detected_mime,"
            " 'original_scan', 'passed', created_at FROM assets WHERE edition_id = 'e1'")
    snapshot.write_text(json.dumps([{"id": "b1", "title": "Book One", "category": "Bibles"},
                                    {"id": "b2", "title": "Book Two", "category": "Bibles"}]))
    archive_library = tmp_path / "AKI" / "Library"
    export_library(database, root / "blobs", archive_library, snapshot=snapshot)
    onedrive = tmp_path / "OneDrive project" / "Library"
    (onedrive / "Bibles").mkdir(parents=True)
    good = (archive_library / "Bibles" / "Book One.pdf").read_bytes()
    (onedrive / "Bibles" / "Book One.pdf").write_bytes(b"\0" * len(good))  # same size, wrong bytes
    (onedrive / "Bibles" / "Stray.pdf").write_bytes(b"unknown")
    first = sync_library(database, archive_library, onedrive, report_dir=tmp_path / "logs")
    assert first["counts"] == {"replaced": 1, "copied": 1, "quarantined": 1}
    assert (onedrive / "Bibles" / "Book One.pdf").read_bytes() == good
    assert len(list((onedrive / "_quarantine").rglob("*.pdf"))) == 2  # moved aside, not deleted
    second = sync_library(database, archive_library, onedrive, report_dir=tmp_path / "logs")
    assert second["counts"] == {"identical": 2}
    with database.transaction() as connection:  # The Holy Word narrows the grant for book two
        connection.execute(
            "INSERT INTO rights_decisions (id, edition_id, state, scope, notes, created_at)"
            " VALUES ('rd-e2-narrow', 'e2', 'cleared_limited', ?, 'narrowed', '2999-01-01T00:00:00Z')",
            (json.dumps({"permitted_operations": ["internal_search"]}),))
    third = sync_library(database, archive_library, onedrive, report_dir=tmp_path / "logs")
    assert third["counts"] == {"identical": 1, "quarantined": 1}
    assert not (onedrive / "Bibles" / "Book Two.pdf").exists()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='library_sync'").fetchone()[0] == 3


def test_preflight_measures_and_reports_backup(home, tmp_path):
    database, root, _ = home
    report = preflight(database, root / "blobs")
    assert report["vault_files"] > 0 and report["active_job_leases"] == 0
    assert report["verified_archive_backup"] is None
    backup_archive(database, root / "blobs", tmp_path / "bk")
    assert preflight(database, root / "blobs")["verified_archive_backup"] is not None


def test_serve_check_runs_without_a_browser(home, monkeypatch):
    import argparse

    from aki.cli_room import cmd_serve

    database, root, _ = home
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    code = cmd_serve(argparse.Namespace(database=str(database.path), blob_root=str(root / "blobs"),
                                        host="127.0.0.1", port=8765, open=True, check=True))
    assert code == 0 and opened == []
    assert json.loads((root / "logs" / "serve-check.json").read_text())["ok"]


def test_relocation_refuses_incomplete_vault(home, tmp_path):
    _, root, _ = home
    next(p for p in (root / "blobs").rglob("*") if p.is_file()).unlink()
    with pytest.raises(RelocationError, match="not in the vault"):
        relocate(root, tmp_path / "AKI", project_root=root.parent, require_backup=False)
    assert not (tmp_path / "AKI" / "aki.sqlite3").exists()


def test_relocation_leaves_stray_files_behind(home, tmp_path):
    """An Office lock file beside a page image is not a vault file and must not stop a move."""
    database, root, _ = home
    blob = next(f for f in (root / "blobs" / "sha256").rglob("*") if f.is_file())
    stray = blob.with_name("~$" + blob.name[2:])  # same length as a real name, as Office makes it
    stray.write_bytes(b"lock" * 162)
    target = tmp_path / "local" / "AKI"
    report = relocate(root, target, project_root=root.parent, require_backup=False)
    assert report["stray_files_left_behind"] == [stray.relative_to(root / "blobs").as_posix()]
    assert not (target / "blobs" / stray.relative_to(root / "blobs")).exists()
    assert (target / "blobs" / blob.relative_to(root / "blobs")).read_bytes() == blob.read_bytes()
    assert stray.exists()  # nothing at the source is deleted


def test_keep_awake_never_fails_and_is_released(monkeypatch):
    """The stay-awake request is best effort on Windows and a no-op elsewhere."""
    import sys

    from aki import cli_room

    from aki import power

    assert power.keep_awake(True) in (True, False)
    assert power.keep_awake(False) in (True, False)
    if sys.platform == "win32":
        assert power.full_speed() is True  # this process may always opt itself out
    monkeypatch.setattr(sys, "platform", "linux")
    assert power.keep_awake(True) is False and power.full_speed() is False
    monkeypatch.undo()

    calls = []
    monkeypatch.setattr(cli_room, "keep_awake", lambda on: calls.append(on) or True)
    monkeypatch.setattr(cli_room, "_finish", lambda args: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        cli_room.cmd_finish(None)
    assert calls == [True, False]  # released even when the run fails


def test_books_are_shared_out_by_load_not_by_hash():
    """One huge scanned book must not leave its worker starved while others sit idle."""
    from aki.cli_room import plan_workers

    costs = {"bible": 1224.0, "a": 300.0, "b": 290.0, "c": 280.0, "d": 40.0, "e": 30.0,
             "f": 5.0, "g": 4.0}
    plan = plan_workers(costs, workers=4, cores=24)
    dealt = [edition for share in plan for edition in share["editions"]]
    assert sorted(dealt) == sorted(costs)  # every book exactly once
    heavy = next(share for share in plan if "bible" in share["editions"])
    assert heavy["editions"] == ["bible"]  # the big book reads alone
    assert heavy["threads"] == max(share["threads"] for share in plan) >= 10
    assert sum(share["threads"] for share in plan) <= 24  # the cores are filled, not flooded
    assert all(share["threads"] >= 1 for share in plan)
    others = [share["load"] for share in plan if share is not heavy]
    assert max(others) - min(others) <= 60  # the rest are balanced
    for share in plan:  # short books first within a worker
        assert share["editions"] == sorted(share["editions"], key=lambda e: (costs[e], e))

    assert plan_workers({"only": 10.0}, workers=8, cores=24) == [
        {"editions": ["only"], "load": 10.0, "threads": 24}]
    assert plan_workers({}, workers=8, cores=24) == []
    assert plan_workers({"x": 1.0, "y": 1.0}, workers=2, cores=1)[0]["threads"] == 1
