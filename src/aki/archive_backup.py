"""A recoverable copy of the archive, proven by a restore test.

``aki backup`` copies the database only, and a fingerprint manifest is not a copy. This
writes a second, self-contained archive:

    <dest>/aki.sqlite3          online backup of the database
    <dest>/blobs/sha256/...     every original scan (and, with --include-pages, every page image)
    <dest>/BACKUP.json          what was copied and the result of the restore test

The restore test does what a recovery would: it restores the backed-up database into a
scratch file with ``Database.restore``, integrity-checks it, confirms that every original
it references is present in the backup and hashes to its recorded SHA-256. A backup is
recorded as verified (``archive_backup_verified`` audit event) only if all of that passes.
``aki relocate`` refuses to move the archive without a verified backup from the last 7 days.

Page images are derived (re-rendered from the originals by ingestion), so they are optional
here; the originals and the database are what cannot be recreated cheaply.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import Database, new_id, utcnow

FRESH_DAYS = 7


class BackupError(RuntimeError):
    """The backup could not be made or did not pass its restore test."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _same_device(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def backup_archive(database: Database, blob_root: str | Path, destination: str | Path,
                   *, include_pages: bool = False, progress: Any = None) -> dict[str, Any]:
    say = progress or (lambda message: None)
    started = time.time()
    blob_root = Path(blob_root).resolve()
    home = Path(database.path).resolve().parent
    destination = Path(destination).expanduser().resolve()
    if destination == home or home in destination.parents:
        raise BackupError("the backup must be outside the archive it protects")
    if (destination / "aki.sqlite3").exists():
        raise BackupError(f"{destination} already holds a backup; choose an empty folder")
    destination.mkdir(parents=True, exist_ok=True)

    with database.connect() as connection:
        originals = connection.execute(
            "SELECT DISTINCT storage_key, sha256, byte_size FROM assets").fetchall()
        pages = connection.execute("SELECT DISTINCT page_image_key FROM pages").fetchall() \
            if include_pages else []
    keys = {row["storage_key"]: row["sha256"] for row in originals}
    for row in pages:
        key = str(row["page_image_key"])
        keys.setdefault(key, key.rsplit("/", 1)[-1])
    needed = sum((blob_root / key).stat().st_size for key in keys if (blob_root / key).is_file())
    needed += Path(database.path).stat().st_size
    free = shutil.disk_usage(destination).free
    if free < needed * 1.05:
        raise BackupError(f"not enough space at {destination}: need {needed / 2**30:.1f} GiB, "
                          f"have {free / 2**30:.1f} GiB")

    say(f"Backing up the database and {len(keys):,} files ({needed / 2**30:.1f} GiB)")
    database.backup(destination / "aki.sqlite3")
    missing: list[str] = []
    for count, (key, digest) in enumerate(sorted(keys.items()), 1):
        source = blob_root / key
        if not source.is_file():
            missing.append(key)
            continue
        target = destination / "blobs" / key
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        shutil.copyfile(source, partial)
        if _sha256(partial) != digest:
            partial.unlink(missing_ok=True)
            raise BackupError(f"{key} did not match its recorded SHA-256 while copying")
        os.replace(partial, target)
        if count % 2000 == 0:
            say(f"  {count:,} files copied")
    if missing:
        raise BackupError(f"{len(missing)} archived files are missing from the vault, e.g. {missing[:3]}")

    say("Running the restore test")
    test = verify_backup(destination)
    report = {
        "source_home": str(home), "destination": str(destination), "made_at": utcnow(),
        "include_pages": include_pages, "files": len(keys), "bytes": needed,
        "same_device_as_archive": _same_device(destination, home),
        "restore_test": test, "seconds": round(time.time() - started, 1),
    }
    (destination / "BACKUP.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not test["ok"]:
        raise BackupError(f"the backup did not pass its restore test: {test['problems'][:3]}")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO audit_events (id, occurred_at, actor_type, actor_id, action,"
            " subject_type, subject_id, before_hash, after_hash, details)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (new_id(), utcnow(), "operator", "aki archive-backup", "archive_backup_verified",
             "backup", str(destination), None, test["database_sha256"],
             json.dumps({k: v for k, v in report.items() if k != "restore_test"}
                        | {"originals_verified": test["originals_verified"]}, sort_keys=True)))
    return report


def verify_backup(destination: str | Path) -> dict[str, Any]:
    """Restore the backed-up database to a scratch file and check every original it names."""
    destination = Path(destination)
    problems: list[str] = []
    database_file = destination / "aki.sqlite3"
    if not database_file.is_file():
        return {"ok": False, "problems": ["no aki.sqlite3 in the backup"], "originals_verified": 0,
                "database_sha256": None}
    with tempfile.TemporaryDirectory(prefix="aki-restore-test-") as scratch:
        restored = Database(Path(scratch) / "restored.sqlite3")
        result = restored.restore(database_file, confirm=True)
        integrity = result["integrity"]
        if integrity["quick_check"] != "ok" or integrity["foreign_key_violations"]:
            problems.append(f"restored database failed its integrity check: {integrity}")
        with restored.connect() as connection:
            originals = connection.execute(
                "SELECT DISTINCT storage_key, sha256 FROM assets").fetchall()
    verified = 0
    for row in originals:
        path = destination / "blobs" / row["storage_key"]
        if not path.is_file():
            problems.append(f"missing original {row['storage_key']}")
        elif _sha256(path) != row["sha256"]:
            problems.append(f"original {row['storage_key']} does not match its SHA-256")
        else:
            verified += 1
    return {"ok": not problems, "problems": problems, "originals_expected": len(originals),
            "originals_verified": verified, "database_integrity": integrity,
            "database_sha256": _sha256(database_file), "tested_at": utcnow()}


def latest_verified_backup(database: Database, *, days: int = FRESH_DAYS) -> dict[str, Any] | None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    with database.connect() as connection:
        row = connection.execute(
            "SELECT subject_id, occurred_at, details FROM audit_events"
            " WHERE action = 'archive_backup_verified' AND occurred_at >= ?"
            " ORDER BY occurred_at DESC LIMIT 1", (cutoff,)).fetchone()
    if row is None:
        return None
    if not (Path(row["subject_id"]) / "aki.sqlite3").is_file():
        return None  # the backup was moved or deleted since it was verified
    return {"path": row["subject_id"], "verified_at": row["occurred_at"],
            **json.loads(row["details"])}
