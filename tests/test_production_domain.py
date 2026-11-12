from datetime import datetime, timedelta, timezone

import pytest

from aki.production_domain import (
    Asset,
    Claim,
    ClaimEvidenceLink,
    ClaimStatus,
    Edition,
    EvidenceRelation,
    ModelProfile,
    ProcessingRun,
    ProcessingState,
    RightsDecision,
    RightsDecisionState,
    RightsOperation,
    Work,
)


HASH = "a" * 64


def test_work_and_edition_have_independent_identity() -> None:
    work = Work("work-1", "The Book", approximate_date_start=100, approximate_date_end=200)
    edition = Edition("edition-1", work.work_id, "Modern translation", translator="A. Translator")
    assert edition.work_id == work.work_id
    assert edition.edition_id != work.work_id


def test_work_rejects_inverted_date_range() -> None:
    with pytest.raises(ValueError, match="inverted"):
        Work("work-1", "The Book", approximate_date_start=200, approximate_date_end=100)


def test_asset_requires_content_hash_and_nonnegative_size() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        Asset("asset-1", "edition-1", "not-a-hash", "application/pdf", 10)
    with pytest.raises(ValueError, match="cannot be negative"):
        Asset("asset-1", "edition-1", HASH, "application/pdf", -1)


def test_rights_are_operation_scoped_and_fail_closed() -> None:
    decision = RightsDecision(
        "rights-1", "edition-1", RightsDecisionState.CLEARED_LIMITED,
        permitted_operations=frozenset({RightsOperation.STORE_ORIGINAL}),
    )
    assert decision.permits(RightsOperation.STORE_ORIGINAL)
    assert not decision.permits(RightsOperation.EXPORT_TEXT)
    assert not RightsDecision(
        "unknown-1", "edition-1", RightsDecisionState.UNKNOWN,
        permitted_operations=frozenset(),
    ).permits(RightsOperation.STORE_ORIGINAL)


def test_expired_rights_review_fails_closed() -> None:
    now = datetime.now(timezone.utc)
    decision = RightsDecision(
        "rights-1", "asset-1", RightsDecisionState.CLEARED_FULL,
        permitted_operations=frozenset({RightsOperation.OCR_TRANSCRIBE}),
        decided_at=now - timedelta(days=30),
        review_after=now - timedelta(seconds=1),
    )
    assert not decision.permits(RightsOperation.OCR_TRANSCRIBE, now=now)


def test_rights_cannot_grant_and_prohibit_same_operation() -> None:
    with pytest.raises(ValueError, match="both permitted and prohibited"):
        RightsDecision(
            "rights-1", "edition-1", RightsDecisionState.CLEARED_FULL,
            permitted_operations=frozenset({RightsOperation.STORE_ORIGINAL}),
            prohibited_operations=frozenset({RightsOperation.STORE_ORIGINAL}),
        )


def test_processing_run_requires_input_and_valid_hashes() -> None:
    with pytest.raises(ValueError, match="at least one immutable input"):
        ProcessingRun("run-1", "render", (), "renderer", "1.0", HASH,
                      ProcessingState.COMPLETED, datetime.now(timezone.utc))
    run = ProcessingRun("run-1", "render", (HASH,), "renderer", "1.0", HASH,
                        ProcessingState.COMPLETED, datetime.now(timezone.utc))
    assert run.state is ProcessingState.COMPLETED


def test_supported_claim_requires_supporting_evidence() -> None:
    with pytest.raises(ValueError, match="supporting evidence"):
        Claim("claim-1", "A proposition", ClaimStatus.SUPPORTED)
    claim = Claim(
        "claim-1", "A proposition", ClaimStatus.SUPPORTED,
        (ClaimEvidenceLink("evidence-1", EvidenceRelation.SUPPORTS),),
    )
    assert claim.evidence[0].evidence_id == "evidence-1"


def test_contested_claim_requires_contradiction() -> None:
    with pytest.raises(ValueError, match="contradicting evidence"):
        Claim("claim-1", "A proposition", ClaimStatus.CONTESTED)


def test_model_profile_requires_pinned_digest_and_license() -> None:
    with pytest.raises(ValueError, match="digest"):
        ModelProfile("vlm", "mutable-latest", "license", frozenset({"image"}), "ollama")
    profile = ModelProfile("vlm", HASH, "Apache-2.0", frozenset({"image"}), "ollama")
    assert "image" in profile.modalities
