import pymupdf
import pytest

from aki.blob_store import ContentAddressedStore
from aki.domain import ProcessingOperation, RightsGrant, RightsStatus
from aki.pdf import PDFPageProcessor, PDFProcessingError


def make_pdf(path, page_count=2):
    document = pymupdf.open()
    for index in range(page_count):
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 72), f"AKI source page {index + 1}")
    document.save(path)
    document.close()


def storage_grant():
    return RightsGrant(
        status=RightsStatus.LICENSED,
        permitted_operations=frozenset({ProcessingOperation.STORE}),
        evidence_uri="https://example.org/rights-record",
    )


def test_inspect_and_render_preserve_page_provenance(tmp_path):
    source = tmp_path / "source.pdf"
    make_pdf(source)
    processor = PDFPageProcessor()
    inspection = processor.inspect(source)
    pages = processor.render_to_store(
        source, "edition-001", storage_grant(), ContentAddressedStore(tmp_path / "blobs"), dpi=72
    )
    assert inspection.page_count == 2
    assert len(inspection.source_pdf_sha256) == 64
    assert [page.page_number for page in pages] == [1, 2]
    assert all(page.source_pdf_sha256 == inspection.source_pdf_sha256 for page in pages)
    assert all(page.pixel_width == 612 and page.pixel_height == 792 for page in pages)
    assert all(
        (tmp_path / "blobs" / "sha256" / page.image_sha256[:2] / page.image_sha256).is_file()
        for page in pages
    )


def test_render_fails_closed_without_storage_permission(tmp_path):
    source = tmp_path / "source.pdf"
    make_pdf(source, page_count=1)
    denied = RightsGrant(status=RightsStatus.UNKNOWN)
    with pytest.raises(PermissionError):
        PDFPageProcessor().render_to_store(
            source, "edition-001", denied, ContentAddressedStore(tmp_path / "blobs")
        )


def test_inspection_enforces_page_limit(tmp_path):
    source = tmp_path / "source.pdf"
    make_pdf(source, page_count=2)
    with pytest.raises(PDFProcessingError, match="maximum"):
        PDFPageProcessor(max_pages=1).inspect(source)


def test_render_rejects_excessive_dpi(tmp_path):
    source = tmp_path / "source.pdf"
    make_pdf(source, page_count=1)
    with pytest.raises(ValueError, match="dpi"):
        PDFPageProcessor().render_to_store(
            source, "edition-001", storage_grant(),
            ContentAddressedStore(tmp_path / "blobs"), dpi=900
        )


def test_poster_sized_page_is_rendered_at_the_resolution_that_fits(tmp_path):
    """A page too large for the pixel limit at the requested dpi is rendered smaller, not refused."""
    from aki.pdf import fitting_dpi

    assert fitting_dpi(612, 792, 200, 80_000_000) == 200  # a letter page is untouched
    poster = fitting_dpi(3024, 4032, 200, 80_000_000)  # 42x56 inches: 94 MP at 200 dpi
    assert poster is not None and poster < 200
    assert round(3024 * poster / 72) * round(4032 * poster / 72) <= 80_000_000
    assert round(3024 * (poster + 1) / 72) * round(4032 * (poster + 1) / 72) > 80_000_000
    assert fitting_dpi(100_000, 100_000, 200, 80_000_000) is None  # beyond any sense

    document = pymupdf.open()
    document.new_page(width=420, height=600)
    document.new_page(width=3024, height=4032)
    document.save(str(tmp_path / "poster.pdf"))
    processor = PDFPageProcessor(max_pixels_per_page=20_000_000)
    pages = processor.render_to_store(tmp_path / "poster.pdf", "poster", storage_grant(),
                                      ContentAddressedStore(tmp_path / "blobs"), dpi=200)
    assert [p.dpi for p in pages][0] == 200 and pages[1].dpi < 200
    assert pages[1].pixel_width * pages[1].pixel_height <= 20_000_000
