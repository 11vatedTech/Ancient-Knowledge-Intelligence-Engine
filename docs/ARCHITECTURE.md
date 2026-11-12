# Architecture — Initial Baseline

## Bounded contexts
1. Archive: work, edition, scan, page, file identity, acquisition history.
2. Rights: operation-specific permission decisions and evidence.
3. Document intelligence: page segmentation, OCR, layout, script/language detection, vision analysis.
4. Knowledge: entities, concepts, claims, relations, chronology, competing hypotheses.
5. Research: retrieval, comparison, synthesis, citation, review.
6. Operations: jobs, model registry, resource scheduling, audit, backup, observability.

## Data flow
Catalog candidate -> rights review -> acquisition -> immutable blob store -> page extraction
-> OCR and vision -> human review -> normalized evidence -> retrieval and knowledge graph
-> research synthesis with page/region citations.

Every derived artifact must record input hashes, processor identity/version, configuration hash,
timestamp, and status. Raw inputs and model outputs remain separate from normalized records.

## Initial implementation
The first vertical slice implements Archive and Rights domain contracts with SQLite persistence
and a JSONL manifest importer. It deliberately does not fetch arbitrary URLs. Network acquisition
requires a later source adapter with explicit policy and operator review.

## Identity model
- source_id: stable identifier for a work/edition record, not a mutable title.
- source_uri: catalog locator, not proof of permission.
- page number: one-based within a specific edition.
- page image: SHA-256 digest of immutable source or normalized image artifact.
- assertion: typed statement with page references, method, confidence, review state, and alternatives.

## Reliability
- Catalog writes are idempotent.
- Reuse of a source identifier with changed metadata is a conflict, not an overwrite.
- Rights checks fail closed.
- OCR, translation, visual observation, historical claim, and hypothesis remain distinct types.
- Future ingestion workers require bounded retries, explicit concurrency limits, leases, and recovery.
