# Retrospective audit of the pre-existing Library copies

**Audited:** 2026-11-14 (read-only; no file or database row was changed)
**Scope:** every file under `Library/` that existed before the rights-gated export (ADR-016).
**Per-file record:** `LIBRARY_RETROSPECTIVE_AUDIT.csv` (one row per file and edition).

## Method

For each file: compute its SHA-256 from disk; find the original asset with that digest in
the vault catalogue; for each edition that asset belongs to, read the edition's **latest**
rights decision and whether it permits `store_original`; compare the decision time with the
time the copy was written. A file is recorded as authorized only if it matches an original
byte for byte, every edition it belongs to permits `store_original`, and the permitting
decision predates the copy.

## Result

| Measure | Count |
|---|---|
| Files found | 109 |
| Byte-identical to a vault original | 109 |
| Every edition permits `store_original` | 109 |
| Decision predates the copy | 110 of 110 file–edition rows |
| **Recorded outcome: authorized** | **110 of 110** |
| Not authorized, mismatched, or unmatched | 0 |

Decision timestamps involved: 2026-11-12T16:24:53.215764Z. Copies written between 2026-11-13T22:37:09Z and 2026-11-13T23:13:53Z.

## What this does and does not establish

Every pre-existing copy is an exact copy of an archived original, made after a recorded
decision permitting `store_original` for its edition. That decision is the self-attested
permission of 2026-11-12 (`PERMISSION_ATTESTATION.md`); this audit inherits its evidentiary
status and does not strengthen it. No file was grandfathered: each was checked individually.
If the attestation is later narrowed, `aki library-sync` re-checks every destination file
against current decisions and quarantines those no longer permitted.
