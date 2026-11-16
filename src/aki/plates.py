"""Plate detection: find pages that are pictures rather than prose.

Old books carry engraved plates, frontispieces, maps, seals and diagrams, and a reader
browsing 35,000 pages wants to see them. No vision-language model is installed, so this
is a measured heuristic, recorded as such:

* the page has little transcribed text (fewer than ``MAX_TEXT_CHARS`` characters), and
* its rendered image carries substantial ink: either a meaningful share of dark pixels
  with the spread of mid-tones an engraving or photograph has, or a large solid area of
  ink such as a woodcut, seal or map. Blank, stained and bleed-through leaves have neither.

Each detection is a ``visual_observations`` row with ``evidence_class =
'model_inference'``, the heuristic's name and version as the model, and the measured
values in ``alternatives`` so a reviewer can see why a page was picked. Nothing is
claimed about what the picture shows.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .db import Database, json_dumps, new_id, utcnow
from .gate import permitted_editions

MODEL = "aki-plate-heuristic"
VERSION = "1"
MAX_TEXT_CHARS = 160
MIN_INK = 0.06
MIN_MIDTONE = 0.05
SOLID_INK = 0.12
MAX_INK = 0.92


def measure(image_path: Path) -> dict[str, float]:
    """Ink coverage and mid-tone share of a page image, downsampled for speed."""
    import numpy as np
    import pymupdf

    pixmap = pymupdf.Pixmap(str(image_path))
    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)
    while pixmap.width > 400:
        pixmap.shrink(1)
    samples = np.frombuffer(pixmap.samples, dtype=np.uint8)
    channels = pixmap.n
    pixels = samples.reshape(pixmap.height, pixmap.width, channels)[:, :, :3].mean(axis=2)
    height, width = pixels.shape
    inner = pixels[int(height * .06):int(height * .94), int(width * .06):int(width * .94)]
    paper = float(np.percentile(inner, 90))
    relative = inner / max(paper, 1.0)
    ink = float((relative < 0.55).mean())
    midtone = float(((relative >= 0.55) & (relative < 0.85)).mean())
    return {"ink": round(ink, 4), "midtone": round(midtone, 4), "paper": round(paper, 1)}


def is_plate(text_chars: int, metrics: dict[str, float]) -> bool:
    return (
        text_chars < MAX_TEXT_CHARS
        and MIN_INK <= metrics["ink"] <= MAX_INK
        and (metrics["midtone"] >= MIN_MIDTONE or metrics["ink"] >= SOLID_INK)
    )


def detect(database: Database, blob_root: str | Path, *, limit: int | None = None
           ) -> dict[str, Any]:
    """Examine every unexamined low-text page and record the ones that look like plates."""
    database.require_current()
    allowed = permitted_editions(database, "vision_analyze")
    root = Path(blob_root)
    clock = time.time()
    started = utcnow()
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT p.id, p.page_image_key, a.edition_id,"
            " COALESCE((SELECT LENGTH(t.text_content) FROM transcriptions t"
            "           WHERE t.page_id = p.id ORDER BY t.created_at DESC LIMIT 1), 0) AS chars"
            " FROM pages p JOIN assets a ON a.id = p.asset_id"
            " WHERE NOT EXISTS (SELECT 1 FROM visual_observations v WHERE v.page_id = p.id"
            "                   AND v.model_name = ?)",
            (MODEL,),
        ).fetchall()
    candidates = [row for row in rows if row["edition_id"] in allowed
                  and int(row["chars"]) < MAX_TEXT_CHARS]
    if limit is not None:
        candidates = candidates[:limit]
    examined = plates = 0
    for row in candidates:
        path = root / str(row["page_image_key"])
        try:
            metrics = measure(path)
        except Exception:  # noqa: BLE001 - one unreadable image must not stop the pass
            continue
        examined += 1
        plate = is_plate(int(row["chars"]), metrics)
        plates += int(plate)
        with database.transaction() as connection:
            observation_id = new_id()
            connection.execute(
                "INSERT INTO visual_observations (id, page_id, observation_type,"
                " literal_observation, evidence_class, model_name, model_digest,"
                " prompt_version, confidence, alternatives, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (observation_id, row["id"], "plate" if plate else "not_plate",
                 "Page has little or no text and substantial printed or drawn content."
                 if plate else "Page examined; it does not look like a plate.",
                 "model_inference", MODEL, f"{MODEL}-v{VERSION}", "n/a",
                 0.6 if plate else 0.6,
                 json.dumps({**metrics, "text_chars": int(row["chars"])}), utcnow()),
            )
            if plate:
                connection.execute(
                    "INSERT INTO evidence (id, edition_id, page_id, visual_observation_id,"
                    " locator, evidence_kind, evidence_class, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (new_id(), row["edition_id"], row["id"], observation_id, "{}",
                     "visual_observation", "model_inferred", utcnow()),
                )
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO model_runs (id, task_type, provider, model_name, model_digest,"
            " runtime_version, parameters, started_at, completed_at, outcome, metrics)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (new_id(), "plate_detection", "aki", MODEL, f"{MODEL}-v{VERSION}", "n/a",
             json_dumps({"max_text_chars": MAX_TEXT_CHARS, "min_ink": MIN_INK,
                         "min_midtone": MIN_MIDTONE, "solid_ink": SOLID_INK,
                         "max_ink": MAX_INK}),
             started, utcnow(), "succeeded", json_dumps({"examined": examined, "plates": plates})),
        )
    return {"examined": examined, "plates": plates, "seconds": round(time.time() - clock, 1)}
