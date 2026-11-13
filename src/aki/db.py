"""Canonical relational layer: versioned migrations, integrity, backup and restore.

SQLite is the supported local-first backend. All identifiers are application-generated
UUIDv4 strings, all timestamps are ISO-8601 UTC with a trailing ``Z`` (lexicographically
sortable), and all structured metadata is stored as JSON text. The schema deliberately
mirrors the AKI-ARCH-001 normative baseline so a later PostgreSQL migration is a
translation of the same tables and constraints rather than a redesign.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 2


def utcnow() -> str:
    """Current UTC instant as a sortable ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id() -> str:
    return str(uuid.uuid4())


def parse_instant(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str
    requires_foreign_keys_off: bool = False


# --------------------------------------------------------------------------------------
# Migration 1 — canonical schema baseline (AKI-ARCH-001 §7.4, SQLite dialect)
# --------------------------------------------------------------------------------------

_M1 = """
CREATE TABLE source_records (
    id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    record_url TEXT NOT NULL,
    display_title TEXT,
    category_labels TEXT NOT NULL DEFAULT '[]',
    discovered_at TEXT NOT NULL,
    discovery_method TEXT NOT NULL,
    source_updated_at TEXT,
    record_status TEXT NOT NULL CHECK (record_status IN
        ('discovered','metadata_captured','duplicate','unavailable','excluded')),
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (source_name, record_url)
);

CREATE TABLE works (
    id TEXT PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    work_class TEXT NOT NULL CHECK (work_class IN
        ('ancient_primary','historical_secondary','historical_edition',
         'modern_commentary','unclassified')),
    attributed_author TEXT,
    attribution_confidence REAL CHECK (attribution_confidence IS NULL
        OR (attribution_confidence >= 0 AND attribution_confidence <= 1)),
    origin_period_start TEXT,
    origin_period_end TEXT,
    origin_place TEXT,
    original_language TEXT,
    original_script TEXT,
    description TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_works_normalized ON works (normalized_title, COALESCE(attributed_author, ''));

CREATE TABLE editions (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES works(id) ON DELETE RESTRICT,
    source_record_id TEXT REFERENCES source_records(id) ON DELETE SET NULL,
    edition_title TEXT NOT NULL,
    publication_year TEXT,
    publication_place TEXT,
    publisher TEXT,
    translator TEXT,
    editor TEXT,
    language TEXT,
    script TEXT,
    edition_type TEXT NOT NULL CHECK (edition_type IN
        ('original_artifact','facsimile','transcription','translation',
         'critical_edition','commentary','unknown')),
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX ix_editions_work ON editions (work_id);
CREATE INDEX ix_editions_source_record ON editions (source_record_id);

CREATE TABLE rights_decisions (
    id TEXT PRIMARY KEY,
    edition_id TEXT REFERENCES editions(id) ON DELETE RESTRICT,
    source_record_id TEXT REFERENCES source_records(id) ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK (state IN
        ('unknown','review_required','cleared_limited','cleared_full',
         'restricted','denied','expired_or_revoked')),
    jurisdiction TEXT,
    rights_holder TEXT,
    license_name TEXT,
    license_version TEXT,
    license_url TEXT,
    evidence_uri TEXT,
    reviewer TEXT,
    decided_at TEXT,
    review_due_at TEXT,
    scope TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (edition_id IS NOT NULL OR source_record_id IS NOT NULL)
);
CREATE INDEX ix_rights_edition ON rights_decisions (edition_id);
CREATE INDEX ix_rights_source_record ON rights_decisions (source_record_id);

CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    edition_id TEXT NOT NULL REFERENCES editions(id) ON DELETE RESTRICT,
    rights_decision_id TEXT NOT NULL REFERENCES rights_decisions(id) ON DELETE RESTRICT,
    source_url TEXT,
    storage_key TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    detected_mime TEXT NOT NULL,
    original_filename TEXT,
    asset_role TEXT NOT NULL CHECK (asset_role IN
        ('original_scan','supplement','cover','page_image','derivative','permission_document')),
    acquired_at TEXT,
    immutable INTEGER NOT NULL DEFAULT 1 CHECK (immutable IN (0, 1)),
    preflight_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (preflight_status IN ('pending','passed','quarantined','rejected')),
    preflight_report TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (edition_id, sha256, asset_role)
);
CREATE INDEX ix_assets_edition ON assets (edition_id);

CREATE TABLE pages (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    page_index INTEGER NOT NULL CHECK (page_index >= 0),
    printed_page_label TEXT,
    width_px INTEGER NOT NULL CHECK (width_px > 0),
    height_px INTEGER NOT NULL CHECK (height_px > 0),
    dpi_x REAL,
    dpi_y REAL,
    rotation_degrees INTEGER NOT NULL DEFAULT 0,
    page_image_key TEXT NOT NULL,
    page_sha256 TEXT NOT NULL CHECK (length(page_sha256) = 64),
    quality_report TEXT NOT NULL DEFAULT '{}',
    processing_state TEXT NOT NULL DEFAULT 'pending' CHECK (processing_state IN
        ('pending','queued','running','completed','held','failed','review_required','cancelled')),
    created_at TEXT NOT NULL,
    UNIQUE (asset_id, page_index)
);
CREATE INDEX ix_pages_state ON pages (processing_state);

CREATE TABLE page_regions (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    region_type TEXT NOT NULL,
    polygon TEXT NOT NULL,
    reading_order INTEGER,
    detection_method TEXT NOT NULL,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    parent_region_id TEXT REFERENCES page_regions(id) ON DELETE SET NULL,
    region_image_key TEXT,
    pipeline_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_regions_page ON page_regions (page_id);

CREATE TABLE transcriptions (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    region_id TEXT REFERENCES page_regions(id) ON DELETE SET NULL,
    layer TEXT NOT NULL CHECK (layer IN
        ('diplomatic','normalized','transliteration','translation','editorial')),
    text_content TEXT NOT NULL,
    language_code TEXT,
    script_code TEXT,
    engine_name TEXT NOT NULL,
    model_digest TEXT,
    engine_version TEXT NOT NULL,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    token_geometry TEXT NOT NULL DEFAULT '[]',
    review_state TEXT NOT NULL DEFAULT 'unreviewed' CHECK (review_state IN
        ('unreviewed','accepted','corrected','rejected')),
    supersedes_id TEXT REFERENCES transcriptions(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_transcriptions_page ON transcriptions (page_id, layer);

CREATE TABLE visual_observations (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    region_id TEXT REFERENCES page_regions(id) ON DELETE SET NULL,
    observation_type TEXT NOT NULL,
    literal_observation TEXT NOT NULL,
    interpretation TEXT,
    evidence_class TEXT NOT NULL CHECK (evidence_class IN
        ('direct_visual_observation','model_inference','editorial_caption','human_interpretation')),
    model_name TEXT NOT NULL,
    model_digest TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    alternatives TEXT NOT NULL DEFAULT '[]',
    source_crop_sha256 TEXT,
    review_state TEXT NOT NULL DEFAULT 'unreviewed' CHECK (review_state IN
        ('unreviewed','accepted','corrected','rejected')),
    created_at TEXT NOT NULL
);
CREATE INDEX ix_observations_page ON visual_observations (page_id);

CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    description TEXT,
    temporal_scope TEXT NOT NULL DEFAULT '{}',
    geographic_scope TEXT NOT NULL DEFAULT '{}',
    canonical_status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (canonical_status IN ('candidate','confirmed','rejected')),
    created_at TEXT NOT NULL
);

CREATE TABLE entity_aliases (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    language_code TEXT,
    script_code TEXT,
    transliteration_system TEXT,
    alias_type TEXT NOT NULL,
    UNIQUE (entity_id, alias, language_code, script_code)
);

CREATE TABLE evidence (
    id TEXT PRIMARY KEY,
    edition_id TEXT REFERENCES editions(id) ON DELETE RESTRICT,
    page_id TEXT REFERENCES pages(id) ON DELETE RESTRICT,
    region_id TEXT REFERENCES page_regions(id) ON DELETE SET NULL,
    transcription_id TEXT REFERENCES transcriptions(id) ON DELETE SET NULL,
    visual_observation_id TEXT REFERENCES visual_observations(id) ON DELETE SET NULL,
    external_source_uri TEXT,
    locator TEXT NOT NULL DEFAULT '{}',
    quoted_text TEXT,
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN
        ('passage','page_image','region','visual_observation','external_record','editorial_note')),
    evidence_class TEXT NOT NULL CHECK (evidence_class IN
        ('source_explicit','editorial','historical_context','model_inferred',
         'comparative_hypothesis','unverified')),
    source_hash TEXT CHECK (source_hash IS NULL OR length(source_hash) = 64),
    created_at TEXT NOT NULL,
    CHECK (page_id IS NOT NULL OR external_source_uri IS NOT NULL)
);
CREATE INDEX ix_evidence_page ON evidence (page_id);
CREATE INDEX ix_evidence_transcription ON evidence (transcription_id);

CREATE TABLE mentions (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    mention_text TEXT,
    mention_kind TEXT NOT NULL CHECK (mention_kind IN ('text','visual','editorial')),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    resolution_state TEXT NOT NULL DEFAULT 'candidate'
        CHECK (resolution_state IN ('candidate','confirmed','rejected'))
);

CREATE TABLE relationships (
    id TEXT PRIMARY KEY,
    subject_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    predicate TEXT NOT NULL,
    object_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    evidence_class TEXT NOT NULL CHECK (evidence_class IN
        ('source_explicit','editorial','historical_context','model_inferred',
         'comparative_hypothesis','unverified')),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    temporal_scope TEXT NOT NULL DEFAULT '{}',
    geographic_scope TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN
        ('candidate','verified','disputed','rejected')),
    created_by_run TEXT,
    created_at TEXT NOT NULL,
    CHECK (subject_entity_id <> object_entity_id OR predicate = 'self_reference')
);
CREATE INDEX ix_rel_subject ON relationships (subject_entity_id);
CREATE INDEX ix_rel_object ON relationships (object_entity_id);

CREATE TABLE relationship_evidence (
    relationship_id TEXT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    support_type TEXT NOT NULL CHECK (support_type IN
        ('supports','contradicts','qualifies','contextualizes')),
    PRIMARY KEY (relationship_id, evidence_id, support_type)
);

CREATE TABLE claims (
    id TEXT PRIMARY KEY,
    research_case_id TEXT,
    claim_text TEXT NOT NULL,
    claim_type TEXT NOT NULL CHECK (claim_type IN
        ('source_explicit','editorial','historical_context','model_inferred',
         'comparative_hypothesis','unverified')),
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN
        ('proposed','supported','contested','unverified','rejected')),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    created_by_run TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE claim_evidence (
    claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    relation TEXT NOT NULL CHECK (relation IN
        ('supports','contradicts','qualifies','contextualizes','source_of_claim')),
    PRIMARY KEY (claim_id, evidence_id, relation)
);

CREATE TABLE model_runs (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_digest TEXT NOT NULL,
    runtime_version TEXT NOT NULL,
    prompt_version TEXT,
    parameters TEXT NOT NULL DEFAULT '{}',
    hardware_profile TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('succeeded','failed','cancelled','partial')),
    metrics TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL CHECK (state IN
        ('queued','leased','running','succeeded','retry_wait','failed','cancelled','held')),
    priority INTEGER NOT NULL DEFAULT 50,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    checkpoint TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT UNIQUE,
    last_error TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX ix_jobs_schedule ON jobs (state, available_at, priority);
CREATE INDEX ix_jobs_lease ON jobs (lease_expires_at);

CREATE TABLE job_locks (
    lock_key TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    before_hash TEXT,
    after_hash TEXT,
    details TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_audit_subject ON audit_events (subject_type, subject_id);
CREATE INDEX ix_audit_action ON audit_events (action);

CREATE TABLE research_cases (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '{}',
    corpus_snapshot TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'created' CHECK (state IN
        ('created','retrieving','analyzing','verifying','complete','failed')),
    dossier TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE chunk_records (
    id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    edition_id TEXT NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
    work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    layer TEXT NOT NULL,
    text TEXT NOT NULL,
    language_code TEXT,
    script_code TEXT,
    pipeline_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_chunks_page ON chunk_records (page_id);
CREATE INDEX ix_chunks_edition ON chunk_records (edition_id);

CREATE VIRTUAL TABLE chunks_fts USING fts5 (
    chunk_id UNINDEXED,
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE VIEW v_coverage AS
SELECT
    (SELECT COUNT(*) FROM source_records) AS source_records,
    (SELECT COUNT(*) FROM works) AS works,
    (SELECT COUNT(*) FROM editions) AS editions,
    (SELECT COUNT(*) FROM assets) AS assets,
    (SELECT COUNT(*) FROM assets WHERE preflight_status = 'passed') AS assets_verified,
    (SELECT COUNT(*) FROM pages) AS pages,
    (SELECT COUNT(*) FROM pages WHERE processing_state = 'completed') AS pages_completed,
    (SELECT COUNT(*) FROM transcriptions) AS transcriptions,
    (SELECT COUNT(*) FROM evidence) AS evidence,
    (SELECT COUNT(*) FROM claims) AS claims,
    (SELECT COUNT(*) FROM jobs WHERE state IN ('queued','retry_wait')) AS jobs_pending;
"""

# Migration 2 — the same bytes may legitimately back more than one edition.
# ``storage_key`` is a content-addressed vault path, not a per-edition identity, so a
# UNIQUE constraint on it wrongly rejects correct deduplication across editions.
_M2 = """
DROP VIEW IF EXISTS v_coverage;
DROP TABLE IF EXISTS assets_v2;
CREATE TABLE assets_v2 (
    id TEXT PRIMARY KEY,
    edition_id TEXT NOT NULL REFERENCES editions(id) ON DELETE RESTRICT,
    rights_decision_id TEXT NOT NULL REFERENCES rights_decisions(id) ON DELETE RESTRICT,
    source_url TEXT,
    storage_key TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    detected_mime TEXT NOT NULL,
    original_filename TEXT,
    asset_role TEXT NOT NULL CHECK (asset_role IN
        ('original_scan','supplement','cover','page_image','derivative','permission_document')),
    acquired_at TEXT,
    immutable INTEGER NOT NULL DEFAULT 1 CHECK (immutable IN (0, 1)),
    preflight_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (preflight_status IN ('pending','passed','quarantined','rejected')),
    preflight_report TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (edition_id, sha256, asset_role)
);
INSERT INTO assets_v2 SELECT id, edition_id, rights_decision_id, source_url, storage_key,
    sha256, byte_size, detected_mime, original_filename, asset_role, acquired_at, immutable,
    preflight_status, preflight_report, created_at FROM assets;
DROP TABLE assets;
ALTER TABLE assets_v2 RENAME TO assets;
CREATE INDEX ix_assets_edition ON assets (edition_id);
CREATE INDEX ix_assets_sha256 ON assets (sha256);
CREATE INDEX ix_assets_storage_key ON assets (storage_key);
CREATE VIEW v_coverage AS
SELECT
    (SELECT COUNT(*) FROM source_records) AS source_records,
    (SELECT COUNT(*) FROM works) AS works,
    (SELECT COUNT(*) FROM editions) AS editions,
    (SELECT COUNT(*) FROM assets) AS assets,
    (SELECT COUNT(*) FROM assets WHERE preflight_status = 'passed') AS assets_verified,
    (SELECT COUNT(*) FROM pages) AS pages,
    (SELECT COUNT(*) FROM pages WHERE processing_state = 'completed') AS pages_completed,
    (SELECT COUNT(*) FROM transcriptions) AS transcriptions,
    (SELECT COUNT(*) FROM evidence) AS evidence,
    (SELECT COUNT(*) FROM claims) AS claims,
    (SELECT COUNT(*) FROM jobs WHERE state IN ('queued','retry_wait')) AS jobs_pending;
"""

MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "canonical_schema_baseline", _M1),
    Migration(2, "shared_content_objects_across_editions", _M2, requires_foreign_keys_off=True),
)


class SchemaError(RuntimeError):
    """The database schema is missing, newer than this build, or tampered with."""


class Database:
    """Owns the canonical SQLite database and its migration state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=60, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 60000")
            # ``journal_mode`` is a property of the database file, not the connection. It
            # used to be re-asserted here on every connect, and because ingestion opens a
            # connection per page that meant three workers serialising on a lock they did
            # not need. It is set once at creation and on migration instead.
            connection.execute(f"PRAGMA synchronous = {_synchronous_mode()}")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serializable write transaction using BEGIN IMMEDIATE."""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    def _ensure_migration_table(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
            " checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )

    def schema_version(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if row is None:
                return 0
            result = connection.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(result["v"] or 0)

    def migrate(self) -> list[int]:
        """Apply pending migrations in order. Re-verify applied migration checksums."""
        applied: list[int] = []
        with self.connect() as connection:
            self._ensure_migration_table(connection)
            rows = connection.execute(
                "SELECT version, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            known = {m.version: m for m in MIGRATIONS}
            for row in rows:
                migration = known.get(int(row["version"]))
                if migration is None:
                    raise SchemaError(
                        f"database migration version {row['version']} is newer than this build"
                    )
                if migration_checksum(migration) != row["checksum"]:
                    raise SchemaError(
                        f"migration {migration.version} checksum mismatch; schema tampered with"
                    )
            done = {int(row["version"]) for row in rows}
            for migration in MIGRATIONS:
                if migration.version in done:
                    continue
                record = (
                    "INSERT INTO schema_migrations (version, name, checksum, applied_at)"
                    f" VALUES ({migration.version}, {_literal(migration.name)},"
                    f" {_literal(migration_checksum(migration))}, {_literal(utcnow())});"
                )
                # executescript implicitly commits, so the transaction lives inside the script
                # itself: the DDL and its bookkeeping row commit together or not at all.
                script = f"BEGIN IMMEDIATE;\n{migration.sql}\n{record}\nCOMMIT;"
                if migration.requires_foreign_keys_off:
                    connection.execute("PRAGMA foreign_keys = OFF")
                try:
                    connection.executescript(script)
                except Exception:
                    try:
                        connection.executescript("ROLLBACK;")
                    except sqlite3.Error:
                        pass
                    raise
                finally:
                    if migration.requires_foreign_keys_off:
                        connection.execute("PRAGMA foreign_keys = ON")
                if migration.requires_foreign_keys_off:
                    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                    if violations:
                        raise SchemaError(
                            f"migration {migration.version} left {len(violations)} "
                            "foreign-key violations"
                        )
                applied.append(migration.version)
        return applied

    def require_current(self) -> None:
        version = self.schema_version()
        if version != SCHEMA_VERSION:
            raise SchemaError(
                f"database schema version {version} does not match required {SCHEMA_VERSION}; "
                "run 'aki migrate'"
            )

    def integrity_check(self) -> dict[str, Any]:
        with self.connect() as connection:
            quick = connection.execute("PRAGMA quick_check").fetchone()[0]
            fk = connection.execute("PRAGMA foreign_key_check").fetchall()
        return {
            "quick_check": quick,
            "foreign_key_violations": len(fk),
            "schema_version": self.schema_version(),
        }

    def coverage(self) -> dict[str, int]:
        self.require_current()
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM v_coverage").fetchone()
        return {key: int(row[key]) for key in row.keys()}

    def backup(self, destination: str | Path) -> dict[str, Any]:
        """Consistent online backup, verified by reopening and integrity-checking it."""
        target = Path(destination).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target == self.path:
            raise ValueError("backup destination must differ from the live database")
        with self.connect() as source, sqlite3.connect(str(target)) as sink:
            source.backup(sink)
        restored = Database(target)
        check = restored.integrity_check()
        if check["quick_check"] != "ok" or check["foreign_key_violations"]:
            raise SchemaError(f"backup failed verification: {check}")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        return {
            "path": str(target),
            "sha256": digest,
            "byte_size": target.stat().st_size,
            "integrity": check,
        }

    def restore(self, source: str | Path, *, confirm: bool = False) -> dict[str, Any]:
        """Replace the live database from a verified backup file."""
        if not confirm:
            raise ValueError("restore requires confirm=True")
        candidate = Path(source).expanduser().resolve()
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        check = Database(candidate).integrity_check()
        if check["quick_check"] != "ok" or check["foreign_key_violations"]:
            raise SchemaError(f"refusing to restore a corrupt backup: {check}")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safety = self.path.with_suffix(self.path.suffix + f".pre-restore-{stamp}")
        if self.path.exists():
            for suffix in ("", "-wal", "-shm"):
                sidecar = Path(str(self.path) + suffix)
                if sidecar.exists():
                    shutil.copy2(sidecar, str(safety) + suffix)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            sidecar.unlink(missing_ok=True)
        shutil.copy2(candidate, self.path)
        return {
            "restored_from": str(candidate),
            "safety_copy": str(safety),
            "integrity": self.integrity_check(),
        }


def _literal(value: str) -> str:
    """Quote a controlled constant for inclusion in a DDL script."""
    return "'" + value.replace("'", "''") + "'"


def migration_checksum(migration: Migration) -> str:
    return hashlib.sha256(f"{migration.version}:{migration.name}:{migration.sql}".encode()).hexdigest()


def _synchronous_mode() -> str:
    """Durability policy for this process.

    ``FULL`` fsyncs on every commit. ``NORMAL`` is still crash-safe under WAL (only the
    last transaction can be lost on power failure, never integrity) and is markedly faster
    for bulk derived work. Default stays FULL; a batch worker opts in explicitly.
    """
    import os

    requested = os.environ.get("AKI_SQLITE_SYNCHRONOUS", "FULL").upper()
    return "NORMAL" if requested == "NORMAL" else "FULL"


def initialize(path: str | Path) -> dict[str, Any]:
    """Create or upgrade a database and return a report of what happened."""
    database = Database(path)
    database.path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(database.path), timeout=60, isolation_level=None) as setup:
        setup.execute("PRAGMA journal_mode = WAL")
    before = database.schema_version()
    applied = database.migrate()
    return {
        "path": str(database.path),
        "version_before": before,
        "version_after": database.schema_version(),
        "applied": applied,
        "integrity": database.integrity_check(),
    }


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
