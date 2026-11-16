# Ancient Knowledge Intelligence Engine (AKI)

**11vatedTech** — A provenance-first, multimodal research system for historical texts, manuscripts, diagrams, and material culture.

AKI preserves the distinction between visible source content, proposed transcription, translation, visual observation, and researcher inference. Every derived assertion must remain traceable to a specific source edition and page-level evidence.

> **Status:** Gate 0 and Gate 1 are complete and executed. Gates 2–7 are partial. Read
> [`docs/execution/KNOWN_LIMITATIONS.md`](docs/execution/KNOWN_LIMITATIONS.md) before relying on
> any capability, and [`docs/execution/ARCHITECTURE_TRACEABILITY.md`](docs/execution/ARCHITECTURE_TRACEABILITY.md)
> for the per-requirement status. No OCR, vision, embedding, or language model is installed.

## Implemented and verified

- **Canonical relational model** — works, editions, assets, pages, regions, transcriptions, visual observations, evidence, claims, entities, relationships, jobs, audit events, research cases, with foreign keys, CHECK constraints, and versioned migrations.
- **Immutable content-addressed vault** — SHA-256 identity, atomic writes, read/write verification, vault-relative keys, duplicate detection.
- **Operation-scoped rights gate** — seven states, fourteen independently gateable operations, fail-closed, re-evaluated at execution time rather than at queue time.
- **File preflight** — content sniffing independent of extension, size/page/pixel bounds, encrypted-PDF rejection, executable rejection, quarantine with sidecar.
- **Constrained acquisition** — allowlist, per-host rate limiting, byte ceiling, redirect validation, SSRF refusal, content-type validation, backoff, audit logging, and a hard refusal without a rights decision.
- **Document ingestion** — preflight → archive → page rendering at controlled DPI with renderer provenance → text extraction → evidence rows → indexed chunks.
- **Durable job engine** — leases bound to process identity, heartbeats, exponential backoff with jitter, dead-lettering, cancellation, checkpoints, idempotency keys, capability-aware scheduling, workspace admission locks, and stale-lease recovery.
- **Lexical retrieval** — SQLite FTS5 BM25, exact-phrase mode, metadata filters, reciprocal-rank fusion, index rebuild from canonical rows.
- **Citation resolution** — evidence → work → edition → page → page image → page hash, with rights state and transcription provenance.
- **Claim ledger with independent verification** — a claim reaches `supported` only with a resolvable supporting citation; contradicting evidence forces `contested`; uncited claims fail verification.
- **Grounded research cases** — deterministic evidence assembly producing a dossier where every quotation resolves to a page. It performs no interpretive synthesis, and says so.
- **Backup and restore** — online backup verified by reopening, integrity check, SHA-256, refusal without confirmation, automatic pre-restore safety copy.
- **Hardware doctor** — measured CPU/RAM/GPU/disk/tool state and a derived scheduling budget; unavailable probes reported as `null`.

## Not implemented

OCR/HTR, layout segmentation, page regions, vision or visual-similarity analysis, embeddings or semantic retrieval, knowledge-graph population, HTTP API, desktop application, and any evaluation of retrieval or transcription accuracy. See [`KNOWN_LIMITATIONS.md`](docs/execution/KNOWN_LIMITATIONS.md).

## Catalog

All **257** records of The Holy Word's public catalog were enumerated, imported idempotently, and **acquired**: every edition holds an archived, SHA-256-verified asset — 2.84 GiB across PDF, EPUB-derived ZIP, PNG and JPEG, with zero outstanding failures and zero integrity violations.

Acquisition ran under a **self-attested permission** recorded at [`docs/execution/PERMISSION_ATTESTATION.md`](docs/execution/PERMISSION_ATTESTATION.md), which states plainly that it is an assertion by the claimant rather than an independently verified licence. Export, redistribution, third-party sharing, commercial use and fine-tuning remain denied.

**OCR is wired in.** Ingestion renders each page, uses the embedded text layer where one exists, and falls back to RapidOCR (PP-OCRv4 on ONNX Runtime, offline, CPU) for scanned pages — recording the engine, version, confidence and per-line boxes on every transcription. `aki ocr providers` reports what is available; `aki ingest-batch --ocr` runs the whole corpus in one resumable pass.

See [`ACQUISITION_REPORT.md`](docs/execution/ACQUISITION_REPORT.md) and [`KNOWN_LIMITATIONS.md`](docs/execution/KNOWN_LIMITATIONS.md) for what is and is not yet true of the processed corpus.

## The Felz Reading Room

A local web interface over the archive, built in memory of Joe Felz. It opens only on this
computer (127.0.0.1), because the corpus permission covers internal reading and research
but not sharing.

- **Library and shelves**: all 257 books by shelf, with covers from their first page.
- **Reader**: the page scan beside its transcription, the engine that produced the text,
  page-by-page navigation, search inside the book, and a copyable citation.
- **Search**: exact words (FTS5) and meaning (model2vec `potion-retrieval-32M` when it can be
  downloaded, otherwise an offline LSA model trained on the corpus itself), fused by rank.
- **Index**: 235 curated figures, places, scriptures, traditions and ideas, each linked to
  every page that names it.
- **Plates**: engravings, maps and diagrams found by measuring each scan.
- **Research**: cited, verified evidence gathering (no generated conclusions).
- **The margin**: beside each transcription, the index names on that page and the nearest
  passages in other books, found by likeness of meaning. Withheld where the machine's
  reading of the page is too unsure to build on.
- **Chart**: the index names drawn as a star chart of which names share pages. It shows
  co-occurrence only; no relationship between two names is claimed.
- **Joe** and **The Colophon**: his story, and the archive's provenance, rights and light.

The room is lit by the computer's clock: five palettes (dawn, noon, golden hour, dusk,
night) blended through the day, with a sundial in the head to hold one. The design, its
reasons and its inspection record are in `docs/design/VISUAL-CANON.md`; `scripts/room_walk.py`
re-runs that inspection in a real browser. Fonts (EB Garamond,
IM Fell; SIL OFL 1.1) are bundled, so the room still makes no request beyond this computer.

Double-click **Finish the reading room.cmd**. It runs five steps and stops at the first
one that fails, without opening the reading room:

1. **Move the archive** out of OneDrive to `C:\AKI` (verified copy; nothing deleted; see ADR-017).
2. **Library**: readable copies of the originals, rights-checked and audited (ADR-016).
3. **Finish**: verified backup and vault manifest, then read every remaining book (resumable),
   build meaning search, the index and the plates.
4. **Library sync**: copy books the project's `Library/` lacks, for OneDrive to sync.
5. **Serve**: open the reading room.

Logs, worker logs, backup results and reports are kept under `C:\AKI\logs`. Afterwards,
**Open the reading room.cmd** opens it directly. The same steps by hand:

```powershell
.\scripts\aki.ps1 -Command relocate       # -To D:\Somewhere to choose another folder
.\scripts\aki.ps1 -Command library
.\scripts\aki.ps1 -Command finish         # -Workers 4 to set parallelism
.\scripts\aki.ps1 -Command library-sync
.\scripts\aki.ps1 -Command serve          # http://127.0.0.1:8765/
```

## Development

Requires Python 3.11+.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
ruff check src tests
aki --help
```

## Operator workflow

```powershell
.\scripts\aki.ps1 -Command setup       # create the environment
.\scripts\aki.ps1 -Command doctor      # measure the machine
.\scripts\aki.ps1 -Command init        # create/migrate the database
.\scripts\aki.ps1 -Command inventory   # census the public catalog
.\scripts\aki.ps1 -Command verify      # database + vault integrity
.\scripts\aki.ps1 -Command test        # pytest + ruff
.\scripts\aki.ps1 -Command backup      # verified database backup
```

Then, for an edition you hold rights for:

```powershell
aki rights grant --edition <id> --state cleared_limited `
  --operations store_original,render_pages,ocr_transcribe,internal_search,internal_research `
  --reviewer "<name>" --license-name "<licence>" --notes "<scope>"
aki ingest <id> "D:\Sources\edition.pdf" --dpi 300
aki search "your terms"
aki research "your question" --show-dossier
aki cite <evidence-id>
```

Full detail: [`docs/execution/DEPLOYMENT_RUNBOOK.md`](docs/execution/DEPLOYMENT_RUNBOOK.md).

## Engineering principles

- **Evidence before synthesis:** generated interpretation is never represented as primary-source fact.
- **Page-level provenance:** derived records resolve to edition, page, and optional region.
- **Rights-aware processing:** acquisition, storage, OCR, model processing, display, and redistribution are independent permissions, and unknown permission denies.
- **Reproducible processing:** runs retain input hashes, processor/model versions, configuration, and outputs.
- **Local-first execution:** local inference and storage are first-class; remote services are explicit opt-ins.
- **Uncertainty is data:** confidence, review state, alternative readings, and contradictions are preserved.
- **No silent source mutation:** source files are content-addressed and never overwritten in place.
- **No fabricated capability:** every subsystem is either measured and tested, or listed as absent.

See `docs/ROADMAP.md`, `docs/ARCHITECTURE.md`, `docs/RIGHTS_AND_PROVENANCE.md`, and `docs/execution/`.

AKI is a research and archival tool, not an authority that settles historical disputes. Similar motifs, words, or cosmological descriptions do not by themselves establish contact, borrowing, or shared origin.
