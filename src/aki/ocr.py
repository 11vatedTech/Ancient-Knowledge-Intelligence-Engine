"""OCR providers for image-only pages.

The engine is a replaceable component, not a policy decision: every provider returns the
same record shape, and every caller records which engine and version produced the text so
a later, better model can supersede it without touching the archived original.

Nothing here decides *whether* OCR is allowed. That question belongs to the rights layer,
which is evaluated before a page image is ever handed to a provider.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence


class OcrUnavailable(RuntimeError):
    """No usable OCR engine is installed, or the requested one is missing."""


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    confidence: float
    box: tuple[float, float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 6),
            "box": [round(value, 3) for value in self.box],
        }


@dataclass(frozen=True, slots=True)
class OcrPage:
    text: str
    confidence: float | None
    lines: tuple[OcrLine, ...]
    engine_name: str
    engine_version: str
    elapsed_seconds: float = 0.0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def geometry_json(self) -> str:
        """Token geometry stored on the transcription, in reading order."""
        return json.dumps([line.to_dict() for line in self.lines], sort_keys=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_name": self.engine_name,
            "engine_version": self.engine_version,
            "characters": len(self.text),
            "lines": len(self.lines),
            "confidence": None if self.confidence is None else round(self.confidence, 4),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "warnings": list(self.warnings),
        }


class OcrProvider(Protocol):
    """Minimal contract every engine adapter satisfies."""

    name: str
    version: str

    def ocr(self, image_path: str | Path) -> OcrPage: ...


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bbox(points: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    xs = [_float(point[0]) for point in points]
    ys = [_float(point[1]) for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


class RapidOcrProvider:
    """PP-OCR models executed through ONNX Runtime. CPU by default, fully offline.

    Models ship inside the wheel, so the first run needs no network access and no model
    download step.
    """

    name = "RapidOCR-PP-OCRv4"

    def __init__(self) -> None:
        try:
            from importlib.metadata import version as package_version

            self.version = package_version("rapidocr-onnxruntime")
        except Exception:  # noqa: BLE001 - version is provenance, never a hard failure
            self.version = "unknown"
        self._engine: Any | None = None

    @staticmethod
    def available() -> bool:
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except Exception:  # noqa: BLE001 - any import failure means unavailable
            return False
        return True

    def _load(self) -> Any:
        if self._engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except Exception as exc:  # noqa: BLE001
                raise OcrUnavailable(
                    f"RapidOCR could not be imported: {type(exc).__name__}: {exc}"
                ) from exc
            self._engine = RapidOCR()
        return self._engine

    def ocr(self, image_path: str | Path) -> OcrPage:
        import time

        engine = self._load()
        started = time.perf_counter()
        result, _elapse = engine(str(image_path))
        elapsed = time.perf_counter() - started
        lines: list[OcrLine] = []
        for entry in result or ():
            if len(entry) < 3:
                continue
            box, text, score = entry[0], str(entry[1]), _float(entry[2])
            if not text.strip():
                continue
            lines.append(OcrLine(text=text, confidence=score, box=_bbox(box)))
        text = "\n".join(line.text for line in lines)
        confidence = (
            sum(line.confidence for line in lines) / len(lines) if lines else None
        )
        return OcrPage(
            text=text,
            confidence=confidence,
            lines=tuple(lines),
            engine_name=self.name,
            engine_version=str(self.version),
            elapsed_seconds=elapsed,
        )


class TesseractProvider:
    """Tesseract through its CLI, used only when the platform binary is present."""

    name = "Tesseract"

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which("tesseract") or ""
        self.version = self._probe_version() if self.executable else "unavailable"

    @classmethod
    def available(cls) -> bool:
        return shutil.which("tesseract") is not None

    def _probe_version(self) -> str:
        try:
            completed = subprocess.run(
                [self.executable, "--version"],
                capture_output=True, text=True, timeout=20, check=False,
            )
            first = (completed.stdout or completed.stderr).splitlines()
            return first[0].strip() if first else "unknown"
        except Exception:  # noqa: BLE001
            return "unknown"

    def ocr(self, image_path: str | Path) -> OcrPage:
        import time

        if not self.executable:
            raise OcrUnavailable("the tesseract binary is not on PATH")
        started = time.perf_counter()
        completed = subprocess.run(
            [self.executable, str(image_path), "stdout", "--psm", "3", "tsv"],
            capture_output=True, text=True, timeout=600, check=False,
        )
        elapsed = time.perf_counter() - started
        if completed.returncode != 0:
            raise OcrUnavailable(
                f"tesseract exited {completed.returncode}: {(completed.stderr or '').strip()[:200]}"
            )
        words: list[OcrLine] = []
        for row in completed.stdout.splitlines()[1:]:
            columns = row.split("\t")
            if len(columns) < 12 or columns[0] != "5":
                continue
            text = columns[11].strip()
            if not text:
                continue
            left, top, width, height = (int(columns[i] or 0) for i in (6, 7, 8, 9))
            words.append(OcrLine(
                text=text,
                confidence=_float(columns[10]) / 100.0,
                box=(float(left), float(top), float(left + width), float(top + height)),
            ))
        text = " ".join(word.text for word in words)
        confidence = (
            sum(word.confidence for word in words) / len(words) if words else None
        )
        return OcrPage(
            text=text,
            confidence=confidence,
            lines=tuple(words),
            engine_name=self.name,
            engine_version=str(self.version),
            elapsed_seconds=elapsed,
        )


PROVIDERS: dict[str, Any] = {
    "rapidocr": RapidOcrProvider,
    "tesseract": TesseractProvider,
}


def provider_status() -> list[dict[str, Any]]:
    """Report every known engine and whether it can actually run here."""
    report: list[dict[str, Any]] = []
    for key, factory in PROVIDERS.items():
        available = bool(factory.available())
        entry: dict[str, Any] = {"provider": key, "available": available}
        if available:
            try:
                instance = factory()
                entry["engine_name"] = instance.name
                entry["engine_version"] = instance.version
            except Exception as exc:  # noqa: BLE001 - presence is never a promise
                entry["available"] = False
                entry["error"] = f"{type(exc).__name__}: {exc}"
        report.append(entry)
    return report


def resolve_provider(name: str | None = None) -> OcrProvider:
    """Return a working provider, or raise with an actionable message.

    Selection order is deterministic: an explicitly requested provider wins, otherwise
    RapidOCR, otherwise Tesseract. No provider is ever silently substituted after a page
    has been processed.
    """
    if name is not None:
        factory = PROVIDERS.get(name)
        if factory is None:
            raise OcrUnavailable(
                f"unknown OCR provider {name!r}; known providers: {sorted(PROVIDERS)}"
            )
        if not factory.available():
            raise OcrUnavailable(f"OCR provider {name!r} is not available on this machine")
        return factory()  # type: ignore[no-any-return]
    for key in ("rapidocr", "tesseract"):
        factory = PROVIDERS[key]
        if factory.available():
            return factory()  # type: ignore[no-any-return]
    raise OcrUnavailable(
        "no OCR engine is available; install 'rapidocr-onnxruntime' or put tesseract on PATH"
    )
