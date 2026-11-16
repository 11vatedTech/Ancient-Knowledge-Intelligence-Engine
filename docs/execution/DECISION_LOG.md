# Decision Log

Architecture decision records for this implementation pass. Each records context, the decision, and the consequence — including what was deliberately *not* built.

---

## ADR-001 — Preserve the existing archival core; extend it, do not rewrite it

**Context.** The repository at `041ce76` contains ~1,100 lines of working code (SQLite catalog, fail-closed rights, content-addressed blob store, PyMuPDF renderer, page manifest, CLI, 20 tests, CI). The specification describes a far larger system.

**Decision.** Keep `domain.py`, `catalog.py`, `rights.py`, `blob_store.py`, `page_store.py`, `pdf.py`, `manifest.py` and their tests intact. Add the canonical model alongside them rather than migrating in place.

**Consequence.** The legacy `sources` table and the new `source_records` table coexist. This is technical debt; the legacy path is still used by `pdf-render` and `catalog-import`. It was judged safer than a rewrite that would have invalidated working, tested behaviour.

---

## ADR-002 — SQLite as the canonical store, PostgreSQL deferred

**Context.** AKI-ARCH-001 §3.3 and §7.1 mandate PostgreSQL. This machine has no PostgreSQL server and no `psql` binary. Installing and operating one was not authorized and would not have been verifiable within this pass.

**Decision.** Implement the normative schema in SQLite using the same table names, columns, checks, and foreign keys. Use `BEGIN IMMEDIATE` for serializable writes, WAL mode, and `PRAGMA foreign_keys=ON`.

**Consequence.** The system is genuinely operational locally. The PostgreSQL port is a translation of an identical model, not a redesign. **No claim of PostgreSQL support is made.** Durable-job semantics that PostgreSQL would give natively (advisory locks, `SKIP LOCKED`) are implemented manually via leases and an admission-lock table — verified by tests, but with more application-level responsibility.

---

## ADR-003 — No vector database without an embedding model

**Context.** The specification mandates Qdrant and hybrid dense+sparse retrieval. No embedding model is installed and none was approved for download.

**Decision.** Do not add Qdrant. Implement FTS5 lexical retrieval, exact phrase mode, metadata filters, and reciprocal-rank fusion. Make `hybrid_search` accept an optional external dense ranking so a future embedding leg is additive.

**Consequence.** Retrieval works and is honest about its single leg: every result reports `legs: ["lexical"]`. Empty semantic capability is visible rather than simulated. The Recall@20 / nDCG@10 targets are unmeasured and are not claimed.

---

## ADR-004 — Rights fail closed on the entire catalog

**Context.** The Holy Word exposes 257 records and its object storage serves PDFs without authentication. It publishes no licence, terms, or public-domain statement.

**Decision.** Catalogue all 257 with full observable metadata, assign each a `review_required` rights decision with **no permitted operations**, and download nothing.

**Consequence.** Zero corpus acquisition. This is the conservative reading of §4.2 and of the handoff rule "no bulk corpus acquisition until access terms and edition/asset rights have been reviewed". Technical reachability was explicitly treated as insufficient. The acquisition engine is fully implemented and tested against a local server instead, so unlocking the corpus later requires a rights record, not new code.

---

## ADR-005 — Research is deterministic evidence assembly, not generation

**Context.** The specification describes a multi-agent synthesis pipeline with independent verification. No local model is installed.

**Decision.** Implement the research case lifecycle, evidence packet, claim ledger, deterministic validators, and dossier rendering. The synthesizer quotes source text and resolves citations; it does not write prose conclusions.

**Consequence.** The output is defensible and fully cited. It is also narrower than the specification's vision. The verifier is real and independent, so a future LLM synthesizer can be added behind the same check rather than replacing it.

---

## ADR-006 — Synthetic fixture as the vertical-slice subject

**Context.** The vertical slice required by the backlog needs a rights-cleared document. None exists in the corpus.

**Decision.** Author a 3-page synthetic PDF (`tests/fixtures/pipeline-fixture.pdf`) containing text that explicitly identifies itself as a fixture, grant it `cleared_limited` as an owner-supplied document, and run the full pipeline on it.

**Consequence.** The archive → render → transcribe → index → retrieve → cite → verify chain is proven on real bytes and real hashes. It is **not** a demonstration of historical research capability, and the fixture states so in its own text.

---

## ADR-007 — Vault-relative storage keys

**Context.** An initial implementation stored absolute filesystem paths as `storage_key` and `page_image_key`, which leaked the machine layout into canonical records and broke portability.

**Decision.** Store paths relative to the vault root (`sha256/ab/<digest>`). Enforced by `test_vault_keys_are_portable`.

**Consequence.** Backups and archives can move between machines. Any pre-existing absolute keys would need a migration; the local database was rebuilt so none remain.

---

## ADR-008 — Windows console output is forced to UTF-8

**Context.** `aki case` failed with `UnicodeEncodeError: 'charmap' codec` when printing a citation arrow on a `cp1252` console.

**Decision.** Reconfigure `sys.stdout`/`sys.stderr` to UTF-8 at CLI entry.

**Consequence.** Quoted source text survives the console. Piping into a legacy-code-page consumer can still mangle it; files are always written UTF-8.

---

## ADR-009 — `executescript` transactions are self-contained

**Context.** `sqlite3.executescript` implicitly commits, which broke an explicit `BEGIN`/`COMMIT` wrapper around migrations.

**Decision.** Embed `BEGIN IMMEDIATE ... COMMIT` inside the DDL script itself, including the migration bookkeeping row, so schema and version record commit atomically.

**Consequence.** Migrations are all-or-nothing. A partially applied schema cannot be recorded as complete.

---

## ADR-014 — OCR provider: RapidOCR on ONNX Runtime, behind a provider contract

**Context.** 99 of 249 PDFs in the acquired corpus have no text layer, and 0 editions had ever been rendered. Tesseract was not installed and would require a system-wide binary; Python 3.14 rules out several ML stacks.

**Decision.** Implement OCR as a *provider contract* (`OcrProvider`) with two adapters, and select at runtime: an explicitly requested provider, else RapidOCR, else Tesseract, else fail with an actionable error. RapidOCR ships PP-OCRv4 models inside the wheel, runs on the ONNX Runtime already present, needs no network access after install, and needs no external binary.

**Consequence.** Measured on real corpus pages: ~0.85 mean confidence, clean text from a 1930s scan, roughly 2–4 s per page at 150 DPI on CPU. Every transcription records `engine_name`, `engine_version`, `confidence` and per-line boxes, so a better model later supersedes the text without touching the archived original. Choosing a different engine is a constructor call, not a rewrite.

**Rejected alternatives.** (a) PaddleOCR — heavy dependency chain, GPU/CUDA friction, unlikely to install on Python 3.14. (b) Tesseract as the default — requires a system-wide install and elevation, and its accuracy on this material is not better. (c) Cloud OCR — violates local-first and would send restricted-origin material to a third party.

---

## ADR-011 — Record the corpus permission as a labelled self-attestation

**Context.** The source publishes no licence and no terms. The claimant stated in session that they are The Holy Word, a friend of [name withheld] and Joe Felz, and instructed acquisition.

**Decision.** Record the grant, but record *what it is*: `docs/execution/PERMISSION_ATTESTATION.md` plus 257 `rights_decisions` rows naming the attester, the timestamp, the exact operations, and an explicit statement that no signed licence or independently verified instrument was supplied. Grant `cleared_limited`, not `cleared_full`; withhold export, redistribution, sharing, commercial use and fine-tuning.

**Consequence.** The corpus can be archived and researched locally, and every byte acquired is attributable to a named person and a stated scope. The record does not launder an unverified claim into a verified one: the DB stores `license_name = "Self-attested permission"` and the notes field says verification is absent. If authority is contested, a superseding decision stops further processing and the archive remains auditable.

**Rejected alternatives.** (a) Refusing to act on the rights holder's own statement — paternalistic and blocks the stated mission. (b) Recording it as a full clearance — overstates the evidence and would authorise redistribution on the strength of a chat message.

---

## ADR-012 — Remove `UNIQUE(storage_key)` from assets

**Context.** Two acquisitions failed because identical bytes backed two editions. Content-addressed storage is *supposed* to deduplicate across editions; a uniqueness constraint on the vault path contradicts the storage model.

**Decision.** Migration 2 rebuilds `assets` without that constraint, keeping `UNIQUE(edition_id, sha256, asset_role)` so a single edition cannot store the same object twice in the same role, and adds a non-unique index on `sha256` for cross-edition lookup.

**Consequence.** One blob can legitimately back several editions with full provenance. Rebuilding a table in SQLite requires care: the migration drops the dependent `v_coverage` view first and recreates it afterwards, and is now restart-safe against a stale `assets_v2`. See `INCIDENT_REPAIR.md` for the failure that motivated that hardening.

---

## ADR-015 — Resolve OCR text before evidence, not after

**Context.** The first OCR integration ran the engine and wrote the transcription correctly, but the evidence loop re-read the PDF text layer instead of the resolved page text. Scanned pages therefore produced a transcription and zero evidence — the pipeline looked successful while contributing nothing searchable.

**Decision.** Resolve each page's text once (text layer or OCR, whichever is authoritative) into a per-page map, and have both the transcription writer and the evidence/index writer read from that map. One resolution point, two consumers.

**Consequence.** A page cannot be transcribed without also being evidence-indexed, which is what made the gap visible in `pages_ocr: 1, evidence: 0` rather than silently degrading the corpus. Covered by `test_scan_with_ocr_records_engine_identity_and_evidence`.

---

## ADR-013 — Repair by replaying the migration's own DDL

**Context.** A partially applied migration left `assets` dropped and its rows stranded in `assets_v2`, which was then lost.

**Decision.** Rather than hand-writing CREATE TABLE, generate the v2 schema by running the real migration runner against a scratch database and apply the resulting DDL to the live database, then record the migration checksum computed by the same function. Repopulate rows from the acquisition run's machine-readable report, verifying blob existence and byte size per row before inserting.

**Consequence.** No schema drift between a repaired database and a freshly migrated one. Recovery depended on an artefact (the run report) that happened to exist — this is disclosed as a weakness, not presented as a guarantee.

---

## ADR-010 — No desktop application in this pass

**Context.** Gates 6 requires a PySide6/QML shell. This environment is a terminal session; a desktop UI cannot be exercised or verified here.

**Decision.** Do not create UI scaffolding, placeholder windows, or a web mock-up.

**Consequence.** Gate 6 is entirely unstarted. An empty shell would have created a false impression of progress.

---

## ADR-016 — The readable Library copy is local storage of originals, gated and audited

**Context.** `Library/<shelf>/<title>` holds a second, human-named copy of each original. The rights model has no operation for "a readable copy on the owner's machine": the nearest are `store_original` (granted) and `export_*` / `redistribute_original` (denied). The first version of the export checked nothing and logged nothing.

**Decision.** Treat the copy as `store_original`: same owner, same machine, byte-identical originals. `aki library-export` skips any edition whose latest decision does not permit `store_original`, verifies every copy against its recorded SHA-256, and writes one `library_export` audit event per run. Synchronising the copy into the OneDrive project folder is a storage location the owner chose, as the vault itself was until ADR-017; it is not sharing, and must not be shared from there.

**Consequence.** If The Holy Word narrows its grant, the next export skips those books. Copies already made are not deleted automatically; the owner removes them.

## ADR-017 — The canonical archive lives outside cloud-synchronised folders

**Context.** The database (WAL), the vault (~15+ GB with page images), derived indexes and thumbnails all lived under OneDrive, which added sync overhead and file-lock risk to every write.

**Decision.** `aki relocate --to C:\AKI` copies the archive with SQLite's online backup and re-hashes every vault file against its content-addressed name, refuses cloud-synced or nested targets, an existing database at the target, or insufficient free space, and records the new home in `aki.config.json`. Every command reads that file (`AKI_HOME` overrides). Nothing is deleted: the old `.aki` gets a `MOVED.txt` and is retired by the owner.

**Consequence.** Two copies exist until the owner deletes the old one. The project folder (code, `Library/`, docs, `.venv`) stays in OneDrive.

## ADR-018 — Backups before processing: verified database plus vault manifest

**Context.** Backups are not an operation in the rights model, and `aki backup` covers the database only.

**Decision.** Before every `finish`, take a verified online database backup (integrity checked twice: by `Database.backup` and by the launcher reading its JSON) and write a vault manifest (every original's key, SHA-256 and size). Database backups are copies of catalogue, rights and derived records on the owner's machine, retained under the same scope as the archive.

**Consequence.** The vault is protected by content addressing and by the relocation copy, not by a scheduled second copy; that remains a gap for the owner to decide on.

## ADR-019 — A loopback web reading room, superseding ADR-010

**Context.** ADR-010 declined to build a desktop shell that could not be exercised. A web interface can be tested end to end (API tests, rendered screenshots).

**Decision.** Build the Felz Reading Room as a FastAPI service with a static front end, bound only to 127.0.0.1 (binding elsewhere is refused), with every route filtered through edition rights: `internal_search` for reading, `render_pages` for derived thumbnails, `internal_research` for research.

**Consequence.** No PySide6 shell; the reading room needs no third-party code in the browser.

## ADR-020 — Stop at the first failure and keep every log

**Context.** The first launcher continued after failures and `finish` exited 0 when workers failed; logs were overwritten.

**Decision.** The launcher checks each step and stops before opening the reading room if anything failed. `finish` exits 1 when any ingest worker, image/EPUB book, meaning index or integrity check fails, and lists the problems. Progress, worker logs, backup results and reports are written with timestamps under `<archive>/logs/`.

**Consequence.** A partial run is visible and resumable rather than silently reported as done.

---

## ADR-021 — No move without a verified, restore-tested archive backup

**Context.** A database backup plus a fingerprint manifest is not a copy of the 15–17 GB archive. The owner required a recoverable copy before migration.

**Decision.** `aki archive-backup --to <folder>` copies the database (online backup) and every original scan, then runs a restore test: `Database.restore` into a scratch file, integrity check, and re-hash of every original the restored database names. Only a passing backup is recorded (`archive_backup_verified`). `aki relocate` refuses without one from the last 7 days whose folder still exists, runs a preflight first (archive size, free space, active job leases, recent ingestion), and refuses while AKI processes are running. The move copies the vault first and the database last, checks that every file the database refers to exists at the target, and resumes cleanly after an interruption. Page images are derived and excluded by default (`--include-pages` adds them).

**Consequence.** The migration is gated on a proven backup. A backup on the same drive is flagged as not protecting against that drive failing.

## ADR-022 — Library copies are reconciled by hash and current rights, never by existence

**Context.** Skipping files that already exist would have trusted the 109 pre-existing copies without checking them, and would not notice damaged or revoked copies.

**Decision.** `aki library-sync` hashes every destination file against its original and re-checks the edition's current `store_original` permission. Identical files are left alone; missing ones are copied and verified; differing, unknown or no-longer-permitted files are moved to `_quarantine/<stamp>/` (never deleted); every run writes a per-file report and an audit event. The 109 pre-existing copies were audited individually (`LIBRARY_RETROSPECTIVE_AUDIT.md/.csv`): 110 of 110 file–edition rows authorized, byte-identical, decision predating the copy.

**Consequence.** No file is grandfathered; narrowing a grant later is enforced on the next sync.

## ADR-023 — Derived cross-references are labelled as machine work and gated like the rest

**Context.** The reading room gained two things that go beyond showing a page: side-notes that set a page beside near passages in other books (`/api/pages/{id}/gloss`), and a chart of index names that share pages (`/api/chart`). Both could be read as claims about how sources relate.

**Decision.** Neither states a relationship. The margin says "found by likeness of meaning, by a machine; near is not the same as in agreement"; the chart says it shows who keeps company "and nothing about why". The `relationships` table stays empty. Both are filtered by the same edition-level rights as the pages they draw on (`internal_search` for passages, `internal_research` for index-derived names and counts). Where OCR confidence for a page is below 0.80 the reader marks the reading as unsure and withholds near passages for it, because comparisons built on a failed reading are noise.

**Review.** An independent read-only review (14 November 2026) found that the first version of the chart counted mentions across every edition and cached the result without regard to rights, and that the research response still carried an unfiltered dossier. Both were corrected: index-derived counts are computed per request over the editions that hold `internal_research`, the chart cache is keyed on that set and on the latest concordance run, and the dossier is no longer returned. `test_index_derived_views_follow_a_narrowed_grant` withdraws a grant from one book while the server is running and checks each view.

**Consequence.** Cross-source discovery exists in an honest form (co-occurrence and likeness) without a knowledge graph being implied. The 0.80 threshold is a judgement from inspecting black-letter pages at about 0.75; it is not calibrated against ground truth.

## ADR-024 — A curator's note on a title that needs one

**Context.** The source lists *The Protocols of the Learned Elders of Zion*, a documented antisemitic forgery, under "Ancient Texts". The launch audit left the question of context to the owner; the owner then handed the remaining decisions to the engineer.

**Decision.** The book stays in the library as listed. `src/aki/app/notes.json` holds notes matched on words of the listed title, and the reader shows the note above the book. One note exists, stating that the text is a forgery, when and how it was exposed, and what it was used for. The note is data, not code: the owner can edit, add or remove notes without touching the program.

**Consequence.** The archive preserves the item without presenting it as a true account. This is a default the owner may overrule.

## ADR-025 — Relocation recognises vault files by name and leaves strays behind

**Context.** The first real relocation (14 November 2026) stopped after copying 34,000 files: a 162-byte Microsoft Office owner-lock file (`~$…`) sat in the vault folder beside a page image, and because its name was 64 characters long it was treated as a content-addressed file and failed its hash check. Separately, the Windows gate had already found that `Database.backup` left a SQLite handle open, which blocked the rename that ends a relocation.

**Decision.** A vault file is one whose name is exactly 64 lowercase hexadecimal characters. Anything else under `blobs/sha256/` is not copied, is listed in the report as `stray_files_left_behind`, and is never deleted. The check that every file the database refers to exists at the target is unchanged, so a stray cannot hide a missing file. SQLite handles opened for backup and initialisation are now closed explicitly.

**Consequence.** Files that Office, OneDrive or Explorer leave in the folder can no longer block a move. Both defects are covered by tests (`test_relocation_leaves_stray_files_behind`, and the two relocation tests that failed on Windows before the handle fix).

## ADR-026 — OCR workers share the cores instead of each claiming all of them

**Context.** The full-corpus run was reading about 12 pages a minute with six workers and the processor at 91–100%. Earlier notes had concluded that "worker count is not a throughput lever" (three shards 0.092 pages/s, four shards 0.073). That measurement was right and its conclusion was wrong: ONNX Runtime sizes its thread pool to every core, so each worker started about 120 threads on a 24-core machine and the workers spent their time contending. Measured on 14 November 2026 on the same six scanned pages per worker: six uncapped workers had not finished 42 pages after ten minutes and 40 CPU-minutes each; six workers capped at four threads finished in 124 s (0.29 pages/s); twelve capped at two finished 84 pages in 226 s (0.32 pages/s).

**Decision.** `aki finish` gives each worker `AKI_OCR_THREADS = cores // workers`, and `aki.ocr` applies it by wrapping the session options RapidOCR builds (RapidOCR 1.2 exposes no thread setting) and by capping OpenCV. The default worker count is about one per three cores, at most eight. Unset, the variable changes nothing, so a single process still uses the whole machine.

**Consequence.** On the live run, machine-read pages went from 0.065 to 0.19 pages/s with eight workers at three threads, with the processor at 60% instead of saturated; twelve workers at two threads were then started to use the idle cores. Recognition settings, models and output are unchanged: only the thread count differs, so pages read before and after are comparable. What remains expensive is the page itself: a 200 dpi scan costs on the order of a core-minute to detect and recognise, and that is not addressed here.

## ADR-027 — A long run asks the computer to stay awake; it changes no setting

**Context.** On 15 November 2026 the full-corpus run lost nine and a half hours: Windows put the computer to sleep at 00:13 after an hour without input and it woke at 09:48. The workers were frozen, not failed, and carried on when it woke.

**Decision.** `aki finish` calls `aki.power.keep_awake`, which is `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`, when it starts and releases it when it ends, including on failure. This is the request a media player makes: it lasts only while the process holds it, the screen may still turn off, and no power plan or system setting is touched. It is best effort and never a reason for the run to fail; on other platforms it does nothing.

**Consequence.** An overnight run is no longer at the mercy of the idle-sleep timer. Closing a laptop lid, a low battery or a manual sleep still stop it; the run resumes where it left off either way. Not verified from the system's side: listing active power requests needs administrator rights, which this project does not ask for.

## ADR-028 — Reading throughput: fair shares, pages side by side, and no power throttling

**Context.** After ADR-026 the run was still far slower than the machine allowed. Three separate causes were found on 15 November 2026, each by measurement on the live corpus.

1. *Shares.* Books were dealt to workers by a hash of their id. With 36 books left, five of twelve workers had nothing to do while two held two thirds of the pages that needed OCR.
2. *One page at a time.* One engine reads one page at a time whatever threads it has. The 1,224-page 1560 Geneva Bible (dense black-letter, about 5,500 characters a page) read at 1.3 pages a minute on five threads: about 15 hours for one book.
3. *Power throttling.* Windows 11 slows processes it takes for background work, and reading workers have no window. On this laptop processor (Core Ultra 9 275HX, Balanced plan) the same Geneva page took 167 s throttled and 46 s after the process opted out, for the same 34 s of processor time. The system showed 68 threads queued with the processor reporting itself half idle.

**Decision.**

- `plan_workers` estimates each unread book's cost by sampling its pages for a text layer, places the heaviest books first on the least loaded worker, and gives each worker cores in proportion to its load. `ingest-batch --editions-file` reads a planned share.
- `PooledOcrProvider` (enabled with `AKI_OCR_PROCESSES`) hands the pages of one book to several single-threaded engines ahead of time and returns each result in the order ingestion asks, so what is written is what the serial path would have written.
- `aki.power.full_speed()` opts the calling process out of power throttling (`SetProcessInformation`, `ProcessPowerThrottling`). `finish`, every `ingest-batch` worker and every pool process call it. Like `keep_awake`, it covers the process only, ends with it, changes no setting and is never a reason to fail.

**Measured.** Geneva Bible pages per minute: 1.3 (one engine, five threads, throttled) → 3.4 (fourteen pooled engines, throttled) → 6.2 (the same, unthrottled, with five other workers still running beside it).

**Consequence.** A long scanned book no longer sets the length of the whole run, and background runs get the speed the processor has. Not done: `finish` does not yet choose pooling by itself (the Geneva Bible was moved onto the pool by hand during the run); pooling versus threads for ordinary workers was not compared; page-level resume does not exist, so an interrupted book still starts again. The plan's cost model counts pages, not the density of text on them, which is why it underestimated the Geneva Bible.

## ADR-029 — Restarting a book must not stop every other book

**Context.** On 15 November 2026 a full run ended with all eight workers failed, most with `database is locked`. Restarting an interrupted book first deletes its half-finished pages. Deleting a page cascades to its transcriptions, and deleting a transcription makes SQLite look for rows whose `supersedes_id` points at it, a column with no index. Each 200-page batch scanned every transcription two hundred times and held the write lock for about 130 s. Every other worker waited its 60 s and failed, and each failure began another slow clean-up. Separately, five books were refused outright: their poster-sized pages exceed the 80-megapixel limit at 200 dpi. Later, two books failed at the same instant with `ONNXRuntime inference failed` while the computer was short of memory.

**Decision.** Migration 4 adds `ix_transcriptions_supersedes`; on a copy of the archive the same clean-up went from about 67 s per 200 pages to about 6. Batch workers wait up to ten minutes for the lock (`AKI_SQLITE_BUSY_MS`), because for them a wait costs seconds and giving up costs a book. A page too large for the limit is rendered at the highest resolution that fits, and the page records the resolution it actually got; only a page that would not fit even at 72 dpi is refused. The pooled reader retries a failed page once.

**Consequence.** On 16 November 2026 the run finished: 257 of 257 books readable, 59,907 pages, 58,832 with text, 755 plates, 87,900 index mentions, integrity `ok`, every original verified against its fingerprint. Every passage, chunk, transcription and mention resolves to a page, and every page image is in the vault. Two pages have no transcription (blank pages for which the engine returned no text).

