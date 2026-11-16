"""Batch acquisition of a catalogued corpus into the immutable vault.

Each object is fetched only after an operation-scoped rights decision permits
``acquire`` for that exact edition. The downloader is the one in ``acquisition.py``:
allowlisted host, rate-limited, size-capped, SSRF-guarded, content-sniffed, and
SHA-256 verified before anything is committed to the database.
"""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .acquisition import AcquisitionPolicy, AcquisitionService, PolicyViolation
from .blob_store import ContentAddressedStore
from .db import Database, new_id, utcnow
from .ingest import _decision_from_row
from .rights import RightsDenied

STORAGE_BUCKET = "books"


class AcquisitionBatchError(RuntimeError):
    """The batch could not be planned or completed."""


@dataclass(frozen=True, slots=True)
class CatalogObject:
    edition_id: str
    source_record_id: str | None
    title: str
    storage_path: str
    media_type: str | None
    declared_size: int | None

    def url(self, base_url: str, bucket: str = STORAGE_BUCKET) -> str:
        quoted = urllib.parse.quote(self.storage_path)
        return f"{base_url.rstrip('/')}/storage/v1/object/{bucket}/{quoted}"


@dataclass
class BatchReport:
    attempted: int = 0
    succeeded: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_stored: int = 0
    started_at: str = ""
    finished_at: str = ""
    errors: list[dict[str, str]] = field(default_factory=list)
    acquired: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_acquisitions(database: Database, *, only_missing: bool = True) -> list[CatalogObject]:
    """Enumerate catalogued objects that carry a storage path."""
    database.require_current()
    query = (
        "SELECT e.id AS edition_id, e.source_record_id, sr.display_title, sr.metadata,"
        " e.metadata AS edition_metadata"
        " FROM editions e"
        " JOIN source_records sr ON sr.id = e.source_record_id"
    )
    with database.connect() as connection:
        rows = connection.execute(query).fetchall()
        already = set()
        if only_missing:
            already = {
                row["edition_id"]
                for row in connection.execute(
                    "SELECT DISTINCT edition_id FROM assets WHERE asset_role='original_scan'"
                )
            }
    planned: list[CatalogObject] = []
    for row in rows:
        if row["edition_id"] in already:
            continue
        metadata = json.loads(row["metadata"] or "{}")
        edition_metadata = json.loads(row["edition_metadata"] or "{}")
        storage_path = metadata.get("storage_path") or edition_metadata.get("storage_path")
        if not storage_path:
            continue
        planned.append(
            CatalogObject(
                edition_id=row["edition_id"],
                source_record_id=row["source_record_id"],
                title=row["display_title"] or "(untitled)",
                storage_path=storage_path,
                media_type=metadata.get("media_type") or edition_metadata.get("media_type"),
                declared_size=metadata.get("declared_size"),
            )
        )
    return planned


def _rights_for(database: Database, edition_id: str) -> tuple[str, Any]:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM rights_decisions WHERE edition_id = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (edition_id,),
        ).fetchone()
    if row is None:
        raise RightsDenied(f"edition {edition_id} has no rights decision")
    decision = _decision_from_row(row)
    return str(row["id"]), decision


def acquire_object(
    database: Database,
    service: AcquisitionService,
    store: ContentAddressedStore,
    target: CatalogObject,
    *,
    base_url: str,
) -> dict[str, Any]:
    """Acquire one object and commit an asset row. Idempotent on source URL."""
    url = target.url(base_url)
    with database.connect() as connection:
        existing = connection.execute(
            "SELECT id, sha256 FROM assets WHERE edition_id=? AND source_url=?",
            (target.edition_id, url),
        ).fetchone()
    if existing is not None:
        return {"edition_id": target.edition_id, "status": "skipped", "sha256": existing["sha256"]}

    decision_id, rights = _rights_for(database, target.edition_id)
    result = service.download(
        url,
        rights=rights,
        destination_dir=Path(service.store.root).parent / "tmp",
        expected_media_types=None,
        max_retries=2,
    )

    asset_id = new_id()
    filename = urllib.parse.unquote(target.storage_path.rsplit("/", 1)[-1])
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO assets (id, edition_id, rights_decision_id, source_url, storage_key,"
            " sha256, byte_size, detected_mime, original_filename, asset_role, acquired_at,"
            " immutable, preflight_status, preflight_report, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                asset_id, target.edition_id, decision_id, url,
                result.stored.path.relative_to(store.root).as_posix(),
                result.sha256, result.byte_size, result.detected_mime, filename,
                "original_scan", utcnow(), 1, "passed",
                json.dumps(result.preflight.to_dict(), sort_keys=True), utcnow(),
            ),
        )
    return {
        "edition_id": target.edition_id,
        "asset_id": asset_id,
        "status": "acquired",
        "sha256": result.sha256,
        "byte_size": result.byte_size,
        "media_type": result.detected_mime,
        "title": target.title,
    }


def acquire_catalog(
    database: Database,
    blob_root: str | Path,
    *,
    base_url: str,
    limit: int | None = None,
    min_interval_seconds: float = 1.0,
    max_bytes: int = 512 * 1024 * 1024,
    progress: Callable[[dict[str, Any]], None] | None = None,
    max_consecutive_failures: int = 12,
) -> BatchReport:
    """Download every rights-cleared catalogued object that is not yet archived."""
    host = urllib.parse.urlparse(base_url).hostname or ""
    if not host:
        raise AcquisitionBatchError(f"invalid base_url: {base_url!r}")
    policy = AcquisitionPolicy(
        allowed_hosts=frozenset({host}),
        min_interval_seconds=min_interval_seconds,
        max_bytes=max_bytes,
        max_redirects=3,
        timeout_seconds=120.0,
    )
    store = ContentAddressedStore(blob_root)
    service = AcquisitionService(database, store, policy)
    targets = plan_acquisitions(database)
    if limit is not None:
        targets = targets[:limit]
    report = BatchReport(started_at=utcnow())
    consecutive = 0

    for index, target in enumerate(targets, start=1):
        report.attempted += 1
        try:
            outcome = acquire_object(database, service, store, target, base_url=base_url)
            if outcome["status"] == "skipped":
                report.skipped += 1
            else:
                report.succeeded += 1
                report.bytes_stored += int(outcome.get("byte_size") or 0)
                report.acquired.append(outcome)
            consecutive = 0
        except (RightsDenied, PolicyViolation) as exc:
            report.failed += 1
            consecutive += 1
            report.errors.append({
                "edition_id": target.edition_id,
                "title": target.title[:120],
                "error": f"{type(exc).__name__}: {exc}",
            })
        except Exception as exc:  # noqa: BLE001 - recorded per object, batch continues
            report.failed += 1
            consecutive += 1
            report.errors.append({
                "edition_id": target.edition_id,
                "title": target.title[:120],
                "error": f"{type(exc).__name__}: {exc}",
            })
        if progress is not None:
            progress({
                "index": index,
                "total": len(targets),
                "edition_id": target.edition_id,
                "title": target.title[:100],
                "succeeded": report.succeeded,
                "failed": report.failed,
                "skipped": report.skipped,
                "bytes": report.bytes_stored,
            })
        if consecutive >= max_consecutive_failures:
            report.errors.append({
                "edition_id": "-",
                "title": "-",
                "error": f"aborted after {consecutive} consecutive failures",
            })
            break

    report.finished_at = utcnow()
    return report


def acquisition_state(database: Database) -> dict[str, Any]:
    """Coverage of the acquisition stage, excluding the synthetic fixture work."""
    database.require_current()
    with database.connect() as connection:
        total_editions = connection.execute(
            "SELECT COUNT(*) AS n FROM editions e WHERE e.source_record_id IS NOT NULL"
        ).fetchone()["n"]
        with_asset = connection.execute(
            "SELECT COUNT(DISTINCT e.id) AS n FROM editions e JOIN assets a ON a.edition_id = e.id"
            " WHERE e.source_record_id IS NOT NULL"
        ).fetchone()["n"]
        stats = connection.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(byte_size),0) AS b FROM assets"
        ).fetchone()
        by_mime = connection.execute(
            "SELECT detected_mime, COUNT(*) AS n FROM assets GROUP BY detected_mime"
        ).fetchall()
    return {
        "catalogued_editions": int(total_editions),
        "editions_with_archived_asset": int(with_asset),
        "assets_total": int(stats["n"]),
        "bytes_total": int(stats["b"]),
        "by_media_type": {row["detected_mime"]: int(row["n"]) for row in by_mime},
    }
