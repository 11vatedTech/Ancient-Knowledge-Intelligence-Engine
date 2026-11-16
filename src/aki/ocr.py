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


def _cap_threads() -> None:
    """Limit this process's OCR threads to ``AKI_OCR_THREADS`` when it is set.

    ONNX Runtime sizes its thread pool to every core by default. One process is fine like
    that; several side by side each start a full pool and spend their time contending
    (measured on 24 cores, 14 November 2026: six uncapped workers did not finish 42 pages in
    ten minutes; the same six capped at four threads finished them in two). RapidOCR 1.2
    exposes no thread setting, so the session options it builds are wrapped here.
    """
    import os

    try:
        threads = int(os.environ.get("AKI_OCR_THREADS", "0"))
    except ValueError:
        threads = 0
    if threads <= 0:
        return
    try:
        import onnxruntime
        import rapidocr_onnxruntime.utils as rapid_utils
    except Exception:  # noqa: BLE001 - without the library there is nothing to cap
        return
    base = getattr(rapid_utils.SessionOptions, "_aki_base", onnxruntime.SessionOptions)

    def capped() -> Any:
        options = base()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        return options

    capped._aki_base = base  # type: ignore[attr-defined]
    rapid_utils.SessionOptions = capped
    try:
        import cv2

        cv2.setNumThreads(threads)
    except Exception:  # noqa: BLE001 - OpenCV threading is an optimisation only
        pass


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
            _cap_threads()
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


_POOL_ENGINE: Any = None


def _pool_start(factory: Any) -> None:
    """Runs once in each pool process: one engine, one thread, kept for the process's life."""
    import os

    global _POOL_ENGINE
    from .power import full_speed

    full_speed()
    os.environ["AKI_OCR_THREADS"] = "1"
    os.environ.pop("AKI_OCR_PROCESSES", None)
    _POOL_ENGINE = factory()


def _pool_read(image_path: str) -> OcrPage:
    return _POOL_ENGINE.ocr(image_path)  # type: ignore[no-any-return]


class PooledOcrProvider:
    """Reads several pages of one book at the same time, each in its own process.

    A dense page costs minutes of processor time, and one engine reads one page at a time
    however many threads it is given, so a long scanned book used to hold a single core's
    worth of progress while the rest of the machine idled. Here the pages that need reading
    are handed out ahead of time to ``processes`` single-threaded engines; ``ocr`` then
    returns each page's result in the order ingestion asks for it, so everything written to
    the database is exactly what the serial path would have written.
    """

    def __init__(self, provider_key: str, processes: int) -> None:
        from concurrent.futures import Future, ProcessPoolExecutor

        factory = PROVIDERS[provider_key]
        inner = factory()
        self.name = inner.name
        self.version = getattr(inner, "version", "unknown")
        self.processes = processes
        self._pool = ProcessPoolExecutor(processes, initializer=_pool_start,
                                         initargs=(factory,))
        self._ahead: dict[str, Future[OcrPage]] = {}

    def prefetch(self, image_paths: Sequence[str | Path]) -> None:
        for image_path in image_paths:
            key = str(image_path)
            if key not in self._ahead:
                self._ahead[key] = self._pool.submit(_pool_read, key)

    def ocr(self, image_path: str | Path) -> OcrPage:
        ahead = self._ahead.pop(str(image_path), None)
        if ahead is None:
            ahead = self._pool.submit(_pool_read, str(image_path))
        try:
            return ahead.result()
        except Exception:  # noqa: BLE001 - one retry, then the page's failure stands
            # A page can fail for reasons that pass: on 15 November 2026 two books failed
            # with "ONNXRuntime inference failed" at the same moment the computer ran short
            # of memory. One more attempt costs a page; giving up costs the whole book.
            return self._pool.submit(_pool_read, str(image_path)).result()

    def close(self) -> None:
        for pending in self._ahead.values():
            pending.cancel()
        self._ahead.clear()
        self._pool.shutdown(wait=True, cancel_futures=True)


def _pooled(key: str, provider: OcrProvider) -> OcrProvider:
    """Wrap ``provider`` in a pool when ``AKI_OCR_PROCESSES`` asks for more than one."""
    import os

    try:
        processes = int(os.environ.get("AKI_OCR_PROCESSES", "0"))
    except ValueError:
        processes = 0
    if processes > 1:
        return PooledOcrProvider(key, processes)  # type: ignore[return-value]
    return provider


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
        return _pooled(name, factory())
    for key in ("rapidocr", "tesseract"):
        factory = PROVIDERS[key]
        if factory.available():
            return _pooled(key, factory())
    raise OcrUnavailable(
        "no OCR engine is available; install 'rapidocr-onnxruntime' or put tesseract on PATH"
    )
