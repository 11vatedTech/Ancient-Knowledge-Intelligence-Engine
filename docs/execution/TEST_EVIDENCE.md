# Test Evidence

**Environment:** Windows 11 (10.0.26200) · Python 3.14.6 · pytest 9.2.0 · ruff 0.17.4 · PyMuPDF 2.1
**Commit under test:** branch `architecture/production-enhancement-v1`, based on `041ce760bfa8ff724e766648e786ad22da3a1f16`
**Database schema version:** 1

---

## Automated suite

```
$ .venv/Scripts/python.exe -m pytest -q
95 passed in 10.19s

$ .venv/Scripts/ruff.exe check src tests
All checks passed!
```

### Coverage by area

| Test module | What it proves |
|---|---|
| `test_domain.py` (inherited) | Rights grants are operation-scoped; assertions require page references |
| `test_catalog.py` (inherited) | Stable source identity, idempotent insert, conflict on changed metadata |
| `test_blob_store.py` (inherited) | SHA-256 addressing, dedup, read-time integrity |
| `test_pdf.py` (inherited) | Inspection, renderer provenance, bounded rendering |
| `test_page_store.py` (inherited) | Transactional page manifests, conflict detection |
| `test_manifest_cli.py` (inherited) | JSONL validation and CLI wiring |
| `test_production_domain.py` (inherited) | Production contract invariants |
| `test_canonical_store.py` | Migration idempotency, FK/CHECK enforcement, tamper detection, backup/restore round trip, lease lifecycle, idempotent enqueue, workspace admission, capability deferral, retry→dead-letter, lease expiry recovery, release semantics |
| `test_acquisition_policy.py` | MIME sniffing by content, executable rejection + quarantine, size limits, SSRF refusal (loopback and RFC1918), HTTPS/allowlist enforcement, rights-gated download, audit row written, redirect-block, HTML refusal |
| `test_retrieval_evidence.py` | FTS query quoting of untrusted input, ranked hits with locators, edition filters, phrase adjacency, index rebuild determinism, RRF agreement, Recall@k / nDCG@k maths, citation resolution, unknown-citation error, supported/contested/unverified claim derivation, uncited-claim rejection, research dossier generation |
| `test_inventory.py` | Title normalization, import idempotency, in-snapshot dedup, one disposition per record, catalogued≠acquired accounting, imported records deny all operations, manifest export denies acquisition |
| `test_pipeline.py` | Ingest denied with no rights decision, denied under `review_required`, denied when an operation is missing; full pipeline (3 pages → transcriptions → evidence → index → search → research verify); re-ingest idempotency; vault-relative keys; missing-file error |

### Tests fixed during development (real defects found)

| Defect | Fix |
|---|---|
| `executescript` auto-commit broke migration atomicity | Transaction moved inside the DDL script (ADR-009) |
| `sniff_mime` compared 4 bytes against a 2-byte magic key, so `MZ` executables were not detected | Switched to `startswith` over the magic table |
| Windows-invalid `:` characters in the pre-restore backup filename | Timestamp format changed to `%Y%m%dT%H%M%SZ` |
| Search selected `fts.rank` but projected it as `score` | Projection corrected |
| Page-artifact manifest FK required a legacy `sources` row | Canonical `pages` table is now the single record |
| Absolute paths stored as vault keys | Vault-relative keys (ADR-007) |
| `UnicodeEncodeError` on Windows console for citation arrows | UTF-8 stream reconfiguration (ADR-008) |
| `--database` rejected after a subcommand | Accepted at both levels |

---

## Live execution evidence

### Integrity

```
$ aki verify
{
  "assets_checked": 1,
  "assets_missing_or_corrupt": [],
  "integrity": { "foreign_key_violations": 0, "quick_check": "ok", "schema_version": 1 },
  "ok": true
}
```

### Corpus census

```
$ aki inventory sync --config .aki/holyword.json
advertised_count: 257   observed_count: 257
source_records_added: 257   works_added: 257   editions_added: 257
rights_decisions_added: 257
```

Re-running the import produced `source_records_added: 0`, `source_records_existing: 257`.

### End-to-end ingest

```
$ aki ingest edt-fixture-0001 tests/fixtures/pipeline-fixture.pdf --dpi 300
{
  "pages_rendered": 3, "pages_recorded": 3, "transcriptions_created": 3,
  "evidence_created": 3, "chunks_indexed": 3, "skipped": false,
  "sha256": "a8ec64b9a77bc5855242fb7b225a894ac59b07e59d63dd0a831d397933306dc9"
}
```

### Citation resolution

```
$ aki cite 4ca15597-0e40-4bc9-9bf0-64f6d98d63dd
{
  "resolved": true,
  "work_title": "AKI Pipeline Fixture",
  "edition_title": "Owner-supplied verification fixture",
  "page_index": 0,
  "page_label": "1",
  "page_image_key": "sha256/ff/ff1fc99cd00243951dd34b847a74ee77c64e0e80e5755b4a5c9a1b1faea88494",
  "page_sha256": "ff1fc99cd00243951dd34b847a74ee77c64e0e80e5755b4a5c9a1b1faea88494",
  "source_sha256": "a8ec64b9...",
  "rights_state": "cleared_limited",
  "transcription_engine": "PyMuPDF-text-layer",
  "evidence_class": "source_explicit"
}
```

### Research + independent verification

```
$ aki research "What does the archive preserve and how is a citation resolved?"
case_id: 19a929a6-9a22-462d-9953-4180306fac29
claim_count: 3   hit_count: 3
verification.ok: true   (3 supported, 0 contested, 0 unverified)
dossier: .aki/dossiers/19a929a6-....md
```

### Durable jobs

```
$ aki jobs enqueue-ingest --edition-id edt-fixture-0001 --pdf tests/fixtures/pipeline-fixture.pdf
{ "job_id": "7a40d7b0-...", "created": true }

$ aki jobs run --max-jobs 5
{ "claimed": 1, "succeeded": 1, "failed": 0, "retried": 0, "errors": [],
  "recovered": { "requeued": 0, "dead_lettered": 0, "locks_released": 0 } }
```

### Backup

```
$ aki backup .aki/backups/ledger-backup.sqlite3
{ "byte_size": 884736,
  "sha256": "756787320a3953e11529f65c948a720fb85866f8946c4e85f45762b9f3be9a7e",
  "integrity": { "quick_check": "ok", "foreign_key_violations": 0, "schema_version": 1 } }
```

### Hardware detection (measured, not assumed)

```
GPU   NVIDIA GeForce RTX 5070 Ti Laptop GPU, 11.94 GiB total, 11.67 GiB free, driver 616.87
CPU   24 logical cores
RAM   33.7 GB total, 14.9 GB available
Disk  190 GB free of 986 GB
Tools ollama 0.40.16, docker 29.6.2, git 2.56.0 present; psql absent; tesseract absent
Budget max_concurrent_gpu_jobs=1, gpu_vram_headroom≈10.4 GB, cpu_workers=4
```

---

## What is **not** tested

- OCR / HTR accuracy — no engine installed, no labelled set, **no CER measured**
- Retrieval quality against a gold query set — Recall@20 and nDCG@10 **unmeasured**
- Semantic or visual retrieval — not implemented
- Vision analysis — not implemented
- Region-level locators — no producer of regions
- Knowledge graph traversal — schema only
- Real process-kill recovery (leases are tested by timestamp manipulation, not by killing a worker)
- Disk exhaustion, GPU OOM, concurrent multi-process contention
- HTTP API, desktop UI, accessibility
- PostgreSQL migration paths
- Dependency vulnerability scan

No figure in this document is extrapolated. Where a target exists but was not measured, it is reported as unmeasured rather than estimated.
