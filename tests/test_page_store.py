import pymupdf
import pytest

from aki.blob_store import ContentAddressedStore
from aki.catalog import SourceCatalog
from aki.domain import ProcessingOperation, RightsGrant, RightsStatus, SourceRecord
from aki.page_store import PageArtifactCatalog
from aki.pdf import PDFPageProcessor, RenderedPage


def test_page_manifest_is_transactional_and_idempotent(tmp_path):
    pdf_path = tmp_path / "book.pdf"
    document = pymupdf.open()
    document.new_page(width=300, height=400)
    document.new_page(width=300, height=400)
    document.save(pdf_path)
    document.close()

    database = tmp_path / "catalog.sqlite3"
    catalog = SourceCatalog(database)
    catalog.add(SourceRecord(
        source_id="book-001",
        title="Test book",
        creator=None,
        language="en",
        source_uri=pdf_path.as_uri(),
        collection="test",
        edition_label="test scan",
        rights=RightsGrant(
            status=RightsStatus.LICENSED,
            permitted_operations=frozenset({ProcessingOperation.STORE}),
            evidence_uri="https://example.org/rights",
        ),
    ))
    blobs = ContentAddressedStore(tmp_path / "blobs")
    pages = PDFPageProcessor().render_to_store(
        pdf_path, "book-001", catalog.authorize("book-001", ProcessingOperation.STORE).rights,
        blobs, dpi=72,
    )
    manifests = PageArtifactCatalog(database, blobs)
    assert manifests.record_batch(pages) == (2, 0)
    assert manifests.record_batch(pages) == (0, 2)
    assert len(manifests.list_pages("book-001")) == 2


def test_page_manifest_rejects_missing_blob(tmp_path):
    database = tmp_path / "catalog.sqlite3"
    SourceCatalog(database)
    blobs = ContentAddressedStore(tmp_path / "blobs")
    manifests = PageArtifactCatalog(database, blobs)
    page = RenderedPage(
        source_id="missing-source",
        page_number=1,
        page_label="1",
        source_pdf_sha256="a" * 64,
        image_sha256="b" * 64,
        image_size_bytes=12,
        pixel_width=10,
        pixel_height=10,
        page_width_points=10.0,
        page_height_points=10.0,
        dpi=72,
        blob_path=str(tmp_path / "missing.png"),
        renderer_name="PyMuPDF",
        renderer_version="test",
        config_sha256="c" * 64,
    )
    with pytest.raises(FileNotFoundError):
        manifests.record_batch([page])
    assert manifests.list_pages("missing-source") == []
