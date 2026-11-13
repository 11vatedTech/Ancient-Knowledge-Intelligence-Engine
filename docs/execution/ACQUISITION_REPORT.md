# Acquisition Report — The Holy Word

**Source:** https://theholyword.net/
**Census and acquisition date:** 2026-11-12
**Method:** anonymous read of the public catalog listing the site serves to unauthenticated visitors, enumerated by row range; objects then fetched one at a time with a 1 request/second floor, size ceiling, SSRF guard and content sniffing.
**Snapshot:** `data/holyword/inventory_snapshot.json` (committed)
**Rights basis:** `docs/execution/PERMISSION_ATTESTATION.md` (self-attested, see below)

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

## Processing status — in progress, measured 2026-11-13T04:30Z

Acquired does not mean processed. Live state while the pass runs:

| Stage | Coverage |
|---|---|
| Archived with verified SHA-256 | 257 / 257 |
| PDF editions rendered + transcribed | **140 / 249** |
| Pages rendered | **25,707** |
| Transcriptions | 25,705 (1,746 produced by OCR, the rest from embedded text layers) |
| Evidence passages indexed | **25,484** |
| Editions completed and logged | 137 (2 recorded failures, both retried) |
| Visual analysis | 0 |

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

---

## Failures encountered and resolved

| Failure | Cause | Resolution |
|---|---|---|
| 2 objects rejected during the batch | `UNIQUE(storage_key)` prevented two editions sharing identical bytes | Schema migration 2 removed the constraint; both re-acquired successfully |
| Partial migration left the `assets` table dropped | Rebuilding a table referenced by a view, inside one script | Migration made restart-safe (drops the view and any stale rebuild table first); damaged table rebuilt from the migration's own DDL and repopulated from the verified run report |
| 0 remaining failed objects | — | — |

Full disclosure of the migration incident: `INCIDENT_REPAIR.md`. No source bytes were lost; all 259 blobs were intact throughout.
