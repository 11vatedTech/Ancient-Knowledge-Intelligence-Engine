"""Source-inventory reconciliation for an external catalog surface.

The catalog adapter is snapshot-based on purpose. A live listing that paginates in the
client cannot be enumerated by URL, so the observable inventory is captured once,
with its capture metadata, and imported idempotently. Nothing is inferred from a title:
missing authors, dates, languages and rights are recorded as absent.

Every imported record starts in rights state ``review_required`` with **no permitted
operations**, because a public download listing is not a licence. Acquisition therefore
fails closed until a human records an evidence-backed decision.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .db import Database, utcnow

_TITLE_NOISE = re.compile(r"[^\w\s]", re.UNICODE)


def stable_key(*parts: str) -> str:
    """Deterministic identifier so repeated imports never duplicate a record."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:20]


def normalize_title(title: str) -> str:
    return " ".join(_TITLE_NOISE.sub(" ", title).casefold().split())


@dataclass(frozen=True, slots=True)
class ListingItem:
    title: str
    author: str | None = None
    description: str | None = None
    published_label: str | None = None
    size_label: str | None = None
    downloads: int | None = None
    detail_url: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("listing item requires a title")


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    source_name: str
    site_url: str
    captured_at: str
    advertised_count: int | None
    items: tuple[ListingItem, ...]
    pagination_model: str
    discovery_method: str
    rights_evidence: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    observed_pages: int | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InventorySnapshot":
        items = tuple(
            ListingItem(
                title=item["title"],
                author=item.get("author"),
                description=item.get("description"),
                published_label=item.get("published_label"),
                size_label=item.get("size_label"),
                downloads=item.get("downloads"),
                detail_url=item.get("detail_url"),
            )
            for item in payload.get("items", [])
        )
        return cls(
            source_name=payload["source_name"],
            site_url=payload["site_url"],
            captured_at=payload.get("captured_at") or utcnow(),
            advertised_count=payload.get("advertised_count"),
            items=items,
            pagination_model=payload.get("pagination_model", "unknown"),
            discovery_method=payload.get("discovery_method", "manual snapshot"),
            rights_evidence=tuple(payload.get("rights_evidence", [])),
            notes=tuple(payload.get("notes", [])),
            observed_pages=payload.get("observed_pages"),
        )

    def deduplicated(self) -> tuple[ListingItem, ...]:
        seen: dict[str, ListingItem] = {}
        for item in self.items:
            key = f"{normalize_title(item.title)}|{(item.author or '').casefold()}"
            seen.setdefault(key, item)
        return tuple(seen.values())


@dataclass(frozen=True, slots=True)
class ImportReport:
    source_records_added: int
    source_records_existing: int
    works_added: int
    works_existing: int
    editions_added: int
    rights_decisions_added: int
    duplicates_in_snapshot: int
    advertised_count: int | None
    observed_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_snapshot(path: str | Path) -> InventorySnapshot:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("inventory snapshot must be a JSON object")
    return InventorySnapshot.from_dict(payload)


def import_snapshot(
    database: Database,
    snapshot: InventorySnapshot,
    *,
    site_name: str | None = None,
) -> ImportReport:
    """Import a captured catalog listing. Idempotent on stable, derived identifiers."""
    database.require_current()
    name = site_name or snapshot.source_name
    items = snapshot.deduplicated()
    duplicates = len(snapshot.items) - len(items)
    added = existing = works_added = works_existing = editions_added = rights_added = 0
    now = utcnow()

    with database.transaction() as connection:
        for item in items:
            record_key = stable_key(name, normalize_title(item.title), (item.author or "").casefold())
            record_id = f"src-{record_key}"
            record_url = item.detail_url or f"{snapshot.site_url.rstrip('/')}/#record-{record_key}"
            record = connection.execute(
                "SELECT id FROM source_records WHERE source_name=? AND record_url=?",
                (name, record_url),
            ).fetchone()
            if record is None:
                connection.execute(
                    "INSERT INTO source_records (id, source_name, record_url, display_title,"
                    " category_labels, discovered_at, discovery_method, record_status, metadata,"
                    " created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        record_id, name, record_url, item.title, "[]", now,
                        snapshot.discovery_method, "metadata_captured",
                        json.dumps(
                            {
                                "author_as_listed": item.author,
                                "description_as_listed": item.description,
                                "published_label": item.published_label,
                                "size_label": item.size_label,
                                "downloads_as_listed": item.downloads,
                                "page_count_observed": snapshot.observed_pages,
                            },
                            sort_keys=True,
                        ),
                        now,
                    ),
                )
                added += 1
            else:
                existing += 1
                record_id = record_id if record["id"] == record_id else record["id"]

            work_key = stable_key("work", normalize_title(item.title), (item.author or "").casefold())
            work_id = f"wrk-{work_key}"
            work = connection.execute("SELECT id FROM works WHERE id=?", (work_id,)).fetchone()
            if work is None:
                connection.execute(
                    "INSERT INTO works (id, canonical_title, normalized_title, work_class,"
                    " attributed_author, attribution_confidence, description, metadata, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        work_id, item.title, normalize_title(item.title), "unclassified",
                        item.author, None, item.description,
                        json.dumps({"classification_basis": "title and listing only"}, sort_keys=True),
                        now,
                    ),
                )
                works_added += 1
            else:
                works_existing += 1

            edition_key = stable_key("edition", work_id, name, record_id)
            edition_id = f"edt-{edition_key}"
            if connection.execute("SELECT id FROM editions WHERE id=?", (edition_id,)).fetchone() is None:
                connection.execute(
                    "INSERT INTO editions (id, work_id, source_record_id, edition_title,"
                    " publication_year, language, edition_type, metadata, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        edition_id, work_id, record_id, item.title, None, None, "unknown",
                        json.dumps(
                            {"publication_data": "not published on the listing"}, sort_keys=True
                        ),
                        now,
                    ),
                )
                editions_added += 1

            decision_id = f"rgt-{stable_key('rights', edition_id)}"
            if connection.execute(
                "SELECT id FROM rights_decisions WHERE id=?", (decision_id,)
            ).fetchone() is None:
                connection.execute(
                    "INSERT INTO rights_decisions (id, edition_id, source_record_id, state,"
                    " evidence_uri, scope, notes, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        decision_id, edition_id, record_id, "review_required",
                        snapshot.site_url, "{}",
                        "No licence, public-domain statement, or permission text was published "
                        "on the catalog surface at capture time. The listed file is a modern "
                        "digital edition whose rights are independent of the age of the "
                        "underlying work. All operations are denied until a reviewer records "
                        "an evidence-backed decision."
                        + ("" if item.detail_url else " No per-record detail URL was observable."),
                        now,
                    ),
                )
                rights_added += 1

    return ImportReport(
        source_records_added=added,
        source_records_existing=existing,
        works_added=works_added,
        works_existing=works_existing,
        editions_added=editions_added,
        rights_decisions_added=rights_added,
        duplicates_in_snapshot=duplicates,
        advertised_count=snapshot.advertised_count,
        observed_count=len(items),
    )


def disposition_counts(database: Database) -> dict[str, int]:
    """Every discovered record must hold exactly one transparent disposition."""
    database.require_current()
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT rs.state AS rights_state, COUNT(DISTINCT sr.id) AS n"
            " FROM source_records sr"
            " LEFT JOIN editions e ON e.source_record_id = sr.id"
            " LEFT JOIN rights_decisions rs ON rs.edition_id = e.id"
            " GROUP BY rs.state"
        ).fetchall()
        assets = connection.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(byte_size), 0) AS bytes FROM assets"
        ).fetchone()
    counts = {(row["rights_state"] or "no_edition_or_rights_record"): int(row["n"]) for row in rows}
    counts["_acquired_assets"] = int(assets["n"])
    counts["_acquired_bytes"] = int(assets["bytes"])
    return counts


def acquisition_report(database: Database) -> dict[str, Any]:
    """Factual inventory report; the denominator is the discovered inventory."""
    database.require_current()
    with database.connect() as connection:
        source_total = int(
            connection.execute("SELECT COUNT(*) AS n FROM source_records").fetchone()["n"]
        )
        works = int(connection.execute("SELECT COUNT(*) AS n FROM works").fetchone()["n"])
        editions = int(connection.execute("SELECT COUNT(*) AS n FROM editions").fetchone()["n"])
        verified_assets = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM assets WHERE preflight_status='passed'"
            ).fetchone()["n"]
        )
        bytes_stored = int(
            connection.execute("SELECT COALESCE(SUM(byte_size),0) AS n FROM assets").fetchone()["n"]
        )
        rights_rows = connection.execute(
            "SELECT state, COUNT(*) AS n FROM rights_decisions GROUP BY state"
        ).fetchall()
    by_state = {row["state"]: int(row["n"]) for row in rights_rows}
    catalogue_only = sum(
        count for state, count in by_state.items()
        if state in {"unknown", "review_required", "restricted", "denied", "expired_or_revoked"}
    )
    return {
        "discovered_source_records": source_total,
        "unique_works": works,
        "unique_editions": editions,
        "acquired_assets_preflight_passed": verified_assets,
        "rights_cleared_editions": by_state.get("cleared_limited", 0)
        + by_state.get("cleared_full", 0),
        "rights_review_required_editions": by_state.get("review_required", 0),
        "catalogued_without_processing": catalogue_only,
        "bytes_stored": bytes_stored,
        "processing_ready": 0 if catalogue_only == editions else editions - catalogue_only,
        "dispositions": disposition_counts(database),
        "note": (
            "Catalogued is not acquired. A record counts as acquired only when an asset "
            "exists, passed preflight, and its SHA-256 is stored in the immutable vault."
        ),
    }


def export_manifest(database: Database, destination: str | Path) -> dict[str, Any]:
    """Write the reviewed JSONL acquisition manifest for operator handoff."""
    database.require_current()
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT sr.id, sr.display_title, sr.record_url, w.attributed_author,"
            " e.id AS edition_id, e.language, rd.state, rd.notes"
            " FROM source_records sr"
            " LEFT JOIN editions e ON e.source_record_id = sr.id"
            " LEFT JOIN works w ON w.id = e.work_id"
            " LEFT JOIN rights_decisions rd ON rd.edition_id = e.id"
            " ORDER BY sr.display_title"
        ).fetchall()
    lines = [
        json.dumps(
            {
                "source_id": row["id"],
                "title": row["display_title"],
                "creator": row["attributed_author"],
                "language": row["language"],
                "source_uri": row["record_url"],
                "collection": "The Holy Word",
                "edition_label": row["edition_id"],
                "rights": {
                    "status": "permission_required",
                    "permitted_operations": [],
                    "evidence_uri": row["record_url"],
                    "attribution_required": True,
                    "notes": row["notes"] or "",
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for row in rows
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": str(target), "records": len(lines)}


@dataclass(frozen=True, slots=True)
class CatalogConfig:
    """Connection settings for a PostgREST-style public catalog listing."""

    base_url: str
    anon_key: str
    table: str = "books"
    page_size: int = 200
    request_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not self.base_url.startswith("https://"):
            raise ValueError("catalog base_url must use https")
        if not self.anon_key.strip():
            raise ValueError("catalog anon_key is required")
        if not 1 <= self.page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")

    @classmethod
    def load(cls, path: str | Path) -> "CatalogConfig":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            base_url=payload["base_url"],
            anon_key=payload["anon_key"],
            table=payload.get("table", "books"),
            page_size=int(payload.get("page_size", 200)),
            request_interval_seconds=float(payload.get("request_interval_seconds", 1.0)),
        )

    @property
    def mask(self) -> str:
        return f"{self.base_url} table={self.table} key=***"


class CatalogFetchError(RuntimeError):
    """The public catalog listing could not be enumerated."""


def fetch_catalog(
    config: CatalogConfig,
    *,
    site_url: str = "https://theholyword.net/",
    status_filter: str = "approved",
    max_records: int = 2000,
    max_attempts: int = 3,
    sleeper: Any = None,
) -> InventorySnapshot:
    """Enumerate an anonymous, publicly readable catalog listing.

    Uses only the listing the public site itself serves to anonymous visitors. Pagination
    is by explicit row range so an interrupted run can resume without duplicating rows.
    """
    import time as _time
    import urllib.error
    import urllib.parse
    import urllib.request

    pause = sleeper or _time.sleep
    columns = (
        "id,title,author,description,file_path,file_type,file_size,"
        "download_count,created_at,category,uploader_name"
    )
    records: dict[str, ListingItem] = {}
    offset = 0
    advertised: int | None = None
    while True:
        query = urllib.parse.urlencode(
            {
                "select": columns,
                "status": f"eq.{status_filter}",
                "order": "pinned_order.asc.nullslast,download_count.desc,created_at.desc",
                "offset": str(offset),
                "limit": str(config.page_size),
            }
        )
        request = urllib.request.Request(
            f"{config.base_url}/rest/v1/{config.table}?{query}",
            headers={
                "apikey": config.anon_key,
                "Authorization": f"Bearer {config.anon_key}",
                "Accept": "application/json",
                "Prefer": "count=exact",
                "User-Agent": "AKI-Research-Engine/0.1 (local archival catalog)",
            },
        )
        payload: list[dict[str, Any]] | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    if response.status not in (200, 206):
                        raise CatalogFetchError(f"catalog returned HTTP {response.status}")
                    content_range = response.headers.get("Content-Range", "")
                    if "/" in content_range:
                        total = content_range.rsplit("/", 1)[1].strip()
                        if total.isdigit():
                            advertised = int(total)
                    payload = json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt == max_attempts:
                    raise CatalogFetchError(f"catalog page at offset {offset} failed: {exc}") from exc
                pause(min(2 ** attempt, 20))
        if payload is None:
            raise CatalogFetchError(f"catalog page at offset {offset} returned no payload")
        if not payload:
            break
        for row in payload:
            identifier = str(row.get("id") or stable_key(str(row.get("title"))))
            size = row.get("file_size")
            records[identifier] = ListingItem(
                title=str(row.get("title") or "(untitled record)").strip(),
                author=(row.get("author") or None),
                description=(row.get("description") or None),
                published_label=(row.get("created_at") or None),
                size_label=(f"{int(size)} bytes" if isinstance(size, int) else None),
                downloads=(row.get("download_count") if isinstance(row.get("download_count"), int) else None),
                detail_url=f"{site_url.rstrip('/')}/bible/{identifier}",
            )
        offset += len(payload)
        if len(records) >= max_records:
            break
        if len(payload) < config.page_size:
            break
        pause(config.request_interval_seconds)

    if not records:
        raise CatalogFetchError("catalog listing returned no approved records")
    return InventorySnapshot(
        source_name="theholyword.net",
        site_url=site_url,
        captured_at=utcnow(),
        advertised_count=advertised,
        items=tuple(records.values()),
        pagination_model="server-side row range pagination over an anonymous read-only listing",
        discovery_method=(
            "anonymous public catalog listing (PostgREST read endpoint used by the public "
            "site itself); no authentication, paywall or access control was bypassed"
        ),
        rights_evidence=(),
        notes=(
            "No licence, terms-of-use, or public-domain statement is published on the catalog "
            "surface. Files are publicly reachable from object storage but reachability is not "
            "permission.",
            "Titles are recorded as listed and are often uploader filenames; they are not "
            "verified bibliographic titles.",
            "Author, date, language and edition data are absent or unverified for most records.",
        ),
        observed_pages=None,
    )


def write_snapshot(snapshot: InventorySnapshot, destination: str | Path) -> str:
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_name": snapshot.source_name,
        "site_url": snapshot.site_url,
        "captured_at": snapshot.captured_at,
        "advertised_count": snapshot.advertised_count,
        "observed_pages": snapshot.observed_pages,
        "pagination_model": snapshot.pagination_model,
        "discovery_method": snapshot.discovery_method,
        "rights_evidence": list(snapshot.rights_evidence),
        "notes": list(snapshot.notes),
        "items": [asdict(item) for item in snapshot.items],
    }
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return str(target)
