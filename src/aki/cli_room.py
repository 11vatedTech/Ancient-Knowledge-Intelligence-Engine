"""CLI commands for completing the corpus and opening the reading room.

``aki finish`` is the one command an operator needs: it brings every acquired edition to
a readable, searchable state and builds the derived layers, resuming wherever an earlier
run stopped. Each stage is also available on its own.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .db import Database, initialize, utcnow
from .power import full_speed, keep_awake


def _emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), flush=True)


_LOG: list[Path] = []


def _say(message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    for path in _LOG:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _state_root(args: argparse.Namespace) -> Path:
    return Path(args.database).resolve().parent


# ------------------------------------------------------------------ catalog categories
def enrich_categories(database: Database, snapshot: Path) -> dict[str, int]:
    """Copy each book's shelf (category) as listed by the source into its source record."""
    books = json.loads(snapshot.read_text(encoding="utf-8"))
    by_id = {str(b["id"]): b for b in books}
    updated = 0
    with database.transaction() as connection:
        rows = connection.execute("SELECT id, record_url, category_labels FROM source_records").fetchall()
        for row in rows:
            book = by_id.get(str(row["record_url"]).rstrip("/").rsplit("/", 1)[-1])
            if not book or not book.get("category"):
                continue
            labels = json.dumps([book["category"]])
            if labels != row["category_labels"]:
                connection.execute(
                    "UPDATE source_records SET category_labels = ? WHERE id = ?", (labels, row["id"]))
                updated += 1
    return {"records": len(rows), "updated": updated}


def cmd_catalog_enrich(args: argparse.Namespace) -> int:
    _emit(enrich_categories(Database(args.database), Path(args.snapshot)))
    return 0


# ------------------------------------------------------------------ media
def run_media(database: Database, blob_root: str, ocr: bool) -> dict[str, Any]:
    from .media import ingest_media_asset, pending_media
    from .ocr import OcrUnavailable, resolve_provider

    engine = None
    if ocr:
        try:
            engine = resolve_provider(None)
        except OcrUnavailable:
            engine = None
    results = []
    for asset in pending_media(database, blob_root):
        try:
            results.append(ingest_media_asset(database, blob_root, asset, ocr_provider=engine).to_dict())
        except Exception as exc:  # noqa: BLE001 - report and continue
            results.append({"edition_id": asset["edition_id"], "error": f"{type(exc).__name__}: {exc}"})
    return {"assets": len(results), "ocr_engine": getattr(engine, "name", None) if engine else None,
            "results": results}


def cmd_media_ingest(args: argparse.Namespace) -> int:
    report = run_media(Database(args.database), args.blob_root, args.ocr)
    _emit(report)
    return 0 if all("error" not in r for r in report["results"]) else 1


# ------------------------------------------------------------------ semantic
def cmd_semantic(args: argparse.Namespace) -> int:
    from .semantic import build_index, index_status, load_index

    database = Database(args.database)
    if args.action == "build":
        _emit(build_index(database, _state_root(args), provider=args.provider, model_name=args.model))
    elif args.action == "status":
        _emit(index_status(_state_root(args)) or {"built": False})
    else:
        index = load_index(_state_root(args))
        _emit([{"chunk_id": m.chunk_id, "score": round(m.score, 4)}
               for m in index.query(args.text, limit=args.limit)])
    return 0


# ------------------------------------------------------------------ concordance and plates
def cmd_concordance(args: argparse.Namespace) -> int:
    from .concordance import build, status

    database = Database(args.database)
    _emit(build(database) if args.action == "build" else status(database))
    return 0


def cmd_plates(args: argparse.Namespace) -> int:
    from .plates import detect

    _emit(detect(Database(args.database), args.blob_root, limit=args.limit))
    return 0


# ------------------------------------------------------------------ serve
def cmd_serve(args: argparse.Namespace) -> int:
    from .app.server import BindRefused, require_loopback

    try:
        require_loopback(args.host)
    except BindRefused as exc:
        _emit({"error": str(exc)})
        return 2
    from .app.api import create_app

    app = create_app(args.database, args.blob_root, _state_root(args))
    if args.check or os.environ.get("AKI_SERVE_CHECK") == "1":
        return _serve_check(app, _state_root(args))
    import uvicorn

    url = f"http://{args.host}:{args.port}/"
    _say(f"The Felz Reading Room is open at {url}  (press Ctrl+C to close it)")
    if args.open:
        import threading
        import webbrowser

        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def _serve_check(app: Any, state_root: Path) -> int:
    """Start the app in-process, exercise its main routes, and stop. Opens no browser."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    checks: dict[str, Any] = {}
    overview = client.get("/api/overview")
    checks["overview"] = overview.status_code
    books = client.get("/api/books").json() if overview.status_code == 200 else []
    checks["books"] = len(books)
    readable = next((b for b in books if b["pages"]), None)
    if readable:
        page_list = client.get(f"/api/books/{readable['id']}").json()["page_list"]
        page = client.get(f"/api/pages/{page_list[0]['id']}")
        checks["page"] = page.status_code
        checks["image"] = client.get(f"/api/pages/{page_list[0]['id']}/image").status_code
    checks["home"] = client.get("/").status_code
    ok = all(v == 200 for k, v in checks.items() if k != "books") and checks["books"] > 0
    marker = state_root / "logs" / "serve-check.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"ok": ok, "checks": checks, "at": utcnow()}), encoding="utf-8")
    _emit({"ok": ok, "checks": checks})
    return 0 if ok else 1


# ------------------------------------------------------------------ finish
def progress(database: Database) -> dict[str, Any]:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT (SELECT COUNT(*) FROM assets a JOIN editions e ON e.id = a.edition_id"
            "        WHERE a.asset_role = 'original_scan' AND e.source_record_id IS NOT NULL) AS books,"
            # Count only catalogued books, as the total does; the archive also holds a small
            # test fixture with no source record, which made the counter read "258 of 257".
            " (SELECT COUNT(DISTINCT a.id) FROM assets a JOIN editions e ON e.id = a.edition_id"
            "    JOIN pages p ON p.asset_id = a.id"
            "    WHERE a.asset_role = 'original_scan' AND e.source_record_id IS NOT NULL) AS books_with_pages,"
            " (SELECT COUNT(*) FROM pages) AS pages,"
            " (SELECT COUNT(*) FROM chunk_records) AS text_pages"
        ).fetchone()
    return dict(row)


def cmd_finish(args: argparse.Namespace) -> int:
    full_speed()
    if keep_awake(True):
        _say("This computer will stay awake until the run ends (the screen may still turn off).")
    try:
        return _finish(args)
    finally:
        keep_awake(False)


def _finish(args: argparse.Namespace) -> int:
    started = time.time()
    database_path = Path(args.database).resolve()
    if args.log_file:
        log = Path(args.log_file)
        log.parent.mkdir(parents=True, exist_ok=True)
        _LOG[:] = [log]
    _say(f"Archive: {database_path.parent}")
    _say("Checking the database")
    report = initialize(database_path)
    database = Database(database_path)
    summary: dict[str, Any] = {"started_at": utcnow(), "migrated": report["applied"]}

    snapshot = Path(args.snapshot)
    if snapshot.is_file():
        summary["categories"] = enrich_categories(database, snapshot)

    if not args.skip_ingest:
        from .ingest import pending_editions

        pending = pending_editions(database, args.blob_root)
        _say(f"{len(pending)} PDF books still need reading, with up to {args.workers} workers"
             f"{' and OCR' if args.ocr else ''}. This is the long part; it resumes if interrupted.")
        if pending:
            summary["ingest"] = _parallel_ingest(args, pending)
        _say("Reading the image and EPUB books")
        summary["media"] = run_media(database, args.blob_root, args.ocr)

    if not args.skip_semantic:
        from .semantic import SemanticUnavailable, build_index

        _say(f"Building meaning search ({args.provider})")
        try:
            summary["semantic"] = build_index(database, database_path.parent, provider=args.provider,
                                              model_name=args.model)
        except SemanticUnavailable as exc:
            summary["semantic"] = {"error": str(exc)}

    from .concordance import build as build_concordance
    from .plates import detect

    _say("Building the index of names, places and ideas")
    summary["concordance"] = build_concordance(database)
    _say("Looking for plates, maps and diagrams")
    summary["plates"] = detect(database, args.blob_root)

    summary["progress"] = progress(database)
    summary["integrity"] = database.integrity_check()
    summary["minutes"] = round((time.time() - started) / 60, 1)
    problems = _problems(summary)
    summary["ok"] = not problems
    summary["problems"] = problems
    logs = database_path.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out = logs / f"finish-report-{stamp}.json"
    text = json.dumps(summary, indent=2, ensure_ascii=False)
    out.write_text(text, encoding="utf-8")
    (database_path.parent / "finish-report.json").write_text(text, encoding="utf-8")
    _emit({k: v for k, v in summary.items() if k != "media"})
    for path in _LOG:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    if problems:
        _say("Finished with problems; nothing was lost and re-running resumes:")
        for problem in problems:
            _say(f"  - {problem}")
        _say(f"Report: {out}")
        return 1
    _say(f"Done in {summary['minutes']} minutes. Report written to {out}")
    return 0


def _problems(summary: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    ingest = summary.get("ingest") or {}
    failed = [i for i, code in enumerate(ingest.get("exit_codes", [])) if code != 0]
    if failed:
        problems.append(f"ingest workers {failed} exited with errors; see their logs in logs/")
    media_errors = [r for r in (summary.get("media") or {}).get("results", []) if "error" in r]
    if media_errors:
        problems.append(f"{len(media_errors)} image/EPUB books failed: "
                        + "; ".join(r["error"] for r in media_errors[:3]))
    if isinstance(summary.get("semantic"), dict) and "error" in summary["semantic"]:
        problems.append(f"meaning search was not built: {summary['semantic']['error']}")
    integrity = summary.get("integrity") or {}
    if integrity.get("quick_check") != "ok" or integrity.get("foreign_key_violations"):
        problems.append(f"database integrity check failed: {integrity}")
    return problems


def ocr_threads(workers: int) -> int:
    """Threads each OCR worker may use so that together they fill, not flood, the cores."""
    return max(1, (os.cpu_count() or 2) // max(1, workers))


def default_workers() -> int:
    """About three cores per worker, at most eight workers, leaving the rest of the machine usable."""
    return max(1, min(8, (os.cpu_count() or 2) // 3))


TEXT_PAGE_COST = 0.02  # a page that carries its own text, relative to one read by OCR


def reading_cost(pdf_path: Path, *, samples: int = 12) -> float:
    """Roughly how much work a book is, in OCR-page equivalents.

    Looks at up to ``samples`` pages spread through the book: a page with a usable text
    layer is nearly free, a page without one has to be read by OCR. A book that cannot be
    opened is costed as all OCR so that it is never underestimated.
    """
    try:
        import pymupdf

        document = pymupdf.open(str(pdf_path))
        pages = document.page_count
        if pages <= 0:
            return 1.0
        step = max(1, pages // samples)
        looked = [len(document[i].get_text().strip()) < 120 for i in range(0, pages, step)][:samples]
        scanned = sum(looked) / len(looked)
        return pages * (scanned + (1 - scanned) * TEXT_PAGE_COST)
    except Exception:  # noqa: BLE001 - an unreadable book still has to be attempted
        return 500.0


def plan_workers(costs: dict[str, float], workers: int, cores: int) -> list[dict[str, Any]]:
    """Deal books to workers so that no worker is left holding most of the slow pages.

    Dealing by a hash of the book's id (the old way) put two thirds of the remaining OCR
    on two of twelve workers while five sat idle. Here the heaviest books are placed
    first, each on the least loaded worker, and each worker's share of the cores follows
    its share of the load, so a worker holding one very large scanned book gets the
    threads to read it. Within a worker, short books go first so they open sooner.
    """
    workers = max(1, min(workers, len(costs) or 1))
    shares: list[dict[str, Any]] = [{"editions": [], "load": 0.0} for _ in range(workers)]
    for edition, cost in sorted(costs.items(), key=lambda item: (-item[1], item[0])):
        lightest = min(shares, key=lambda share: share["load"])
        lightest["editions"].append(edition)
        lightest["load"] += cost
    shares = [share for share in shares if share["editions"]]
    if not shares:
        return []
    total = sum(share["load"] for share in shares) or 1.0
    spare = max(0, cores - len(shares))
    for share in shares:
        share["threads"] = 1 + int(spare * share["load"] / total)
    for _ in range(max(0, cores - sum(share["threads"] for share in shares))):
        # Cores left over by rounding go, one at a time, to whoever has most work per thread.
        max(shares, key=lambda share: share["load"] / share["threads"])["threads"] += 1
    for share in shares:
        share["editions"].sort(key=lambda edition: (costs[edition], edition))
        share["load"] = round(share["load"], 1)
    return shares


def _parallel_ingest(args: argparse.Namespace, pending: list[tuple[str, Path]]) -> dict[str, Any]:
    base_env = {**os.environ, "AKI_SQLITE_SYNCHRONOUS": "NORMAL",
                "AKI_SQLITE_BUSY_MS": os.environ.get("AKI_SQLITE_BUSY_MS", "600000")}
    state = Path(args.database).resolve().parent
    logs = state / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    _say("Looking through the unread books to share them out fairly")
    costs = {edition: reading_cost(path) for edition, path in pending}
    plan = plan_workers(costs, args.workers, os.cpu_count() or 2)
    (logs / f"finish-{stamp}-plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    _say("Shares (books, estimated OCR pages, threads): "
         + ", ".join(f"{len(s['editions'])}/{round(s['load'])}/{s['threads']}" for s in plan))
    processes = []
    for number, share in enumerate(plan):
        share_file = logs / f"finish-{stamp}-share{number}.json"
        share_file.write_text(json.dumps(share["editions"]), encoding="utf-8")
        command = [sys.executable, "-m", "aki.cli", "--database", args.database,
                   "--blob-root", args.blob_root, "ingest-batch", "--dpi", str(args.dpi),
                   "--editions-file", str(share_file),
                   "--log", str(state / "ingest-progress.jsonl")]
        if args.ocr:
            command.append("--ocr")
        # Each worker gets its share of the cores instead of claiming all of them (ADR-026),
        # spent as that many single-threaded readers working on different pages at once,
        # which reads a dense book about two and a half times faster than one reader
        # given the same threads (ADR-028).
        env = {**base_env}
        env.setdefault("AKI_OCR_THREADS", "1")
        env.setdefault("AKI_OCR_PROCESSES", str(share["threads"]))
        out = (logs / f"finish-{stamp}-worker{number}.log").open("w", encoding="utf-8")
        processes.append((subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT, env=env), out))
    database = Database(args.database)
    last = None
    while any(p.poll() is None for p, _ in processes):
        time.sleep(30)
        try:
            state = progress(database)
        except Exception:  # noqa: BLE001 - a busy database just delays the progress line
            continue
        line = f"{state['books_with_pages']} of {state['books']} books read, {state['pages']:,} pages"
        if line != last:
            _say(line)
            last = line
    codes = [p.returncode for p, _ in processes]
    for _, handle in processes:
        handle.close()
    return {"workers": len(plan), "pending_at_start": len(pending), "exit_codes": codes,
            "plan": [{"books": len(s["editions"]), "load": s["load"], "threads": s["threads"]}
                     for s in plan],
            "logs": [str(logs / f"finish-{stamp}-worker{n}.log") for n in range(len(plan))]}


def cmd_relocate(args: argparse.Namespace) -> int:
    from .home import RelocationError, relocate

    source = Path(args.database).resolve().parent
    try:
        report = relocate(source, args.to, project_root=Path.cwd(), allow_cloud=args.allow_cloud,
                          require_backup=not args.without_backup, progress=_say)
    except RelocationError as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1
    _emit({"ok": True, **report})
    return 0


def cmd_library_export(args: argparse.Namespace) -> int:
    from .library_export import export_library

    database = Database(args.database)
    out = Path(args.out) if args.out else Path(args.database).resolve().parent / "Library"
    report = export_library(database, args.blob_root, out, snapshot=args.snapshot,
                            budget_seconds=args.budget)
    _emit(report)
    return 0 if report["ok"] else 1


def cmd_vault_manifest(args: argparse.Namespace) -> int:
    """List every original with its SHA-256, so a backup can be checked against the vault."""
    database = Database(args.database)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT edition_id, storage_key, sha256, byte_size, detected_mime FROM assets"
            " WHERE asset_role = 'original_scan' ORDER BY storage_key").fetchall()
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"generated_at": utcnow(), "originals": [dict(r) for r in rows]},
                                 indent=1), encoding="utf-8")
    _emit({"path": str(target), "originals": len(rows)})
    return 0


def cmd_archive_backup(args: argparse.Namespace) -> int:
    from .archive_backup import BackupError, backup_archive

    try:
        report = backup_archive(Database(args.database), args.blob_root, args.to,
                                include_pages=args.include_pages, progress=_say)
    except BackupError as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1
    if report["same_device_as_archive"]:
        _say("Note: this backup is on the same drive as the archive, so it does not protect "
             "against that drive failing. A second copy on another drive is better.")
    _emit({"ok": True, **report})
    return 0


def cmd_verify_backup(args: argparse.Namespace) -> int:
    from .archive_backup import verify_backup

    result = verify_backup(args.path)
    _emit(result)
    return 0 if result["ok"] else 1


def cmd_library_sync(args: argparse.Namespace) -> int:
    from .library_sync import sync_library

    home = Path(args.database).resolve().parent
    report = sync_library(Database(args.database), args.source or home / "Library", args.to,
                          report_dir=home / "logs")
    _emit(report)
    return 0


def preflight(database: Database, blob_root: str | Path) -> dict[str, Any]:
    """Facts to check immediately before moving or processing the archive."""
    import shutil as _shutil

    from .archive_backup import latest_verified_backup

    home = Path(database.path).resolve().parent
    root = Path(blob_root)
    with database.connect() as connection:
        originals = connection.execute(
            "SELECT COALESCE(SUM(byte_size), 0) FROM (SELECT DISTINCT storage_key, byte_size"
            " FROM assets)").fetchone()[0]
        active = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE state IN ('leased','running')"
            " AND (lease_expires_at IS NULL OR lease_expires_at > ?)", (utcnow(),)).fetchone()[0]
    vault_files = vault_bytes = 0
    if root.exists():
        for entry in root.rglob("*"):
            if entry.is_file():
                vault_files += 1
                vault_bytes += entry.stat().st_size
    wal = Path(str(database.path) + "-wal")
    progress_log = home / "ingest-progress.jsonl"
    return {
        "archive_home": str(home),
        "schema_version": database.schema_version(),
        "database_bytes": Path(database.path).stat().st_size,
        "wal_bytes": wal.stat().st_size if wal.exists() else 0,
        "originals_bytes": int(originals),
        "vault_files": vault_files, "vault_bytes": vault_bytes,
        "free_bytes_at_archive": _shutil.disk_usage(home).free,
        "active_job_leases": int(active),
        "ingest_log_modified_seconds_ago": round(time.time() - progress_log.stat().st_mtime)
        if progress_log.exists() else None,
        "verified_archive_backup": latest_verified_backup(database),
        "measured_at": utcnow(),
    }


def cmd_preflight(args: argparse.Namespace) -> int:
    report = preflight(Database(args.database), args.blob_root)
    busy = report["active_job_leases"] > 0 or (
        report["ingest_log_modified_seconds_ago"] is not None
        and report["ingest_log_modified_seconds_ago"] < 120)
    report["busy"] = busy
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    _emit(report)
    return 2 if busy else 0


def register(sub: Any) -> None:
    archive_backup = sub.add_parser("archive-backup", help="recoverable copy of database and originals")
    archive_backup.add_argument("--to", required=True)
    archive_backup.add_argument("--include-pages", action="store_true")
    archive_backup.set_defaults(func=cmd_archive_backup)

    verify = sub.add_parser("verify-backup", help="restore-test an archive backup")
    verify.add_argument("path")
    verify.set_defaults(func=cmd_verify_backup)

    library_sync = sub.add_parser("library-sync", help="hash-checked copy of the Library elsewhere")
    library_sync.add_argument("--to", required=True)
    library_sync.add_argument("--source", default=None, help="default: <archive home>/Library")
    library_sync.set_defaults(func=cmd_library_sync)

    pre = sub.add_parser("preflight", help="measure the archive and check nothing is running")
    pre.add_argument("--out", default=None)
    pre.set_defaults(func=cmd_preflight)

    relocate_parser = sub.add_parser("relocate", help="move the archive to a local folder, verified")
    relocate_parser.add_argument("--to", required=True)
    relocate_parser.add_argument("--allow-cloud", action="store_true")
    relocate_parser.add_argument("--without-backup", action="store_true",
                                 help="skip the verified-backup requirement (not recommended)")
    relocate_parser.set_defaults(func=cmd_relocate)

    library = sub.add_parser("library-export", help="readable copies of the originals, by shelf")
    library.add_argument("--out", default=None, help="default: <archive home>/Library")
    library.add_argument("--snapshot", default="data/holyword/books_raw.json")
    library.add_argument("--budget", type=float, default=None, help="stop copying after N seconds")
    library.set_defaults(func=cmd_library_export)

    manifest = sub.add_parser("vault-manifest", help="write the list of originals and their hashes")
    manifest.add_argument("out")
    manifest.set_defaults(func=cmd_vault_manifest)

    enrich = sub.add_parser("catalog-enrich", help="record each book's shelf from the source snapshot")
    enrich.add_argument("--snapshot", default="data/holyword/books_raw.json")
    enrich.set_defaults(func=cmd_catalog_enrich)

    media = sub.add_parser("media-ingest", help="read the image and EPUB editions")
    media.add_argument("--ocr", action="store_true")
    media.set_defaults(func=cmd_media_ingest)

    semantic = sub.add_parser("semantic", help="meaning-based search index")
    semantic_sub = semantic.add_subparsers(dest="action", required=True)
    build = semantic_sub.add_parser("build", help="embed every permitted passage")
    build.add_argument("--provider", default="auto", choices=["auto", "model2vec", "lsa"])
    build.add_argument("--model", default="minishlab/potion-retrieval-32M")
    build.set_defaults(func=cmd_semantic)
    semantic_sub.add_parser("status").set_defaults(func=cmd_semantic)
    query = semantic_sub.add_parser("query", help="query the meaning index")
    query.add_argument("text")
    query.add_argument("--limit", type=int, default=10)
    query.set_defaults(func=cmd_semantic)

    concordance = sub.add_parser("concordance", help="index of names, places, books and ideas")
    concordance_sub = concordance.add_subparsers(dest="action", required=True)
    concordance_sub.add_parser("build").set_defaults(func=cmd_concordance)
    concordance_sub.add_parser("status").set_defaults(func=cmd_concordance)

    plates = sub.add_parser("plates", help="find plates, maps and diagrams")
    plates.add_argument("--limit", type=int, default=None)
    plates.set_defaults(func=cmd_plates)

    serve = sub.add_parser("serve", help="open the reading room on this computer")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true", help="open it in the default browser")
    serve.add_argument("--check", action="store_true", help="test the routes and exit; no browser")
    serve.set_defaults(func=cmd_serve)

    finish = sub.add_parser("finish", help="read every remaining book and build all search layers")
    finish.add_argument("--workers", type=int, default=default_workers())
    finish.add_argument("--dpi", type=int, default=200)
    finish.add_argument("--ocr", action="store_true", default=True)
    finish.add_argument("--no-ocr", dest="ocr", action="store_false")
    finish.add_argument("--provider", default="auto", choices=["auto", "model2vec", "lsa"])
    finish.add_argument("--model", default="minishlab/potion-retrieval-32M")
    finish.add_argument("--snapshot", default="data/holyword/books_raw.json")
    finish.add_argument("--skip-ingest", action="store_true")
    finish.add_argument("--skip-semantic", action="store_true")
    finish.add_argument("--log-file", default=None, help="also append progress to this file")
    finish.set_defaults(func=cmd_finish)
