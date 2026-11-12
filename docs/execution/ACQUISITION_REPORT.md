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

## Processing status — acquisition is not research readiness

Acquired does not mean processed. At the time of writing:

| Stage | Coverage |
|---|---|
| Archived with verified SHA-256 | 257 / 257 |
| Pages rendered | 0 / 257 (1 synthetic fixture rendered, 3 pages) |
| OCR / text extraction | 0 / 257 |
| Indexed evidence | 3 fixture passages only |
| Visual analysis | 0 / 257 |

The corpus is on disk and integrity-verified. It is **not yet searchable**, because OCR is not implemented and no OCR model is installed. Moving from acquisition to research requires the OCR layer described in `KNOWN_LIMITATIONS.md` §2.

---

## Failures encountered and resolved

| Failure | Cause | Resolution |
|---|---|---|
| 2 objects rejected during the batch | `UNIQUE(storage_key)` prevented two editions sharing identical bytes | Schema migration 2 removed the constraint; both re-acquired successfully |
| Partial migration left the `assets` table dropped | Rebuilding a table referenced by a view, inside one script | Migration made restart-safe (drops the view and any stale rebuild table first); damaged table rebuilt from the migration's own DDL and repopulated from the verified run report |
| 0 remaining failed objects | — | — |

Full disclosure of the migration incident: `INCIDENT_REPAIR.md`. No source bytes were lost; all 259 blobs were intact throughout.
