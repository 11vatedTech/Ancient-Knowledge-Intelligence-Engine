# AKI Development Roadmap

## Stage 0 — Repository and source governance
- [x] Establish Python package, CLI, source catalog, rights gate, and provenance domain contracts.
- [x] Make catalog insertion idempotent and reject stable-ID collisions.
- [ ] Reconcile the nominated 256-work collection against the live source catalog.
- [ ] Record edition-level rights evidence before bulk acquisition.
- [ ] Add CI, security scanning, coverage enforcement, and release metadata.

## Stage 1 — Archival ingestion
- [ ] Source adapters with terms, robots, rate-limit, and permission handling.
- [ ] Preserve original bytes with SHA-256 identity; never overwrite source assets.
- [ ] Deterministic page extraction and source-to-page mapping.
- [ ] Resumable jobs, retry budgets, leases, and audit events.
- [ ] Artifact reopening and hash verification.

## Stage 2 — Multimodal document intelligence
- [ ] PDF structure, page segmentation, and region coordinates.
- [ ] Script/language-aware OCR adapters benchmarked against reviewed ground truth.
- [ ] Vision-language analysis for diagrams, marginalia, tables, maps, and symbols.
- [ ] Preserve raw model output separately from reviewed transcription.
- [ ] Human review and alternate-reading workflows.

## Stage 3 — Research intelligence
- [ ] Hybrid retrieval over source text, page images, metadata, and visual descriptions.
- [ ] Claim-level knowledge graph with typed relations and provenance.
- [ ] Cross-source comparison, chronology, contradiction detection, and uncertainty accounting.
- [ ] Citation-grounded research workbench and export.

## Stage 4 — Desktop and operations
- [ ] Local-first desktop UX, model/resource manager, job monitor, evidence inspector.
- [ ] Backup/restore, migrations, diagnostics, and signed release process.
- [ ] Corpus-scale performance and recovery testing.

A stage is complete only when acceptance tests, evidence artifacts, documentation, and failure/recovery behavior pass.
