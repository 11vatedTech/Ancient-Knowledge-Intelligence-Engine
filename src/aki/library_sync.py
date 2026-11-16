"""Reconcile a destination Library (e.g. the OneDrive project folder) with the archive's.

Existence is not proof: every file at the destination is hashed and compared with the
original it is supposed to be, and re-checked against the edition's current rights.

Outcomes per file, all written to the run report and one ``library_sync`` audit event:

* ``identical``     — same SHA-256 as the original; left alone.
* ``copied``        — missing at the destination; copied via ``.part`` and verified.
* ``replaced``      — present but different (truncated, damaged, edited); the old file is
                      moved to ``_quarantine/<stamp>/`` and a verified copy put in place.
* ``quarantined``   — present but its edition no longer permits ``store_original``, or the
                      file is not in the catalogue at all; moved to ``_quarantine/<stamp>/``.
* ``not_permitted`` — absent and not permitted; nothing written.

Nothing is ever deleted.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .db import Database, new_id, utcnow
from .gate import permitted_editions

QUARANTINE = "_quarantine"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_verified(source: Path, target: Path, sha256: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    shutil.copyfile(source, partial)
    if _sha256(partial) != sha256:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"copy of {source} did not match its recorded SHA-256")
    os.replace(partial, target)


def sync_library(database: Database, source: str | Path, destination: str | Path,
                 *, report_dir: str | Path) -> dict[str, Any]:
    started = time.time()
    source, destination = Path(source), Path(destination)
    catalog = source / "_catalog.csv"
    if not catalog.is_file():
        raise FileNotFoundError(f"{catalog} not found; run the Library export first")
    with catalog.open(encoding="utf-8", newline="") as handle:
        entries = list(csv.DictReader(handle))
    allowed = permitted_editions(database, "store_original")
    stamp = time.strftime("%Y%m%dT%H%M%S")
    quarantine = destination / QUARANTINE / stamp
    outcomes: list[dict[str, Any]] = []

    def move_aside(path: Path, reason: str) -> str:
        target = quarantine / path.relative_to(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, target)
        return str(target)

    known: set[str] = set()
    for entry in entries:
        relative = entry["file"]
        known.add(relative.replace("\\", "/").lower())
        here = destination / relative
        permitted = entry.get("edition_ids", "") and all(
            e in allowed for e in entry["edition_ids"].split(";") if e)
        record: dict[str, Any] = {"file": relative, "expected_sha256": entry["sha256"]}
        if here.is_file():
            actual = _sha256(here)
            record["found_sha256"] = actual
            if not permitted:
                record.update(outcome="quarantined", reason="edition no longer permits store_original",
                              moved_to=move_aside(here, "rights"))
            elif actual == entry["sha256"]:
                record["outcome"] = "identical"
            else:
                record.update(outcome="replaced", reason="content differs from the original",
                              moved_to=move_aside(here, "mismatch"))
                _copy_verified(source / relative, here, entry["sha256"])
        elif permitted:
            _copy_verified(source / relative, here, entry["sha256"])
            record["outcome"] = "copied"
        else:
            record["outcome"] = "not_permitted"
        outcomes.append(record)

    for path in sorted(destination.rglob("*")) if destination.exists() else []:
        if not path.is_file() or QUARANTINE in path.relative_to(destination).parts:
            continue
        relative = path.relative_to(destination).as_posix()
        if relative == "_catalog.csv" or relative.lower() in known:
            continue
        outcomes.append({"file": relative, "outcome": "quarantined",
                         "reason": "not in the archive's catalogue", "found_sha256": _sha256(path),
                         "moved_to": move_aside(path, "unknown")})

    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(catalog, destination / "_catalog.csv")
    counts: dict[str, int] = {}
    for record in outcomes:
        counts[record["outcome"]] = counts.get(record["outcome"], 0) + 1
    report = {"source": str(source), "destination": str(destination), "at": utcnow(),
              "counts": counts, "seconds": round(time.time() - started, 1),
              "quarantine": str(quarantine) if quarantine.exists() else None}
    report_path = Path(report_dir) / f"library-sync-{stamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({**report, "files": outcomes}, indent=1, ensure_ascii=False),
                           encoding="utf-8")
    report["report"] = str(report_path)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO audit_events (id, occurred_at, actor_type, actor_id, action,"
            " subject_type, subject_id, before_hash, after_hash, details)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (new_id(), utcnow(), "operator", "aki library-sync", "library_sync", "library",
             str(destination), None, None, json.dumps(report, sort_keys=True)))
    return report
