"""Where the canonical archive lives, and moving it there safely.

The archive home holds the database, the content-addressed vault and every derived
artefact (``derived/``, ``logs/``, ``backups/``). By default it is ``.aki`` inside the
project. ``aki relocate`` moves it somewhere else, typically out of a cloud-synchronised
folder, and records the new home in ``aki.config.json`` at the project root, which every
command then reads. ``AKI_HOME`` in the environment overrides both.

Relocation copies and verifies; it never deletes. The database is copied with SQLite's
online backup and integrity-checked; each vault file is copied and re-hashed against its
own content-addressed name. The old home is left in place with a ``MOVED.txt`` note so the
owner can retire it once satisfied.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

CONFIG_NAME = "aki.config.json"
DEFAULT_HOME = ".aki"
_VAULT_NAME = re.compile(r"[0-9a-f]{64}")
_SKIP_TOP = {"aki.sqlite3", "aki.sqlite3-wal", "aki.sqlite3-shm", "blobs", "tmp", "derived"}


class RelocationError(RuntimeError):
    """The archive could not be moved safely; nothing at the source was changed."""


def config_path(project_root: str | Path | None = None) -> Path:
    return Path(project_root or Path.cwd()) / CONFIG_NAME


def resolve_home(project_root: str | Path | None = None) -> Path:
    override = os.environ.get("AKI_HOME")
    if override:
        return Path(override)
    config = config_path(project_root)
    if config.is_file():
        try:
            home = json.loads(config.read_text(encoding="utf-8")).get("home")
        except (OSError, ValueError):
            home = None
        if home:
            return Path(home)
    return Path(project_root or ".") / DEFAULT_HOME if project_root else Path(DEFAULT_HOME)


def is_cloud_synced(path: str | Path) -> bool:
    """True for paths inside OneDrive, Dropbox, Google Drive or iCloud folders."""
    resolved = str(Path(path).expanduser().resolve()).lower().replace("\\", "/")
    roots = [os.environ.get(name, "") for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")]
    if any(root and resolved.startswith(str(Path(root).resolve()).lower().replace("\\", "/")) for root in roots):
        return True
    markers = ("/onedrive", "/dropbox", "/google drive", "/googledrive", "/icloud drive", "/icloudrive")
    return any(marker in resolved for marker in markers)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def relocate(
    source: str | Path,
    target: str | Path,
    *,
    project_root: str | Path,
    allow_cloud: bool = False,
    require_backup: bool = True,
    progress: Any = None,
) -> dict[str, Any]:
    """Copy the archive home from ``source`` to ``target``, verify it, and switch to it."""
    from .db import Database, new_id, utcnow

    started = time.time()
    source = Path(source).expanduser().resolve()
    target = Path(target).expanduser().resolve()
    say = progress or (lambda message: None)
    if not (source / "aki.sqlite3").is_file():
        raise RelocationError(f"no archive database at {source}")
    if target == source or source in target.parents:
        raise RelocationError("the new home must be outside the current one")
    if is_cloud_synced(target) and not allow_cloud:
        raise RelocationError(f"{target} is inside a cloud-synchronised folder; choose a local path")
    if require_backup:
        from .archive_backup import FRESH_DAYS, latest_verified_backup

        backup_record = latest_verified_backup(Database(source / "aki.sqlite3"))
        if backup_record is None:
            raise RelocationError(
                f"no verified archive backup from the last {FRESH_DAYS} days; make one first "
                "with 'aki archive-backup --to <folder>' (ideally on another drive)")
        say(f"Verified archive backup found: {backup_record['path']} ({backup_record['verified_at']})")
    if (target / "aki.sqlite3").exists():
        raise RelocationError(
            f"{target / 'aki.sqlite3'} already exists; remove it or choose another folder so that "
            "an older copy is never mistaken for the live archive")
    target.mkdir(parents=True, exist_ok=True)
    needed = sum(f.stat().st_size for f in source.rglob("*") if f.is_file()
                 and "tmp" not in f.relative_to(source).parts[:1])
    free = shutil.disk_usage(target).free
    headroom = 15 * 2**30  # the remaining books add page images; leave room for them
    if free < needed + headroom:
        raise RelocationError(
            f"not enough free space at {target}: need about {(needed + headroom) / 2**30:.1f} GiB "
            f"({needed / 2**30:.1f} GiB archive plus room to grow), have {free / 2**30:.1f} GiB")

    say("Copying and re-hashing every file in the vault")
    copied = skipped = 0
    bad: list[str] = []
    strays: list[str] = []
    for file in sorted((source / "blobs").rglob("*")):
        if not file.is_file():
            continue
        relative = file.relative_to(source / "blobs")
        if relative.parts[0] == "sha256" and not _VAULT_NAME.fullmatch(file.name):
            # Not a vault file: an Office lock file ("~$..."), a sync conflict copy, a
            # thumbnail cache. Nothing refers to it, so it is left behind and reported.
            strays.append(relative.as_posix())
            continue
        destination = target / "blobs" / relative
        expected = file.name if _VAULT_NAME.fullmatch(file.name) else None
        if destination.is_file() and destination.stat().st_size == file.stat().st_size and (
                expected is None or _sha256(destination) == expected):
            skipped += 1
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        shutil.copyfile(file, partial)
        actual = _sha256(partial)
        if expected is not None and actual != expected:
            partial.unlink(missing_ok=True)
            bad.append(str(relative))
            continue
        os.replace(partial, destination)
        copied += 1
        if copied % 2000 == 0:
            say(f"  {copied:,} vault files copied")
    if bad:
        raise RelocationError(f"{len(bad)} vault files failed verification, e.g. {bad[:3]}; "
                              "the project still uses the old home")
    with Database(source / "aki.sqlite3").connect() as connection:
        referenced = {r[0] for r in connection.execute(
            "SELECT storage_key FROM assets UNION SELECT page_image_key FROM pages")}
    absent = sorted(key for key in referenced if not (target / "blobs" / key).is_file())
    if absent:
        raise RelocationError(
            f"{len(absent)} files the database refers to are not in the vault, e.g. {absent[:3]}; "
            "the archive was not moved and the project still uses the old home")

    if strays:
        say(f"Left behind {len(strays)} file(s) in the vault folder that are not vault files, "
            f"e.g. {strays[:3]}")

    say("Copying logs, backups and reports")
    others = 0
    for entry in source.iterdir():
        if entry.name in _SKIP_TOP or entry.name.startswith("aki.sqlite3."):
            if entry.name.startswith("aki.sqlite3.") and entry.is_file():
                shutil.copy2(entry, target / entry.name)
                others += 1
            continue
        if entry.is_dir():
            shutil.copytree(entry, target / entry.name, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, target / entry.name)
        others += 1

    say(f"Copying the database last, with a verified online backup ({needed / 2**30:.1f} GiB archive)")
    database_target = target / "aki.sqlite3"
    staging = target / "aki.sqlite3.relocating"
    for leftover in (staging, Path(str(staging) + "-wal"), Path(str(staging) + "-shm")):
        leftover.unlink(missing_ok=True)  # from an interrupted earlier attempt
    backup = Database(source / "aki.sqlite3").backup(staging)
    os.replace(staging, database_target)

    verified = sum(1 for file in (target / "blobs").rglob("*") if file.is_file())
    report = {
        "from": str(source), "to": str(target), "database": backup,
        "vault_files_copied": copied, "vault_files_already_present": skipped,
        "vault_files_at_target": verified, "other_items_copied": others,
        "stray_files_left_behind": strays,
        "relocated_at": utcnow(), "seconds": round(time.time() - started, 1),
    }
    with Database(database_target).transaction() as connection:
        connection.execute(
            "INSERT INTO audit_events (id, occurred_at, actor_type, actor_id, action,"
            " subject_type, subject_id, before_hash, after_hash, details)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (new_id(), utcnow(), "operator", "aki relocate", "archive_relocated", "archive",
             str(target), None, backup["sha256"], json.dumps(report, sort_keys=True)))
    config = config_path(project_root)
    config.write_text(json.dumps({"home": str(target), "previous_home": str(source),
                                  "relocated_at": report["relocated_at"]}, indent=2),
                      encoding="utf-8")
    (source / "MOVED.txt").write_text(
        f"The canonical AKI archive moved to {target} on {report['relocated_at']}.\n"
        "This folder is no longer used. It was left in place on purpose: once you have\n"
        "opened the reading room from the new location, you can delete this folder.\n",
        encoding="utf-8")
    return report
