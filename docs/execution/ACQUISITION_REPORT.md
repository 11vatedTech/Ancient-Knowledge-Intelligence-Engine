# Acquisition Report — The Holy Word

**Source:** https://theholyword.net/
**Census and acquisition date:** 2026-11-12
**Method:** anonymous read of the public catalog listing the site serves to unauthenticated visitors, enumerated by row range; objects then fetched one at a time with a 1 request/second floor, size ceiling, SSRF guard and content sniffing.
**Snapshot:** `data/holyword/inventory_snapshot.json` (kept on the owner's computer; not published)
**Rights basis:** `docs/execution/PERMISSION_ATTESTATION_PUBLIC.md` (self-attested, see below)

---

## Result

| Measure | Value |
|---|---|
| Records advertised by the site | 257 |
| Records independently observed | 257 |
| Unique works | 257 |
| Unique editions | 257 |
| **Editions with an archived, hash-verified asset** | **257 of 257** |
| Total assets in vault (incl. 1 synthetic fixture) | 258 |
| Bytes stored | 3,052,062,410 (2.84 GiB) |
| Corpus bytes (excluding fixture) | 3,052,056,793 |
| Failed acquisitions outstanding | **0** |
| Distinct SHA-256 digests | 258 |

Media composition: 249 `application/pdf`, 5 `image/jpeg`, 3 `image/png`, 1 `application/zip`.

Every asset passed content sniffing, size bounds, and SHA-256 verification on write, and re-verifies on read (`aki verify` → 256+ assets checked, 0 missing or corrupt, 0 foreign-key violations).

---

## Rights basis — read this before reusing the corpus

Acquisition was performed under an **attestation supplied by the claimant**, recorded at `docs/execution/PERMISSION_ATTESTATION.md` and in 257 `rights_decisions` rows with state `cleared_limited`.

**Evidentiary status:** the attester stated in session that they are The Holy Word and a friend of [name withheld] and Joe Felz. No signed licence, domain-verified email, or written instrument was supplied. The record therefore says exactly that — an assertion of authority by a specific person at a specific time — and does **not** claim independent verification.

Operations granted: `acquire, store_original, render_pages, ocr_transcribe, vision_analyze, create_embeddings, internal_search, internal_research`.

Operations **not** granted and therefore still denied: `export_text, export_images, share_with_third_parties, redistribute_original, commercial_use, fine_tune_model`.

If that authority is later narrowed or contested, record a superseding decision and stop further processing. Archived bytes remain auditable and can be quarantined without loss of provenance. The engine will refuse any operation not listed above.

---

## Corpus composition

Categories exposed by the site: All, Bibles, Books About Magic, Apothecary, Ancient Texts/Free-Energy, College Medicine.

Observed content includes 19th-century occult and "mental science" titles, Bible editions and apocrypha (1560 Geneva, 1874 and 1884 editions, Maccabees, Esdras, Tobit, Susanna, Bel and the Dragon), Nag Hammadi / Gnostic material (Pistis Sophia, Gospel of Thomas, Gospel of Mary, Hypostasis of the Archons), Freemasonry histories, and encyclopaedic symbol references.

**Metadata caveat, unchanged by acquisition:** titles are recorded exactly as listed and many are uploader filenames (e.g. `123950434-The-Divine-Pymander-of-Hermes-Mercurius-Trismegistus`). They are not verified bibliographic titles. Author, date, language, publisher and edition identity are absent or unverified for most records, and `work_class` is `unclassified` for all 257 by design. No chronology, authorship, or provenance claim may be inferred from a filename.

---

## Processing status — measured 2026-11-13T05:15Z

Acquired does not mean processed. Reconciled state at the close of this pass:

| Stage | Coverage |
|---|---|
| Archived with verified SHA-256 | 257 / 257 |
| PDF editions fully rendered + transcribed | **140 / 249** |
| PDF editions with partial pages (interrupted) | 4 |
| PDF editions not started | **105** |
| Pages rendered | **26,635** |
| Transcriptions | 26,631 — **24,856 from embedded text layers, 1,775 from OCR** |
| Evidence passages | **26,339** |
| Search chunks / FTS rows | 26,336 / 26,336 |
| Visual analysis | 0 |

Page-count reconciliation per edition is exact: 140 complete + 4 partial + 105 not started = 249 PDF editions, 0 unreadable. `aki verify` reports 258 assets checked, none missing or corrupt, `quick_check: ok`, 0 foreign-key violations. `aki index-rebuild` rebuilt 26,336 chunks and the FTS row count matches.

The 4 partial editions are the ones cut off when the shards were stopped for this snapshot; they are detected as incomplete and re-rendered on the next run rather than left stranded.

**Deferred, out of scope for this pass: 8 editions holding non-PDF assets** — 5 JPEG, 3 PNG and 1 ZIP. The ingestion pipeline consumes PDFs (rendering, text layer, OCR fallback); it has no image-ingestion path, and building one was explicitly excluded. They remain archived, hash-verified and rights-cleared; they are simply not rendered or searchable. That accounts for the whole corpus: 249 PDF editions + 8 non-PDF editions = 257.

### OCR accuracy, measured rather than asserted

No human transcription exists for this corpus, so OCR was scored against the publisher's embedded text layer for the same page. That is **reference agreement, not verified truth**, and the measurement is a lower bound — demonstrated concretely on one sampled page (*Meditations; A New Translation*, page 5) where the embedded layer reads `candsiivveacaccarcads meine dane atop dooce te set ees rage` while OCR correctly returns `contents title page chronology half title page introduction by gregory hays`. The reference was the broken text, so a perfect OCR scored CER 1.0.

Same six pages, both resolutions, `RapidOCR-PP-OCRv4`:

| Metric | 150 DPI | 300 DPI |
|---|---|---|
| Mean CER | **0.2139** | 0.2421 |
| Median CER | **0.0515** | 0.0848 |
| p90 CER | **0.2114** | 0.2748 |
| Mean WER | **0.3224** | 0.3485 |
| Wall time, 6 pages | 108 s | 144 s |

150 DPI is both more accurate on this sample and roughly a third cheaper, so the pass runs at 150. A wider 10-page sample at 150 DPI gave mean CER 0.2232, median 0.0515, p90 0.732 — the spread is the real signal: most pages transcribe near 5% error, while a minority fail badly. Those failures are not flagged in the index today, which is why a confidence-based review queue remains an open gate.

### Why the pass stops here, and what is left

105 editions are untouched and 4 are partial, covering roughly 33,000 pages. Measured worker cost across 139 completed editions is **3.33 s/page** on average, but that average hides the distribution: text-layer editions run at **~1 s/page** while large-format scanned editions run at **20–85 s/page**, because OCR cost scales with pixel area. On this machine that is on the order of **8–12 hours** of additional continuous runtime, dominated by oversized scans.

Three throughput levers were measured, not assumed:

- **Database pragma defect — fixed.** Re-asserting `journal_mode` per connection cost 25×; correcting it moved text-layer work from 0.067 to 1.7 pages/s.
- **CUDA — measured as inert.** 6.16 s/page on the GPU against 6.20 s on CPU; RapidOCR's preprocessing dominates.
- **Worker count — no measurable gain.** Four shards averaged 0.073 pages/s against 0.092 for three; within noise, so worker count is not the lever and is not claimed as one.

Resuming is a single command per shard; the queue is derived from the database, partial editions self-heal, and completed ones are skipped.

---

## Failures encountered and resolved

| Failure | Cause | Resolution |
|---|---|---|
| 2 objects rejected during the batch | `UNIQUE(storage_key)` prevented two editions sharing identical bytes | Schema migration 2 removed the constraint; both re-acquired successfully |
| Partial migration left the `assets` table dropped | Rebuilding a table referenced by a view, inside one script | Migration made restart-safe (drops the view and any stale rebuild table first); damaged table rebuilt from the migration's own DDL and repopulated from the verified run report |
| 0 remaining failed objects | — | — |

Full disclosure of the migration incident: `INCIDENT_REPAIR.md`. No source bytes were lost; all 259 blobs were intact throughout.
