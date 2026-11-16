"""Ingestion for the editions that are not PDFs: single scanned images and an EPUB.

Eight of the 257 editions arrived as JPEG/PNG photographs of printed pages or as an EPUB
inside a ZIP. The PDF pipeline cannot read them, so they were archived but never made
readable. This module gives them the same treatment as every other edition:

* an image asset becomes one page whose page image *is* the archived original (nothing is
  re-encoded), transcribed by the OCR engine when one is available;
* an EPUB is laid out by PyMuPDF's reflow engine, each laid-out page is rendered to a PNG
  in the vault, and its text comes from the document itself rather than from OCR.

Each page, transcription, evidence row and search chunk records the engine that produced
it, exactly as PDF ingestion does, and a re-run skips editions already completed.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .blob_store import ContentAddressedStore
from .db import Database, new_id, utcnow
from .evidence import add_evidence
from .ingest import _require, _work_id_for_edition
from .ocr import OcrProvider
from .search import index_chunk

PIPELINE_VERSION = "media-v1"
IMAGE_MIMES = {"image/jpeg", "image/png"}
EPUB_DPI = 110


@dataclass(frozen=True, slots=True)
class MediaResult:
    edition_id: str
    kind: str
    pages: int
    pages_with_text: int
    pages_ocr: int
    skipped: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "edition_id": self.edition_id, "kind": self.kind, "pages": self.pages,
            "pages_with_text": self.pages_with_text, "pages_ocr": self.pages_ocr,
            "skipped": self.skipped, "note": self.note,
        }


def pending_media(database: Database, blob_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Non-PDF originals not yet fully read: no pages at all, or an EPUB cut off midway."""
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT a.id, a.edition_id, a.storage_key, a.detected_mime, a.sha256,"
            " (SELECT COUNT(*) FROM pages p WHERE p.asset_id = a.id) AS recorded"
            " FROM assets a WHERE a.asset_role = 'original_scan'"
            " AND a.detected_mime != 'application/pdf' ORDER BY a.byte_size"
        ).fetchall()
    pending = []
    for row in rows:
        if row["recorded"] == 0:
            pending.append(dict(row))
        elif row["detected_mime"] == "application/zip" and blob_root is not None:
            path = Path(blob_root) / str(row["storage_key"])
            if is_epub(path) and row["recorded"] < _epub_pages(path):
                pending.append(dict(row))
    return pending


def _epub_pages(path: Path) -> int:
    with _pymupdf().open(str(path), filetype="epub") as document:
        return int(document.page_count)


def is_epub(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            if "mimetype" not in archive.namelist():
                return False
            return archive.read("mimetype").strip() == b"application/epub+zip"
    except (zipfile.BadZipFile, OSError):
        return False


def ingest_media_asset(
    database: Database,
    blob_root: str | Path,
    asset: dict[str, Any],
    *,
    ocr_provider: OcrProvider | None = None,
) -> MediaResult:
    database.require_current()
    edition_id = str(asset["edition_id"])
    _require(database, edition_id)
    store = ContentAddressedStore(blob_root)
    path = store.root / str(asset["storage_key"])
    mime = str(asset["detected_mime"])
    if mime in IMAGE_MIMES:
        return _ingest_image(database, store, asset, path, ocr_provider)
    if mime == "application/zip" and is_epub(path):
        return _ingest_epub(database, store, asset, path)
    return MediaResult(edition_id, mime, 0, 0, 0, skipped=True,
                       note="no reader for this media type; archived original kept as is")


def _pymupdf() -> Any:
    import pymupdf

    return pymupdf


def _ingest_image(
    database: Database, store: ContentAddressedStore, asset: dict[str, Any], path: Path,
    engine: OcrProvider | None,
) -> MediaResult:
    pymupdf = _pymupdf()
    pixmap = pymupdf.Pixmap(str(path))
    width, height = pixmap.width, pixmap.height
    text, engine_name, engine_version, confidence, geometry = "", "none", "n/a", None, "[]"
    pages_ocr = 0
    if engine is not None:
        recognised = engine.ocr(path)
        if recognised.text.strip():
            text = recognised.text.strip()
            engine_name, engine_version = recognised.engine_name, recognised.engine_version
            confidence, geometry = recognised.confidence, recognised.geometry_json()
            pages_ocr = 1
    _record_page(
        database, asset, page_index=0, label="1", width=width, height=height, dpi=None,
        image_key=str(asset["storage_key"]), image_sha=str(asset["sha256"]),
        text=text, engine_name=engine_name, engine_version=engine_version,
        confidence=confidence, geometry=geometry, quality={"source": "original image, not re-encoded"},
    )
    return MediaResult(str(asset["edition_id"]), "image", 1, int(bool(text)), pages_ocr)


def _ingest_epub(
    database: Database, store: ContentAddressedStore, asset: dict[str, Any], path: Path,
) -> MediaResult:
    pymupdf = _pymupdf()
    pages = with_text = 0
    with database.connect() as connection:
        done = {int(r[0]) for r in connection.execute(
            "SELECT page_index FROM pages WHERE asset_id = ?", (asset["id"],))}
    with pymupdf.open(str(path), filetype="epub") as document:
        version = str(pymupdf.VersionBind)
        for index in range(document.page_count):
            if index in done:
                continue  # resuming an interrupted run
            page = document.load_page(index)
            pixmap = page.get_pixmap(dpi=EPUB_DPI, colorspace=pymupdf.csRGB, alpha=False)
            blob = store.put_bytes(pixmap.tobytes("png"))
            text = page.get_text("text").strip()
            _record_page(
                database, asset, page_index=index, label=str(index + 1),
                width=pixmap.width, height=pixmap.height, dpi=float(EPUB_DPI),
                image_key=blob.path.relative_to(store.root).as_posix(), image_sha=blob.sha256,
                text=text, engine_name="PyMuPDF-epub-reflow", engine_version=version,
                confidence=None, geometry="[]",
                quality={"source": "EPUB laid out by PyMuPDF reflow; page breaks are not the publisher's"},
            )
            pages += 1
            with_text += int(bool(text))
    return MediaResult(str(asset["edition_id"]), "epub", pages, with_text, 0)


def _record_page(
    database: Database, asset: dict[str, Any], *, page_index: int, label: str, width: int,
    height: int, dpi: float | None, image_key: str, image_sha: str, text: str,
    engine_name: str, engine_version: str, confidence: float | None, geometry: str,
    quality: dict[str, Any],
) -> None:
    import json

    edition_id = str(asset["edition_id"])
    page_id = new_id()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO pages (id, asset_id, page_index, printed_page_label, width_px,"
            " height_px, dpi_x, dpi_y, rotation_degrees, page_image_key, page_sha256,"
            " quality_report, processing_state, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (page_id, asset["id"], page_index, label, width, height, dpi, dpi, 0, image_key,
             image_sha, json.dumps(quality, sort_keys=True), "completed", utcnow()),
        )
    if not text:
        return
    transcription_id = new_id()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO transcriptions (id, page_id, layer, text_content, engine_name,"
            " engine_version, confidence, token_geometry, review_state, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (transcription_id, page_id, "normalized", text, engine_name, engine_version,
             confidence, geometry, "unreviewed", utcnow()),
        )
    evidence_id = add_evidence(
        database, edition_id=edition_id, page_id=page_id, transcription_id=transcription_id,
        evidence_kind="passage", evidence_class="source_explicit", quoted_text=text,
        locator={"page_index": page_index, "page_label": label},
        source_hash=str(asset["sha256"]),
    )
    index_chunk(
        database, evidence_id=evidence_id, page_id=page_id, edition_id=edition_id,
        work_id=_work_id_for_edition(database, edition_id), layer="normalized", text=text,
        pipeline_version=PIPELINE_VERSION,
    )
