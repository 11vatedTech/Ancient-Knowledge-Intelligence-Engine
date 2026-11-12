# AKI Production Implementation Backlog

This backlog translates AKI-ARCH-001 into dependency-ordered work. It is deliberately evidence-gated: source code, tests and artifacts—not task descriptions—determine completion.

## Status vocabulary

- **Not started** — no implementation exists.
- **In progress** — code is being changed; acceptance is not met.
- **Blocked** — an external decision, permission, environment or dependency prevents progress.
- **Implemented / unverified** — code exists but required tests/evidence have not passed.
- **Verified** — acceptance tests and evidence are recorded.
- **Accepted** — verified and reviewed against the release gate.

Never mark a feature complete based only on a passing unit test if its integration, failure and recovery behavior is untested.

## Gate 0 — Repository, corpus and rights reconciliation

**Objective:** Establish trustworthy scope and a reproducible local baseline.

Work:
- [ ] Capture repository commit, tree, dependency lock, test baseline and known defects.
- [ ] Reconcile the advertised 256 catalog target against actual discoverable records; record each record's disposition.
- [ ] Implement catalog source adapter only after terms, robots, privacy and permitted access paths are reviewed.
- [ ] Support user-supplied JSONL manifests and local files as first-class acquisition paths.
- [ ] Add edition/asset rights decision schema and migration mapping for legacy rights records.
- [ ] Create a rights register with evidence, reviewer, operation scopes and expiry/review date.
- [ ] Add a local environment doctor reporting Python, OS, CPU, RAM, GPU/VRAM, storage, model runtime and optional service state.
- [ ] Produce baseline report with unresolved legal, source and technical blockers.

Acceptance:
- Every discovered target record has exactly one transparent disposition.
- No bulk download begins while rights/access is unknown.
- Existing tests remain green.
- Doctor output distinguishes detected facts from unavailable measurements.

## Gate 1 — Archive integrity and durable processing foundation

**Objective:** Safely process one authorized edition with deterministic provenance and restart recovery.

Work:
- [ ] Introduce canonical Work, Edition, SourceRecord, Asset, Page, Region and ProcessingRun contracts.
- [ ] Design PostgreSQL schema and Alembic migrations; preserve SQLite test/development compatibility only where justified.
- [ ] Add append-only audit events and migration/version metadata.
- [ ] Extend object vault namespaces for originals, page masters, normalized images, crops, layout, transcription, vision, exports and evaluation.
- [ ] Add file preflight: content sniffing, size/page/pixel/decompression bounds, encryption, malformed structure and parser warnings.
- [ ] Quarantine suspicious files and isolate parsing in bounded worker processes.
- [ ] Add page-level durable state machine, transactional outbox/job enqueue and idempotency keys.
- [ ] Add lease owner identity, process-start identity, heartbeat, expiry, workspace admission and safe dead-owner reclamation.
- [ ] Add retry budgets, cancellation, orphan-blob reconciliation and crash recovery.
- [ ] Add backup/restore and integrity verification commands.

Acceptance:
- A process kill at each processing boundary can be resumed without losing committed data or duplicating logical artifacts.
- Concurrent independent runs cannot write the same workspace/page without explicit admission.
- Every page reaches a recorded state.
- Original bytes remain immutable and hash-verifiable.
- Restore drill recreates a queryable catalog and verified artifact set.

## Gate 2 — Document intelligence and human review

**Objective:** Produce source-linked machine readings without confusing them with verified text.

Work:
- [ ] Page quality assessment: orientation, skew, blur, contrast, bleed-through, crop loss and scan quality.
- [ ] Layout segmentation for columns, text blocks, headers, footers, marginalia, tables, captions and illustrations.
- [ ] Script/language detection with confidence and human override.
- [ ] OCR adapters behind provider contracts; evaluate Tesseract/PaddleOCR and script-specific alternatives.
- [ ] HTR path for manuscript sources; no universal ancient-script OCR claim.
- [ ] Preserve token/line/block polygons in original page coordinate space.
- [ ] Preserve raw OCR output, normalized output and human-reviewed transcription as separate versions.
- [ ] Alternate reading, transliteration and translation alignment.
- [ ] Review queue with correction provenance and reviewer identity.

Acceptance:
- Gold set is stratified by script, period, print/manuscript, quality and layout.
- CER/WER and layout metrics are reported per stratum, not only as a pooled score.
- Clean-print CER target <=2% applies only to the declared clean-print subset.
- Every transcription span links to page/region evidence and engine/config/model identity.
- Human edits create a new version; raw output is never overwritten.

## Gate 3 — Visual intelligence and multimodal evidence

**Objective:** Extract literal observations from page images while separating interpretation.

Work:
- [ ] VLM provider interface with local image-capable runtime adapter.
- [ ] Model registry records digest, license, quantization, modality, limits and measured resource profile.
- [ ] Region/crop manager with reversible coordinate transforms to source page.
- [ ] Tasks for diagram/illustration description, map/table/chart extraction, marginalia and symbol observation.
- [ ] Output schema separates visible features from semantic interpretation.
- [ ] Visual similarity index for candidate discovery, with source and crop locators.
- [ ] Pairwise human labeling and evaluation fixture creation.
- [ ] Human review for uncertain/high-impact observations.

Acceptance:
- Every observation resolves to the exact page image and region.
- Model prompt, digest, runtime, config and raw response are retained.
- Similarity never establishes origin, contact or influence.
- Unsupported interpretation is rejected or labeled as hypothesis.

## Gate 4 — Knowledge model and hybrid retrieval

**Objective:** Make the archive searchable across text, images, metadata and evidence relationships.

Work:
- [ ] Canonical entities, aliases, concepts, claims, typed relations and chronology.
- [ ] Evidence links for every assertion/relationship, including evidence class and provenance.
- [ ] Contradiction, qualification and support edges; preserve competing claims.
- [ ] PostgreSQL lexical/exact search.
- [ ] Qdrant dense/sparse retrieval as rebuildable indexes.
- [ ] Image/region visual retrieval.
- [ ] Metadata filters: edition, date, language, script, geography, source class and rights.
- [ ] Hybrid rank fusion and reranker behind replaceable interfaces.
- [ ] Versioned index manifests tied to embedding/model digest and source artifact version.
- [ ] Graph neighborhood retrieval with depth/degree/time bounds.

Acceptance:
- Gold-query Recall@20 >=0.90 and nDCG@10 >=0.80 on the curated evaluation set, reported per stratum.
- Exact search remains available and is tested independently.
- Index rebuild reproduces the declared index version from canonical records.
- Every returned evidence item has a stable locator and rights state.
- Graph edges distinguish source-stated, editorial, machine-inferred and comparative hypothesis.

## Gate 5 — Research orchestration and independent verification

**Objective:** Generate auditable research dossiers, not unsupported narrative answers.

Work:
- [ ] ResearchCase lifecycle and corpus/model/index snapshot.
- [ ] Question decomposition and bounded plan generation.
- [ ] Specialist agents for bibliographic discovery, transcription review, visual observation, chronology, entity resolution and synthesis.
- [ ] Typed tool contracts, least-privilege permissions and per-tool budgets.
- [ ] Evidence board: supports / contradicts / qualifies / unresolved.
- [ ] Claim ledger with status and evidence sufficiency.
- [ ] Independent verifier separate from synthesis agent.
- [ ] Deterministic citation, rights, schema and prohibited-inference validators.
- [ ] Alternative explanations and uncertainty accounting.
- [ ] Markdown/PDF dossier export with rights-aware filtering.

Acceptance:
- Every corpus-grounded claim resolves to page/region evidence or is explicitly unverified.
- Synthesis agent cannot self-certify.
- Unsupported claims are measured on adversarial fixtures.
- Contradictory evidence is surfaced rather than hidden.
- Export enforces text/image rights separately.

## Gate 6 — Local API, desktop and model operations

**Objective:** Deliver a reliable, accessible research workstation over the local services.

Work:
- [ ] FastAPI local API with versioned Pydantic schemas.
- [ ] Loopback-only binding, installation-scoped mutation token, Origin/Host checks and restrictive CORS.
- [ ] SSE or polling for durable job/research events.
- [ ] PySide6/QML shell and navigation: Library, Page Lab, Knowledge Graph, Research, Review Queue, Models & System.
- [ ] Page viewer with zoom, pan, rotation, tiles, region overlays and evidence inspector.
- [ ] Model manager with explicit download/update consent, license and digest display.
- [ ] Runtime diagnostics and resource-aware scheduler.
- [ ] Keyboard navigation, focus, scalable text, high contrast and reduced motion.
- [ ] Real telemetry only; no simulated AI activity or decorative fake progress.

Acceptance:
- UI remains responsive while OCR/VLM jobs execute.
- API cannot read arbitrary host paths or invoke unrestricted commands.
- Offline indexed-corpus workflows function with network disabled.
- All status indicators are driven by persisted job/runtime events.

## Gate 7 — Release hardening and corpus scale

Work:
- [ ] Security review and dependency/license inventory.
- [ ] Unit, integration, contract, end-to-end, golden, adversarial and fault-injection suites.
- [ ] Concurrency, load, memory and long-running soak tests.
- [ ] Database/object-store backup and restore drills.
- [ ] Rights revocation propagation and export remediation.
- [ ] Reproducible model/package manifests and release provenance.
- [ ] Installer, upgrade, migration rollback and diagnostics bundle.
- [ ] Known limitations and corpus coverage report.

Acceptance:
- No critical/high unresolved security findings.
- Restore, offline, crash-recovery, rights-revocation and citation gates pass.
- Benchmark report is reproducible and includes hardware/model versions.
- Release notes distinguish implemented, measured, limited and unsupported capabilities.

## Required first vertical slice

Before scaling, process one rights-cleared test edition end-to-end:

1. Register Work, Edition, SourceRecord and rights evidence.
2. Acquire or import the file and preserve original bytes.
3. Preflight and record the report.
4. Render all pages and verify hashes.
5. Run OCR and preserve raw output.
6. Run one visual observation on a cited page region.
7. Human-review at least one transcription/observation.
8. Index text and visual evidence.
9. Ask a research question and generate a claim ledger.
10. Independently verify citations and unsupported claims.
11. Kill/restart the worker during a second run and prove recovery.
12. Export a rights-compliant dossier.

Do not declare Gate 1–5 complete based on separate toy examples; the vertical slice proves their integration.

## Release reporting format

For each gate report:
- commit SHA and changed paths;
- implemented behavior and explicit non-goals;
- commands executed and environment;
- test counts and failures;
- benchmark dataset/version and measured values;
- evidence artifact locations and hashes;
- security/rights review outcomes;
- known defects and blocked acceptance criteria;
- independent reviewer decision.

A feature without this evidence remains unverified.
