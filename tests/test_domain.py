import pytest
from aki.domain import AssertionKind, EvidenceAssertion, PageReference, RightsGrant, RightsStatus, SourceRecord


def test_source_rejects_invalid_scheme():
    with pytest.raises(ValueError):
        SourceRecord("id", "Title", None, None, "javascript:alert(1)", "c", "e",
                     RightsGrant(RightsStatus.UNKNOWN))


def test_page_reference_requires_sha256_and_one_based_page():
    with pytest.raises(ValueError):
        PageReference("source", 0, "0" * 64)
    with pytest.raises(ValueError):
        PageReference("source", 1, "not-a-hash")


def test_page_region_is_normalized():
    with pytest.raises(ValueError):
        PageReference("source", 1, "a" * 64, (0.8, 0.2, 0.1, 0.9))


def test_assertion_requires_evidence_and_valid_confidence():
    with pytest.raises(ValueError):
        EvidenceAssertion("a", AssertionKind.HISTORICAL_CLAIM, "claim", (), "model")
    with pytest.raises(ValueError):
        EvidenceAssertion("a", AssertionKind.HISTORICAL_CLAIM, "claim",
                          (PageReference("s", 1, "a" * 64),), "model", confidence=1.5)


def test_unknown_rights_never_authorize_processing():
    from aki.domain import ProcessingOperation
    grant = RightsGrant(RightsStatus.UNKNOWN, frozenset({ProcessingOperation.OCR}))
    assert grant.permits(ProcessingOperation.OCR) is False
