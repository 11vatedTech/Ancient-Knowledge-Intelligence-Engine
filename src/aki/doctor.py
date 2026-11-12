"""Runtime environment detection and resource budgeting.

The doctor reports what it can actually measure and marks everything else as
unavailable. It never infers hardware from a specification document, and it never
substitutes a plausible number for a failed probe.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GPU_SAFETY_RESERVE_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class GpuInfo:
    name: str
    total_vram_bytes: int
    free_vram_bytes: int | None
    driver_version: str | None


@dataclass(frozen=True, slots=True)
class DoctorReport:
    os_name: str
    os_version: str
    python_version: str
    python_executable: str
    cpu_count: int
    total_ram_bytes: int | None
    available_ram_bytes: int | None
    disk_free_bytes: int | None
    disk_total_bytes: int | None
    gpus: tuple[GpuInfo, ...] = ()
    tools: dict[str, str | None] = field(default_factory=dict)
    libraries: dict[str, str | None] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["gpus"] = [asdict(gpu) for gpu in self.gpus]
        return payload

    def budget(self) -> dict[str, Any]:
        """Conservative scheduling budget derived from measurements only."""
        heavy_jobs = 1
        vram_headroom = None
        if self.gpus and self.gpus[0].free_vram_bytes is not None:
            vram_headroom = self.gpus[0].free_vram_bytes - GPU_SAFETY_RESERVE_BYTES
            if vram_headroom < 0:
                vram_headroom = 0
                heavy_jobs = 0
        ram_headroom = None
        if self.available_ram_bytes is not None:
            ram_headroom = max(self.available_ram_bytes - 4 * 1024 * 1024 * 1024, 0)
        return {
            "max_concurrent_gpu_jobs": heavy_jobs,
            "cpu_workers": max(1, min(4, self.cpu_count - 2)),
            "gpu_vram_headroom_bytes": vram_headroom,
            "ram_headroom_bytes": ram_headroom,
            "single_flight_models": True,
            "derived_from_measurements": bool(self.gpus or self.available_ram_bytes),
        }


def _run(command: list[str], *, timeout: float = 15.0) -> str | None:
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _nvidia_smi() -> tuple[GpuInfo, ...]:
    output = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.free,driver_version",
        "--format=csv,noheader,nounits",
    ])
    if not output:
        return ()
    gpus: list[GpuInfo] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            total = int(float(parts[1])) * 1024 * 1024
            free = int(float(parts[2])) * 1024 * 1024
        except ValueError:
            continue
        gpus.append(GpuInfo(parts[0], total, free, parts[3] or None))
    return tuple(gpus)


def _memory() -> tuple[int | None, int | None]:
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        psutil = None
    if psutil is not None:
        memory = psutil.virtual_memory()
        return int(memory.total), int(memory.available)
    if sys.platform == "win32":
        output = _run(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory", "/value"])
        if output:
            for line in output.splitlines():
                if "=" in line:
                    _, _, value = line.partition("=")
                    if value.strip().isdigit():
                        total = int(value.strip())
                        return total, None
    return None, None


def _tool_version(command: list[str]) -> str | None:
    output = _run(command)
    if output is None:
        return None
    return output.splitlines()[0].strip()


def _library_version(module: str) -> str | None:
    try:
        imported = __import__(module)
    except ImportError:
        return None
    return str(getattr(imported, "__version__", getattr(imported, "VersionBind", "unknown")))


def run_doctor(*, data_root: str | Path | None = None) -> DoctorReport:
    warnings: list[str] = []
    root = Path(data_root).expanduser().resolve() if data_root else Path.cwd()
    disk_free = disk_total = None
    probe = root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
        disk_free, disk_total = int(usage.free), int(usage.total)
    except OSError as exc:
        warnings.append(f"disk usage unavailable for {probe}: {exc}")

    total_ram, available_ram = _memory()
    if total_ram is None:
        warnings.append("system RAM could not be measured; run budgets are conservative")

    gpus = _nvidia_smi()
    if not gpus:
        warnings.append("no NVIDIA GPU detected via nvidia-smi; GPU workloads unavailable")

    tools = {
        "git": _tool_version(["git", "--version"]),
        "ollama": _tool_version(["ollama", "--version"]),
        "docker": _tool_version(["docker", "--version"]),
        "tesseract": _tool_version(["tesseract", "--version"]),
        "psql": _tool_version(["psql", "--version"]),
        "nvidia_smi": _tool_version(["nvidia-smi", "--version"]),
    }
    libraries = {
        "pymupdf": _library_version("pymupdf"),
        "pillow": _library_version("PIL"),
        "psutil": _library_version("psutil"),
        "httpx": _library_version("httpx"),
    }

    return DoctorReport(
        os_name=platform.system(),
        os_version=platform.version(),
        python_version=platform.python_version(),
        python_executable=sys.executable,
        cpu_count=os.cpu_count() or 1,
        total_ram_bytes=total_ram,
        available_ram_bytes=available_ram,
        disk_free_bytes=disk_free,
        disk_total_bytes=disk_total,
        gpus=gpus,
        tools=tools,
        libraries=libraries,
        warnings=tuple(warnings),
    )


def doctor_json(*, data_root: str | Path | None = None) -> str:
    report = run_doctor(data_root=data_root)
    return json.dumps(
        {"environment": report.to_dict(), "budget": report.budget()},
        indent=2,
        sort_keys=True,
    )
