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

### Corpus acquisition (batch, live)

```
$ aki acquire run --config .aki/holyword.json --log .aki/acquisition-progress.jsonl
attempted: 255   succeeded: 253   failed: 2   bytes_stored: 2,998,683,774

  ERR  Gospel of Mary Magdalene                UNIQUE constraint failed: assets.storage_key
  ERR  threesevensasto00phelgoog              UNIQUE constraint failed: assets.storage_key

# after migration 2 removed the bad constraint:
$ aki acquire run --config .aki/holyword.json
attempted: 2   succeeded: 2   failed: 0
```

Final acquisition state:

```
$ aki acquire status
editions_with_archived_asset: 257   (of 257 catalogued)
assets_total: 258
bytes_total: 3,052,062,410
by_media_type: pdf 249, jpeg 5, png 3, zip 1

$ aki verify
assets_checked: 256 (pre-final-two)
assets_missing_or_corrupt: []
quick_check: ok   foreign_key_violations: 0   schema_version: 2   ok: true
```

Migration 2 and the asset-table repair are documented in `INCIDENT_REPAIR.md`. The repair re-derived 256 asset rows from the run report, verifying each blob's on-disk size against the recorded size before insert; 0 errors.

### OCR (RapidOCR / ONNX, CPU)

```
$ aki ocr providers
rapidocr   available: true   RapidOCR-PP-OCRv4 / 1.2.3
tesseract  available: false  (binary not on PATH)
```

Measured on a real corpus scan (*The Key to Mental and Physical Power*, 1897), 150 DPI:

```
per-page: 4.10s / 2.12s / 5.67s      chars: 1364 / 4 / 344
mean confidence: 0.894 / 0.567 / 0.852
```

End-to-end ingestion of a real scanned edition (*The Lost Books of The Bible — Gospel
According To Peter*), 5 pages, 1 OCR'd:

```
pages_rendered: 5   pages_ocr: 1   evidence_created: 5   chunks_indexed: 5   elapsed: 6.1s
recognised: "THE LOST GOSPEL ACCORDING TO PETER / Anonymous"  confidence 0.853
search "the" now returns this corpus text, not the fixture
```

Unit coverage: `tests/test_ocr.py` — provider availability reporting, unknown-provider
rejection, scan-without-OCR records empty text, scan-with-OCR records engine identity +
confidence + geometry + evidence, and text-layer pages are never overwritten by OCR.

### OCR accuracy — measured against embedded text layers

`aki ocr evaluate` scores OCR output against the publisher's text layer for the same page.
Reference agreement, not human-verified ground truth; treated as a lower bound.

Same six sampled pages, both resolutions:

```
150 DPI   mean CER 0.2139   median 0.0515   p90 0.2114   mean WER 0.3224   108 s
300 DPI   mean CER 0.2421   median 0.0848   p90 0.2748   mean WER 0.3485   144 s
```

Per page (CER 150 / 300): 0.000/0.000 · 0.211/0.275 · 0.000/0.085 · 0.021/0.021 ·
1.000/1.000 · 0.051/0.072.

**The 1.000 is a false negative.** *Meditations; A New Translation* p5 has a corrupt
embedded layer (`candsiivveacaccarcads meine dane atop dooce te set ees rage`); OCR returned
the correct contents page (`contents title page chronology half title page introduction by
gregory hays ...`) at 0.865 confidence. Scoring it against the broken reference marks a
correct transcription as total failure. This is why the figure is a floor, not an estimate.

10-page sample at 150 DPI: mean CER 0.2232, median 0.0515, p90 0.732 — bimodal. Most pages
near 5%, a minority failing outright. Nothing routes those failures to review yet.

**Confidence is not accuracy.** The failing page carried 0.865 mean confidence while being
completely wrong against its reference (and correct in fact). Confidence cannot substitute
for the review queue.

### Throughput defect found and fixed

The pass was sustaining 0.067 pages/s with the CPU at 69%. Cause: every connection re-ran
`PRAGMA journal_mode = WAL`, which takes a lock, and ingestion opens a connection per page,
so three workers serialised on it. With the pragma applied once at creation and
`synchronous=NORMAL` for batch workers, the same workload sustained **1.7 pages/s** — 25×.

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
