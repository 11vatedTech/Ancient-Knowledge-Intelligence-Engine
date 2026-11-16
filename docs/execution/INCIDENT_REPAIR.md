# Incident Report — Partial Migration and Asset Table Repair

**Date:** 2026-11-12
**Severity:** High (data-shape loss, no source-byte loss)
**Status:** Resolved and verified

---

## What happened

Two acquisitions failed with `UNIQUE constraint failed: assets.storage_key`. The constraint was wrong: `storage_key` is a content-addressed vault path, and identical bytes may legitimately back more than one edition. Removing it required rebuilding the `assets` table, since SQLite cannot drop a constraint in place.

The rebuild migration (`DROP TABLE assets; ALTER TABLE assets_v2 RENAME TO assets`) was written with an explicit `BEGIN IMMEDIATE … COMMIT` inside the script and with `PRAGMA foreign_keys = OFF` set around it.

Two compounding faults:

1. **The coverage view blocked the rebuild.** `v_coverage` selects from `assets`. Dropping the table while the view referenced it made the subsequent rename fail with `error in view v_coverage: no such table: main.assets`.
2. **The failure was not cleanly rolled back.** The partially executed script left `assets` dropped, `assets_v2` populated, and `v_coverage` gone, while `schema_migrations` still reported version 1. My repair step then checked for a stray `assets_v2` (found 256 rows) and dropped it — at which point the only copy of the asset rows was gone.

That third step was my error, not the migration's: I dropped a table whose contents I had not yet preserved.

---

## Blast radius

| Item | Impact |
|---|---|
| Source bytes in `.aki/blobs/` | **None.** 259 objects, 3,048,278,264 bytes, untouched throughout |
| Catalog (`source_records`, `works`, `editions`) | None — 257 / 258 / 258 intact |
| Rights decisions | None — 515 intact |
| `assets` rows | **Lost** (256 rows) |
| `pages` / `transcriptions` / `evidence` / `claims` | Rows intact, but 3 `pages` rows orphaned against the dropped asset id |

---

## Recovery

1. Preserved the damaged database as `.aki/aki.sqlite3.damaged-repair` before touching it.
2. Generated the **exact** v2 `assets` DDL by running the real migration runner against a scratch database, then applied that DDL to the live database — avoiding hand-written schema drift.
3. Recorded `schema_migrations` version 2 with the checksum computed by the same function the runner uses.
4. Repopulated `assets` from the acquisition run's own report (`253` entries with edition id, SHA-256, byte size, media type), verifying for each row that the blob exists **and** that its on-disk size matches the recorded size before inserting.
5. Added the 2 objects fetched in the pre-batch validation run (hashes recorded in the session log) and the synthetic fixture original.
6. Repointed the 3 orphaned `pages` rows to the restored fixture asset id.

Result: 256 assets inserted, 0 errors, 3 foreign-key violations → 0.

---

## Verification

```
aki verify
  assets_checked: 256
  assets_missing_or_corrupt: []
  quick_check: ok
  foreign_key_violations: 0
  schema_version: 2
  ok: true
```

Then the two remaining objects were re-acquired through the normal path, reaching **257 of 257 editions with a verified asset**. Final state: 258 assets, 3,052,062,410 bytes, `aki verify` ok.

---

## Corrective actions taken

- Migration 2 now drops `v_coverage` and any stale `assets_v2` before rebuilding, and recreates the view at the end.
- The runner refuses a migration that leaves foreign-key violations (`PRAGMA foreign_key_check` after any `requires_foreign_keys_off` migration).
- **Not yet done:** the runner does not detect or repair a partially applied script when `executescript` fails mid-way. The explicit-rollback path exists but did not take effect here. This remains an open defect; the safe operational rule is to take `aki backup` before any migration, which is now documented in the runbook.

---

## Honest assessment

No source data was lost. The catalog, rights record and every archived byte survived. What was lost was one table of derived metadata, and it was recoverable only because the acquisition run emitted a machine-readable report containing the hashes. Had that report not existed, the 257 edition→hash associations would have required re-downloading 2.8 GiB.

The lesson is recorded rather than smoothed over: the repair depended on an artefact I happened to keep, not on a guarantee. A future revision should make asset registration transactional with the download and should treat the database as authoritative by backing it up before schema changes.
