# Known Limitations

> **Update, 13 November 2026.** Several items below are now partly addressed and are
> qualified here rather than deleted:
>
> - **§1, §2:** the 8 non-PDF editions now have an ingestion path (`aki media-ingest`):
>   images become one OCR'd page using the archived original as the page image; the EPUB is
>   laid out by PyMuPDF reflow. `aki finish` resumes the remaining PDF editions.
> - **§3:** plate detection exists as a *measured heuristic* (`aki plates`), recorded as
>   `model_inference` with its metrics. It finds pictures; it does not describe them. No
>   vision-language model is installed.
> - **§4:** a meaning leg exists (`aki semantic build`): model2vec when downloadable, else
>   corpus-trained LSA. The provider that ran is recorded in `model_runs` and shown in the UI.
>   Still unmeasured: no gold query set, so no recall/nDCG figure is claimed.
> - **§7:** the concordance populates `entities`, `entity_aliases` and `mentions` from a curated
>   term list by exact, case-sensitive token matching. Entities stay `candidate`; "often on the
>   same page" is co-occurrence, not a typed relationship. `relationships` remains empty.
> - **§8:** a loopback-only HTTP service and web interface exist (`aki serve`). There is still
>   no desktop shell, auth token, or SSE stream; it refuses to bind beyond 127.0.0.1.
> - **§16:** API and pipeline tests exist (`tests/test_reading_room.py`); there is still no
>   browser end-to-end test or accessibility audit.

> **Update, 16 November 2026: the corpus is read.** All 257 books are open in the reading
> room: 59,907 pages, 58,832 with text (50,112 from the scans' own text layers, 9,505 read by
> OCR, 288 from the EPUB), 755 plates, 87,900 index mentions. §1 below no longer applies. The
> 1560 Geneva Bible and other black-letter books were read by OCR at about 0.78 confidence:
> their page images are exact, their text is approximate.

> **Update, 14 November 2026.**
>
> - **§7:** the reading room now shows co-occurrence two more ways: a chart of index names
>   that share pages, and a margin of near passages in other books. Both are labelled as
>   such. `relationships` is still empty; nothing extracts typed relations or claims.
> - **§6:** unchanged. Research still assembles and cites evidence; it writes no prose.
> - **§2:** pages whose OCR confidence is below 0.80 are marked "read by machine, unsurely"
>   in the reader and are kept out of the margin's comparisons, both as the page being read
>   and as a passage offered from another book. They are still in the search index, so
>   black-letter and rotated pages still produce noisy search hits.
> - **§16:** the interface was inspected as rendered pages in Edge at desktop and phone
>   widths (`docs/design/VISUAL-CANON.md`). There is still no automated browser test, no
>   screen-reader pass and no measured contrast audit of the blended light states.
> - **Rights at read time:** the index, the chart, index entries, book name lists and
>   research findings are counted only over editions that currently hold `internal_research`;
>   the margin also requires `create_embeddings`. Still not re-checked at read time: meaning
>   search results against `create_embeddings` (a withdrawn grant lingers until the index is
>   rebuilt), and the full page image, which needs `internal_search` but not `render_pages`.
>   The colophon shows the single most recent rights decision as if it covered every book.
> - **The light** follows the computer's clock and assumes a northern year. It does not know
>   the reader's latitude, so its sunrise and sunset are approximate by design.
> - **The frontispiece** on the home page is drawn from machine-found plates; some are
>   modern covers or blank leaves.

Every item here is a capability that does **not** exist or is **not** proven. Nothing in this file is implemented; nothing implemented is concealed here.

---

## 1. Corpus acquired but not readable

All 257 catalogued editions now hold an archived, SHA-256-verified asset (2.84 GiB). Acquisition is complete.

**But acquisition is not research readiness.** 0 of 257 editions have been rendered into pages, 0 have been transcribed, and 0 are indexed. The corpus is bytes on disk, not searchable evidence. The only indexed text in the system is a 3-page synthetic fixture.

Root cause: there is no OCR layer (§2), and no batch render/ingest pass has been run over the corpus. Until both happen, a research query over this corpus returns nothing, correctly and visibly.

Rights basis is a **self-attestation** by the claimant, not an independently verified licence. Export, redistribution, third-party sharing, commercial use and fine-tuning remain denied.

## 2. OCR exists; HTR, layout analysis and proofing do not

OCR is implemented: `src/aki/ocr.py` defines a provider contract with two adapters — RapidOCR (PP-OCRv4 via ONNX Runtime, models bundled in the wheel, fully offline) and Tesseract (used only if its binary is present). Ingestion falls back to OCR for any page whose embedded text layer yields fewer than 120 characters, and records the engine name, engine version, mean confidence and per-line boxes on each transcription.

**What this does not do:**

- **No HTR.** Handwritten material is out of scope; the engine will produce low-confidence nonsense rather than refuse. Confidence is stored and visible precisely so this can be filtered, but nothing filters it automatically.
- **No proofing or correction workflow.** Transcriptions are `unreviewed`. There is no human review queue and no diff against a second engine pass.
- **No layout analysis or reading order beyond box geometry.** Multi-column pages come out in detector order, which is usually but not always reading order. Tables and marginalia are not modelled.
- **GPU acceleration measured as ineffective, not merely absent.** `onnxruntime-gpu` and the CUDA 12 / cuDNN 9 DLLs are installed and `CUDAExecutionProvider` is available, but OCR of the same page took 6.16 s on CUDA against 6.20 s on CPU. The cost sits in RapidOCR's CPU-side preprocessing and post-processing, not in inference, so the RTX 5070 Ti buys nothing here. Recorded so nobody repeats the experiment.
- **No language model post-correction.** 19th-century long-s, ligatures and archaic spelling survive into the index as recognised.
- **Eight editions hold image or ZIP assets, not PDFs** (5 JPEG, 3 PNG, 1 ZIP across 8 editions). The PDF pipeline cannot ingest them; they need an image-ingestion path that does not yet exist.

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
