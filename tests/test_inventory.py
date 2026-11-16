"""Inventory import identity, disposition accounting and manifest export."""
from __future__ import annotations

import json

import pytest

from aki.db import Database, initialize
from aki.inventory import (
    InventorySnapshot,
    ListingItem,
    acquisition_report,
    disposition_counts,
    export_manifest,
    import_snapshot,
    load_snapshot,
    normalize_title,
    stable_key,
    write_snapshot,
)


def _snapshot(count: int = 3) -> InventorySnapshot:
    return InventorySnapshot(
        source_name="theholyword.net",
        site_url="https://theholyword.net/",
        captured_at="2026-11-12T00:00:00.000000Z",
        advertised_count=257,
        items=tuple(
            ListingItem(title=f"Record {index}", author=None, size_label="1.0 MB")
            for index in range(count)
        ),
        pagination_model="server-side",
        discovery_method="test",
    )


@pytest.fixture()
def database(tmp_path):
    db = Database(tmp_path / "aki.sqlite3")
    initialize(db.path)
    return db


def test_normalize_title_is_stable():
    assert normalize_title("The  KEY to Mental Power!") == "the key to mental power"
    assert stable_key("a", "b") == stable_key("a", "b")


def test_import_is_idempotent(database):
    first = import_snapshot(database, _snapshot())
    second = import_snapshot(database, _snapshot())
    assert first.source_records_added == 3
    assert second.source_records_added == 0
    assert second.source_records_existing == 3
    assert database.coverage()["works"] == 3


def test_import_deduplicates_within_snapshot(database):
    snapshot = InventorySnapshot(
        source_name="s",
        site_url="https://example.org/",
        captured_at="2026-11-12T00:00:00.000000Z",
        advertised_count=2,
        items=(ListingItem("Same Title"), ListingItem("same  title")),
        pagination_model="server-side",
        discovery_method="test",
    )
    report = import_snapshot(database, snapshot)
    assert report.duplicates_in_snapshot == 1
    assert report.observed_count == 1


def test_every_record_holds_one_disposition(database):
    import_snapshot(database, _snapshot(count=4))
    counts = disposition_counts(database)
    assert counts["review_required"] == 4
    assert counts["_acquired_assets"] == 0


def test_report_never_counts_catalogued_as_acquired(database):
    import_snapshot(database, _snapshot(count=4))
    report = acquisition_report(database)
    assert report["discovered_source_records"] == 4
    assert report["acquired_assets_preflight_passed"] == 0
    assert report["rights_cleared_editions"] == 0
    assert report["processing_ready"] == 0


def test_imported_records_deny_all_operations(database):
    from aki.ingest import _decision_from_row

    import_snapshot(database, _snapshot(count=1))
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM rights_decisions LIMIT 1").fetchone()
    decision = _decision_from_row(row)
    assert decision.state.value == "review_required"
    assert decision.permitted_operations == frozenset()
    from aki.production_domain import RightsOperation

    assert decision.permits(RightsOperation.STORE_ORIGINAL) is False


def test_manifest_export_denies_acquisition(database, tmp_path):
    import_snapshot(database, _snapshot(count=2))
    result = export_manifest(database, tmp_path / "manifest.jsonl")
    lines = (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert result["records"] == 2
    for line in lines:
        payload = json.loads(line)
        assert payload["rights"]["permitted_operations"] == []
        assert payload["rights"]["status"] == "permission_required"
        assert payload["source_id"].startswith("src-")


def test_snapshot_round_trip(database, tmp_path):
    snapshot = _snapshot(count=2)
    path = write_snapshot(snapshot, tmp_path / "snap.json")
    reloaded = load_snapshot(path)
    assert reloaded.advertised_count == 257
    assert len(reloaded.items) == 2
    assert reloaded.items[0].title == "Record 0"
