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

257 records were enumerated from The Holy Word's public catalog, imported idempotently, and each assigned one transparent disposition: `review_required` with **no permitted operations**. Zero files were acquired, because the source publishes no licence or permission statement. Catalogued is not acquired. See [`ACQUISITION_REPORT.md`](docs/execution/ACQUISITION_REPORT.md).

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
