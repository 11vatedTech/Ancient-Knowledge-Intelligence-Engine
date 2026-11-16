# Current State — 16 November 2026

## Done: the corpus is read and the reading room is complete

- **Archive** at `C:\AKI` (set in `aki.config.json`; verified backup at `C:\AKI-backup`).
  257/257 books readable, 59,907 pages, 58,832 with text, 755 plates, 87,900 index
  mentions. Integrity ok, schema 4, every original re-hashed.
- **Reading room**: `Open the reading room.cmd` (loopback only). Library, reader with
  side-notes, search by words and meaning, index, chart of shared names, plates,
  cited research, Joe's memorial, colophon. Design canon: `docs/design/VISUAL-CANON.md`.
- **Verification**: `pytest` (163 pass), `Test the launchers.cmd` (12/12),
  `scripts/room_walk.py --base http://127.0.0.1:8765` (49/49 on the real archive, including
  live answers on desktop and phone; run it with the system Python, which has Playwright).
- **Decisions**: `docs/execution/DECISION_LOG.md` ADR-016 to ADR-031.
- **Release**: v0.2.0 (`CHANGELOG.md`). The shared repository holds the engine only; the
  books stay in `Library/` and `C:\AKI` on this computer and are ignored by git.

## Open items

- Many Library books are still under their authors' copyright. Before anything is
  shared publicly, sort the books into public-domain titles and in-copyright titles.
- Black-letter books (1560 Geneva Bible and others) were read by OCR at about 0.78
  confidence: images exact, text approximate.
- Not yet re-checked at read time: meaning search against `create_embeddings`; full
  page images against `render_pages`.

## Next: the intelligence layer (the original vision)

**Models chosen and installed (ADR-030):** `qwen3.5:9b` as the main model (about 55 tokens/s, on the GPU)
and `gemma4:26b` as "think harder" (about 9 tokens/s). Both are Apache 2.0 and run through the local
Ollama. Both answered a test question with every sentence supported by its cited page. The research
layer must pass whole passages (about 1,400 characters), not snippets.

What exists is retrieval and evidence: lexical + meaning search, a curated
concordance, co-occurrence, and research that gathers cited passages but writes no prose.
What the vision asks for next, in order of value per effort:

1. **Cited answers — built (ADR-031).** Research page: "Answer" (Qwen 3.5 9B) and
   "Think harder" (Gemma 4 26B), both local through Ollama. Every sentence is checked
   against the page it cites and withheld if it fails; the run, the passages and each
   sentence are recorded as a case, a model run, evidence and claims.
2. **Comparing sources** — the same packet grouped by book, so the model can say where
   books agree, differ or contradict each other, always quoting.
3. **Entity extraction beyond the curated list** — proposed people, places and works,
   kept `candidate` until confirmed; typed relationships with evidence rows.
4. **Plate descriptions** — a vision model describes plates, stored as
   `visual_observations` marked as machine description.
5. **OCR quality** — re-read low-confidence pages (black-letter) with a stronger
   engine; measure CER on a hand-checked sample.

Known limit of step 1: the checker proves a sentence is made from its page's words, not
that it keeps the page's sense (one kept sentence reversed a negation). Step 2 could add a
second, independent check of sense.

Rules that carry over: everything cites a page; machine work is labelled as such;
rights are checked at read time; nothing leaves this computer unless the owner says so.
