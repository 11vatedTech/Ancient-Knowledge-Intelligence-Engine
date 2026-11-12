"""Reference-agreement scoring: normalisation, edit distance and report shape."""
from __future__ import annotations

import pytest

from aki.ocr import OcrLine, OcrPage
from aki.ocr_eval import (
    Sample,
    character_error_rate,
    evaluate,
    evaluation_fingerprint,
    normalize,
    word_error_rate,
)


class StubProvider:
    name = "StubOCR"
    version = "1.0"

    def __init__(self, text: str, confidence: float = 0.9):
        self.text = text
        self.confidence = confidence

    def ocr(self, image_path):  # noqa: ANN001 - matches provider protocol
        return OcrPage(
            text=self.text,
            confidence=self.confidence,
            lines=(OcrLine(self.text, self.confidence, (0, 0, 1, 1)),) if self.text else (),
            engine_name=self.name,
            engine_version=self.version,
        )


def test_normalize_drops_punctuation_and_case():
    assert normalize("The  GOSPEL, of Peter!") == "the gospel of peter"
    assert normalize("long-s hyphens—and dashes") == "long s hyphens and dashes"


def test_identical_text_scores_zero():
    reference = "the quick brown fox jumps over the lazy dog"
    assert character_error_rate(reference, reference) == 0.0
    assert word_error_rate(reference, reference) == 0.0


def test_single_character_substitution():
    reference = "abcdefghij"
    hypothesis = "abcdefghix"
    assert character_error_rate(reference, hypothesis) == pytest.approx(0.1)


def test_empty_reference_is_not_a_division_by_zero():
    assert character_error_rate("", "") == 0.0
    assert character_error_rate("", "something") == 1.0
    assert word_error_rate("", "something") == 1.0


def test_error_rate_is_capped_at_one():
    assert character_error_rate("a", "z" * 5000) <= 1.0


def test_evaluate_scores_a_perfect_and_a_poor_page(tmp_path):
    image = tmp_path / "page.png"
    image.write_bytes(b"not really a png")
    reference = "the lost gospel according to peter anonymous"
    samples = [
        Sample("e1", "Perfect", 0, image, reference),
        Sample("e2", "Poor", 0, image, reference),
    ]

    class SequenceProvider:
        name = "Seq"
        version = "1"

        def __init__(self):
            self.calls = 0

        def ocr(self, image_path):  # noqa: ANN001
            self.calls += 1
            text = reference if self.calls == 1 else "zzz zzz zzz"
            return OcrPage(text, 0.9, (), self.name, self.version)

    report = evaluate(samples, SequenceProvider())
    assert report["samples_scored"] == 2
    assert report["mean_cer"] is not None
    worst = report["pages"][0]
    assert worst["edition_id"] == "e2"  # sorted worst-first
    assert report["pages"][1]["cer"] == 0.0


def test_missing_image_is_reported_not_scored(tmp_path):
    sample = Sample("e1", "Missing", 0, tmp_path / "absent.png", "text")
    report = evaluate([sample], StubProvider("text"))
    assert report["samples_scored"] == 0
    assert report["issues"][0]["status"] == "missing_image"


def test_fingerprint_is_stable_and_content_addressed(tmp_path):
    image = tmp_path / "p.png"
    image.write_bytes(b"x")
    sample = Sample("e1", "T", 0, image, "alpha beta")
    first = evaluate([sample], StubProvider("alpha beta"))
    second = evaluate([sample], StubProvider("alpha beta"))
    assert evaluation_fingerprint(first) == evaluation_fingerprint(second)
