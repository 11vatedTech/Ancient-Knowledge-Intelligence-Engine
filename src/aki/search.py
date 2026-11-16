"""Lexical retrieval, rank fusion and retrieval evaluation.

Semantic and visual retrieval are deliberately absent: no embedding or vision model is
installed in this build, and this module must not pretend otherwise. ``hybrid_search``
fuses whatever legs are actually available via reciprocal-rank fusion, so adding a dense
leg later is additive rather than a rewrite.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

from .db import Database, new_id, utcnow

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


class SearchError(ValueError):
    """The query could not be interpreted."""


@dataclass(frozen=True, slots=True)
class SearchFilters:
    work_id: str | None = None
    edition_id: str | None = None
    language: str | None = None
    script: str | None = None
    evidence_class: str | None = None
    work_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: str
    evidence_id: str
    score: float
    snippet: str
    page_id: str
    page_index: int
    page_label: str | None
    edition_id: str
    edition_title: str
    work_id: str
    work_title: str
    work_class: str
    language: str | None
    rights_state: str | None
    image_key: str
    legs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_match_query(text: str, *, mode: str = "any") -> str:
    """Build a safe FTS5 MATCH expression from untrusted user input.

    Every token is quoted so FTS5 operators in user text cannot alter query structure.
    """
    tokens = [token for token in _TOKEN.findall(text) if len(token) > 1]
    if not tokens:
        raise SearchError("query contains no searchable tokens")
    if mode == "phrase":
        return '"' + " ".join(tokens) + '"'
    joiner = " OR " if mode == "any" else " AND "
    return joiner.join(f'"{token}"' for token in tokens)


_FILTER_SQL = {
    "work_id": "w.id = ?",
    "edition_id": "e.id = ?",
    "language": "c.language_code = ?",
    "script": "c.script_code = ?",
    "evidence_class": "ev.evidence_class = ?",
    "work_class": "w.work_class = ?",
}

_SEARCH_SELECT = """
SELECT c.id AS chunk_id, c.evidence_id, c.page_id, c.edition_id, c.work_id,
       c.text AS chunk_text, fts.rank AS score, fts.snippet AS snippet,
       p.page_index, p.printed_page_label, p.page_image_key,
       e.edition_title, e.language AS edition_language, w.canonical_title, w.work_class,
       (SELECT rd.state FROM rights_decisions rd WHERE rd.edition_id = e.id
        ORDER BY rd.created_at DESC LIMIT 1) AS rights_state
FROM chunk_records c
JOIN fts ON fts.fts_chunk_id = c.id
JOIN pages p ON p.id = c.page_id
JOIN editions e ON e.id = c.edition_id
JOIN works w ON w.id = c.work_id
"""


def _filters(filters: SearchFilters | None) -> tuple[str, list[str]]:
    if filters is None:
        return "", []
    clauses: list[str] = []
    parameters: list[str] = []
    for key, value in filters.to_dict().items():
        if value is None:
            continue
        clauses.append(_FILTER_SQL[key])
        parameters.append(value)
    return (" AND " + " AND ".join(clauses)) if clauses else "", parameters


def _search_sql(where: str) -> str:
    return (
        "WITH fts AS ("
        " SELECT chunk_id AS fts_chunk_id, bm25(chunks_fts) AS rank,"
        " snippet(chunks_fts, 1, '[', ']', ' ... ', 14) AS snippet"
        " FROM chunks_fts WHERE chunks_fts MATCH ?"
        ") "
        + _SEARCH_SELECT
        + (" WHERE " + where.lstrip(" AND ") if where else "")
        + " ORDER BY fts.rank ASC LIMIT ?"
    )


def search(
    database: Database,
    query: str,
    *,
    filters: SearchFilters | None = None,
    limit: int = 20,
    mode: str = "any",
) -> list[SearchHit]:
    """BM25-ranked full-text search restricted to the requested metadata filters."""
    database.require_current()
    match = build_match_query(query, mode=mode)
    where, parameters = _filters(filters)
    sql = _search_sql(where)
    with database.connect() as connection:
        rows = connection.execute(sql, [match, *parameters, limit]).fetchall()
    hits: list[SearchHit] = []
    for row in rows:
        hits.append(SearchHit(
            chunk_id=row["chunk_id"],
            evidence_id=row["evidence_id"],
            score=float(row["score"]),
            snippet=row["snippet"],
            page_id=row["page_id"],
            page_index=int(row["page_index"]),
            page_label=row["printed_page_label"],
            edition_id=row["edition_id"],
            edition_title=row["edition_title"],
            work_id=row["work_id"],
            work_title=row["canonical_title"],
            work_class=row["work_class"],
            language=row["edition_language"],
            rights_state=row["rights_state"],
            image_key=row["page_image_key"],
            legs=("lexical",),
        ))
    return hits


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[str]], *, k: int = 60
) -> list[tuple[str, float]]:
    """Fuse ranked id lists. RRF score = sum(1 / (k + rank)) across legs."""
    if k < 1:
        raise ValueError("k must be >= 1")
    scores: dict[str, float] = {}
    for ids in rankings.values():
        for position, identifier in enumerate(ids, start=1):
            scores[identifier] = scores.get(identifier, 0.0) + 1.0 / (k + position)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def hybrid_search(
    database: Database,
    query: str,
    *,
    filters: SearchFilters | None = None,
    limit: int = 20,
    semantic_scores: Mapping[str, float] | None = None,
    k: int = 60,
) -> list[SearchHit]:
    """Fuse lexical results with an optional dense leg.

    Without an installed embedding model the dense leg is absent from the result
    ``legs`` metadata rather than silently simulated.
    """
    lexical = search(database, query, filters=filters, limit=max(limit * 3, 30))
    if not lexical:
        return []
    by_id = {hit.chunk_id: hit for hit in lexical}
    rankings: dict[str, list[str]] = {"lexical": [hit.chunk_id for hit in lexical]}
    if semantic_scores:
        dense = sorted(
            (chunk_id for chunk_id in semantic_scores if chunk_id in by_id),
            key=lambda chunk_id: (-semantic_scores[chunk_id], chunk_id),
        )
        if dense:
            rankings["dense"] = dense
    fused = reciprocal_rank_fusion(rankings, k=k)
    results: list[SearchHit] = []
    for chunk_id, score in fused[:limit]:
        hit = by_id[chunk_id]
        legs = tuple(leg for leg, ids in rankings.items() if chunk_id in ids)
        results.append(SearchHit(**{**hit.to_dict(), "score": score, "legs": legs}))
    return results


def index_chunk(
    database: Database,
    *,
    evidence_id: str,
    page_id: str,
    edition_id: str,
    work_id: str,
    layer: str,
    text: str,
    pipeline_version: str,
    language_code: str | None = None,
    script_code: str | None = None,
) -> str:
    """Register a versioned retrieval chunk for an existing evidence record."""
    if not text.strip():
        raise ValueError("cannot index empty text")
    chunk_id = new_id()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO chunk_records (id, evidence_id, page_id, edition_id, work_id,"
            " layer, text, language_code, script_code, pipeline_version, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (chunk_id, evidence_id, page_id, edition_id, work_id, layer, text,
             language_code, script_code, pipeline_version, utcnow()),
        )
        connection.execute(
            "INSERT INTO chunks_fts (chunk_id, text) VALUES (?,?)", (chunk_id, text)
        )
    return chunk_id


def rebuild_index(database: Database) -> dict[str, int]:
    """Rebuild the derived FTS index from canonical chunk records."""
    with database.transaction() as connection:
        connection.execute("DELETE FROM chunks_fts")
        rows = connection.execute("SELECT id, text FROM chunk_records").fetchall()
        connection.executemany(
            "INSERT INTO chunks_fts (chunk_id, text) VALUES (?,?)",
            [(row["id"], row["text"]) for row in rows],
        )
    return {"chunks_indexed": len(rows)}


def recall_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0
    return len(set(retrieved[:k]) & relevant_set) / len(relevant_set)


def ndcg_at_k(retrieved: Sequence[str], relevance: Mapping[str, float], k: int) -> float:
    def dcg(values: Sequence[float]) -> float:
        return sum(value / math.log2(position + 2) for position, value in enumerate(values))

    gains = [float(relevance.get(identifier, 0.0)) for identifier in retrieved[:k]]
    ideal = dcg(sorted((float(value) for value in relevance.values()), reverse=True)[:k])
    if ideal == 0.0:
        return 0.0
    return dcg(gains) / ideal


def evaluate_retrieval(
    database: Database,
    gold: Sequence[Mapping[str, Any]],
    *,
    k: int = 20,
) -> dict[str, Any]:
    """Score retrieval against a curated gold set.

    Each entry requires ``query`` and ``relevant`` (chunk ids); optional ``weights``
    maps chunk ids to graded relevance for nDCG.
    """
    if not gold:
        raise SearchError("gold set is empty")
    recalls: list[float] = []
    ndcgs: list[float] = []
    failures: list[dict[str, Any]] = []
    for case in gold:
        hits = search(database, str(case["query"]), limit=max(k, 20))
        retrieved = [hit.chunk_id for hit in hits]
        relevant = case["relevant"]
        recall = recall_at_k(retrieved, relevant, k)
        ndcg = ndcg_at_k(retrieved, case.get("weights", {i: 1.0 for i in relevant}), 10)
        recalls.append(recall)
        ndcgs.append(ndcg)
        if recall < 1.0:
            failures.append({
                "query": case["query"],
                f"recall@{k}": round(recall, 4),
                "missing": sorted(set(relevant) - set(retrieved)),
            })
    return {
        "cases": len(gold),
        f"recall@{k}": sum(recalls) / len(recalls),
        "ndcg@10": sum(ndcgs) / len(ndcgs),
        "failures": failures,
    }
