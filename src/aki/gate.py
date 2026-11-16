"""Edition-level rights checks for derived processing and the reading room.

Ingestion already gates each edition on its latest rights decision. Everything built on
top of ingested text (meaning-based search, the concordance, plate detection, the local
reading room) re-checks the same decision for its own operation, so narrowing a grant
later takes effect without rebuilding anything.
"""
from __future__ import annotations

from .db import Database


def permitted_editions(database: Database, operation: str) -> set[str]:
    """Edition ids whose most recent rights decision permits ``operation``."""
    from .ingest import _decision_from_row
    from .production_domain import RightsOperation

    wanted = RightsOperation(operation)
    allowed: set[str] = set()
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT rd.* FROM rights_decisions rd"
            " JOIN (SELECT edition_id, MAX(created_at) AS latest FROM rights_decisions"
            "       WHERE edition_id IS NOT NULL GROUP BY edition_id) last"
            "   ON last.edition_id = rd.edition_id AND last.latest = rd.created_at"
        ).fetchall()
    for row in rows:
        try:
            decision = _decision_from_row(row)
        except (ValueError, KeyError):
            continue
        if decision.permits(wanted):
            allowed.add(str(row["edition_id"]))
    return allowed
