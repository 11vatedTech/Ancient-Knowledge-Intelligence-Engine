# Acquisition Report — The Holy Word

**Source:** https://theholyword.net/
**Census date:** 2026-11-12
**Method:** anonymous read of the public catalog listing the site serves to unauthenticated visitors, enumerated by row range with a 1 request/second floor. No authentication, paywall, CAPTCHA, or access control was bypassed.
**Snapshot:** `data/holyword/inventory_snapshot.json` (committed)
**Raw listing cache:** `data/holyword/books_raw.json` (gitignored)

---

## Headline figures

| Measure | Value |
|---|---|
| Records advertised by the site | **257** |
| Records independently observed | **257** |
| Unique works created | 257 |
| Unique editions created | 257 |
| Assets acquired | **0** |
| Assets that passed preflight and hashed | **0** |
| Bytes stored | **0** |
| Records in `review_required` rights state | **257** |
| Records rights-cleared for any operation | **0** |

The denominator is the actually discovered inventory (257), not the "256 books available" figure rendered in the site's own banner. The two differ by one, and the site's own text is internally inconsistent between renders. The observed count is authoritative because it comes from the enumerated record set.

---

## Why zero records were downloaded

This is a deliberate, documented decision, not a failure:

1. **No licence is published.** The catalog surface, the site footer, and the record views present no terms of use, no licence, and no public-domain statement. A robots.txt request returns the single-page-app shell, i.e. no robots policy is published either.
2. **The files are modern digital editions.** Several titles are 19th-century works whose *underlying text* may be public domain, but each listed PDF is a specific modern scan and compilation. Rights in a scan, its editorial matter, and its typesetting are independent of the age of the underlying work.
3. **Public reachability is not permission.** The object-storage URLs are unauthenticated and technically retrievable. The project rule is explicit: unknown permissions fail closed for the affected operation.
4. **The directive is unambiguous.** "Do not download or process the restricted asset until permission is established." Acquisition without a recorded decision would have violated the rights gate the platform exists to enforce.

The acquisition engine itself is implemented and tested (`tests/test_acquisition_policy.py`): allowlist, per-host rate limiting, byte ceiling, redirect validation, SSRF refusal, content sniffing, SHA-256 verification, audit logging, and a hard refusal when the rights decision does not permit `acquire`.

---

## Corpus composition observed

Categories exposed by the site: All, Bibles, Books About Magic, Apothecary, Ancient Texts/Free-Energy, College Medicine.

The listing is dominated by:
- reproductions of 19th-century occult, magnetic, and "mental science" titles;
- Bible editions and apocrypha (1560 Geneva, 1874 and 1884 editions, Maccabees, Esdras, Tobit, Susanna, Bel and the Dragon);
- Nag Hammadi / Gnostic material (Pistis Sophia, Gospel of Thomas, Gospel of Mary, Hypostasis of the Archons);
- Freemasonry histories and encyclopaedic symbol references;
- uploaded filenames rather than bibliographic titles.

**Critical caveat recorded in the data:** titles are recorded exactly as listed, and many are uploader filenames (e.g. `123950434-The-Divine-Pymander-of-Hermes-Mercurius-Trismegistus`). They are **not** verified bibliographic titles. Author, date, language, publisher, and edition data are absent or unverified for most records. Work class is therefore `unclassified` for every one of the 257, with the basis recorded as "title and listing only". No chronology, authorship, or provenance claim was inferred from a filename.

---

## What is needed to proceed

The smallest owner action that unlocks the corpus:

1. A written permission statement from The Holy Word's operator, **or** an owner-held copy of the files, covering the specific operations:
   `acquire, store_original, render_pages, ocr_transcribe, vision_analyze, create_embeddings, internal_search, internal_research`.
2. Record it per edition:
   ```
   aki rights grant --edition <edition-id> --state cleared_limited \
     --operations store_original,render_pages,ocr_transcribe,internal_search,internal_research \
     --reviewer "<name>" --license-name "<licence or permission>" --evidence-uri "<url or file>" \
     --notes "<what was granted, to whom, for which operations>"
   ```
3. Then acquisition and ingestion are already wired:
   ```
   aki jobs enqueue-ingest --edition-id <edition-id> --pdf <path>
   aki jobs run --max-jobs 25
   ```

A permission that covers only some operations must be recorded as exactly those operations; the engine will refuse the rest.

---

## Manifest

`data/holyword/inventory_snapshot.json` holds all 257 records with: title as listed, author as listed (or null), description as listed, listed size, listed download count, listing date, stable local record id, and a per-record detail URL derived from the site's own `/bible/{id}` route.

`aki inventory manifest <path>` exports the reviewed JSONL manifest with every rights block set to `permission_required` and `permitted_operations: []`, so an importer cannot accidentally treat cataloguing as authorisation.
