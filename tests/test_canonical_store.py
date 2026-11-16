"""Canonical schema, migration integrity, backup/restore and durable job semantics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aki.db import SCHEMA_VERSION, Database, SchemaError, initialize
from aki.jobs import JobError, JobQueue, JobState, LeaseLost, backoff_delay


@pytest.fixture()
def database(tmp_path):
    db = Database(tmp_path / "aki.sqlite3")
    initialize(db.path)
    return db


def test_migration_is_idempotent(database):
    report = initialize(database.path)
    assert report["applied"] == []
    assert report["version_after"] == SCHEMA_VERSION
    assert database.integrity_check()["quick_check"] == "ok"


def test_foreign_keys_and_constraints(database):
    with pytest.raises(Exception):
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO editions (id, work_id, edition_title, edition_type, created_at)"
                " VALUES ('e1','missing-work','t','unknown','now')"
            )
    with pytest.raises(Exception):
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO works (id, canonical_title, normalized_title, work_class, created_at)"
                " VALUES ('w1','T','t','not-a-class','now')"
            )


def test_checksum_tampering_is_detected(database):
    with database.transaction() as connection:
        connection.execute("UPDATE schema_migrations SET checksum='0' WHERE version=1")
    with pytest.raises(SchemaError):
        database.migrate()


def test_backup_and_restore_round_trip(database, tmp_path):
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO works (id, canonical_title, normalized_title, work_class, created_at)"
            " VALUES ('w1','Title','title','ancient_primary','now')"
        )
    backup = database.backup(tmp_path / "backup.sqlite3")
    assert backup["integrity"]["quick_check"] == "ok"
    with database.transaction() as connection:
        connection.execute("DELETE FROM works")
    assert database.coverage()["works"] == 0
    database.restore(backup["path"], confirm=True)
    assert database.coverage()["works"] == 1


def test_restore_requires_confirmation(database, tmp_path):
    backup = database.backup(tmp_path / "b.sqlite3")
    with pytest.raises(ValueError):
        database.restore(backup["path"])


def test_enqueue_is_idempotent(database):
    queue = JobQueue(database)
    first, created = queue.enqueue("ingest.pdf", "edition", "e1", idempotency_key="k")
    second, created_again = queue.enqueue("ingest.pdf", "edition", "e1", idempotency_key="k")
    assert first == second
    assert created is True
    assert created_again is False


def test_claim_lease_and_stale_owner(database):
    queue = JobQueue(database)
    job_id, _ = queue.enqueue("ingest.pdf", "edition", "e1")
    job = queue.claim("worker-a")
    assert job is not None and job.state is JobState.LEASED and job.attempt_count == 1
    queue.start(job.id, "worker-a")
    with pytest.raises(LeaseLost):
        queue.complete(job.id, "worker-b")
    queue.complete(job.id, "worker-a")
    assert queue.get(job.id).state is JobState.SUCCEEDED


def test_workspace_admission_blocks_second_worker(database):
    queue = JobQueue(database)
    queue.enqueue("ingest.pdf", "edition", "e1", workspace="edition:e1")
    queue.enqueue("ingest.pdf", "edition", "e2", workspace="edition:e1")
    first = queue.claim("worker-a")
    assert first is not None
    assert queue.claim("worker-b") is None
    queue.complete(first.id, "worker-a")
    second = queue.claim("worker-b")
    assert second is not None
    assert second.id != first.id


def test_capability_requirement_defers_job(database):
    queue = JobQueue(database)
    queue.enqueue("vision.analyze", "page", "p1", {"requires": ["gpu"]})
    assert queue.claim("cpu-worker", capabilities=frozenset()) is None
    claimed = queue.claim("gpu-worker", capabilities=frozenset({"gpu"}))
    assert claimed is not None


def test_retry_then_dead_letter(database):
    queue = JobQueue(database)
    job_id, _ = queue.enqueue("ingest.pdf", "edition", "e1", max_attempts=2)
    queue.claim("w")
    job = queue.fail(job_id, "w", "transient", now=datetime.now(timezone.utc))
    assert job.state is JobState.RETRY_WAIT
    assert job.last_error is not None
    queue.claim("w", now=datetime.now(timezone.utc) + timedelta(seconds=600))
    final = queue.fail(job_id, "w", "again", now=datetime.now(timezone.utc) + timedelta(seconds=900))
    assert final.state is JobState.FAILED


def test_non_retryable_failure_dead_letters_immediately(database):
    queue = JobQueue(database)
    job_id, _ = queue.enqueue("ingest.pdf", "edition", "e1", max_attempts=5)
    queue.claim("w")
    job = queue.fail(job_id, "w", "permanent", retryable=False)
    assert job.state is JobState.FAILED


def test_expired_lease_is_reclaimed(database):
    queue = JobQueue(database)
    job_id, _ = queue.enqueue("ingest.pdf", "edition", "e1", max_attempts=3)
    queue.claim("worker-a", lease_seconds=1)
    past = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with database.transaction() as connection:
        connection.execute("UPDATE jobs SET lease_expires_at=? WHERE id=?", (past, job_id))
    summary = queue.recover()
    assert summary["requeued"] == 1
    assert queue.get(job_id).state is JobState.RETRY_WAIT


def test_exhausted_lease_dead_letters_on_recovery(database):
    queue = JobQueue(database)
    job_id, _ = queue.enqueue("ingest.pdf", "edition", "e1", max_attempts=1)
    queue.claim("worker-a", lease_seconds=1)
    past = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with database.transaction() as connection:
        connection.execute("UPDATE jobs SET lease_expires_at=? WHERE id=?", (past, job_id))
    assert queue.recover()["dead_lettered"] == 1
    assert queue.get(job_id).state is JobState.FAILED


def test_release_does_not_consume_attempt(database):
    queue = JobQueue(database)
    job_id, _ = queue.enqueue("ingest.pdf", "edition", "e1")
    queue.claim("w")
    queue.release(job_id, "w")
    job = queue.get(job_id)
    assert job.state is JobState.QUEUED
    assert job.attempt_count == 0


def test_cancel_terminal_job_is_rejected(database):
    queue = JobQueue(database)
    job_id, _ = queue.enqueue("ingest.pdf", "edition", "e1")
    queue.claim("w")
    queue.complete(job_id, "w")
    assert queue.cancel(job_id) is False


def test_unknown_job_raises(database):
    with pytest.raises(JobError):
        JobQueue(database).get("nope")


def test_backoff_is_bounded_and_deterministic():
    assert backoff_delay(1) == 2.0
    assert backoff_delay(20) == 300.0
    assert backoff_delay(3, "abc") == backoff_delay(3, "abc")
