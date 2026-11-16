"""Versioned production-domain contracts introduced by AKI-ARCH-001.

These contracts are intentionally persistence- and provider-agnostic. They extend
rather than replace the original catalog contracts during the migration period.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Mapping
from urllib.parse import urlparse


class RightsDecisionState(StrEnum):
    UNKNOWN = "unknown"
    REVIEW_REQUIRED = "review_required"
    CLEARED_LIMITED = "cleared_limited"
    CLEARED_FULL = "cleared_full"
    RESTRICTED = "restricted"
    DENIED = "denied"
    EXPIRED_OR_REVOKED = "expired_or_revoked"


class RightsOperation(StrEnum):
    ACQUIRE = "acquire"
    STORE_ORIGINAL = "store_original"
    RENDER_PAGES = "render_pages"
    OCR_TRANSCRIBE = "ocr_transcribe"
    VISION_ANALYZE = "vision_analyze"
    CREATE_EMBEDDINGS = "create_embeddings"
    INTERNAL_SEARCH = "internal_search"
    INTERNAL_RESEARCH = "internal_research"
    FINE_TUNE_MODEL = "fine_tune_model"
    EXPORT_TEXT = "export_text"
    EXPORT_IMAGES = "export_images"
    SHARE_WITH_THIRD_PARTIES = "share_with_third_parties"
    REDISTRIBUTE_ORIGINAL = "redistribute_original"
    COMMERCIAL_USE = "commercial_use"


@dataclass(frozen=True, slots=True)
class RightsDecision:
    """A scoped, evidence-bearing rights decision for one edition or asset."""

    decision_id: str
    subject_id: str
    state: RightsDecisionState
    permitted_operations: frozenset[RightsOperation] = frozenset()
    prohibited_operations: frozenset[RightsOperation] = frozenset()
    evidence_uri: str | None = None
    license_name: str | None = None
    license_version: str | None = None
    jurisdiction: str | None = None
    reviewer: str | None = None
    decided_at: datetime | None = None
    review_after: datetime | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.decision_id.strip() or not self.subject_id.strip():
            raise ValueError("decision_id and subject_id are required")
        if self.permitted_operations & self.prohibited_operations:
            raise ValueError("an operation cannot be both permitted and prohibited")
        if self.state not in {
            RightsDecisionState.CLEARED_LIMITED,
            RightsDecisionState.CLEARED_FULL,
        } and self.permitted_operations:
            raise ValueError("only cleared rights decisions may grant operations")
        if self.evidence_uri and urlparse(self.evidence_uri).scheme not in {"http", "https", "file"}:
            raise ValueError("evidence_uri must use http, https, or file scheme")
        if self.review_after and self.decided_at and self.review_after < self.decided_at:
            raise ValueError("review_after cannot precede decided_at")

    def permits(self, operation: RightsOperation, *, now: datetime | None = None) -> bool:
        """Fail closed for unknown, stale, revoked, or explicitly prohibited decisions."""
        instant = now or datetime.now(timezone.utc)
        if self.state not in {
            RightsDecisionState.CLEARED_LIMITED,
            RightsDecisionState.CLEARED_FULL,
        }:
            return False
        if operation in self.prohibited_operations or operation not in self.permitted_operations:
            return False
        if self.review_after and instant >= self.review_after:
            return False
        return True


@dataclass(frozen=True, slots=True)
class Work:
    work_id: str
    canonical_title: str
    creators: tuple[str, ...] = ()
    original_language: str | None = None
    approximate_date_start: int | None = None
    approximate_date_end: int | None = None

    def __post_init__(self) -> None:
        if not self.work_id.strip() or not self.canonical_title.strip():
            raise ValueError("work_id and canonical_title are required")
        if (self.approximate_date_start is not None and self.approximate_date_end is not None
                and self.approximate_date_start > self.approximate_date_end):
            raise ValueError("approximate date range is inverted")


@dataclass(frozen=True, slots=True)
class Edition:
    edition_id: str
    work_id: str
    title: str
    publication_year: int | None = None
    language: str | None = None
    translator: str | None = None
    editor: str | None = None
    publisher: str | None = None
    identifier: str | None = None

    def __post_init__(self) -> None:
        if not self.edition_id.strip() or not self.work_id.strip() or not self.title.strip():
            raise ValueError("edition_id, work_id, and title are required")


@dataclass(frozen=True, slots=True)
class Asset:
    asset_id: str
    edition_id: str
    sha256: str
    media_type: str
    size_bytes: int
    original_uri: str | None = None

    def __post_init__(self) -> None:
        if not self.asset_id.strip() or not self.edition_id.strip() or not self.media_type.strip():
            raise ValueError("asset identity and media_type are required")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        if self.size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")


@dataclass(frozen=True, slots=True)
class Region:
    region_id: str
    page_id: str
    coordinate_space_sha256: str
    polygon: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if not self.region_id.strip() or not self.page_id.strip():
            raise ValueError("region_id and page_id are required")
        if len(self.coordinate_space_sha256) != 64:
            raise ValueError("coordinate_space_sha256 must be a SHA-256 digest")
        if len(self.polygon) < 3:
            raise ValueError("polygon requires at least three points")
        if any(not (0 <= x <= 1 and 0 <= y <= 1) for x, y in self.polygon):
            raise ValueError("region polygon coordinates must be normalized to [0, 1]")


class ProcessingState(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    HELD = "held"
    FAILED = "failed"
    REVIEW_REQUIRED = "review_required"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ProcessingRun:
    run_id: str
    operation: str
    input_sha256: tuple[str, ...]
    processor_name: str
    processor_version: str
    config_sha256: str
    state: ProcessingState
    created_at: datetime
    model_digest: str | None = None
    parent_artifact_ids: tuple[str, ...] = ()
    rights_decision_id: str | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.run_id.strip() or not self.operation.strip():
            raise ValueError("run_id and operation are required")
        if not self.input_sha256:
            raise ValueError("processing run requires at least one immutable input")
        for digest in (*self.input_sha256, self.config_sha256):
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("input and config hashes must be lowercase SHA-256 digests")
        if not self.processor_name.strip() or not self.processor_version.strip():
            raise ValueError("processor name and version are required")


class ClaimStatus(StrEnum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    CONTESTED = "contested"
    UNVERIFIED = "unverified"
    REJECTED = "rejected"


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"


@dataclass(frozen=True, slots=True)
class ClaimEvidenceLink:
    evidence_id: str
    relation: EvidenceRelation
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_id.strip():
            raise ValueError("evidence_id is required")


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    statement: str
    status: ClaimStatus = ClaimStatus.PROPOSED
    evidence: tuple[ClaimEvidenceLink, ...] = ()
    alternatives: tuple[str, ...] = ()
    verifier_id: str | None = None

    def __post_init__(self) -> None:
        if not self.claim_id.strip() or not self.statement.strip():
            raise ValueError("claim_id and statement are required")
        if self.status is ClaimStatus.SUPPORTED and not any(
            link.relation is EvidenceRelation.SUPPORTS for link in self.evidence
        ):
            raise ValueError("a supported claim requires at least one supporting evidence link")
        if self.status is ClaimStatus.CONTESTED and not any(
            link.relation is EvidenceRelation.CONTRADICTS for link in self.evidence
        ):
            raise ValueError("a contested claim requires contradicting evidence")


@dataclass(frozen=True, slots=True)
class ModelProfile:
    model_id: str
    digest: str
    license_name: str
    modalities: frozenset[str]
    runtime: str
    measured_peak_vram_bytes: int | None = None
    measured_peak_ram_bytes: int | None = None
    measured_context_tokens: int | None = None
    evaluation: Mapping[str, float] | None = None

    def __post_init__(self) -> None:
        if not self.model_id.strip() or not self.license_name.strip() or not self.runtime.strip():
            raise ValueError("model identity, license and runtime are required")
        if len(self.digest) != 64 or any(c not in "0123456789abcdef" for c in self.digest):
            raise ValueError("model digest must be a lowercase SHA-256 digest")
        for value in (self.measured_peak_vram_bytes, self.measured_peak_ram_bytes,
                      self.measured_context_tokens):
            if value is not None and value <= 0:
                raise ValueError("measured resource values must be positive")
