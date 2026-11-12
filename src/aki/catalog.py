"""SQLite source catalog with immutable source identity."""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator
from .domain import ProcessingOperation, RightsGrant, RightsStatus, SourceRecord
from .rights import require_permission

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sources (
 source_id TEXT PRIMARY KEY, title TEXT NOT NULL, creator TEXT, language TEXT,
 source_uri TEXT NOT NULL, collection_name TEXT NOT NULL, edition_label TEXT NOT NULL,
 rights_status TEXT NOT NULL, permitted_operations TEXT NOT NULL, rights_evidence_uri TEXT,
 attribution_required INTEGER NOT NULL, rights_notes TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class CatalogConflict(ValueError):
    """A stable source identifier was reused with different metadata."""


class SourceCatalog:
    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
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

    @staticmethod
    def _values(source: SourceRecord) -> tuple[object, ...]:
        grant = source.rights
        return (
            source.source_id, source.title, source.creator, source.language, source.source_uri,
            source.collection, source.edition_label, grant.status.value,
            json.dumps(sorted(op.value for op in grant.permitted_operations)),
            grant.evidence_uri, int(grant.attribution_required), grant.notes,
        )

    def add(self, source: SourceRecord) -> bool:
        """Insert once; exact duplicate returns False; conflicting identity raises."""
        values = self._values(source)
        with self._connect() as db:
            row = db.execute(
                "SELECT source_id,title,creator,language,source_uri,collection_name,edition_label,"
                "rights_status,permitted_operations,rights_evidence_uri,attribution_required,rights_notes "
                "FROM sources WHERE source_id=?", (source.source_id,)
            ).fetchone()
            if row is not None:
                if tuple(row) != values:
                    raise CatalogConflict(f"source_id '{source.source_id}' already exists with different metadata")
                return False
            db.execute(
                "INSERT INTO sources (source_id,title,creator,language,source_uri,collection_name,"
                "edition_label,rights_status,permitted_operations,rights_evidence_uri,"
                "attribution_required,rights_notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values
            )
        return True

    def get(self, source_id: str) -> SourceRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        if row is None:
            return None
        ops = frozenset(ProcessingOperation(x) for x in json.loads(row["permitted_operations"]))
        return SourceRecord(
            source_id=row["source_id"], title=row["title"], creator=row["creator"],
            language=row["language"], source_uri=row["source_uri"], collection=row["collection_name"],
            edition_label=row["edition_label"],
            rights=RightsGrant(
                status=RightsStatus(row["rights_status"]), permitted_operations=ops,
                evidence_uri=row["rights_evidence_uri"],
                attribution_required=bool(row["attribution_required"]), notes=row["rights_notes"],
            ),
        )

    def list_sources(self) -> list[SourceRecord]:
        with self._connect() as db:
            ids = [r[0] for r in db.execute("SELECT source_id FROM sources ORDER BY title")]
        return [record for key in ids if (record := self.get(key)) is not None]

    def authorize(self, source_id: str, operation: ProcessingOperation) -> SourceRecord:
        source = self.get(source_id)
        if source is None:
            raise KeyError(f"Unknown source: {source_id}")
        require_permission(source.rights, operation)
        return source

    def import_records(self, records: Iterable[SourceRecord]) -> tuple[int, int]:
        added = duplicates = 0
        for record in records:
            if self.add(record):
                added += 1
            else:
                duplicates += 1
        return added, duplicates
