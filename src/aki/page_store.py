"""Transactional persistence for rendered page manifests."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from .blob_store import ContentAddressedStore
from .pdf import RenderedPage


class PageManifestConflict(ValueError):
    """A page identity was previously recorded with different provenance."""


class PageArtifactCatalog:
    """Persist page metadata only after every referenced blob passes integrity checks."""

    def __init__(self, database: str | Path, blobs: ContentAddressedStore) -> None:
        self.database = str(database)
        self.blobs = blobs
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("""
                CREATE TABLE IF NOT EXISTS page_artifacts (
                    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE RESTRICT,
                    source_pdf_sha256 TEXT NOT NULL,
                    page_number INTEGER NOT NULL CHECK(page_number > 0),
                    page_label TEXT NOT NULL,
                    image_sha256 TEXT NOT NULL,
                    image_size_bytes INTEGER NOT NULL CHECK(image_size_bytes >= 0),
                    pixel_width INTEGER NOT NULL CHECK(pixel_width > 0),
                    pixel_height INTEGER NOT NULL CHECK(pixel_height > 0),
                    page_width_points REAL NOT NULL CHECK(page_width_points > 0),
                    page_height_points REAL NOT NULL CHECK(page_height_points > 0),
                    dpi INTEGER NOT NULL CHECK(dpi > 0),
                    renderer_name TEXT NOT NULL,
                    renderer_version TEXT NOT NULL,
                    config_sha256 TEXT NOT NULL,
                    blob_path TEXT NOT NULL,
                    PRIMARY KEY(source_id, source_pdf_sha256, page_number, dpi)
                )
            """)

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record_batch(self, pages: Iterable[RenderedPage]) -> tuple[int, int]:
        batch = list(pages)
        if not batch:
            return 0, 0
        identities = set()
        for page in batch:
            identity = (page.source_id, page.source_pdf_sha256, page.page_number, page.dpi)
            if identity in identities:
                raise PageManifestConflict(f"duplicate page in batch: {identity}")
            identities.add(identity)
            data = self.blobs.get_bytes(page.image_sha256)
            if len(data) != page.image_size_bytes:
                raise PageManifestConflict(f"blob size mismatch for page {identity}")

        added = duplicates = 0
        with self._connect() as db:
            for page in batch:
                values = (
                    page.source_id, page.source_pdf_sha256, page.page_number, page.page_label,
                    page.image_sha256, page.image_size_bytes, page.pixel_width, page.pixel_height,
                    page.page_width_points, page.page_height_points, page.dpi, page.renderer_name,
                    page.renderer_version, page.config_sha256, page.blob_path,
                )
                row = db.execute(
                    "SELECT * FROM page_artifacts WHERE source_id=? AND source_pdf_sha256=? "
                    "AND page_number=? AND dpi=?",
                    (page.source_id, page.source_pdf_sha256, page.page_number, page.dpi),
                ).fetchone()
                if row is not None:
                    if tuple(row) != values:
                        raise PageManifestConflict(
                            f"page identity already exists with different provenance: "
                            f"{page.source_id}/{page.page_number}@{page.dpi}dpi"
                        )
                    duplicates += 1
                    continue
                db.execute(
                    "INSERT INTO page_artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values
                )
                added += 1
        return added, duplicates

    def list_pages(self, source_id: str) -> list[dict[str, object]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM page_artifacts WHERE source_id=? "
                "ORDER BY source_pdf_sha256, page_number, dpi", (source_id,)
            ).fetchall()
        return [dict(row) for row in rows]
