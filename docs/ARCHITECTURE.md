# Architecture — Initial Implementation Baseline

## Bounded contexts
1. Archive: work, edition, scan, page, file identity, acquisition history.
2. Rights: operation-specific permission decisions and evidence.
3. Document intelligence: page segmentation, OCR, layout, script/language detection, vision analysis.
4. Knowledge: entities, concepts, claims, relations, chronology, competing hypotheses.
5. Research: retrieval, comparison, synthesis, citation, review.
6. Operations: jobs, model registry, resource scheduling, audit, backup, observability.

## Data flow
Catalog candidate -> rights review -> acquisition -> immutable source blob -> PDF inspection
-> page rasterization -> OCR and vision -> human review -> normalized evidence -> retrieval and knowledge graph
-> research synthesis with page/region citations.

Every derived artifact must record input hashes, processor identity/version, configuration hash,
timestamp, and status. Raw inputs and model outputs remain separate from normalized records.

## Implemented vertical slices
- Archive catalog: SQLite source records, strict JSONL import, stable-ID collision detection.
- Rights: operation-specific fail-closed permission checks.
- Blob storage: SHA-256 paths, deduplication, exclusive insertion, and read-time integrity checks.
- PDF: lazy optional PyMuPDF adapter; source hash and metadata inspection; bounded page count;
  DPI-controlled page rendering; page labels, dimensions, source hash, renderer identity/version,
  and rendered-image hash.
- Page manifest: SQLite records committed as a batch only after every referenced blob is verified;
  repeat inserts are idempotent and conflicting provenance is rejected.
- CLI: catalog initialization/import/list, PDF inspection, and rights-gated PDF rendering.

## Identity and provenance
- source_id: stable identifier for a work/edition record, not a mutable title.
- source_uri: catalog locator, not proof of permission.
- page number: one-based within a specific edition.
- source PDF SHA-256: identity of the exact PDF byte sequence processed.
- page image SHA-256: identity of the rendered PNG bytes stored in the blob archive.
- renderer and config hashes: identify the page-rasterization implementation and parameters.
- assertion: typed statement with page references, method, confidence, review state, and alternatives.

The renderer checks the PDF's size and modification timestamp around inspection and re-hashes it after
rendering. If the source content changes during rendering, no page manifest is committed. It does not
claim cryptographic authenticity of the publisher or source. A page render is a derived artifact,
not a replacement for the original PDF.

## Reliability and safety
- Catalog writes are idempotent.
- Reuse of a source identifier with changed metadata is a conflict, not an overwrite.
- Rights checks fail closed; rendering requires explicit STORE permission.
- PDF page count, DPI, and output pixel count are bounded.
- Encrypted PDFs are rejected; password handling is intentionally not implicit.
- Page manifests are transactionally persisted after blob verification.
- OCR, translation, visual observation, historical claim, and hypothesis remain distinct types.
- Crash-resumable page scheduling, bounded retries, leases, and orphan cleanup remain future work.

## Deliberate non-goals at this stage
No arbitrary URL acquisition, OCR/VLM execution, vector database, knowledge graph, or desktop UI is
claimed implemented. These are introduced only after evidence, rights, and processing contracts are
tested.
