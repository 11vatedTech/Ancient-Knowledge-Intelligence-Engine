"""Grounded research workflow: plan, retrieve, claim, verify, report.

This implementation is deliberately deterministic. It performs no generative synthesis:
it assembles *source-explicit excerpts* with resolvable page citations and states plainly
what it did not do. That is a real capability, not a placeholder for one — a later
model-backed synthesis leg must consume the same evidence packet and pass the same
independent verifier.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .db import Database, new_id, utcnow
from .evidence import (
    VerificationReport,
    add_evidence,
    create_claim,
    link_claim,
    resolve_citation,
    verify_claims,
)
from .search import SearchFilters, SearchHit, search

MIN_QUOTE_CHARS = 24


class ResearchError(RuntimeError):
    """A research case could not be executed."""


@dataclass(frozen=True, slots=True)
class ResearchPlan:
    question: str
    terms: tuple[str, ...]
    subquestions: tuple[str, ...]
    filters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ResearchResult:
    case_id: str
    plan: ResearchPlan
    hits: tuple[SearchHit, ...]
    claim_ids: tuple[str, ...]
    verification: VerificationReport
    dossier: str
    dossier_path: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "plan": self.plan.to_dict(),
            "hit_count": len(self.hits),
            "claim_count": len(self.claim_ids),
            "verification": self.verification.to_dict(),
            "dossier_path": self.dossier_path,
        }


_SPLIT = re.compile(r"[.;?]+\s+|\band\b|\bversus\b|\bvs\.?\b", re.IGNORECASE)


def plan_research(question: str, *, filters: SearchFilters | None = None) -> ResearchPlan:
    """Decompose a question into search terms and investigable sub-questions."""
    stripped = question.strip()
    if len(stripped) < 8:
        raise ResearchError("question is too short to plan an investigation")
    clauses = [clause.strip() for clause in _SPLIT.split(stripped) if clause and len(clause.strip()) > 3]
    terms = tuple(
        dict.fromkeys(
            token.lower()
            for token in re.findall(r"[^\W_]+", stripped, re.UNICODE)
            if len(token) > 2
        )
    )
    subquestions = tuple(clauses) if len(clauses) > 1 else (
        f"What do the catalogued sources state about: {stripped}?",
    )
    return ResearchPlan(
        question=stripped,
        terms=terms,
        subquestions=subquestions,
        filters=(filters.to_dict() if filters else {}),
    )


def _quote_from_hit(database: Database, hit: SearchHit) -> str | None:
    try:
        citation = resolve_citation(database, hit.evidence_id)
    except Exception:
        return None
    return citation.quoted_text


def run_research(
    database: Database,
    question: str,
    *,
    limit: int = 12,
    filters: SearchFilters | None = None,
    output_dir: str | Path | None = None,
) -> ResearchResult:
    database.require_current()
    plan = plan_research(question, filters=filters)
    hits = search(database, question, filters=filters, limit=limit)
    case_id = new_id()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO research_cases (id, question, scope, corpus_snapshot, state, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                case_id, plan.question,
                json.dumps({"filters": plan.filters, "limit": limit}, sort_keys=True),
                json.dumps(database.coverage(), sort_keys=True),
                "retrieving", utcnow(),
            ),
        )

    claim_ids: list[str] = []
    for hit in hits:
        quote = _quote_from_hit(database, hit)
        if not quote or len(quote.strip()) < MIN_QUOTE_CHARS:
            continue
        evidence_id = add_evidence(
            database,
            edition_id=hit.edition_id,
            page_id=hit.page_id,
            evidence_kind="passage",
            evidence_class="source_explicit",
            quoted_text=quote.strip(),
            locator={
                "page_index": hit.page_index,
                "page_label": hit.page_label,
                "retrieval_chunk": hit.chunk_id,
            },
        )
        label = hit.page_label or str(hit.page_index)
        statement = (
            f"The catalogued source {hit.work_title!r} ({hit.edition_title!r}), "
            f"page {label}, contains this passage: \u201c{quote.strip()}\u201d"
        )
        claim_id = create_claim(
            database,
            statement=statement,
            claim_type="source_explicit",
            research_case_id=case_id,
            created_by_run="deterministic-research-v1",
        )
        link_claim(database, claim_id, evidence_id, "source_of_claim")
        claim_ids.append(claim_id)

    verification = verify_claims(database, claim_ids) if claim_ids else VerificationReport(())
    dossier = render_dossier(plan, hits, claim_ids, verification, database.coverage())
    path: str | None = None
    if output_dir is not None:
        directory = Path(output_dir).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{case_id}.md"
        target.write_text(dossier, encoding="utf-8")
        path = str(target)

    with database.transaction() as connection:
        connection.execute(
            "UPDATE research_cases SET state=?, dossier=?, completed_at=? WHERE id=?",
            (
                "complete" if verification.ok else "failed",
                dossier, utcnow(), case_id,
            ),
        )
    return ResearchResult(case_id, plan, tuple(hits), tuple(claim_ids), verification, dossier, path)


def render_dossier(
    plan: ResearchPlan,
    hits: Sequence[SearchHit],
    claim_ids: Sequence[str],
    verification: VerificationReport,
    coverage: dict[str, int],
) -> str:
    lines: list[str] = []
    lines.append(f"# Research dossier — {plan.question}")
    lines.append("")
    lines.append("## Status")
    lines.append("")
    lines.append(
        "This dossier was produced by a **deterministic evidence-assembly** pass. "
        "It quotes catalogued source text and resolves each quotation to a page. "
        "It performs **no interpretive synthesis**: no historical claim, translation, "
        "or comparison in this document is model-generated."
    )
    lines.append("")
    lines.append(f"- Claims: {len(claim_ids)}")
    lines.append(f"- Verified: {verification.ok}")
    lines.append(f"- Support: {sum(1 for a in verification.claims if a.status == 'supported')}")
    lines.append(f"- Contested: {sum(1 for a in verification.claims if a.status == 'contested')}")
    lines.append(f"- Unverified: {sum(1 for a in verification.claims if a.status == 'unverified')}")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append(f"- Search terms: {', '.join(plan.terms) or '(none)'}")
    lines.append("- Retrieval: BM25 lexical full-text search over indexed page passages.")
    if plan.filters:
        lines.append(f"- Filters: `{json.dumps(plan.filters, sort_keys=True)}`")
    lines.append("- Verification: independent citation-resolution pass over every claim.")
    lines.append("")
    lines.append("## Corpus coverage at run time")
    lines.append("")
    for key in ("works", "editions", "assets", "pages", "transcriptions", "evidence"):
        lines.append(f"- {key.replace('_', ' ')}: {coverage.get(key, 0)}")
    lines.append("")
    lines.append("## Investigable sub-questions")
    lines.append("")
    for subquestion in plan.subquestions:
        lines.append(f"1. {subquestion}")
    lines.append("")

    if not claim_ids:
        lines.append("## Findings")
        lines.append("")
        lines.append(
            "No citable passage was found for this question in the current corpus. "
            "This is a coverage gap, not a negative historical finding."
        )
        lines.append("")
    else:
        lines.append("## Findings (source-explicit excerpts)")
        lines.append("")
        for assessment in verification.claims:
            lines.append(f"### {assessment.status.upper()} — {assessment.claim_type}")
            lines.append("")
            lines.append(assessment.statement)
            lines.append("")
            for citation in assessment.citations:
                polygon = (
                    f", region `{json.dumps(citation.polygon)}`" if citation.polygon else ""
                )
                lines.append(
                    f"- Cite: `{citation.evidence_id}` → {citation.short()} "
                    f"(page index {citation.page_index}, image `{citation.page_image_key[:16]}…`, "
                    f"source sha256 `{(citation.source_sha256 or '')[:16]}…`{polygon})"
                )
                lines.append(
                    f"  - Evidence class: `{citation.evidence_class}`; "
                    f"rights: `{citation.rights_state}`; "
                    f"transcription: {citation.transcription_engine or 'n/a'} "
                    f"{citation.transcription_version or ''}".rstrip()
                )
            lines.append("")

    lines.append("## Contradictions and alternative readings")
    lines.append("")
    contradicted = [a for a in verification.claims if a.status == "contested"]
    if contradicted:
        for assessment in contradicted:
            lines.append(f"- Contested: {assessment.statement}")
    else:
        lines.append(
            "None identified in this pass. Absence of detected contradiction is not evidence "
            "of agreement: the corpus is small and the check is limited to linked evidence."
        )
    lines.append("")
    lines.append("## Open questions and limitations")
    lines.append("")
    for warning in verification.warnings:
        lines.append(f"- {warning}")
    lines.append("- Semantic/visual retrieval is unavailable: no embedding or vision model is installed.")
    lines.append("- Passages are machine-transcribed where OCR was applied; verify against the page image.")
    lines.append("- Similarity between sources is not evidence of contact, influence, or transmission.")
    lines.append("")
    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- Case: `{plan.question[:0]}`deterministic-research-v1")
    lines.append("- Pipeline version: `deterministic-research-v1`")
    lines.append("- Every citation above is an `evidence` row resolvable through `aki cite <id>`.")
    lines.append("")
    return "\n".join(lines)


def list_cases(database: Database, *, limit: int = 50) -> list[dict[str, Any]]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id, question, state, created_at, completed_at FROM research_cases"
            " ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_case(database: Database, case_id: str) -> dict[str, Any] | None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM research_cases WHERE id=?", (case_id,)
        ).fetchone()
        if row is None:
            return None
        claims = connection.execute(
            "SELECT id, claim_text, claim_type, status FROM claims WHERE research_case_id=?"
            " ORDER BY created_at",
            (case_id,),
        ).fetchall()
    payload = dict(row)
    payload["claims"] = [dict(claim) for claim in claims]
    return payload


def assess_case(database: Database, case_id: str) -> dict[str, Any]:
    """Re-verify every claim in a case against current evidence."""
    with database.connect() as connection:
        ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM claims WHERE research_case_id=?", (case_id,)
            )
        ]
    if not ids:
        raise ResearchError(f"case {case_id} has no claims")
    report = verify_claims(database, ids)
    return report.to_dict()


def _unused(*_: Any) -> None:  # pragma: no cover - keeps assess_claim imported for API parity
    return None
