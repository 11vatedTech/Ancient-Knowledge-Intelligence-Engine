"""OCR provider contract, availability reporting and ingestion integration."""
from __future__ import annotations

import json

import pytest

from aki.db import Database, initialize, utcnow
from aki.ingest import ingest_pdf
from aki.ocr import OcrLine, OcrPage, OcrUnavailable, provider_status, resolve_provider

pymupdf = pytest.importorskip("pymupdf")


class FakeProvider:
    """Deterministic stand-in so the integration test needs no model weights."""

    name = "FakeOCR"
    version = "9.9"

    def __init__(self, text="RECOGNISED FROM IMAGE ONLY"):
        self.text = text
        self.calls = []

    def ocr(self, image_path):
        self.calls.append(str(image_path))
        return OcrPage(
            text=self.text,
            confidence=0.81,
            lines=(OcrLine(self.text, 0.81, (1.0, 2.0, 30.0, 12.0)),),
            engine_name=self.name,
            engine_version=self.version,
        )


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
            " VALUES ('e1','w1','Scan','transcription','{}',?)",
            (utcnow(),),
        )
    # A PDF with no text layer: exactly the case OCR exists for.
    pdf = tmp_path / "scan.pdf"
    document = pymupdf.open()
    page = document.new_page(width=400, height=500)
    page.draw_rect(pymupdf.Rect(40, 40, 300, 120), color=(0, 0, 0), fill=(0.2, 0.2, 0.2))
    document.save(str(pdf))
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO rights_decisions (id, edition_id, state, scope, notes, created_at)"
            " VALUES ('rd1','e1','cleared_limited',?, 'test', ?)",
            (json.dumps({"permitted_operations": [
                "store_original", "render_pages", "ocr_transcribe",
                "internal_search", "internal_research",
            ]}), utcnow()),
        )
    return database, pdf, tmp_path / "blobs"


def test_provider_status_reports_availability_without_raising():
    status = provider_status()
    assert {entry["provider"] for entry in status} == {"rapidocr", "tesseract"}
    for entry in status:
        assert isinstance(entry["available"], bool)
        if not entry["available"]:
            assert "engine_version" not in entry


def test_unknown_provider_is_rejected_by_name():
    with pytest.raises(OcrUnavailable):
        resolve_provider("does-not-exist")


def test_scan_without_ocr_records_no_text(seeded):
    database, pdf, blobs = seeded
    result = ingest_pdf(database, blobs, pdf, edition_id="e1", dpi=100)
    assert result.pages_ocr == 0
    assert result.evidence_created == 0
    with database.connect() as connection:
        row = connection.execute("SELECT text_content FROM transcriptions").fetchone()
    assert row["text_content"] == ""


def test_scan_with_ocr_records_engine_identity_and_evidence(seeded):
    database, pdf, blobs = seeded
    provider = FakeProvider()
    result = ingest_pdf(
        database, blobs, pdf, edition_id="e1", dpi=100,
        ocr=True, ocr_provider=provider,
    )
    assert result.pages_ocr == 1
    assert result.evidence_created == 1
    assert result.chunks_indexed == 1
    assert len(provider.calls) == 1
    with database.connect() as connection:
        row = connection.execute(
            "SELECT engine_name, engine_version, confidence, token_geometry, text_content"
            " FROM transcriptions"
        ).fetchone()
    assert row["engine_name"] == "FakeOCR"
    assert row["engine_version"] == "9.9"
    assert row["confidence"] == pytest.approx(0.81)
    assert row["text_content"] == "RECOGNISED FROM IMAGE ONLY"
    geometry = json.loads(row["token_geometry"])
    assert geometry[0]["box"] == [1.0, 2.0, 30.0, 12.0]


def test_text_layer_is_not_overwritten_by_ocr(seeded, tmp_path):
    database, _pdf, blobs = seeded
    textual = tmp_path / "textual.pdf"
    document = pymupdf.open()
    page = document.new_page(width=420, height=600)
    for line in range(10):
        page.insert_text((20, 40 + line * 16), "AUTHORITATIVE TEXT LAYER LINE", fontsize=8)
    document.save(str(textual))
    provider = FakeProvider()
    result = ingest_pdf(
        database, blobs, textual, edition_id="e1", dpi=100,
        ocr=True, ocr_provider=provider,
    )
    assert result.pages_ocr == 0
    assert provider.calls == []
    with database.connect() as connection:
        row = connection.execute("SELECT engine_name FROM transcriptions").fetchone()
    assert row["engine_name"] == "PyMuPDF-text-layer"


def test_ocr_threads_are_shared_between_workers(monkeypatch):
    """Workers divide the cores between them, and a worker caps the sessions RapidOCR builds."""
    import os

    from aki import cli_room
    from aki.ocr import _cap_threads

    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    assert cli_room.default_workers() == 8
    assert cli_room.ocr_threads(8) == 3 and cli_room.ocr_threads(6) == 4
    assert cli_room.ocr_threads(64) == 1
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    assert cli_room.default_workers() == 1

    utils = pytest.importorskip("rapidocr_onnxruntime.utils")
    if not hasattr(utils, "SessionOptions"):
        monkeypatch.setenv("AKI_OCR_THREADS", "3")
        _cap_threads()  # a RapidOCR without the hook is left alone, never fatal
        return
    original = utils.SessionOptions
    try:
        monkeypatch.setenv("AKI_OCR_THREADS", "3")
        _cap_threads()
        _cap_threads()  # capping twice must not wrap the wrapper
        options = utils.SessionOptions()
        assert options.intra_op_num_threads == 3 and options.inter_op_num_threads == 1
        monkeypatch.setenv("AKI_OCR_THREADS", "nonsense")
        _cap_threads()  # ignored, never fatal
    finally:
        utils.SessionOptions = original


class _SlowFake:
    """Stands in for an engine inside pool processes; must live at module level to be found."""

    name = "FakeOCR"
    version = "1"

    @staticmethod
    def available():
        return True

    def ocr(self, image_path):
        import os
        import time

        from aki.ocr import OcrPage

        time.sleep(0.4)
        return OcrPage(text=f"read {os.path.basename(str(image_path))}", confidence=0.9, lines=(),
                       engine_name="FakeOCR", engine_version=f"pid{os.getpid()}")


def test_pooled_ocr_reads_pages_side_by_side_in_order(monkeypatch):
    """A pool returns each page's own result, in the order asked, faster than one at a time."""
    import time

    from aki import ocr

    monkeypatch.setitem(ocr.PROVIDERS, "fake", _SlowFake)
    monkeypatch.setenv("AKI_OCR_PROCESSES", "4")
    engine = ocr.resolve_provider("fake")
    assert isinstance(engine, ocr.PooledOcrProvider) and engine.name == "FakeOCR"
    try:
        warm = [f"warm{n}.png" for n in range(8)]  # process start-up is not what is being timed
        engine.prefetch(warm)
        for page in warm:
            engine.ocr(page)
        pages = [f"page{n}.png" for n in range(8)]
        started = time.perf_counter()
        engine.prefetch(pages)
        results = [engine.ocr(page) for page in pages]
        elapsed = time.perf_counter() - started
        assert [r.text for r in results] == [f"read {page}" for page in pages]
        assert len({r.engine_version for r in results}) > 1  # more than one process did the work
        assert elapsed < 8 * 0.4 * 0.8  # serial would take 3.2 s; four at once about 0.8 s
        assert engine.ocr("late.png").text == "read late.png"  # a page never prefetched still works
    finally:
        engine.close()

    monkeypatch.setenv("AKI_OCR_PROCESSES", "1")
    assert isinstance(ocr.resolve_provider("fake"), _SlowFake)  # one process means no pool


class _FlakyFake(_SlowFake):
    """Fails the first time it sees a page in a given process, then reads it."""

    def ocr(self, image_path):
        import os
        import tempfile

        marker = os.path.join(tempfile.gettempdir(), f"aki-flaky-{os.path.basename(str(image_path))}")
        if not os.path.exists(marker):
            open(marker, "w").close()
            raise RuntimeError("ONNXRuntime inferece failed.")
        os.remove(marker)
        return super().ocr(image_path)


def test_pooled_ocr_retries_a_failed_page_once(monkeypatch, tmp_path):
    from aki import ocr

    monkeypatch.setitem(ocr.PROVIDERS, "flaky", _FlakyFake)
    monkeypatch.setenv("AKI_OCR_PROCESSES", "2")
    engine = ocr.resolve_provider("flaky")
    try:
        page = f"flaky-{tmp_path.name}.png"
        assert engine.ocr(page).text == f"read {page}"
    finally:
        engine.close()
