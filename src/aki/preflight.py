"""File preflight: content sniffing, bounded parsing and quarantine.

Imported files are untrusted input. Nothing is opened by a document parser until its
declared type agrees with its actual bytes, its size is within policy, and structural
hazards (encryption, executables, archive bombs) have been ruled out.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_PAGES = 5_000
DEFAULT_MAX_PIXELS = 80_000_000

_EXECUTABLE_MAGIC = {
    b"MZ": "windows PE executable",
    b"\x7fELF": "ELF executable",
    b"\xca\xfe\xba\xbe": "Mach-O executable",
    b"\xcf\xfa\xed\xfe": "Mach-O executable",
}


@dataclass(frozen=True, slots=True)
class PreflightLimits:
    max_bytes: int = DEFAULT_MAX_BYTES
    max_pages: int = DEFAULT_MAX_PAGES
    max_pixels_per_page: int = DEFAULT_MAX_PIXELS

    def __post_init__(self) -> None:
        if self.max_bytes < 1 or self.max_pages < 1 or self.max_pixels_per_page < 1:
            raise ValueError("preflight limits must be positive")


@dataclass(frozen=True, slots=True)
class PreflightReport:
    sha256: str
    byte_size: int
    detected_mime: str
    declared_suffix: str
    status: str
    page_count: int | None = None
    encrypted: bool = False
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    parser: str | None = None
    parser_version: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class Quarantine:
    """Move a rejected file into a quarantine directory with a sidecar reason file."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def hold(self, path: str | Path, report: PreflightReport, *, reason: str) -> Path:
        source = Path(path).expanduser().resolve()
        target = self.root / f"{report.sha256}{source.suffix.lower()}"
        if not target.exists():
            shutil.copy2(source, target)
        sidecar = target.with_suffix(target.suffix + ".reason.json")
        sidecar.write_text(
            json.dumps({"reason": reason, "report": report.to_dict()}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return target


def sniff_mime(head: bytes) -> str | None:
    """Identify a file by its leading bytes, not by its name."""
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"BM"):
        return "image/bmp"
    if head.startswith(b"PK\x03\x04"):
        return "application/zip"
    if head.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "application/x-7z-compressed"
    if head.startswith(b"Rar!\x1a\x07"):
        return "application/vnd.rar"
    if head.startswith(b"SQLite format 3\x00"):
        return "application/vnd.sqlite3"
    if any(head.startswith(magic) for magic in _EXECUTABLE_MAGIC):
        return "application/x-executable"
    if not head:
        return None
    sample = head[:4096]
    printable = sum(1 for byte in sample if 32 <= byte < 127 or byte in (9, 10, 13))
    if printable / len(sample) > 0.95:
        return "text/plain"
    return "application/octet-stream"


def _sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def preflight(path: str | Path, limits: PreflightLimits | None = None) -> PreflightReport:
    """Inspect a file and decide whether it may enter the processing pipeline."""
    policy = limits or PreflightLimits()
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    warnings: list[str] = []
    errors: list[str] = []
    size = source.stat().st_size
    if size == 0:
        errors.append("file is empty")
    if size > policy.max_bytes:
        errors.append(f"file exceeds maximum size of {policy.max_bytes} bytes")

    with source.open("rb") as stream:
        head = stream.read(16 * 1024)
    digest, measured = _sha256_and_size(source)
    mime = sniff_mime(head) or "application/octet-stream"
    suffix = source.suffix.lower()

    if mime == "application/x-executable":
        label = next(
            (name for magic, name in _EXECUTABLE_MAGIC.items() if head.startswith(magic)),
            "unknown",
        )
        errors.append(f"executable content rejected ({label})")
    if mime in {"application/zip", "application/gzip", "application/x-7z-compressed",
                "application/vnd.rar"} and suffix not in {".epub", ".zip"}:
        warnings.append(f"archive container ({mime}) requires bounded extraction before parsing")

    expected = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".txt": "text/plain",
    }.get(suffix)
    if expected and mime != expected and mime != "application/octet-stream":
        warnings.append(f"extension {suffix} declares {expected} but content is {mime}")

    page_count: int | None = None
    encrypted = False
    parser: str | None = None
    parser_version: str | None = None
    details: dict[str, object] = {}

    if mime == "application/pdf":
        try:
            import pymupdf
        except ImportError:
            warnings.append("PyMuPDF unavailable; structural PDF checks were skipped")
        else:
            parser, parser_version = "PyMuPDF", str(pymupdf.VersionBind)
            try:
                with pymupdf.open(str(source)) as document:
                    encrypted = bool(document.needs_pass)
                    page_count = int(document.page_count)
                    details["pdf_metadata_keys"] = sorted(
                        str(key) for key in (document.metadata or {})
                    )
                    details["has_javascript"] = bool(document.is_form_pdf) if hasattr(
                        document, "is_form_pdf"
                    ) else None
                    if document.is_encrypted:
                        encrypted = True
            except Exception as exc:  # malformed structure
                errors.append(f"PDF structure could not be parsed: {exc}")
            if encrypted:
                errors.append("encrypted PDF requires a reviewed password flow")
            if page_count == 0:
                errors.append("PDF contains no pages")
            if page_count is not None and page_count > policy.max_pages:
                errors.append(
                    f"PDF has {page_count} pages; policy maximum is {policy.max_pages}"
                )
    elif mime.startswith("image/"):
        try:
            from PIL import Image
        except ImportError:
            warnings.append("Pillow unavailable; image dimension checks were skipped")
        else:
            try:
                with Image.open(source) as image:
                    width, height = image.size
                    details["pixel_width"] = width
                    details["pixel_height"] = height
                    if width * height > policy.max_pixels_per_page:
                        errors.append(
                            f"image is {width}x{height}; policy maximum is "
                            f"{policy.max_pixels_per_page} pixels"
                        )
            except Exception as exc:
                errors.append(f"image could not be decoded: {exc}")

    if size != measured:
        errors.append("file size changed while being read")

    status = "rejected" if errors else "passed"
    if errors and any("executable" in error or "encrypted" in error for error in errors):
        status = "quarantined"
    return PreflightReport(
        sha256=digest,
        byte_size=measured,
        detected_mime=mime,
        declared_suffix=suffix,
        status=status,
        page_count=page_count,
        encrypted=encrypted,
        warnings=tuple(warnings),
        errors=tuple(errors),
        parser=parser,
        parser_version=parser_version,
        details=details,
    )
