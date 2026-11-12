"""Durable job engine: leases, heartbeats, retries, dead-lettering, recovery.

Design constraints from AKI-ARCH-001 §3.2.1 and §7.3:

* A job is only executed by a worker holding a live lease bound to its process identity.
* Two workers can never mutate the same declared workspace concurrently (admission locks).
* A crashed worker's lease expires and the job becomes eligible again, without duplicating
  committed side effects: executors record idempotency keys and checkpoints transactionally.
* Exhausted retry budgets terminate in ``failed`` (dead letter) and are never silently retried.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Mapping

from .db import Database, new_id, parse_instant, utcnow

BACKOFF_BASE_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 300.0
DEFAULT_LEASE_SECONDS = 120


class JobState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    CANCELLED = "cancelled"
    HELD = "held"


TERMINAL_STATES = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED})


class JobError(RuntimeError):
    """A job transition was rejected."""


class LeaseLost(JobError):
    """This worker no longer owns the job lease."""


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    job_type: str
    subject_type: str
    subject_id: str
    payload: Mapping[str, Any]
    state: JobState
    priority: int
    attempt_count: int
    max_attempts: int
    available_at: str
    lease_owner: str | None
    lease_expires_at: str | None
    checkpoint: Mapping[str, Any]
    last_error: str | None
    created_at: str
    completed_at: str | None

    @classmethod
    def from_row(cls, row: Any) -> "Job":
        return cls(
            id=row["id"],
            job_type=row["job_type"],
            subject_type=row["subject_type"],
            subject_id=row["subject_id"],
            payload=json.loads(row["payload"]),
            state=JobState(row["state"]),
            priority=int(row["priority"]),
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
            available_at=row["available_at"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            checkpoint=json.loads(row["checkpoint"]),
            last_error=row["last_error"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )


def backoff_delay(attempt: int, job_id: str = "") -> float:
    """Deterministic exponential backoff with a stable per-job jitter component."""
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    base = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_CAP_SECONDS)
    if not job_id:
        return base
    digest = hashlib.sha256(job_id.encode()).digest()
    jitter = 1.0 + (digest[0] / 255.0) * 0.25  # 0-25% spread, deterministic
    return min(base * jitter, BACKOFF_CAP_SECONDS)


def _shift(instant: str, seconds: float) -> str:
    moment = parse_instant(instant) + timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _now_value(now: datetime | None) -> str:
    if now is None:
        return utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class JobQueue:
    """Transactional job store. One instance per process; safe across processes."""

    def __init__(self, database: Database) -> None:
        self.database = database
        database.require_current()

    # -- production ------------------------------------------------------------------
    def enqueue(
        self,
        job_type: str,
        subject_type: str,
        subject_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        priority: int = 50,
        max_attempts: int = 3,
        delay_seconds: float = 0.0,
        idempotency_key: str | None = None,
        workspace: str | None = None,
    ) -> tuple[str, bool]:
        """Insert a job. Returns (job_id, created). An existing idempotency key is reused."""
        if not job_type.strip() or not subject_id.strip():
            raise ValueError("job_type and subject_id are required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        body = dict(payload or {})
        if workspace is not None:
            body["workspace"] = workspace
        now = utcnow()
        job_id = new_id()
        with self.database.transaction() as connection:
            if idempotency_key is not None:
                existing = connection.execute(
                    "SELECT id FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if existing is not None:
                    return str(existing["id"]), False
            connection.execute(
                "INSERT INTO jobs (id, job_type, subject_type, subject_id, payload, state,"
                " priority, attempt_count, max_attempts, available_at, checkpoint,"
                " idempotency_key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id, job_type, subject_type, subject_id,
                    json.dumps(body, sort_keys=True), JobState.QUEUED.value,
                    int(priority), 0, int(max_attempts),
                    _shift(now, delay_seconds), "{}", idempotency_key, now,
                ),
            )
        return job_id, True

    def claim(
        self,
        owner: str,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        capabilities: frozenset[str] | set[str] | None = None,
        now: datetime | None = None,
    ) -> Job | None:
        """Atomically lease the next eligible job, honoring workspace admission locks."""
        if not owner.strip():
            raise ValueError("owner is required")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be >= 1")
        stamp = _now_value(now)
        caps = frozenset(capabilities or ())
        with self.database.transaction() as connection:
            candidates = connection.execute(
                "SELECT * FROM jobs WHERE state IN (?,?) AND available_at <= ?"
                " ORDER BY priority ASC, available_at ASC, created_at ASC LIMIT 50",
                (JobState.QUEUED.value, JobState.RETRY_WAIT.value, stamp),
            ).fetchall()
            for row in candidates:
                job = Job.from_row(row)
                required = frozenset(job.payload.get("requires", []) or [])
                if not required.issubset(caps):
                    continue
                workspace = job.payload.get("workspace")
                if workspace is not None:
                    held = connection.execute(
                        "SELECT owner, expires_at FROM job_locks WHERE lock_key = ?",
                        (str(workspace),),
                    ).fetchone()
                    if held is not None and held["expires_at"] > stamp:
                        continue
                expires = _shift(stamp, lease_seconds)
                connection.execute(
                    "UPDATE jobs SET state=?, lease_owner=?, lease_expires_at=?,"
                    " heartbeat_at=?, attempt_count=attempt_count+1 WHERE id=?",
                    (JobState.LEASED.value, owner, expires, stamp, job.id),
                )
                if workspace is not None:
                    connection.execute(
                        "INSERT INTO job_locks (lock_key, job_id, owner, acquired_at, expires_at)"
                        " VALUES (?,?,?,?,?) ON CONFLICT(lock_key) DO UPDATE SET"
                        " job_id=excluded.job_id, owner=excluded.owner,"
                        " acquired_at=excluded.acquired_at, expires_at=excluded.expires_at",
                        (str(workspace), job.id, owner, stamp, expires),
                    )
                updated = connection.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
                return Job.from_row(updated)
        return None

    # -- execution transitions -------------------------------------------------------
    def start(self, job_id: str, owner: str) -> Job:
        with self.database.transaction() as connection:
            self._require_lease(connection, job_id, owner)
            connection.execute(
                "UPDATE jobs SET state=? WHERE id=?", (JobState.RUNNING.value, job_id)
            )
        return self.get(job_id)

    def heartbeat(self, job_id: str, owner: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None:
        stamp = utcnow()
        with self.database.transaction() as connection:
            self._require_lease(connection, job_id, owner)
            connection.execute(
                "UPDATE jobs SET heartbeat_at=?, lease_expires_at=? WHERE id=?",
                (stamp, _shift(stamp, lease_seconds), job_id),
            )
            connection.execute(
                "UPDATE job_locks SET expires_at=? WHERE job_id=?",
                (_shift(stamp, lease_seconds), job_id),
            )

    def checkpoint(self, job_id: str, owner: str, data: Mapping[str, Any]) -> None:
        with self.database.transaction() as connection:
            self._require_lease(connection, job_id, owner)
            connection.execute(
                "UPDATE jobs SET checkpoint=? WHERE id=?",
                (json.dumps(dict(data), sort_keys=True), job_id),
            )

    def complete(self, job_id: str, owner: str, *, result: Mapping[str, Any] | None = None) -> None:
        stamp = utcnow()
        with self.database.transaction() as connection:
            self._require_lease(connection, job_id, owner)
            checkpoint = {"result": dict(result or {})}
            connection.execute(
                "UPDATE jobs SET state=?, completed_at=?, lease_owner=NULL,"
                " lease_expires_at=NULL, checkpoint=?, last_error=NULL WHERE id=?",
                (JobState.SUCCEEDED.value, stamp, json.dumps(checkpoint, sort_keys=True), job_id),
            )
            connection.execute("DELETE FROM job_locks WHERE job_id=?", (job_id,))

    def fail(
        self,
        job_id: str,
        owner: str,
        error: str,
        *,
        retryable: bool = True,
        now: datetime | None = None,
    ) -> Job:
        """Record a failure; schedule a backoff retry or dead-letter the job."""
        stamp = _now_value(now)
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise JobError(f"unknown job {job_id}")
            job = Job.from_row(row)
            if job.state in TERMINAL_STATES:
                return job
            if job.lease_owner not in (None, owner):
                raise LeaseLost(f"job {job_id} is leased by {job.lease_owner}")
            exhausted = job.attempt_count >= job.max_attempts
            payload = json.dumps({"message": error, "attempt": job.attempt_count}, sort_keys=True)
            if not retryable or exhausted:
                connection.execute(
                    "UPDATE jobs SET state=?, last_error=?, completed_at=?, lease_owner=NULL,"
                    " lease_expires_at=NULL WHERE id=?",
                    (JobState.FAILED.value, payload, stamp, job_id),
                )
                connection.execute("DELETE FROM job_locks WHERE job_id=?", (job_id,))
            else:
                delay = backoff_delay(job.attempt_count, job_id)
                connection.execute(
                    "UPDATE jobs SET state=?, last_error=?, available_at=?, lease_owner=NULL,"
                    " lease_expires_at=NULL WHERE id=?",
                    (JobState.RETRY_WAIT.value, payload, _shift(stamp, delay), job_id),
                )
                connection.execute("DELETE FROM job_locks WHERE job_id=?", (job_id,))
        return self.get(job_id)

    def release(self, job_id: str, owner: str, *, delay_seconds: float = 0.0) -> None:
        """Return a claimed job to the queue without consuming an attempt."""
        with self.database.transaction() as connection:
            self._require_lease(connection, job_id, owner)
            connection.execute(
                "UPDATE jobs SET state=?, lease_owner=NULL, lease_expires_at=NULL,"
                " available_at=?, attempt_count=MAX(attempt_count - 1, 0) WHERE id=?",
                (JobState.QUEUED.value, _shift(utcnow(), delay_seconds), job_id),
            )
            connection.execute("DELETE FROM job_locks WHERE job_id=?", (job_id,))

    def cancel(self, job_id: str) -> bool:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return False
            if JobState(row["state"]) in TERMINAL_STATES:
                return False
            connection.execute(
                "UPDATE jobs SET state=?, completed_at=? WHERE id=?",
                (JobState.CANCELLED.value, utcnow(), job_id),
            )
            connection.execute("DELETE FROM job_locks WHERE job_id=?", (job_id,))
        return True

    # -- recovery --------------------------------------------------------------------
    def recover(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Reclaim expired leases and expired admission locks. Safe to run repeatedly."""
        stamp = _now_value(now)
        requeued = dead = 0
        with self.database.transaction() as connection:
            stale = connection.execute(
                "SELECT * FROM jobs WHERE state IN (?,?) AND lease_expires_at IS NOT NULL"
                " AND lease_expires_at <= ?",
                (JobState.LEASED.value, JobState.RUNNING.value, stamp),
            ).fetchall()
            for row in stale:
                job = Job.from_row(row)
                exhausted = job.attempt_count >= job.max_attempts
                error = json.dumps(
                    {
                        "message": "lease expired; worker presumed terminated",
                        "attempt": job.attempt_count,
                        "previous_owner": job.lease_owner,
                    },
                    sort_keys=True,
                )
                if exhausted:
                    connection.execute(
                        "UPDATE jobs SET state=?, last_error=?, completed_at=?,"
                        " lease_owner=NULL, lease_expires_at=NULL WHERE id=?",
                        (JobState.FAILED.value, error, stamp, job.id),
                    )
                    dead += 1
                else:
                    connection.execute(
                        "UPDATE jobs SET state=?, last_error=?, available_at=?,"
                        " lease_owner=NULL, lease_expires_at=NULL WHERE id=?",
                        (
                            JobState.RETRY_WAIT.value, error,
                            _shift(stamp, backoff_delay(job.attempt_count, job.id)), job.id,
                        ),
                    )
                    requeued += 1
                connection.execute("DELETE FROM job_locks WHERE job_id=?", (job.id,))
            released_locks = connection.execute(
                "DELETE FROM job_locks WHERE expires_at <= ?", (stamp,)
            ).rowcount
        return {"requeued": requeued, "dead_lettered": dead, "locks_released": released_locks}

    # -- reads -----------------------------------------------------------------------
    def get(self, job_id: str) -> Job:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise JobError(f"unknown job {job_id}")
        return Job.from_row(row)

    def list_jobs(self, *, state: JobState | None = None, limit: int = 100) -> list[Job]:
        query = "SELECT * FROM jobs"
        parameters: tuple[Any, ...] = ()
        if state is not None:
            query += " WHERE state=?"
            parameters = (state.value,)
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters += (limit,)
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [Job.from_row(row) for row in rows]

    def stats(self) -> dict[str, int]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS n FROM jobs GROUP BY state"
            ).fetchall()
        return {row["state"]: int(row["n"]) for row in rows}

    def _require_lease(self, connection: Any, job_id: str, owner: str) -> None:
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise JobError(f"unknown job {job_id}")
        job = Job.from_row(row)
        if job.lease_owner != owner:
            raise LeaseLost(f"job {job_id} is not leased by {owner!r}")
        if job.lease_expires_at is not None and job.lease_expires_at <= utcnow():
            raise LeaseLost(f"lease on job {job_id} expired")
