"""Readable copies of the originals: ``<out>/<shelf>/<title>.<ext>`` plus ``_catalog.csv``.

The vault stores originals under their SHA-256, which keeps them safe but unreadable to a
person browsing folders. This writes a second, human-named copy of each original for the
owner on the same machine.

Rights: this is treated as local storage of the original (``store_original``), not as
export or redistribution (see ADR-012 in docs/execution/DECISION_LOG.md). Editions whose
latest rights decision does not permit ``store_original`` are skipped and listed. Each run
writes one ``library_export`` audit event with its counts.

Idempotent: a file already present at the right size and SHA-256 is left alone; copies
go through a ``.part`` file and an atomic rename; duplicate titles are numbered in a
stable order (by source URL), so repeated runs produce the same names.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .db import Database, new_id, utcnow
from .gate import permitted_editions

EXT = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png",
       "application/zip": ".zip"}
MAX_NAME = 150  # matches the names of copies made before this module existed


def clean(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(". ")
    return text[:MAX_NAME].rstrip(". ") or "untitled"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def export_library(
    database: Database, blob_root: str | Path, out: str | Path, *, snapshot: str | Path,
    budget_seconds: float | None = None, actor: str = "aki library-export",
) -> dict[str, Any]:
    started = time.time()
    out = Path(out)
    books = {str(b["id"]): b for b in json.loads(Path(snapshot).read_text(encoding="utf-8"))}
    allowed = permitted_editions(database, "store_original")
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT a.edition_id, a.storage_key, a.byte_size, a.detected_mime, a.sha256,"
            " s.record_url FROM assets a JOIN editions e ON e.id = a.edition_id"
            " JOIN source_records s ON s.id = e.source_record_id"
            " WHERE a.asset_role = 'original_scan' ORDER BY s.record_url"
        ).fetchall()

    used: set[str] = set()
    manifest: list[dict[str, Any]] = []
    copied = present = 0
    skipped_rights: list[str] = []
    missing: list[str] = []
    deferred = 0
    for row in rows:
        book = books.get(str(row["record_url"]).rstrip("/").rsplit("/", 1)[-1], {})
        title = book.get("title") or row["record_url"]
        if row["edition_id"] not in allowed:
            skipped_rights.append(title)
            continue
        extension = EXT.get(row["detected_mime"], "")
        shelf = clean(book.get("category") or "Uncategorised")
        name = clean(title)
        relative = f"{shelf}/{name}{extension}"
        number = 2
        while relative.lower() in used:
            relative = f"{shelf}/{name} ({number}){extension}"
            number += 1
        used.add(relative.lower())
        target = out / relative
        entry = {
            "shelf": shelf, "file": relative, "title": title, "edition_ids": row["edition_id"],
            "author": book.get("author") or "", "description": book.get("description") or "",
            "bytes": row["byte_size"], "sha256": row["sha256"], "source": row["record_url"],
        }
        if target.is_file() and target.stat().st_size == row["byte_size"]:
            if _sha256(target) == row["sha256"]:
                present += 1
                manifest.append(entry)
                continue
        if budget_seconds is not None and time.time() - started > budget_seconds:
            deferred += 1
            continue
        original = Path(blob_root) / row["storage_key"]
        if not original.is_file():
            missing.append(title)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        shutil.copyfile(original, partial)
        if _sha256(partial) != row["sha256"]:
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"copy of {title!r} did not match its recorded SHA-256")
        os.replace(partial, target)
        copied += 1
        manifest.append(entry)

    out.mkdir(parents=True, exist_ok=True)
    if manifest:
        with (out / "_catalog.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(manifest[0]))
            writer.writeheader()
            writer.writerows(manifest)
    report = {
        "out": str(out), "books": len(rows), "exported": len(manifest), "copied_now": copied,
        "already_present": present, "deferred": deferred,
        "skipped_without_store_original": skipped_rights, "missing_originals": missing,
        "ok": not missing, "seconds": round(time.time() - started, 1),
    }
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO audit_events (id, occurred_at, actor_type, actor_id, action,"
            " subject_type, subject_id, before_hash, after_hash, details)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (new_id(), utcnow(), "operator", actor, "library_export", "library", str(out),
             None, None, json.dumps({k: v for k, v in report.items()
                                     if k != "skipped_without_store_original"}
                                    | {"skipped": len(skipped_rights)}, sort_keys=True)))
    return report
