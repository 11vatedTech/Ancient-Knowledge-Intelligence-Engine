"""The reading room: a local web interface over the AKI database and vault.

It is a loopback-only service. The corpus rights record permits internal search and
research and denies sharing with third parties, so the server refuses to bind to any
address other than this machine. Every page and passage it serves is filtered through the
same edition-level rights decision that governed ingestion.
"""
from __future__ import annotations

import ipaddress
import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..db import Database
from ..gate import permitted_editions

STATIC = Path(__file__).with_name("static")
_SAFE_KEY = re.compile(r"^sha256/[0-9a-f]{2}/[0-9a-f]{64}$")


class BindRefused(RuntimeError):
    """The requested address would expose the reading room beyond this machine."""


def require_loopback(host: str) -> None:
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        if host == "localhost":
            return
    raise BindRefused(
        f"refusing to listen on {host!r}: the corpus rights record denies sharing with third "
        "parties, so the reading room only serves this computer (127.0.0.1)"
    )


class Library:
    """Read model for the reading room, cached per process and refreshed on demand."""

    def __init__(self, database: Database, blob_root: Path, state_root: Path) -> None:
        self.database = database
        self.blob_root = blob_root.resolve()
        self.state_root = state_root.resolve()
        self.thumbs = self.state_root / "derived" / "thumbs"
        self._editions: dict[str, dict[str, Any]] | None = None
        self._render: set[str] | None = None
        self._loaded_at = 0.0

    # ---------------------------------------------------------------- catalog
    def editions(self) -> dict[str, dict[str, Any]]:
        if self._editions is not None and time.time() - self._loaded_at < 60:
            return self._editions
        allowed = permitted_editions(self.database, "internal_search")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT e.id, e.edition_title, e.work_id, s.display_title, s.category_labels,"
                " s.metadata AS source_meta, s.record_url,"
                " a.id AS asset_id, a.detected_mime, a.byte_size, a.sha256,"
                " (SELECT COUNT(*) FROM pages p WHERE p.asset_id = a.id) AS pages,"
                " (SELECT COUNT(*) FROM chunk_records c WHERE c.edition_id = e.id) AS text_pages,"
                " (SELECT p.id FROM pages p WHERE p.asset_id = a.id ORDER BY p.page_index"
                "  LIMIT 1) AS cover_page"
                " FROM editions e JOIN assets a ON a.edition_id = e.id"
                "   AND a.asset_role = 'original_scan'"
                " LEFT JOIN source_records s ON s.id = e.source_record_id"
            ).fetchall()
        editions: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row["id"] not in allowed or row["record_url"] is None:
                continue
            meta = json.loads(row["source_meta"] or "{}")
            labels = json.loads(row["category_labels"] or "[]")
            description = (meta.get("description_as_listed") or "").strip()
            editions[row["id"]] = {
                "id": row["id"],
                "title": _clean_title(row["display_title"] or row["edition_title"]),
                "listed_title": row["display_title"] or row["edition_title"],
                "author": (meta.get("author_as_listed") or "").strip() or None,
                "description": description or None,
                "category": labels[0] if labels else "Uncategorised",
                "downloads": int(meta.get("downloads_as_listed") or 0),
                "listed_at": meta.get("published_label"),
                "joes": "joe" in description.lower(),
                "media": row["detected_mime"],
                "bytes": int(row["byte_size"]),
                "pages": int(row["pages"]),
                "text_pages": int(row["text_pages"]),
                "cover_page": row["cover_page"],
                "source": row["record_url"],
                "sha256": row["sha256"],
                "noted": context_note(row["display_title"] or row["edition_title"]) is not None,
            }
        self._editions = editions
        self._loaded_at = time.time()
        return editions

    def chunk_allowed(self, edition_id: str) -> bool:
        return edition_id in self.editions()

    # ---------------------------------------------------------------- pages
    def page(self, page_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT p.id, p.page_index, p.printed_page_label, p.width_px, p.height_px,"
                " p.dpi_x, p.page_sha256, p.quality_report, a.edition_id, a.id AS asset_id"
                " FROM pages p JOIN assets a ON a.id = p.asset_id WHERE p.id = ?", (page_id,),
            ).fetchone()
            if row is None or row["edition_id"] not in self.editions():
                return None
            transcription = connection.execute(
                "SELECT id, text_content, engine_name, engine_version, confidence, review_state"
                " FROM transcriptions WHERE page_id = ? ORDER BY created_at DESC LIMIT 1",
                (page_id,),
            ).fetchone()
            evidence = connection.execute(
                "SELECT id FROM evidence WHERE page_id = ? AND evidence_kind = 'passage'"
                " ORDER BY created_at DESC LIMIT 1", (page_id,),
            ).fetchone()
            neighbours = connection.execute(
                "SELECT page_index, id FROM pages WHERE asset_id = ? AND page_index IN (?, ?)",
                (row["asset_id"], row["page_index"] - 1, row["page_index"] + 1),
            ).fetchall()
            plate = connection.execute(
                "SELECT 1 FROM visual_observations WHERE page_id = ? AND observation_type='plate'",
                (page_id,),
            ).fetchone()
        nav = {int(n["page_index"]): n["id"] for n in neighbours}
        edition = self.editions()[row["edition_id"]]
        label = row["printed_page_label"] or str(row["page_index"] + 1)
        return {
            "id": row["id"],
            "index": int(row["page_index"]),
            "label": label,
            "width": int(row["width_px"]),
            "height": int(row["height_px"]),
            "dpi": row["dpi_x"],
            "sha256": row["page_sha256"],
            "edition": {key: edition[key] for key in ("id", "title", "author", "category", "pages")},
            "previous": nav.get(int(row["page_index"]) - 1),
            "next": nav.get(int(row["page_index"]) + 1),
            "plate": plate is not None,
            "text": transcription["text_content"] if transcription else "",
            "engine": transcription["engine_name"] if transcription else None,
            "engine_version": transcription["engine_version"] if transcription else None,
            "confidence": transcription["confidence"] if transcription else None,
            "review_state": transcription["review_state"] if transcription else None,
            "evidence_id": evidence["id"] if evidence else None,
            "citation": _citation(edition, label, int(row["page_index"]), row["page_sha256"]),
        }

    def page_list(self, edition_id: str) -> list[dict[str, Any]]:
        if edition_id not in self.editions():
            return []
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT p.id, p.page_index, p.printed_page_label,"
                " EXISTS(SELECT 1 FROM chunk_records c WHERE c.page_id = p.id) AS has_text,"
                " EXISTS(SELECT 1 FROM visual_observations v WHERE v.page_id = p.id"
                "        AND v.observation_type = 'plate') AS plate"
                " FROM pages p JOIN assets a ON a.id = p.asset_id"
                " WHERE a.edition_id = ? AND a.asset_role = 'original_scan'"
                " ORDER BY p.page_index", (edition_id,),
            ).fetchall()
        return [{"id": r["id"], "index": int(r["page_index"]),
                 "label": r["printed_page_label"] or str(r["page_index"] + 1),
                 "text": bool(r["has_text"]), "plate": bool(r["plate"])} for r in rows]

    def image_path(self, page_id: str) -> Path | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT p.page_image_key, a.edition_id FROM pages p JOIN assets a"
                " ON a.id = p.asset_id WHERE p.id = ?", (page_id,),
            ).fetchone()
        if row is None or row["edition_id"] not in self.editions():
            return None
        key = str(row["page_image_key"])
        if not _SAFE_KEY.match(key):
            return None
        path = (self.blob_root / key).resolve()
        if self.blob_root not in path.parents or not path.is_file():
            return None
        return path

    def render_allowed(self) -> set[str]:
        if self._render is None or time.time() - self._loaded_at > 60:
            self._render = permitted_editions(self.database, "render_pages")
        return self._render

    def thumbnail(self, page_id: str, width: int) -> Path | None:
        """Derived image output: needs ``render_pages`` as well as ``internal_search``."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT a.edition_id FROM pages p JOIN assets a ON a.id = p.asset_id WHERE p.id = ?",
                (page_id,)).fetchone()
        if row is None or row["edition_id"] not in self.render_allowed():
            return None
        source = self.image_path(page_id)
        if source is None:
            return None
        width = max(120, min(int(width), 900))
        target = self.thumbs / f"{source.name}-{width}.jpg"
        if target.is_file():
            return target
        import pymupdf

        pixmap = pymupdf.Pixmap(str(source))
        if pixmap.alpha:
            pixmap = pymupdf.Pixmap(pixmap, 0)
        if pixmap.n > 3:
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
        while pixmap.width >= width * 2:
            pixmap.shrink(1)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".part")
        temporary.write_bytes(pixmap.tobytes("jpg", jpg_quality=82))
        temporary.replace(target)
        return target

    # ---------------------------------------------------------------- overview
    def overview(self) -> dict[str, Any]:
        editions = self.editions()
        with self.database.connect() as connection:
            counts = connection.execute(
                "SELECT (SELECT COUNT(*) FROM pages) AS pages,"
                " (SELECT COUNT(*) FROM chunk_records) AS passages,"
                " (SELECT COUNT(*) FROM transcriptions WHERE engine_name LIKE 'RapidOCR%'"
                "    OR engine_name LIKE 'Tesseract%') AS ocr_pages,"
                " (SELECT COUNT(*) FROM visual_observations WHERE observation_type='plate') AS plates,"
                " (SELECT COUNT(*) FROM entities) AS entities,"
                " (SELECT COUNT(*) FROM mentions) AS mentions"
            ).fetchone()
        categories: dict[str, int] = {}
        for edition in editions.values():
            categories[edition["category"]] = categories.get(edition["category"], 0) + 1
        return {
            "books": len(editions),
            "readable_books": sum(1 for e in editions.values() if e["pages"]),
            "waiting_books": sum(1 for e in editions.values() if not e["pages"]),
            "bytes": sum(e["bytes"] for e in editions.values()),
            **{key: int(counts[key]) for key in counts.keys()},
            "categories": sorted(categories.items(), key=lambda item: -item[1]),
        }


def _clean_title(title: str) -> str:
    """Readable title from an uploader filename, without inventing bibliographic facts."""
    text = re.sub(r"^\d{6,}[-_ ]+", "", title.strip())
    text = re.sub(r"\(\s*PDFDrive\s*\)", "", text, flags=re.IGNORECASE)
    if text.count("-") >= 3 and " " not in text:
        text = text.replace("-", " ")
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip(" -")
    return text or title


def _citation(edition: dict[str, Any], label: str, index: int, sha: str) -> str:
    author = f"{edition['author']}, " if edition.get("author") else ""
    return (f"{author}{edition['title']}, page {label} (scan page {index + 1}). "
            f"The Holy Word library, theholyword.net. Page image SHA-256 {sha[:16]}…")


@lru_cache(maxsize=1)
def memorial() -> dict[str, Any]:
    return json.loads((Path(__file__).with_name("memorial.json")).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _notes() -> list[dict[str, Any]]:
    path = Path(__file__).with_name("notes.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def context_note(listed_title: str) -> str | None:
    """A curator's note for a title that needs one. The book itself is shown as listed."""
    lowered = listed_title.lower()
    for entry in _notes():
        if all(word in lowered for word in entry["title_contains"]):
            return str(entry["note"])
    return None
