"""Deterministic PDF inspection and page rasterization with provenance."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .blob_store import ContentAddressedStore, StoredBlob
from .domain import ProcessingOperation, RightsGrant
from .rights import require_permission


class PDFProcessingError(RuntimeError):
    """A PDF could not be safely inspected or rendered."""


@dataclass(frozen=True, slots=True)
class PDFInspection:
    source_pdf_sha256: str
    file_size_bytes: int
    page_count: int
    metadata: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class RenderedPage:
    source_id: str
    page_number: int
    page_label: str
    source_pdf_sha256: str
    image_sha256: str
    image_size_bytes: int
    pixel_width: int
    pixel_height: int
    page_width_points: float
    page_height_points: float
    dpi: int
    blob_path: str
    renderer_name: str
    renderer_version: str
    config_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class PDFPageProcessor:
    """Inspect PDFs and store rendered pages as immutable PNG blobs."""

    def __init__(self, max_pages: int = 10_000, max_pixels_per_page: int = 80_000_000) -> None:
        if max_pages < 1 or max_pixels_per_page < 1:
            raise ValueError("resource limits must be positive")
        self.max_pages = max_pages
        self.max_pixels_per_page = max_pixels_per_page

    @staticmethod
    def _pymupdf() -> Any:
        try:
            import pymupdf
        except ImportError as exc:
            raise PDFProcessingError(
                "PDF support is optional; install AKI with the 'documents' extra."
            ) from exc
        return pymupdf

    def inspect(self, pdf_path: str | Path) -> PDFInspection:
        path = Path(pdf_path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise PDFProcessingError(f"Not a regular file: {path}")
        before = path.stat()
        digest, size = _sha256_file(path)
        pymupdf = self._pymupdf()
        try:
            with pymupdf.open(str(path)) as document:
                if document.needs_pass:
                    raise PDFProcessingError(
                        "Encrypted PDF requires a separately reviewed password flow."
                    )
                page_count = document.page_count
                if page_count < 1:
                    raise PDFProcessingError("PDF contains no pages.")
                if page_count > self.max_pages:
                    raise PDFProcessingError(
                        f"PDF has {page_count} pages; configured maximum is {self.max_pages}."
                    )
                metadata = tuple(sorted(
                    (str(key), str(value))
                    for key, value in (document.metadata or {}).items()
                    if value is not None
                ))
        except PDFProcessingError:
            raise
        except Exception as exc:
            raise PDFProcessingError(f"Unable to inspect PDF: {exc}") from exc
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise PDFProcessingError(
                "PDF changed while it was being inspected; retry with a stable file."
            )
        return PDFInspection(digest, size, page_count, metadata)

    def render_to_store(
        self,
        pdf_path: str | Path,
        source_id: str,
        rights: RightsGrant,
        store: ContentAddressedStore,
        dpi: int = 200,
    ) -> list[RenderedPage]:
        if not source_id.strip():
            raise ValueError("source_id must not be empty")
        if not 36 <= dpi <= 600:
            raise ValueError("dpi must be between 36 and 600")
        require_permission(rights, ProcessingOperation.STORE)
        path = Path(pdf_path).expanduser().resolve(strict=True)
        inspection = self.inspect(path)
        pymupdf = self._pymupdf()
        rendered: list[RenderedPage] = []
        config_sha256 = hashlib.sha256(
            f"dpi={dpi};colorspace=RGB;alpha=false;annots=true;format=PNG".encode("utf-8")
        ).hexdigest()
        try:
            with pymupdf.open(str(path)) as document:
                for index in range(document.page_count):
                    page = document.load_page(index)
                    width_points = float(page.rect.width)
                    height_points = float(page.rect.height)
                    pixel_width = round(width_points * dpi / 72)
                    pixel_height = round(height_points * dpi / 72)
                    if pixel_width * pixel_height > self.max_pixels_per_page:
                        raise PDFProcessingError(
                            f"Page {index + 1} would render to {pixel_width}x{pixel_height} pixels, "
                            f"exceeding the {self.max_pixels_per_page:,}-pixel limit."
                        )
                    pixmap = page.get_pixmap(
                        dpi=dpi, colorspace=pymupdf.csRGB, alpha=False, annots=True
                    )
                    png_bytes = pixmap.tobytes("png")
                    blob: StoredBlob = store.put_bytes(png_bytes)
                    label = page.get_label() or str(index + 1)
                    rendered.append(RenderedPage(
                        source_id=source_id,
                        page_number=index + 1,
                        page_label=str(label),
                        source_pdf_sha256=inspection.source_pdf_sha256,
                        image_sha256=blob.sha256,
                        image_size_bytes=blob.size_bytes,
                        pixel_width=pixmap.width,
                        pixel_height=pixmap.height,
                        page_width_points=width_points,
                        page_height_points=height_points,
                        dpi=dpi,
                        blob_path=str(blob.path),
                        renderer_name="PyMuPDF",
                        renderer_version=str(pymupdf.VersionBind),
                        config_sha256=config_sha256,
                    ))
        except PDFProcessingError:
            raise
        except Exception as exc:
            raise PDFProcessingError(f"Unable to render PDF pages: {exc}") from exc
        final_inspection = self.inspect(path)
        if final_inspection.source_pdf_sha256 != inspection.source_pdf_sha256:
            raise PDFProcessingError("PDF content changed during rendering; rendered pages were not manifested.")
        return rendered
