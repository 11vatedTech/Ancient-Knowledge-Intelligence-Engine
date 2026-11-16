"""Durable worker runtime: lease, execute, heartbeat, recover.

Handlers are the only place where side effects happen. Each handler must be idempotent on
its subject and must record completed work before returning, so a reclaimed job resumes
rather than repeats.
"""
from __future__ import annotations

import json
import os
import platform
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .db import Database, utcnow
from .ingest import ingest_pdf
from .jobs import Job, JobQueue, LeaseLost
from .search import rebuild_index

Handler = Callable[[Database, Job, Mapping[str, Any]], dict[str, Any]]


class HandlerError(RuntimeError):
    """A job handler failed; the message is recorded on the job."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def default_owner() -> str:
    host = socket.gethostname()
    return f"{host}:{platform.system()}:pid{os.getpid()}"


def _handle_ingest_pdf(database: Database, job: Job, payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {"pdf_path", "edition_id", "blob_root"}
    missing = required - payload.keys()
    if missing:
        raise HandlerError(f"ingest.pdf missing payload fields: {sorted(missing)}", retryable=False)
    result = ingest_pdf(
        database,
        payload["blob_root"],
        payload["pdf_path"],
        edition_id=str(payload["edition_id"]),
        dpi=int(payload.get("dpi", 300)),
    )
    return result.to_dict()


def _handle_index_rebuild(database: Database, job: Job, payload: Mapping[str, Any]) -> dict[str, Any]:
    return rebuild_index(database)


HANDLERS: dict[str, Handler] = {
    "ingest.pdf": _handle_ingest_pdf,
    "index.rebuild": _handle_index_rebuild,
}


@dataclass
class WorkerReport:
    owner: str
    claimed: int = 0
    succeeded: int = 0
    failed: int = 0
    retried: int = 0
    skipped: int = 0
    recovered: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "claimed": self.claimed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "retried": self.retried,
            "skipped": self.skipped,
            "recovered": self.recovered,
            "errors": self.errors,
        }


def run_worker(
    database: Database,
    *,
    owner: str | None = None,
    max_jobs: int = 10,
    lease_seconds: int = 120,
    capabilities: frozenset[str] | None = None,
    poll_seconds: float = 0.0,
    max_idle_cycles: int = 1,
    handlers: Mapping[str, Handler] | None = None,
) -> WorkerReport:
    """Drain eligible jobs. Recovers stale leases before claiming anything."""
    identity = owner or default_owner()
    queue = JobQueue(database)
    registry = dict(handlers or HANDLERS)
    report = WorkerReport(identity)
    report.recovered = queue.recover()

    idle = 0
    while report.claimed < max_jobs:
        job = queue.claim(
            identity,
            lease_seconds=lease_seconds,
            capabilities=capabilities if capabilities is not None else frozenset(),
        )
        if job is None:
            idle += 1
            if idle >= max_idle_cycles or poll_seconds <= 0:
                break
            time.sleep(poll_seconds)
            continue
        idle = 0
        report.claimed += 1
        handler = registry.get(job.job_type)
        if handler is None:
            queue.fail(job.id, identity, f"no handler registered for {job.job_type!r}",
                       retryable=False)
            report.failed += 1
            report.errors.append(f"unhandled job type {job.job_type!r}")
            continue
        try:
            queue.start(job.id, identity)
            queue.heartbeat(job.id, identity, lease_seconds=lease_seconds)
            result = handler(database, job, job.payload)
            queue.complete(job.id, identity, result=result)
            report.succeeded += 1
        except LeaseLost as exc:
            report.errors.append(str(exc))
            continue
        except HandlerError as exc:
            updated = queue.fail(job.id, identity, str(exc), retryable=exc.retryable)
            report.failed += 1 if updated.state.value == "failed" else 0
            report.retried += 1 if updated.state.value == "retry_wait" else 0
            report.errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            updated = queue.fail(job.id, identity, f"{type(exc).__name__}: {exc}")
            report.failed += 1 if updated.state.value == "failed" else 0
            report.retried += 1 if updated.state.value == "retry_wait" else 0
            report.errors.append(f"{type(exc).__name__}: {exc}")
    return report


def enqueue_ingest(
    database: Database,
    *,
    edition_id: str,
    pdf_path: str,
    blob_root: str,
    dpi: int = 300,
    priority: int = 50,
) -> tuple[str, bool]:
    """Queue an ingestion job, deduplicated by edition + content-addressable inputs."""
    queue = JobQueue(database)
    key = f"ingest.pdf:{edition_id}:{pdf_path}:{dpi}"
    return queue.enqueue(
        "ingest.pdf",
        "edition",
        edition_id,
        {"pdf_path": pdf_path, "edition_id": edition_id, "blob_root": blob_root, "dpi": dpi},
        priority=priority,
        idempotency_key=key,
        workspace=f"edition:{edition_id}",
    )


def system_snapshot() -> dict[str, Any]:
    return {
        "captured_at": utcnow(),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "pid": os.getpid(),
    }


def describe_jobs(database: Database) -> str:
    queue = JobQueue(database)
    return json.dumps({"stats": queue.stats(), "system": system_snapshot()}, indent=2, sort_keys=True)
