# Changelog

## 0.2.0 — 16 November 2026

The corpus is read, the reading room is complete, and the intelligence layer begins.

### Added
- **Cited answers** in the reading room (ADR-030, ADR-031): "Answer" with Qwen 3.5 9B and
  "Think harder" with Gemma 4 26B, both local through Ollama on 127.0.0.1. Whole passages
  are stored as evidence before the model reads them; every sentence is checked
  deterministically against the page it cites (names, numbers, quotations, word share) and
  withheld with its reason if it fails. Cases, model runs and per-sentence claims are recorded.
- The Felz Reading Room: library, reader with side-notes, search by words and by meaning,
  curated index, chart of shared names, plates, research, Joe's memorial and the colophon.
- OCR provider layer (RapidOCR) and one-pass, resumable, parallel ingestion of the corpus.
- Meaning search (model2vec or corpus-trained LSA), the concordance, plate detection.
- Archive relocation out of OneDrive with verified copy, archive backups and vault manifest.
- Launchers (`Finish the reading room.cmd`, `Open the reading room.cmd`,
  `Back up the archive.cmd`) and their failure-injection test (`Test the launchers.cmd`).
- Browser walk of the running room (`scripts/room_walk.py`), now including live answers.
- A curator's note marks the library's saved Grok 3 chat transcript as not an old text.

### Changed
- CI installs the reading-room extras so its tests run, and lints `scripts/`.
- New optional extra `ocr`, pinned to RapidOCR 1.2 (the release the corpus was read with).

### Fixed
- `AKI_OCR_THREADS` no longer fails with RapidOCR releases that lack the 1.2 session hook;
  found by installing from a clean checkout.

### Repository
- `LICENSE`: proprietary, all rights reserved; the bundled fonts keep the SIL OFL 1.1.
- Records about the collection itself stay with the archive and are not published: the
  per-book census, the per-file library audit, the catalog snapshot, and the original
  permission record. A redacted public copy is `PERMISSION_ATTESTATION_PUBLIC.md`.
- The books (`Library/`), the archive, `aki.config.json` and launcher test runs are machine
  local and ignored by git. The corpus permission denies redistribution.

## 0.1.0

Canonical store, content-addressed vault, rights gate, preflight, constrained acquisition,
durable jobs, lexical retrieval, citation resolution and the claim ledger.
