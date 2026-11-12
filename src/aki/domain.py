"""Core domain contracts separating source observations from interpretation."""
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


class RightsStatus(StrEnum):
    UNKNOWN = "unknown"
    PUBLIC_DOMAIN = "public_domain"
    LICENSED = "licensed"
    RESTRICTED = "restricted"
    PERMISSION_REQUIRED = "permission_required"


class ProcessingOperation(StrEnum):
    ACQUIRE = "acquire"
    STORE = "store"
    OCR = "ocr"
    MODEL_ANALYSIS = "model_analysis"
    DISPLAY = "display"
    REDISTRIBUTE = "redistribute"


@dataclass(frozen=True, slots=True)
class RightsGrant:
    """Permission is operation-specific; public-domain status is not a blanket grant."""
    status: RightsStatus
    permitted_operations: frozenset[ProcessingOperation] = frozenset()
    evidence_uri: str | None = None
    attribution_required: bool = False
    notes: str = ""

    def permits(self, operation: ProcessingOperation) -> bool:
        if self.status in {RightsStatus.RESTRICTED, RightsStatus.PERMISSION_REQUIRED,
                           RightsStatus.UNKNOWN}:
            return False
        return operation in self.permitted_operations


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    title: str
    creator: str | None
    language: str | None
    source_uri: str
    collection: str
    edition_label: str
    rights: RightsGrant

    def __post_init__(self) -> None:
        for name in ("source_id", "title", "source_uri", "collection", "edition_label"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if urlparse(self.source_uri).scheme not in {"http", "https", "file"}:
            raise ValueError("source_uri must use http, https, or file scheme")


@dataclass(frozen=True, slots=True)
class PageReference:
    source_id: str
    page_number: int
    image_sha256: str
    region: tuple[float, float, float, float] | None = None

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("page_number is one-based and must be positive")
        if len(self.image_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.image_sha256):
            raise ValueError("image_sha256 must be a lowercase SHA-256 digest")
        if self.region is not None:
            x1, y1, x2, y2 = self.region
            if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                raise ValueError("region must be normalized coordinates with positive area")


class AssertionKind(StrEnum):
    TRANSCRIPTION = "transcription"
    TRANSLATION = "translation"
    VISUAL_OBSERVATION = "visual_observation"
    HISTORICAL_CLAIM = "historical_claim"
    HYPOTHESIS = "hypothesis"


@dataclass(frozen=True, slots=True)
class EvidenceAssertion:
    assertion_id: str
    kind: AssertionKind
    statement: str
    evidence: tuple[PageReference, ...]
    method: str
    confidence: float | None = None
    reviewed_by_human: bool = False
    alternatives: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.assertion_id.strip() or not self.statement.strip() or not self.method.strip():
            raise ValueError("assertion_id, statement, and method are required")
        if not self.evidence:
            raise ValueError("every assertion must cite at least one page reference")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
