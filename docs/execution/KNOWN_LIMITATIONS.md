# Known Limitations

Every item here is a capability that does **not** exist or is **not** proven. Nothing in this file is implemented; nothing implemented is concealed here.

---

## 1. Corpus acquired but not readable

All 257 catalogued editions now hold an archived, SHA-256-verified asset (2.84 GiB). Acquisition is complete.

**But acquisition is not research readiness.** 0 of 257 editions have been rendered into pages, 0 have been transcribed, and 0 are indexed. The corpus is bytes on disk, not searchable evidence. The only indexed text in the system is a 3-page synthetic fixture.

Root cause: there is no OCR layer (§2), and no batch render/ingest pass has been run over the corpus. Until both happen, a research query over this corpus returns nothing, correctly and visibly.

Rights basis is a **self-attestation** by the claimant, not an independently verified licence. Export, redistribution, third-party sharing, commercial use and fine-tuning remain denied.

## 2. No OCR or HTR — the blocking gap

There is no OCR engine. The only text ingestion path is the PDF's embedded text layer (PyMuPDF). Scanned image-only PDFs produce pages and page images but **no text**, and therefore no searchable evidence.

Most of the acquired corpus is scanned material, so this is the single largest functional gap in the platform: 257 archived editions cannot be read by the engine.

No accuracy figure (CER/WER) is claimed because none was measured.

## 3. No vision / multimodal analysis

No vision-language model is installed and no model was downloaded. There is no illustration, diagram, symbol, table, map, or marginalia analysis. `visual_observations` exists as a schema with evidence-class separation, and nothing produces rows in it.

## 4. No semantic or visual retrieval

No embedding model, no vector index. Retrieval is FTS5 lexical BM25 plus exact-phrase and metadata filters. `hybrid_search` will fuse an externally supplied dense ranking but none is produced. `legs` in every result says `["lexical"]`, truthfully.

## 5. No retrieval-quality measurement

Recall@20 and nDCG@10 are implemented as functions and unit-tested against hand-computed values, but **no curated gold query set exists**, so the specification's 0.90 / 0.80 targets are unmeasured. Do not cite them as achieved.

## 6. No generative synthesis

The research workflow is deterministic evidence assembly. It quotes source text and resolves each quotation to a page. It does not write prose conclusions, does not compare sources, and does not infer historical relationships. That is a design decision, not an omission: a synthesis leg must consume the same evidence packet and pass the same independent verifier.

## 7. No knowledge graph population

Entity, mention, relationship, and claim schemas exist with constraints. Nothing extracts entities or proposes edges. Graph neighborhood retrieval does not exist.

## 8. No HTTP API and no desktop application

The interface is the CLI. There is no FastAPI service, no loopback token auth, no SSE stream, and no PySide6/QML shell. Gates 6's interface deliverables are entirely absent.

## 9. No region-level evidence

Citations resolve to an edition, a page, a page image, and a SHA-256. `polygon` is always `null` in practice because no layout engine produces page regions. Region-level citation is therefore unimplemented in effect, even though the schema and resolver support it.

## 10. Single-process job execution

The job engine is durable and transaction-safe across processes (leases, admission locks, recovery), and is verified by tests. But the worker runs jobs sequentially in one process; there is no background heartbeat thread, no GPU/CPU queue separation at the process level, and no long-running daemon mode.

## 11. Backoff and recovery are time-based, not crash-tested with a real kill

Lease expiry and reclamation are tested by manipulating timestamps, not by killing a process mid-job. The mechanism is correct; a true SIGKILL/`Stop-Process` fault-injection test is absent.

## 12. No migration path exercised beyond version 1

There is one migration. Downgrade paths and multi-version upgrade paths do not exist and were not tested.

## 13. No dependency vulnerability scanning

CI runs lint and tests. It does not run `pip-audit`, licence inventory, or SBOM generation.

## 14. Windows console encoding

CLI output forces UTF-8 on stdout/stderr. Piping output through a legacy-code-page consumer can still mangle non-ASCII source text. Data files are always written UTF-8.

## 15. Catalog metadata is unverified

The 257 titles are uploader filenames in many cases. Authors, dates, languages, publishers, and edition identity are absent or unverified. Work class is `unclassified` for all 257 by design. No historical, chronological, or authorship claim may be derived from this catalog without separate bibliographic work.

## 16. Not tested

No end-to-end browser test, no accessibility audit, no performance/load test, no disk-exhaustion test, no OOM handling test, no multi-account or multi-user scenario (there is no user model).
