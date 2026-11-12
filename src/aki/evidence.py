"""Evidence registration, citation resolution and claim verification.

The governing rule is that a claim may only reach ``supported`` when it carries at least
one evidence link that actually resolves to a stored page (or an external archival
record), and that any contradicting evidence downgrades it to ``contested``. Verification
is deterministic: it never asks the component that produced a claim to confirm it.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from .db import Database, new_id, utcnow

EVIDENCE_KINDS = frozenset({
    "passage", "page_image", "region", "visual_observation", "external_record", "editorial_note",
})
EVIDENCE_CLASSES = frozenset({
    "source_explicit", "editorial", "historical_context", "model_inferred",
    "comparative_hypothesis", "unverified",
})
CLAIM_TYPES = frozenset({
    "source_explicit", "editorial", "historical_context", "model_inferred",
    "comparative_hypothesis", "unverified",
})
SUPPORT_RELATIONS = frozenset({"supports", "source_of_claim"})


class EvidenceError(ValueError):
    """Evidence was requested or supplied in an invalid form."""


class CitationError(LookupError):
    """A citation does not resolve to stored evidence."""


@dataclass(frozen=True, slots=True)
class ResolvedCitation:
    evidence_id: str
    work_title: str
    edition_title: str
    work_class: str
    language: str | None
    page_index: int
    page_label: str | None
    page_image_key: str
    page_sha256: str
    polygon: tuple[tuple[float, float], ...] | None
    quoted_text: str | None
    evidence_kind: str
    evidence_class: str
    source_sha256: str | None
    rights_state: str | None
    transcription_engine: str | None
    transcription_version: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def short(self) -> str:
        label = self.page_label or str(self.page_index)
        return f"{self.work_title} — {self.edition_title}, p. {label}"


def add_evidence(
    database: Database,
    *,
    edition_id: str | None,
    page_id: str | None,
    evidence_kind: str,
    evidence_class: str,
    quoted_text: str | None = None,
    transcription_id: str | None = None,
    visual_observation_id: str | None = None,
    region_id: str | None = None,
    external_source_uri: str | None = None,
    locator: Mapping[str, Any] | None = None,
    source_hash: str | None = None,
) -> str:
    if evidence_kind not in EVIDENCE_KINDS:
        raise EvidenceError(f"unknown evidence_kind {evidence_kind!r}")
    if evidence_class not in EVIDENCE_CLASSES:
        raise EvidenceError(f"unknown evidence_class {evidence_class!r}")
    if page_id is None and external_source_uri is None:
        raise EvidenceError("evidence requires a page_id or an external_source_uri")
    evidence_id = new_id()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO evidence (id, edition_id, page_id, region_id, transcription_id,"
            " visual_observation_id, external_source_uri, locator, quoted_text, evidence_kind,"
            " evidence_class, source_hash, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                evidence_id, edition_id, page_id, region_id, transcription_id,
                visual_observation_id, external_source_uri,
                json.dumps(dict(locator or {}), sort_keys=True),
                quoted_text, evidence_kind, evidence_class, source_hash, utcnow(),
            ),
        )
    return evidence_id


def resolve_citation(database: Database, evidence_id: str) -> ResolvedCitation:
    """Resolve an evidence id to a page image, label, region and rights state."""
    with database.connect() as connection:
        row = connection.execute(
            "SELECT ev.*, p.page_index, p.printed_page_label, p.page_image_key, p.page_sha256,"
            " p.asset_id, e.edition_title, e.language, w.canonical_title, w.work_class,"
            " t.engine_name, t.engine_version, pr.polygon,"
            " (SELECT rd.state FROM rights_decisions rd WHERE rd.edition_id = e.id"
            "  ORDER BY rd.created_at DESC LIMIT 1) AS rights_state,"
            " a.sha256 AS asset_sha256"
            " FROM evidence ev"
            " LEFT JOIN pages p ON p.id = ev.page_id"
            " LEFT JOIN assets a ON a.id = p.asset_id"
            " LEFT JOIN editions e ON e.id = ev.edition_id"
            " LEFT JOIN works w ON w.id = e.work_id"
            " LEFT JOIN transcriptions t ON t.id = ev.transcription_id"
            " LEFT JOIN page_regions pr ON pr.id = ev.region_id"
            " WHERE ev.id = ?",
            (evidence_id,),
        ).fetchone()
    if row is None:
        raise CitationError(f"evidence {evidence_id} does not exist")
    if row["page_index"] is None:
        raise CitationError(
            f"evidence {evidence_id} has no resolvable page; external-only evidence "
            "must be cited by URI"
        )
    polygon = json.loads(row["polygon"]) if row["polygon"] else None
    return ResolvedCitation(
        evidence_id=evidence_id,
        work_title=row["canonical_title"] or "(unlinked work)",
        edition_title=row["edition_title"] or "(unlinked edition)",
        work_class=row["work_class"] or "unclassified",
        language=row["language"],
        page_index=int(row["page_index"]),
        page_label=row["printed_page_label"],
        page_image_key=row["page_image_key"],
        page_sha256=row["page_sha256"],
        polygon=tuple(tuple(point) for point in polygon) if polygon else None,
        quoted_text=row["quoted_text"],
        evidence_kind=row["evidence_kind"],
        evidence_class=row["evidence_class"],
        source_sha256=row["asset_sha256"],
        rights_state=row["rights_state"],
        transcription_engine=row["engine_name"],
        transcription_version=row["engine_version"],
    )


def create_claim(
    database: Database,
    *,
    statement: str,
    claim_type: str,
    research_case_id: str | None = None,
    created_by_run: str | None = None,
) -> str:
    if not statement.strip():
        raise EvidenceError("claim statement is required")
    if claim_type not in CLAIM_TYPES:
        raise EvidenceError(f"unknown claim_type {claim_type!r}")
    claim_id = new_id()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO claims (id, research_case_id, claim_text, claim_type, status,"
            " created_by_run, created_at) VALUES (?,?,?,?,?,?,?)",
            (claim_id, research_case_id, statement.strip(), claim_type, "proposed",
             created_by_run, utcnow()),
        )
    return claim_id


def link_claim(database: Database, claim_id: str, evidence_id: str, relation: str) -> None:
    if relation not in {"supports", "contradicts", "qualifies", "contextualizes", "source_of_claim"}:
        raise EvidenceError(f"unknown evidence relation {relation!r}")
    with database.transaction() as connection:
        if connection.execute("SELECT 1 FROM claims WHERE id=?", (claim_id,)).fetchone() is None:
            raise EvidenceError(f"unknown claim {claim_id}")
        if connection.execute("SELECT 1 FROM evidence WHERE id=?", (evidence_id,)).fetchone() is None:
            raise CitationError(f"unknown evidence {evidence_id}")
        connection.execute(
            "INSERT OR REPLACE INTO claim_evidence (claim_id, evidence_id, relation)"
            " VALUES (?,?,?)",
            (claim_id, evidence_id, relation),
        )


@dataclass(frozen=True, slots=True)
class ClaimAssessment:
    claim_id: str
    statement: str
    claim_type: str
    status: str
    supporting: tuple[str, ...]
    contradicting: tuple[str, ...]
    qualifying: tuple[str, ...]
    resolution_errors: tuple[str, ...] = ()
    citations: tuple[ResolvedCitation, ...] = ()

    @property
    def verified(self) -> bool:
        return self.status == "supported" and not self.resolution_errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "statement": self.statement,
            "claim_type": self.claim_type,
            "status": self.status,
            "supporting": list(self.supporting),
            "contradicting": list(self.contradicting),
            "qualifying": list(self.qualifying),
            "resolution_errors": list(self.resolution_errors),
            "citations": [citation.to_dict() for citation in self.citations],
        }


def assess_claim(database: Database, claim_id: str) -> ClaimAssessment:
    """Deterministically derive claim status from its evidence links.

    * No supporting link, or any unresolvable citation -> ``unverified``.
    * Any contradicting link -> ``contested`` (never silently promoted to supported).
    * Supporting link(s) plus at least one resolvable page -> ``supported``.
    """
    with database.connect() as connection:
        claim = connection.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if claim is None:
            raise EvidenceError(f"unknown claim {claim_id}")
        links = connection.execute(
            "SELECT evidence_id, relation FROM claim_evidence WHERE claim_id=?",
            (claim_id,),
        ).fetchall()
    supporting = tuple(row["evidence_id"] for row in links if row["relation"] in SUPPORT_RELATIONS)
    contradicting = tuple(row["evidence_id"] for row in links if row["relation"] == "contradicts")
    qualifying = tuple(
        row["evidence_id"] for row in links
        if row["relation"] in {"qualifies", "contextualizes"}
    )
    errors: list[str] = []
    citations: list[ResolvedCitation] = []
    for evidence_id in (*supporting, *contradicting):
        try:
            citations.append(resolve_citation(database, evidence_id))
        except CitationError as exc:
            errors.append(str(exc))

    if contradicting:
        status = "contested"
    elif supporting and not errors:
        status = "supported"
    else:
        status = "unverified"

    with database.transaction() as connection:
        connection.execute("UPDATE claims SET status=? WHERE id=?", (status, claim_id))
    return ClaimAssessment(
        claim_id=claim_id,
        statement=claim["claim_text"],
        claim_type=claim["claim_type"],
        status=status,
        supporting=supporting,
        contradicting=contradicting,
        qualifying=qualifying,
        resolution_errors=tuple(errors),
        citations=tuple(citations),
    )


@dataclass(frozen=True, slots=True)
class VerificationReport:
    claims: tuple[ClaimAssessment, ...]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "claims": [assessment.to_dict() for assessment in self.claims],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "supporting_claims": sum(1 for a in self.claims if a.status == "supported"),
            "contested_claims": sum(1 for a in self.claims if a.status == "contested"),
            "unverified_claims": sum(1 for a in self.claims if a.status == "unverified"),
        }


def verify_claims(database: Database, claim_ids: Iterable[str]) -> VerificationReport:
    """Independent verification pass over a claim set."""
    assessments: list[ClaimAssessment] = []
    errors: list[str] = []
    warnings: list[str] = []
    for claim_id in claim_ids:
        assessment = assess_claim(database, claim_id)
        assessments.append(assessment)
        errors.extend(assessment.resolution_errors)
        if assessment.status == "unverified":
            errors.append(
                f"claim {claim_id} is unverified: needs at least one resolvable supporting "
                f"citation (class {assessment.claim_type})"
            )
        if assessment.status == "contested":
            warnings.append(
                f"claim {claim_id} is contested; contradicting evidence must be reported "
                "alongside it"
            )
        for citation in assessment.citations:
            if citation.rights_state not in {"cleared_limited", "cleared_full"}:
                warnings.append(
                    f"evidence {citation.evidence_id} resolves under rights state "
                    f"{citation.rights_state!r}; internal research only"
                )
    return VerificationReport(tuple(assessments), tuple(errors), tuple(warnings))
