"""Concordance: which pages name which figures, places, books, traditions and ideas.

This is lexical mention detection against a curated term list
(``concordance_terms.TERMS``), not interpretation. A mention means "this word appears on
this page", recorded as a ``mentions`` row pointing at the page's evidence passage, so
every index entry resolves to a citable page. Entities are stored as ``candidate``:
nobody has reviewed whether every "Hiram" is Hiram Abiff.

"Appears with" is computed from shared pages at read time. It is co-occurrence, which
says nothing about how two names relate, and the reading room labels it that way.
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from typing import Any

from .concordance_terms import TERMS, TYPE_LABELS
from .db import Database, json_dumps, new_id, utcnow
from .gate import permitted_editions

BUILDER = "aki-concordance-v1"
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def _matcher() -> dict[str, list[tuple[tuple[str, ...], int, str]]]:
    """First token → candidate alias token sequences, longest first."""
    table: dict[str, list[tuple[tuple[str, ...], int, str]]] = defaultdict(list)
    for position, (_, _, aliases) in enumerate(TERMS):
        for alias in aliases:
            parts = tuple(_tokens(alias))
            if parts:
                table[parts[0]].append((parts, position, alias))
    for candidates in table.values():
        candidates.sort(key=lambda item: -len(item[0]))
    return table


def find_mentions(text: str, table: dict[str, list[tuple[tuple[str, ...], int, str]]] | None = None
                  ) -> dict[int, tuple[str, int]]:
    """Term index → (first alias seen, number of occurrences) for one page of text."""
    table = table if table is not None else _matcher()
    tokens = _tokens(text)
    found: dict[int, tuple[str, int]] = {}
    i = 0
    while i < len(tokens):
        candidates = table.get(tokens[i])
        matched = 0
        if candidates:
            for parts, position, alias in candidates:
                width = len(parts)
                if tuple(tokens[i:i + width]) == parts:
                    first, count = found.get(position, (alias, 0))
                    found[position] = (first, count + 1)
                    matched = width
                    break
        i += matched or 1
    return found


def build(database: Database) -> dict[str, Any]:
    """Rebuild every concordance entity and mention from the current transcriptions."""
    database.require_current()
    clock = time.time()
    started = utcnow()
    allowed = permitted_editions(database, "internal_research")
    table = _matcher()
    with database.transaction() as connection:
        connection.execute("DELETE FROM entities WHERE description = ?", (BUILDER,))
        entity_ids: list[str] = []
        for name, kind, aliases in TERMS:
            entity_id = new_id()
            entity_ids.append(entity_id)
            connection.execute(
                "INSERT INTO entities (id, entity_type, canonical_name, description,"
                " canonical_status, created_at) VALUES (?,?,?,?,?,?)",
                (entity_id, kind, name, BUILDER, "candidate", utcnow()),
            )
            for alias in dict.fromkeys(aliases):
                connection.execute(
                    "INSERT OR IGNORE INTO entity_aliases (id, entity_id, alias, alias_type)"
                    " VALUES (?,?,?,?)", (new_id(), entity_id, alias, "curated_term"),
                )
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT c.evidence_id, c.edition_id, c.text FROM chunk_records c"
        ).fetchall()
    pending: list[tuple[Any, ...]] = []
    pages = mentions = 0
    for row in rows:
        if row["edition_id"] not in allowed:
            continue
        pages += 1
        for position, (alias, count) in find_mentions(str(row["text"]), table).items():
            pending.append((new_id(), entity_ids[position], row["evidence_id"],
                            alias if count == 1 else f"{alias} ({count}×)", "text", 1.0,
                            "candidate"))
        if len(pending) >= 20_000:
            mentions += _flush(database, pending)
    mentions += _flush(database, pending)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO model_runs (id, task_type, provider, model_name, model_digest,"
            " runtime_version, parameters, started_at, completed_at, outcome, metrics)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (new_id(), "concordance", "aki", BUILDER, f"terms:{len(TERMS)}", "n/a",
             json_dumps({"matching": "case-sensitive whole tokens"}), started, utcnow(),
             "succeeded", json_dumps({"pages": pages, "mentions": mentions})),
        )
    return {"terms": len(TERMS), "pages_scanned": pages, "mentions": mentions,
            "seconds": round(time.time() - clock, 1)}


def _flush(database: Database, pending: list[tuple[Any, ...]]) -> int:
    if not pending:
        return 0
    with database.transaction() as connection:
        connection.executemany(
            "INSERT INTO mentions (id, entity_id, evidence_id, mention_text, mention_kind,"
            " confidence, resolution_state) VALUES (?,?,?,?,?,?,?)", pending,
        )
    count = len(pending)
    pending.clear()
    return count


def overview(database: Database) -> list[dict[str, Any]]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT en.id, en.entity_type, en.canonical_name,"
            " COUNT(m.id) AS pages,"
            " COUNT(DISTINCT ev.edition_id) AS books"
            " FROM entities en LEFT JOIN mentions m ON m.entity_id = en.id"
            " LEFT JOIN evidence ev ON ev.id = m.evidence_id"
            " WHERE en.description = ? GROUP BY en.id"
            " ORDER BY CASE en.entity_type WHEN 'divine' THEN 0 WHEN 'person' THEN 1"
            " WHEN 'place' THEN 2 WHEN 'text' THEN 3 WHEN 'tradition' THEN 4 ELSE 5 END,"
            " pages DESC, en.canonical_name", (BUILDER,),
        ).fetchall()
    return [{**dict(row), "type_label": TYPE_LABELS.get(row["entity_type"], row["entity_type"])}
            for row in rows]


def entity_detail(database: Database, entity_id: str, *, limit: int = 60, offset: int = 0,
                  editions: set[str] | None = None) -> dict[str, Any] | None:
    """One index entry. With ``editions``, every count and passage is limited to them."""
    scope = ""
    scoped: tuple[str, ...] = ()
    if editions is not None:
        scoped = tuple(sorted(editions)) or ("",)
        scope = f" AND ev.edition_id IN ({','.join('?' * len(scoped))})"
    with database.connect() as connection:
        entity = connection.execute(
            "SELECT id, entity_type, canonical_name FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        if entity is None:
            return None
        aliases = [row[0] for row in connection.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id = ? ORDER BY alias", (entity_id,))]
        books = connection.execute(
            "SELECT ev.edition_id, e.edition_title, COUNT(*) AS pages FROM mentions m"
            " JOIN evidence ev ON ev.id = m.evidence_id JOIN editions e ON e.id = ev.edition_id"
            f" WHERE m.entity_id = ?{scope} GROUP BY ev.edition_id ORDER BY pages DESC",
            (entity_id, *scoped),
        ).fetchall()
        passages = connection.execute(
            "SELECT m.mention_text, ev.id AS evidence_id, ev.page_id, ev.edition_id,"
            " e.edition_title, p.page_index, p.printed_page_label, ev.quoted_text"
            " FROM mentions m JOIN evidence ev ON ev.id = m.evidence_id"
            " JOIN editions e ON e.id = ev.edition_id JOIN pages p ON p.id = ev.page_id"
            f" WHERE m.entity_id = ?{scope}"
            " ORDER BY e.edition_title, p.page_index LIMIT ? OFFSET ?",
            (entity_id, *scoped, limit, offset),
        ).fetchall()
        related = connection.execute(
            "SELECT en.id, en.canonical_name, en.entity_type, COUNT(*) AS shared"
            " FROM mentions a JOIN mentions b ON a.evidence_id = b.evidence_id"
            " JOIN entities en ON en.id = b.entity_id"
            " JOIN evidence ev ON ev.id = a.evidence_id"
            f" WHERE a.entity_id = ? AND b.entity_id != ?{scope}"
            " GROUP BY b.entity_id ORDER BY shared DESC LIMIT 16",
            (entity_id, entity_id, *scoped),
        ).fetchall()
        total = connection.execute(
            "SELECT COUNT(*) FROM mentions m JOIN evidence ev ON ev.id = m.evidence_id"
            f" WHERE m.entity_id = ?{scope}", (entity_id, *scoped)
        ).fetchone()[0]
    return {
        "entity": {**dict(entity), "type_label": TYPE_LABELS.get(entity["entity_type"], "")},
        "aliases": aliases,
        "pages": int(total),
        "book_count": len(books),
        "books": [dict(row) for row in books[:40]],
        "passages": [
            {**dict(row), "excerpt": _excerpt(str(row["quoted_text"] or ""), aliases)}
            for row in passages
        ],
        "related": [dict(row) for row in related],
    }


def _excerpt(text: str, aliases: list[str], width: int = 220) -> str:
    flat = " ".join(text.split())
    best = -1
    for alias in sorted(aliases, key=len, reverse=True):
        match = re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", flat)
        if match:
            best = match.start()
            break
    if best < 0:
        return flat[:width]
    start = max(0, best - width // 2)
    snippet = flat[start:start + width]
    return ("… " if start else "") + snippet + (" …" if start + width < len(flat) else "")


def status(database: Database) -> dict[str, Any]:
    with database.connect() as connection:
        entities = connection.execute(
            "SELECT COUNT(*) FROM entities WHERE description = ?", (BUILDER,)).fetchone()[0]
        mentions = connection.execute("SELECT COUNT(*) FROM mentions").fetchone()[0]
        run = connection.execute(
            "SELECT completed_at, metrics FROM model_runs WHERE task_type='concordance'"
            " ORDER BY completed_at DESC LIMIT 1").fetchone()
    return {"entities": int(entities), "mentions": int(mentions),
            "built_at": run["completed_at"] if run else None,
            "metrics": json.loads(run["metrics"]) if run else None}
