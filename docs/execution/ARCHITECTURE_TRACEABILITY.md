# Architecture Traceability Matrix

Vocabulary: **Verified** (code + executed evidence), **Implemented / unverified** (code exists, acceptance not demonstrated), **Partial**, **Not implemented**, **Blocked** (external dependency), **N/A**.

Sources reconciled: `AKI_Production_Architecture_Specification_v1.0.pdf` (AKI-ARCH-001), `docs/AKI_ARCH_001_INTEGRATION_DELTA.md`, `docs/IMPLEMENTATION_BACKLOG.md`, `docs/RIGHTS_AND_PROVENANCE.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/FREEBUFF_LOCAL_HANDOFF.md`, and the source tree at `041ce76`.

---

## Domain and identity (spec §7.2)

| Requirement | Status | Evidence / gap |
|---|---|---|
| Work ≠ Edition ≠ Asset ≠ Page separated | **Verified** | `db.py` schema; 257 works + 257 editions imported from one listing surface |
| SourceRecord holds catalog identity, not a rights grant | **Verified** | `inventory.py`; rights stored in separate `rights_decisions` rows |
| Stable, idempotent import identity | **Verified** | `test_import_is_idempotent`; live re-import added 0 |
| Page → asset → edition → work lineage | **Verified** | `resolve_citation` traverses the full chain |
| Region polygons in normalized page coordinates | **Partial** | `page_regions` table + `Region` contract exist; **no layout engine produces regions yet** |
| Attribution confidence, period, place, script fields | **Partial** | columns exist; only title/author populated for the renamed records |

## Rights (spec §4.2)

| Requirement | Status | Evidence / gap |
|---|---|---|
| Seven rights states | **Verified** | `RightsDecisionState`; DB CHECK constraint |
| 14 independently gateable operations | **Verified** | `RightsOperation` enum, stored in `scope` JSON |
| Fail-closed on unknown/review/expired | **Verified** | `RightsDecision.permits`; `test_ingest_denied_*` |
| Rights re-evaluated at execution, not queue time | **Verified** | `ingest._require` reads the latest decision on every run |
| Legacy `public_domain`/`licensed` not widened silently | **Verified** | new `rights_decisions` table; legacy `sources` table untouched |
| Permission expiry / review-due enforcement | **Partial** | `review_after` enforced in the in-memory contract; no scheduling job re-checks stored `review_due_at` |
| Rights revocation propagation | **Not implemented** | no re-scan of completed artifacts on decision change |
| Licence evidence text capture | **Partial** | `evidence_uri`, `license_name`, `license_url`, `notes` stored; no excerpt field |

## Archive and integrity (spec §5.1–5.2)

| Requirement | Status | Evidence / gap |
|---|---|---|
| Content-addressed immutable vault | **Verified** | `blob_store.py`; `put_bytes`/`put_path`; atomic link/replace |
| SHA-256 verification on read and write | **Verified** | `test_*` in `test_blob_store.py`; `aki verify` on live DB |
| Duplicate detection | **Verified** | digest path is the identity; re-ingest `skipped=True` |
| Vault-relative storage keys (portable archives) | **Verified** | `ingest._relative_key`; `test_vault_keys_are_portable` |
| Preflight: content sniffing | **Verified** | `preflight.sniff_mime`; 8 signature families |
| Preflight: size / page / pixel bounds | **Verified** | `test_preflight_rejects_oversize` |
| Preflight: encrypted PDF rejection | **Verified** | preflight + `PDFPageProcessor.inspect` |
| Preflight: executable rejection + quarantine | **Verified** | `test_preflight_rejects_executable`, `test_quarantine_writes_sidecar` |
| Archive-bomb / decompression bounds | **Partial** | container formats warned, not extracted; no archive extraction implemented |
| Orphan-blob reconciliation | **Not implemented** | no GC pass |
| Backup / restore with verification | **Verified** | `test_backup_and_restore_round_trip`; live backup 884,736 bytes, integrity ok |

## Processing pipeline (spec §5.4, §3.11)

| Requirement | Status | Evidence / gap |
|---|---|---|
| Page rendering at controlled DPI with renderer provenance | **Verified** | 3 pages @200 DPI, PyMuPDF 2.1 recorded |
| Page-level explicit states, no silent omission | **Verified** | `pages.processing_state`; coverage counters |
| Text extraction with engine identity | **Verified** | PyMuPDF text layer stored as `normalized`; engine + version recorded |
| OCR / HTR adapters | **Not implemented** | no OCR engine installed; `ocr_transcribe` gate exists |
| Layout segmentation, reading order | **Not implemented** | — |
| Region crops and coordinates | **Not implemented** | schema ready, no producer |
| Transcription revision history (`supersedes_id`) | **Partial** | column exists; no correction workflow |
| Human review queue | **Not implemented** | `review_state` column exists only |
| Translation / transliteration layers | **Partial** | `layer` enum includes them; no provider |
| Reprocess with a better model without replacing the original | **Verified (mechanism)** | derived rows are separate; originals immutable. Requires OCR to exercise fully |
| Durable job queue with leases | **Verified** | `test_claim_lease_and_stale_owner` etc. |
| Exponential backoff + dead letter | **Verified** | `test_retry_then_dead_letter` |
| Workspace admission (no concurrent mutation) | **Verified** | `test_workspace_admission_blocks_second_worker` |
| Crash recovery via lease expiry | **Verified** | `test_expired_lease_is_reclaimed` |
| Idempotency keys | **Verified** | `test_enqueue_is_idempotent` |
| Checkpointing | **Verified** | `checkpoint()` stores arbitrary progress; no automatic resume logic in handlers yet |
| Capability-aware scheduling (GPU deferral) | **Verified** | `test_capability_requirement_defers_job` |
| Multi-process worker with heartbeat loop | **Partial** | heartbeat is per-job, not a background thread |

## Retrieval and evidence (spec §8)

| Requirement | Status | Evidence / gap |
|---|---|---|
| Exact phrase search | **Verified** | `mode="phrase"`; `test_phrase_mode_requires_adjacency` |
| Lexical BM25 full-text search | **Verified** | FTS5 `bm25()`; live query returned ranked page hits |
| Metadata filters (edition/work/language/class) | **Verified** | `SearchFilters` |
| Reciprocal-rank fusion | **Verified** | `test_reciprocal_rank_fusion_prefers_agreement` |
| Dense / semantic retrieval | **Not implemented** | no embedding model installed; `hybrid_search` accepts an external dense leg and reports `legs` honestly |
| Visual similarity retrieval | **Not implemented** | — |
| Index rebuildable from canonical rows | **Verified** | `test_rebuild_index_reproduces_results` |
| Recall@20 ≥ 0.90 / nDCG@10 ≥ 0.80 | **Unmeasured** | metric functions implemented and unit-tested; **no curated gold set exists**, so no claim is made |
| Citation resolves to edition + page + image + hash | **Verified** | `aki cite` resolved a live evidence row |
| Region-level locator | **Partial** | polygon returned when present; no producer |
| Source-explicit claim requires resolvable evidence | **Verified** | `assess_claim`; `test_supported_claim_requires_resolvable_support` |
| Contradiction downgrades a claim to contested | **Verified** | `test_contradicting_evidence_marks_claim_contested` |
| Independent verification pass | **Verified** | `verify_claims` is deterministic and separate from claim creation |
| Uncited claim fails verification | **Verified** | `test_uncited_claim_fails_verification` |
| Research dossier with citations | **Verified** | `aki research` produced 3 supported claims + dossier file |
| Generative synthesis (LLM) | **Not implemented** | deliberately deterministic; no model installed |

## Knowledge graph (spec §7.5)

| Requirement | Status | Evidence / gap |
|---|---|---|
| Entities, aliases, mentions | **Partial** | schema + constraints; no extraction |
| Typed relationships with evidence links | **Partial** | schema + `relationship_evidence`; no producer |
| Predicate taxonomy incl. `similar_to` ≠ `influenced_by` | **Partial** | documented; not enforced by a validator |
| Graph neighborhood retrieval | **Not implemented** | — |
| Entity resolution | **Not implemented** | — |

## API, desktop, operations (spec §10, §11)

| Requirement | Status | Evidence / gap |
|---|---|---|
| Versioned local HTTP API | **Not implemented** | no FastAPI layer; CLI is the current interface |
| Loopback binding + mutation token | **Not implemented** | — |
| Desktop application (PySide6/QML) | **Not implemented** | — |
| Library / Page Lab / Graph / Research / Review / System workspaces | **Not implemented** | — |
| CLI administration | **Verified** | 20+ commands exercised live |
| Hardware doctor with honest unavailable values | **Verified** | `psql`/`tesseract` returned null, not fabricated |
| Resource budget from measurements | **Verified** | 1 GPU job, ~10.4 GB VRAM headroom, 4 CPU workers |
| Structured telemetry | **Partial** | coverage counters + doctor + job stats; no time-series |
| Offline operation after assets present | **Verified** | all query paths are local SQLite/FTS5 |

## Evaluation (spec §8.4)

| Requirement | Status | Evidence / gap |
|---|---|---|
| Metric implementations (Recall@k, nDCG@k, CER) | **Partial** | Recall/nDCG implemented and tested; **CER not implemented** |
| Versioned gold corpus | **Not implemented** | blocks any retrieval-quality claim |
| OCR CER ≤ 2% on clean print | **Unmeasured** | no OCR engine, no labelled subset |
| Locator accuracy ≥ 99.5% | **Unmeasured** | deterministic citation resolution is 100% on the fixture; that is not the same measure |
| Adversarial / fault-injection suite | **Partial** | corrupt-file, oversize, executable, redirect, SSRF, rights-denial, stale-lease tests exist; disk-exhaustion, OOM, and concurrent-process kill tests do not |

## Security (spec §7)

| Requirement | Status | Evidence / gap |
|---|---|---|
| SSRF protection | **Verified** | `assert_public_address`; loopback/private/link-local refused |
| Redirect validation | **Verified** | `_RedirectGuard` |
| URL allowlisting + rate limiting | **Verified** | policy + `RateLimiter` |
| SQL parameterization | **Verified** | all queries parameterized; FTS input tokenized and quoted |
| Untrusted document treated as data | **Verified** | no execution path from document content |
| Secrets kept out of the repo | **Verified** | catalog credentials in gitignored `.aki/`; no key in source |
| Dependency vulnerability scanning | **Not implemented** | no `pip-audit` in CI |
| Local service authentication | **N/A** | no HTTP service exists yet |

---

## Conflicts resolved

| Conflict | Resolution |
|---|---|
| Spec requires PostgreSQL; environment has no PostgreSQL server | SQLite is the implemented backend; schema mirrors the normative DDL so a PostgreSQL port is translation, not redesign. Recorded in `DECISION_LOG.md` ADR-002. |
| Spec requires Qdrant; no embedding model installed | No vector store was introduced. Lexical retrieval is real and verified; dense retrieval is absent rather than faked. ADR-003. |
| Backlog demands OCR; no model approved for download | OCR is not implemented. The rights/lineage/evidence plumbing around it is. Gate 2 is not claimed complete. |
| Spec targets 256 records; site banner renders both 256 and 257 | Enumerated count (257) is authoritative; both figures recorded. |
| Spec names "desktop application"; environment is a headless CLI session | Desktop not attempted rather than stubbed. |
