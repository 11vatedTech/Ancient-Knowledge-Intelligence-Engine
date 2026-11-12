"""End-to-end ingestion: archive, render, transcribe, cite, retrieve, verify."""
from __future__ import annotations

import json

import pytest

from aki.db import Database, initialize, utcnow
from aki.ingest import ingest_pdf
from aki.research import run_research
from aki.search import search

pymupdf = pytest.importorskip("pymupdf")

PAGE_TEXTS = [
    "ARCHIVE FIXTURE PAGE ONE The archive preserves the original image exactly.",
    "CITATION FIXTURE PAGE TWO A citation resolves to an edition and a page rectangle.",
]


def _make_pdf(path):
    document = pymupdf.open()
    for text in PAGE_TEXTS:
        page = document.new_page(width=420, height=600)
        page.insert_text((40, 80), text, fontsize=9)
    document.save(str(path))
    return path


@pytest.fixture()
def seeded(tmp_path):
    database = Database(tmp_path / "aki.sqlite3")
    initialize(database.path)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO works (id, canonical_title, normalized_title, work_class, metadata,"
            " created_at) VALUES ('w1','Fixture','fixture','modern_commentary','{}',?)",
            (utcnow(),),
        )
        connection.execute(
            "INSERT INTO editions (id, work_id, edition_title, edition_type, metadata, created_at)"
            " VALUES ('e1','w1','Fixture edition','transcription','{}',?)",
            (utcnow(),),
        )
    pdf = _make_pdf(tmp_path / "fixture.pdf")
    return database, pdf, tmp_path / "blobs"


_ALL_OPERATIONS = [
    "store_original", "render_pages", "ocr_transcribe",
    "internal_search", "internal_research",
]


def _grant(database, state="cleared_limited", operations=None):
    operations = _ALL_OPERATIONS if operations is None else operations
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO rights_decisions (id, edition_id, state, scope, notes, created_at)"
            " VALUES ('rd1','e1',?,?,'test grant',?)",
            (state, json.dumps({"permitted_operations": operations}), utcnow()),
        )


def test_ingest_denied_without_rights_decision(seeded):
    database, pdf, blobs = seeded
    with pytest.raises(PermissionError):
        ingest_pdf(database, blobs, pdf, edition_id="e1", dpi=150)
    assert database.coverage()["assets"] == 0


def test_ingest_denied_when_rights_state_is_review_required(seeded):
    database, pdf, blobs = seeded
    _grant(database, state="review_required", operations=[])
    with pytest.raises(PermissionError):
        ingest_pdf(database, blobs, pdf, edition_id="e1", dpi=150)


def test_ingest_is_denied_when_operation_not_permitted(seeded):
    database, pdf, blobs = seeded
    _grant(database, operations=["internal_search"])
    with pytest.raises(PermissionError):
        ingest_pdf(database, blobs, pdf, edition_id="e1", dpi=150)


def test_full_pipeline_ingest_search_and_verify(seeded):
    database, pdf, blobs = seeded
    _grant(database)
    result = ingest_pdf(database, blobs, pdf, edition_id="e1", dpi=150)

    assert result.skipped is False
    assert result.pages_rendered == len(PAGE_TEXTS)
    assert result.transcriptions_created == len(PAGE_TEXTS)
    assert result.evidence_created == len(PAGE_TEXTS)
    assert result.chunks_indexed == len(PAGE_TEXTS)

    coverage = database.coverage()
    assert coverage["pages"] == len(PAGE_TEXTS)
    assert coverage["pages_completed"] == len(PAGE_TEXTS)
    assert coverage["assets_verified"] == 1

    hits = search(database, "citation resolves page rectangle", limit=5)
    assert hits
    assert hits[0].page_index == 1
    assert hits[0].rights_state == "cleared_limited"

    research = run_research(database, "How is a citation resolved to a page?", limit=5)
    assert research.verification.ok
    assert research.claim_ids
    assert "Cite:" in research.dossier


def test_reingest_identical_bytes_is_idempotent(seeded):
    database, pdf, blobs = seeded
    _grant(database)
    first = ingest_pdf(database, blobs, pdf, edition_id="e1", dpi=150)
    second = ingest_pdf(database, blobs, pdf, edition_id="e1", dpi=150)
    assert first.skipped is False
    assert second.skipped is True
    assert database.coverage()["assets"] == 1
    assert database.coverage()["pages"] == len(PAGE_TEXTS)


def test_vault_keys_are_portable(seeded):
    database, pdf, blobs = seeded
    _grant(database)
    ingest_pdf(database, blobs, pdf, edition_id="e1", dpi=150)
    with database.connect() as connection:
        page = connection.execute("SELECT page_image_key FROM pages LIMIT 1").fetchone()
        asset = connection.execute("SELECT storage_key FROM assets LIMIT 1").fetchone()
    assert ":" not in page["page_image_key"]
    assert page["page_image_key"].startswith("sha256/")
    assert asset["storage_key"].startswith("sha256/")


def test_reingest_repairs_an_interrupted_render(seeded):
    """A worker killed mid-document must not strand the edition forever."""
    database, pdf, blobs = seeded
    _grant(database)
    first = ingest_pdf(database, blobs, pdf, edition_id="e1", dpi=150)
    assert first.pages_recorded == len(PAGE_TEXTS)

    with database.transaction() as connection:
        page = connection.execute(
            "SELECT id FROM pages ORDER BY page_index DESC LIMIT 1"
        ).fetchone()
        connection.execute(
            "DELETE FROM chunks_fts WHERE chunk_id IN"
            " (SELECT id FROM chunk_records WHERE page_id=?)", (page["id"],)
        )
        connection.execute("DELETE FROM chunk_records WHERE page_id=?", (page["id"],))
        connection.execute("DELETE FROM evidence WHERE page_id=?", (page["id"],))
        connection.execute("DELETE FROM pages WHERE id=?", (page["id"],))
    assert database.coverage()["pages"] == len(PAGE_TEXTS) - 1

    second = ingest_pdf(database, blobs, pdf, edition_id="e1", dpi=150)
    assert second.skipped is False
    assert database.coverage()["pages"] == len(PAGE_TEXTS)
    assert database.coverage()["transcriptions"] == len(PAGE_TEXTS)


def test_ingest_rejects_missing_edition_file(seeded, tmp_path):
    database, pdf, blobs = seeded
    _grant(database)
    with pytest.raises(FileNotFoundError):
        ingest_pdf(database, blobs, tmp_path / "absent.pdf", edition_id="e1")
