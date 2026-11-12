import pytest
from aki.catalog import CatalogConflict, SourceCatalog
from aki.domain import ProcessingOperation, RightsGrant, RightsStatus, SourceRecord
from aki.rights import RightsDenied


def source(title="A Treatise", status=RightsStatus.PUBLIC_DOMAIN):
    return SourceRecord(
        source_id="collection:work:edition-1", title=title, creator="Unknown", language="en",
        source_uri="https://example.org/work/1", collection="test-collection",
        edition_label="scan edition 1",
        rights=RightsGrant(status=status, permitted_operations=frozenset({
            ProcessingOperation.STORE, ProcessingOperation.OCR
        })),
    )


def test_catalog_round_trip_and_idempotent_insert(tmp_path):
    catalog = SourceCatalog(tmp_path / "catalog.sqlite3")
    record = source()
    assert catalog.add(record) is True
    assert catalog.add(record) is False
    assert catalog.get(record.source_id) == record
    assert len(catalog.list_sources()) == 1


def test_source_id_cannot_be_reused_for_different_metadata(tmp_path):
    catalog = SourceCatalog(tmp_path / "catalog.sqlite3")
    catalog.add(source())
    with pytest.raises(CatalogConflict):
        catalog.add(source(title="Different work"))


def test_rights_are_fail_closed(tmp_path):
    catalog = SourceCatalog(tmp_path / "catalog.sqlite3")
    record = source(status=RightsStatus.UNKNOWN)
    catalog.add(record)
    with pytest.raises(RightsDenied):
        catalog.authorize(record.source_id, ProcessingOperation.OCR)


def test_authorized_operation_returns_source(tmp_path):
    catalog = SourceCatalog(tmp_path / "catalog.sqlite3")
    record = source()
    catalog.add(record)
    assert catalog.authorize(record.source_id, ProcessingOperation.OCR) == record
