"""Strict JSONL manifest parsing."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Iterator
from .domain import ProcessingOperation, RightsGrant, RightsStatus, SourceRecord

_REQUIRED = {"source_id", "title", "source_uri", "collection", "edition_label", "rights"}


def read_manifest(path: str | Path) -> Iterator[SourceRecord]:
    with Path(path).open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, start=1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                item = json.loads(line)
                missing = _REQUIRED - item.keys()
                if missing:
                    raise ValueError(f"missing fields: {', '.join(sorted(missing))}")
                rights_data = item["rights"]
                grant = RightsGrant(
                    status=RightsStatus(rights_data["status"]),
                    permitted_operations=frozenset(
                        ProcessingOperation(x) for x in rights_data.get("permitted_operations", [])
                    ),
                    evidence_uri=rights_data.get("evidence_uri"),
                    attribution_required=bool(rights_data.get("attribution_required", False)),
                    notes=rights_data.get("notes", ""),
                )
                yield SourceRecord(
                    source_id=item["source_id"], title=item["title"], creator=item.get("creator"),
                    language=item.get("language"), source_uri=item["source_uri"],
                    collection=item["collection"], edition_label=item["edition_label"], rights=grant,
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{number}: {exc}") from exc
