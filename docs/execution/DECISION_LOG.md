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

## ADR-010 — No desktop application in this pass

**Context.** Gates 6 requires a PySide6/QML shell. This environment is a terminal session; a desktop UI cannot be exercised or verified here.

**Decision.** Do not create UI scaffolding, placeholder windows, or a web mock-up.

**Consequence.** Gate 6 is entirely unstarted. An empty shell would have created a false impression of progress.
