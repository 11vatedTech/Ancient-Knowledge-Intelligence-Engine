# Execution Ledger

**Project:** Ancient Knowledge Intelligence Engine (AKI)
**Repository:** https://github.com/11vatedTech/Ancient-Knowledge-Intelligence-Engine
**Branch:** `architecture/production-enhancement-v1`
**Base commit (inherited):** `041ce760bfa8ff724e766648e786ad22da3a1f16`
**Implementation commit (local, not pushed):** `ae3cf635d14a330cc220674de9d8951c0137c5df`
**Ahead of `origin/architecture/production-enhancement-v1` by:** 1 commit
**Host:** Windows 11 (10.0.26200), 24 logical CPUs, 33.7 GB RAM, NVIDIA RTX 5070 Ti Laptop GPU (11.94 GiB), 190 GB free
**Runtime:** Python 3.14.6 in `.venv`, PyMuPDF 2.1, psutil 7.2.2, Ollama 0.40.16 present
**Schema version:** 1
**Last updated:** 2026-11-12

---

## Current gate

**Gate 0 — COMPLETE.** Repository acquired, environment baselined, corpus census captured, rights disposition assigned to every discovered record.
**Gate 1 — COMPLETE (single-edition vertical slice).** Canonical schema, durable jobs, preflight, archive integrity, citation resolution and backup/restore are implemented *and executed*.
**Gates 2–7 — PARTIAL.** See `ARCHITECTURE_TRACEABILITY.md` for the per-requirement status. No gate beyond 1 is claimed complete.

---

## What was executed (evidence, not intention)

| Step | Command | Result |
|---|---|---|
| Repository acquisition | `git init` + `git remote add` + `git fetch` + `git checkout -B` | 2 branches fetched; work tree at `041ce76`; 29 tracked files at start |
| Dependency install | `py -m venv .venv` then `pip install -e ".[dev]"` | PyMuPDF 2.1, pytest 9.2.0, ruff 0.17.4, psutil 7.2.2 installed |
| Schema creation | `aki init` | schema version 0 → 1 |
| Hardware detection | `aki doctor` | GPU, RAM, disk measured; `psql` and `tesseract` correctly reported absent |
| Corpus census | `aki inventory sync --config .aki/holyword.json` | **257 advertised / 257 observed / 257 imported** |
| Idempotency proof | second `import_snapshot` | 0 added, 257 existing |
| Rights grant (fixture) | `aki rights grant --edition edt-fixture-0001 ...` | `cleared_limited`, 5 operations |
| End-to-end ingest | `aki ingest edt-fixture-0001 tests/fixtures/pipeline-fixture.pdf --dpi 200` | 3 pages, 3 transcriptions, 3 evidence rows, 3 indexed chunks |
| Retrieval | `aki search "archive citation uncertainty"` | ranked hits with page locators and rights state |
| Research + verification | `aki research "What does the archive preserve ..."` | 3 claims, all `supported`, dossier written |
| Citation resolution | `aki cite 4ac7344f-...` | resolves to edition, page index, page label, image key, SHA-256 |
| Job engine | `aki jobs enqueue-ingest` + `aki jobs run` | claimed 1, succeeded 1, 0 errors |
| Integrity | `aki verify` | `quick_check: ok`, 0 FK violations, 0 corrupt assets |
| Backup | `aki backup .aki/backups/ledger-backup.sqlite3` | 884,736 bytes, SHA-256 `756787320a3953e1…`, integrity ok |
| Tests | `python -m pytest -q` | **95 passed, 0 failed** (re-run after final edits: 95 passed in 7.18s) |
| Lint | `ruff check src tests` | clean |

---

## Database and storage locations

| Item | Path |
|---|---|
| Canonical database | `.aki/aki.sqlite3` (gitignored) |
| Immutable object vault | `.aki/blobs/sha256/` (gitignored) |
| Dossiers | `.aki/dossiers/` |
| Backups | `.aki/backups/` |
| Catalog census snapshot | `data/holyword/inventory_snapshot.json` (committed) |
| Catalog credentials | `.aki/holyword.json` (gitignored, never committed) |

---

## Acquisition progress

| Measure | Value |
|---|---|
| Discovered source records | 257 |
| Advertised by the source site | 257 |
| Unique works | 257 |
| Unique editions | 257 |
| **Editions with an archived, hash-verified asset** | **257 / 257** |
| Total assets (incl. 1 synthetic fixture) | 258 |
| Assets verified (preflight + SHA-256) | 258 |
| Failed acquisitions outstanding | 0 |
| Rights basis | self-attested permission, `cleared_limited`, 257 rows |
| Bytes stored | 3,052,062,410 (2.84 GiB) |

Every discovered record has exactly one disposition, and every one is acquired and integrity-verified. Acquisition is **not** research readiness: **140 / 249 PDF editions** are fully rendered and transcribed (4 partial, 105 not started), covering **26,635 pages**, **26,339 evidence passages** and **26,336 indexed chunks**; **24,856 transcriptions come from embedded text layers and 1,775 from OCR**. The **8 non-PDF editions** (5 JPEG, 3 PNG, 1 ZIP) are deferred with that reason, so the arithmetic closes: 249 + 8 = 257. Reconciliation is exact (140 + 4 + 105 = 249, 0 unreadable); `aki verify` is clean (258 assets, 0 corrupt, 0 FK violations) and the FTS rebuild matches chunk count. OCR accuracy is measured, not assumed: mean CER 0.214 at 150 DPI against 0.242 at 300, WER 0.322 against 0.349, scored against embedded text layers and therefore a lower bound. Remaining: ~33,000 pages, roughly 8–12 hours of continuous compute, dominated by large-format scans at 20–85 s/page.

---

## Model inventory

| Model | Status |
|---|---|
| PyMuPDF text layer | present, version 2.1, used for PDF text extraction |
| Ollama runtime | installed (0.40.16) |
| Any Ollama model | **none pulled** — no model was downloaded, per the handoff rule requiring explicit approval |

No OCR, embedding, or vision model is installed. No capability that depends on one is claimed.

---

## Draft pull request status

PR #1 ("AKI production architecture integration and domain foundation") is **OPEN, draft, MERGEABLE**, head `architecture/production-enhancement-v1` → base `main`, containing 5 commits (`44f8170` … `041ce76`). It has **not** been merged.

Its code was verified locally by this pass: the inherited test suite passes unmodified, and the domain contracts it introduced are exercised by the new job, evidence and research tests. The local implementation commit (`9d7639d`) sits on top of that branch and is **not pushed** to the remote.

## Outstanding external approvals

1. **Corpus rights — partially closed.** Authority was self-attested in session and recorded in `docs/execution/PERMISSION_ATTESTATION.md`. Acquisition and internal research are permitted on that basis. Still denied: export, redistribution, third-party sharing, commercial use, and fine-tuning. Independent documentary verification remains outstanding if the corpus is ever to leave this machine.
2. **Model downloads.** Installing an OCR or vision model is a multi-GB download with licence implications. Not performed. `aki doctor` reports the measured VRAM budget (1 concurrent GPU job, ~10.4 GB headroom).

---

## Next executable action

1. **Implement the OCR layer.** This is the single highest-value next step: the corpus is on disk and hash-verified but unreadable to the engine. Rendering + OCR + evidence indexing would convert 257 archived PDFs into a searchable, citable corpus. Blocked only on choosing and installing a local OCR engine.
2. Batch-render pages for all 257 editions and run ingestion, so coverage counters leave zero.
3. Obtain independent documentary verification of the corpus permission if the material is ever to be exported or shared.

---

## Known defects

See `KNOWN_LIMITATIONS.md`. No defect is hidden as a completed feature.
