"""Reference-agreement measurement for OCR.

No human transcription exists for this corpus, so there is no ground truth in the strict
sense. What does exist is the publisher's embedded text layer for pages that have one —
a digital rendering of the same page the OCR engine is asked to read.

Comparing OCR against that reference measures **fidelity to the source text**, on real
scans, with real typography. It is not absolute accuracy: where the embedded layer is
itself wrong, this understates OCR quality; where the layer is cleaner than the image,
it overstates the difficulty. Every report states which comparison was made.

Higher is better: 0.0 CER means identical text.
"""
from __future__ import annotations

import hashlib
import json
import random
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .db import Database
from .ocr import OcrProvider

MAX_CHARS = 1500


def normalize(text: str) -> str:
    """Casefold and reduce to letters, digits and single spaces.

    Punctuation, hyphenation and line breaks are the first things OCR gets wrong and the
    least meaningful to a reader, so they are removed before comparison rather than
    allowed to dominate the error rate.
    """
    lowered = text.casefold()
    kept = [character if character.isalnum() else " " for character in lowered]
    return " ".join("".join(kept).split())


def _levenshtein(reference: Sequence[Any], hypothesis: Sequence[Any], *, band: int = 400) -> int:
    """Edit distance with a banded two-row DP.

    Full DP is quadratic; with a band the cost stays proportional to the real edit count.
    Cells outside the band are treated as unreachable rather than computed.
    """
    if not reference:
        return len(hypothesis)
    if not hypothesis:
        return len(reference)
    infinity = len(reference) + len(hypothesis)
    previous: dict[int, int] = {0: 0}
    for j in range(1, min(len(hypothesis), band) + 1):
        previous[j] = j
    for i in range(1, len(reference) + 1):
        current: dict[int, int] = {}
        low = max(0, i - band)
        high = min(len(hypothesis), i + band)
        if low == 0:
            current[0] = i
        for j in range(max(1, low), high + 1):
            cost = 0 if reference[i - 1] == hypothesis[j - 1] else 1
            current[j] = min(
                previous.get(j, infinity) + 1,
                current.get(j - 1, infinity) + 1,
                previous.get(j - 1, infinity) + cost,
            )
        previous = current
    return previous.get(len(hypothesis), infinity)


def character_error_rate(reference: str, hypothesis: str) -> float:
    ref = reference[:MAX_CHARS]
    hyp = hypothesis[:MAX_CHARS]
    if not ref:
        return 0.0 if not hyp else 1.0
    return min(_levenshtein(ref, hyp) / len(ref), 1.0)


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref = reference.split()
    hyp = hypothesis.split()
    if not ref:
        return 0.0 if not hyp else 1.0
    edits = _levenshtein(ref, hyp, band=200)
    return edits / len(ref)


@dataclass(frozen=True, slots=True)
class Sample:
    edition_id: str
    edition_title: str
    page_index: int
    image_path: Path
    reference: str
    pdf_path: Path | None = None


def sample_pages(
    database: Database,
    blob_root: str | Path,
    *,
    limit: int = 30,
    min_chars: int = 400,
    per_edition: int = 1,
    seed: int = 7,
) -> list[Sample]:
    """Pick pages that have both a stored page image and an embedded text layer.

    Sampling is spread across editions first, so one long book cannot dominate the score.
    """
    root = Path(blob_root)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT a.edition_id, e.edition_title, p.page_index, p.page_image_key,"
            " a.storage_key, t.text_content"
            " FROM transcriptions t"
            " JOIN pages p ON p.id = t.page_id"
            " JOIN assets a ON a.id = p.asset_id"
            " JOIN editions e ON e.id = a.edition_id"
            " WHERE t.engine_name = 'PyMuPDF-text-layer' AND length(t.text_content) >= ?"
            " ORDER BY a.edition_id, p.page_index",
            (min_chars,),
        ).fetchall()
    by_edition: dict[str, list[Any]] = {}
    for row in rows:
        by_edition.setdefault(row["edition_id"], []).append(row)
    pool: list[Sample] = []
    for edition_rows in by_edition.values():
        for row in edition_rows[:per_edition]:
            pool.append(Sample(
                edition_id=row["edition_id"],
                edition_title=row["edition_title"],
                page_index=int(row["page_index"]),
                image_path=root / row["page_image_key"],
                reference=row["text_content"],
                pdf_path=root / row["storage_key"],
            ))
    rng = random.Random(seed)
    rng.shuffle(pool)
    chosen, seen = [], set()
    for candidate in pool:
        if candidate.edition_id in seen:
            continue
        seen.add(candidate.edition_id)
        chosen.append(candidate)
        if len(chosen) >= limit:
            break
    return chosen


def _render_at(pdf_path: Path, page_index: int, dpi: int, destination: Path) -> Path:
    import pymupdf

    with pymupdf.open(str(pdf_path)) as document:
        document.load_page(page_index).get_pixmap(dpi=dpi).save(str(destination))
    return destination


def evaluate(
    samples: Sequence[Sample],
    provider: OcrProvider,
    *,
    render_dpi: int | None = None,
) -> dict[str, Any]:
    """OCR each sampled page and score it against its reference text.

    With ``render_dpi`` the page is re-rendered from the archived PDF at that resolution
    instead of using the stored image, which is what makes a 150-vs-300 DPI comparison
    measure resolution rather than something else.
    """
    per_page: list[dict[str, Any]] = []
    started = time.perf_counter()
    scratch = Path(tempfile.mkdtemp(prefix="aki-eval-"))
    for sample in samples:
        image = sample.image_path
        if render_dpi is not None and sample.pdf_path is not None:
            image = _render_at(
                sample.pdf_path, sample.page_index, render_dpi,
                scratch / f"{sample.edition_id}-{sample.page_index}-{render_dpi}.png",
            )
        if not image.exists():
            per_page.append({
                "edition_id": sample.edition_id,
                "page_index": sample.page_index,
                "status": "missing_image",
            })
            continue
        try:
            recognised = provider.ocr(image)
        except Exception as exc:  # noqa: BLE001 - recorded per sample, not swallowed
            per_page.append({
                "edition_id": sample.edition_id,
                "page_index": sample.page_index,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        reference = normalize(sample.reference)
        hypothesis = normalize(recognised.text)
        per_page.append({
            "edition_id": sample.edition_id,
            "edition_title": sample.edition_title[:60],
            "page_index": sample.page_index,
            "status": "scored",
            "render_dpi": render_dpi,
            "reference_chars": len(reference),
            "ocr_chars": len(hypothesis),
            "cer": round(character_error_rate(reference, hypothesis), 4),
            "wer": round(word_error_rate(reference, hypothesis), 4),
            "mean_confidence": None if recognised.confidence is None
            else round(recognised.confidence, 4),
            "seconds": round(recognised.elapsed_seconds, 2),
        })
    scored = [row for row in per_page if row["status"] == "scored"]
    cers = sorted(row["cer"] for row in scored)
    wers = sorted(row["wer"] for row in scored)
    return {
        "provider": provider.name,
        "provider_version": provider.version,
        "reference": "embedded text layer of the same page",
        "caveat": (
            "Reference agreement, not human-verified ground truth. Where the embedded "
            "layer is itself defective this understates OCR quality."
        ),
        "samples_requested": len(samples),
        "samples_scored": len(scored),
        "mean_cer": round(sum(cers) / len(cers), 4) if cers else None,
        "median_cer": round(cers[len(cers) // 2], 4) if cers else None,
        "p90_cer": round(cers[int(len(cers) * 0.9) - 1], 4) if cers else None,
        "mean_wer": round(sum(wers) / len(wers), 4) if wers else None,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "pages": sorted(scored, key=lambda row: row["cer"], reverse=True),
        "issues": [row for row in per_page if row["status"] != "scored"],
    }


def evaluation_fingerprint(report: dict[str, Any]) -> str:
    """Stable digest of a report's scores, so a claim can be tied to its evidence."""
    body = json.dumps(
        [(row["edition_id"], row["page_index"], row["cer"], row["wer"]) for row in report["pages"]],
        sort_keys=True,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
