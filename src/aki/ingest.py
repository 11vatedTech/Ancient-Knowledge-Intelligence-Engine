"""Document ingestion: preflight, immutable archive, page rendering, text layer, evidence.

Every stage records its own provenance and can be re-run without destroying prior work.
A re-ingest of identical bytes is idempotent; a changed source produces a new asset rather
than overwriting the archived original.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .blob_store import ContentAddressedStore
from .db import Database, new_id, utcnow
from .evidence import add_evidence
from .ocr import OcrProvider, resolve_provider
from .pdf import PDFPageProcessor, RenderedPage
from .preflight import PreflightLimits, preflight
from .rights import RightsDenied
from .search import index_chunk

PIPELINE_VERSION = "ingest-v1"


class IngestError(RuntimeError):
    """A document could not be ingested under the current policy."""


@dataclass(frozen=True, slots=True)
class IngestResult:
    asset_id: str
    edition_id: str
    sha256: str
    pages_rendered: int
    pages_recorded: int
    pages_reused: int
    transcriptions_created: int
    evidence_created: int
    chunks_indexed: int
    pages_ocr: int = 0
    skipped: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "edition_id": self.edition_id,
            "sha256": self.sha256,
            "pages_rendered": self.pages_rendered,
            "pages_recorded": self.pages_recorded,
            "pages_reused": self.pages_reused,
            "transcriptions_created": self.transcriptions_created,
            "evidence_created": self.evidence_created,
            "chunks_indexed": self.chunks_indexed,
            "pages_ocr": self.pages_ocr,
            "skipped": self.skipped,
            "warnings": list(self.warnings),
        }


def _require(database: Database, edition_id: str) -> str:
    """Confirm a rights decision permits storage and page rendering."""
    from .production_domain import RightsOperation

    with database.connect() as connection:
        row = connection.execute(
            "SELECT rd.* FROM rights_decisions rd WHERE rd.edition_id = ?"
            " ORDER BY rd.created_at DESC LIMIT 1",
            (edition_id,),
        ).fetchone()
    if row is None:
        raise RightsDenied(f"edition {edition_id} has no rights decision; processing is denied")
    decision = _decision_from_row(row)
    for operation in (RightsOperation.STORE_ORIGINAL, RightsOperation.RENDER_PAGES,
                      RightsOperation.OCR_TRANSCRIBE, RightsOperation.INTERNAL_SEARCH):
        if not decision.permits(operation):
            raise RightsDenied(
                f"rights state {decision.state.value!r} does not permit {operation.value!r} "
                f"for edition {edition_id}"
            )
    return decision.decision_id


def _decision_from_row(row: Any) -> Any:
    from .production_domain import RightsDecision, RightsDecisionState, RightsOperation

    scope = json.loads(row["scope"] or "{}")
    permitted = frozenset(
        RightsOperation(name) for name in scope.get("permitted_operations", [])
    )
    prohibited = frozenset(
        RightsOperation(name) for name in scope.get("prohibited_operations", [])
    )
    return RightsDecision(
        decision_id=row["id"],
        subject_id=row["edition_id"] or row["source_record_id"],
        state=RightsDecisionState(row["state"]),
        permitted_operations=permitted,
        prohibited_operations=prohibited,
        evidence_uri=row["evidence_uri"],
        license_name=row["license_name"],
        license_version=row["license_version"],
        jurisdiction=row["jurisdiction"],
        reviewer=row["reviewer"],
        notes=row["notes"],
    )


def ingest_pdf(
    database: Database,
    blob_root: str | Path,
    pdf_path: str | Path,
    *,
    edition_id: str,
    dpi: int = 300,
    limits: PreflightLimits | None = None,
    extract_text_layer: bool = True,
    ocr: bool = False,
    ocr_provider: OcrProvider | None = None,
    ocr_min_chars: int = 120,
    provider_name: str | None = None,
) -> IngestResult:
    """Archive, render and index one PDF for a rights-cleared edition.

    When ``ocr`` is enabled, any page whose embedded text layer yields fewer than
    ``ocr_min_chars`` characters is passed to the OCR engine, and the resulting text is
    recorded with that engine's identity and confidence. The archived original is never
    modified, so a better model later supersedes the transcription instead of the file.
    """
    database.require_current()
    decision_id = _require(database, edition_id)
    engine: OcrProvider | None = None
    if ocr:
        engine = ocr_provider if ocr_provider is not None else resolve_provider(provider_name)
    source = Path(pdf_path).expanduser().resolve(strict=True)
    store = ContentAddressedStore(blob_root)
    report = preflight(source, limits)
    if not report.accepted:
        raise IngestError(
            f"preflight rejected {source.name}: {'; '.join(report.errors) or report.status}"
        )

    stored = store.put_path(source)
    archive_key = stored.path.relative_to(store.root).as_posix()
    existing = None
    pages_present = False
    with database.connect() as connection:
        existing = connection.execute(
            "SELECT id, preflight_status FROM assets WHERE edition_id=? AND sha256=?"
            " AND asset_role='original_scan'",
            (edition_id, report.sha256),
        ).fetchone()
        recorded_pages = recorded_transcriptions = 0
        if existing is not None:
            recorded_pages = connection.execute(
                "SELECT COUNT(*) AS n FROM pages WHERE asset_id=?", (existing["id"],)
            ).fetchone()["n"]
            recorded_transcriptions = connection.execute(
                "SELECT COUNT(*) AS n FROM transcriptions t JOIN pages p ON p.id=t.page_id"
                " WHERE p.asset_id=?", (existing["id"],)
            ).fetchone()["n"]
    pages_present = recorded_pages > 0
    # An asset that exists but has no pages is an *acquired* object that has never been
    # rendered. Skipping it there would make acquisition and ingestion mutually exclusive.
    if existing is not None and pages_present:
        expected_pages = _pdf_page_count(source)
        complete = (
            (expected_pages is None or recorded_pages >= expected_pages)
            and recorded_transcriptions >= recorded_pages
        )
        if complete:
            return IngestResult(
                asset_id=existing["id"], edition_id=edition_id, sha256=report.sha256,
                pages_rendered=0, pages_recorded=0, pages_reused=0, transcriptions_created=0,
                evidence_created=0, chunks_indexed=0, skipped=True,
                warnings=("identical bytes already archived for this edition",),
            )
        # A previous run was interrupted mid-document. Its derived rows are partial, so
        # they are replaced rather than left to masquerade as a finished edition.
        _purge_derived(database, str(existing["id"]))

    now = utcnow()
    if existing is not None:
        asset_id = str(existing["id"])
    else:
        asset_id = new_id()
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO assets (id, edition_id, rights_decision_id, storage_key, sha256,"
                " byte_size, detected_mime, original_filename, asset_role, acquired_at, immutable,"
                " preflight_status, preflight_report, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    asset_id, edition_id, decision_id, archive_key, report.sha256,
                    report.byte_size, report.detected_mime, source.name, "original_scan",
                    now, 1, "passed", json.dumps(report.to_dict(), sort_keys=True), now,
                ),
            )

    processor = PDFPageProcessor()
    rendered: list[RenderedPage] = processor.render_to_store(
        source, edition_id, _permissive(), store, dpi=dpi
    )

    page_rows: list[dict[str, Any]] = []
    transcription_ids: list[tuple[str, str]] = []
    resolved_text: dict[int, str] = {}
    transcriptions_created = evidence_created = chunks = pages_ocr = 0
    text_layer: dict[int, str] = {}
    if extract_text_layer:
        text_layer = _extract_text_layer(source)

    for page in rendered:
        page_id = new_id()
        index = page.page_number - 1
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO pages (id, asset_id, page_index, printed_page_label, width_px,"
                " height_px, dpi_x, dpi_y, rotation_degrees, page_image_key, page_sha256,"
                " quality_report, processing_state, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    page_id, asset_id, index, page.page_label, page.pixel_width,
                    page.pixel_height, float(page.dpi), float(page.dpi), 0,
                    _relative_key(page.blob_path, store.root), page.image_sha256, "{}",
                    "completed", utcnow(),
                ),
            )
        page_rows.append({"page_id": page_id, "index": index, "label": page.page_label})

        text = text_layer.get(index, "").strip()
        engine_name = "PyMuPDF-text-layer"
        engine_version = _pymupdf_version()
        confidence: float | None = None
        geometry = "[]"
        if engine is not None and len(text) < ocr_min_chars:
            recognised = engine.ocr(page.blob_path)
            if recognised.text.strip():
                text = recognised.text.strip()
                engine_name = recognised.engine_name
                engine_version = recognised.engine_version
                confidence = recognised.confidence
                geometry = recognised.geometry_json()
                pages_ocr += 1
        elif not extract_text_layer:
            continue
        resolved_text[index] = text
        transcription_id = new_id()
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO transcriptions (id, page_id, layer, text_content, language_code,"
                " engine_name, engine_version, confidence, token_geometry, review_state, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    transcription_id, page_id, "normalized", text, None,
                    engine_name, engine_version, confidence, geometry, "unreviewed", utcnow(),
                ),
            )
        transcriptions_created += 1
        transcription_ids.append((page_id, transcription_id))

    for page_id, transcription_id, page in zip(
        [row["page_id"] for row in page_rows],
        [tid for _, tid in transcription_ids],
        rendered,
        strict=False,
    ):
        text = resolved_text.get(page.page_number - 1, "").strip()
        if not text:
            continue
        evidence_id = add_evidence(
            database,
            edition_id=edition_id,
            page_id=page_id,
            transcription_id=transcription_id,
            evidence_kind="passage",
            evidence_class="source_explicit",
            quoted_text=text,
            locator={"page_index": page.page_number - 1, "page_label": page.page_label},
            source_hash=page.source_pdf_sha256,
        )
        evidence_created += 1
        index_chunk(
            database,
            evidence_id=evidence_id,
            page_id=page_id,
            edition_id=edition_id,
            work_id=_work_id_for_edition(database, edition_id),
            layer="normalized",
            text=text,
            pipeline_version=PIPELINE_VERSION,
        )
        chunks += 1

    return IngestResult(
        asset_id=asset_id,
        edition_id=edition_id,
        sha256=report.sha256,
        pages_rendered=len(rendered),
        pages_recorded=len(rendered),
        pages_reused=0,
        transcriptions_created=transcriptions_created,
        evidence_created=evidence_created,
        chunks_indexed=chunks,
        pages_ocr=pages_ocr,
        warnings=report.warnings,
    )


def pending_editions(database: Database, blob_root: str | Path) -> list[tuple[str, Path]]:
    """Editions holding a PDF with no rendered pages yet, smallest first.

    Resume state is the database itself: an edition disappears from this list as soon as
    its first page row exists, so an interrupted batch continues where it stopped without
    needing an external manifest.
    """
    root = Path(blob_root)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT a.edition_id, a.storage_key, a.byte_size,"
            " (SELECT COUNT(*) FROM pages p WHERE p.asset_id = a.id) AS recorded_pages,"
            " (SELECT COUNT(*) FROM transcriptions t JOIN pages p ON p.id = t.page_id"
            "   WHERE p.asset_id = a.id) AS recorded_transcriptions"
            " FROM assets a"
            " WHERE a.detected_mime = 'application/pdf' AND a.asset_role = 'original_scan'"
            " ORDER BY a.byte_size ASC"
        ).fetchall()
    pending: list[tuple[str, Path]] = []
    for row in rows:
        path = root / str(row["storage_key"])
        expected = _pdf_page_count(path)
        incomplete = (
            row["recorded_pages"] < (expected or 1)
            or row["recorded_transcriptions"] < row["recorded_pages"]
        )
        if incomplete:
            pending.append((str(row["edition_id"]), path))
    return pending


def _permissive() -> Any:
    """Internal grant object for the renderer.

    Rights were already evaluated against the persisted decision above; the renderer's own
    legacy check must not become a second, divergent policy.
    """
    from .domain import ProcessingOperation, RightsGrant, RightsStatus

    return RightsGrant(
        status=RightsStatus.LICENSED,
        permitted_operations=frozenset({ProcessingOperation.STORE, ProcessingOperation.OCR}),
    )


def _extract_text_layer(path: Path) -> dict[int, str]:
    try:
        import pymupdf
    except ImportError:
        return {}
    texts: dict[int, str] = {}
    with pymupdf.open(str(path)) as document:
        for index in range(document.page_count):
            texts[index] = document.load_page(index).get_text("text")
    return texts


def _pdf_page_count(path: Path) -> int | None:
    """Cheap structural count, used to detect an interrupted render. None if unreadable."""
    try:
        import pymupdf
    except ImportError:
        return None
    try:
        with pymupdf.open(str(path)) as document:
            return document.page_count
    except Exception:  # noqa: BLE001 - an unreadable PDF is a preflight problem
        return None


PURGE_BATCH_PAGES = 200


def _purge_derived(database: Database, asset_id: str, *, attempts: int = 6) -> None:
    """Remove derived rows for an incompletely rendered asset so it can be redone.

    Only derived data is touched: pages, transcriptions, evidence and index rows. The
    archived original is immutable and is never deleted here.

    Deletion is batched. A 400-page document purged in one transaction holds the write
    lock long enough for concurrent workers to fail with ``database is locked``, which is
    how the first repair attempt actually failed; small transactions plus a bounded retry
    keep the lock window short.
    """
    import sqlite3
    import time

    for attempt in range(attempts):
        try:
            _purge_derived_batched(database, asset_id)
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(2.0 * (attempt + 1))


def _purge_derived_batched(database: Database, asset_id: str) -> None:
    while True:
        with database.transaction() as connection:
            page_ids = [
                row[0] for row in connection.execute(
                    "SELECT id FROM pages WHERE asset_id=? LIMIT ?",
                    (asset_id, PURGE_BATCH_PAGES),
                )
            ]
            if not page_ids:
                return
            marks = ",".join("?" * len(page_ids))
            connection.execute(
                "DELETE FROM chunks_fts WHERE chunk_id IN"
                f" (SELECT id FROM chunk_records WHERE page_id IN ({marks}))",
                page_ids,
            )
            connection.execute(
                f"DELETE FROM chunk_records WHERE page_id IN ({marks})", page_ids
            )
            connection.execute(f"DELETE FROM evidence WHERE page_id IN ({marks})", page_ids)
            connection.execute(f"DELETE FROM pages WHERE id IN ({marks})", page_ids)


def _pymupdf_version() -> str:
    try:
        import pymupdf
    except ImportError:
        return "unavailable"
    return str(pymupdf.VersionBind)


def _relative_key(blob_path: str, root: Path) -> str:
    """Vault keys are vault-relative so archives stay portable across machines."""
    return Path(blob_path).resolve().relative_to(root).as_posix()


def _work_id_for_edition(database: Database, edition_id: str) -> str:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT work_id FROM editions WHERE id=?", (edition_id,)
        ).fetchone()
    if row is None:
        raise IngestError(f"unknown edition {edition_id}")
    return str(row["work_id"])
