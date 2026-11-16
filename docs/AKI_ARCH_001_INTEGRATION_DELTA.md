# AKI-ARCH-001 Integration Delta

**Status:** Approved engineering baseline for implementation planning  
**Specification:** `AKI_Production_Architecture_Specification_v1.0.pdf` (39 pages, 2026-11-11)  
**Repository baseline:** Existing Python archival core on `main` at integration time  
**Purpose:** Translate the architecture specification into explicit changes to this repository without misrepresenting planned capabilities as implemented.

## 1. Architectural decision

Adopt the specification's **modular local-services architecture**, with a single desktop application, local API, durable worker runtime, PostgreSQL system of record, Qdrant retrieval index, local object vault and replaceable local model adapters.

Do not convert the current codebase into a desktop monolith. Do not introduce every service at once. Preserve the existing working archival vertical slice and evolve it behind stable domain contracts.

### Decision record

| Decision | Direction | Reason / constraint |
|---|---|---|
| Domain architecture | Modular monolith packages with process boundaries only where justified | Testability without premature distributed-systems overhead |
| Canonical persistence | PostgreSQL for production multi-domain records and durable jobs | Transactions, constraints, migrations and concurrent workers |
| Existing SQLite | Retain as supported lightweight development/test backend only while parity is maintained | Avoid breaking current catalog and page-store behavior |
| Vector retrieval | Qdrant as a rebuildable index, never the system of record | Indexes can be regenerated from canonical evidence |
| Lexical retrieval | PostgreSQL full-text + exact identifier/phrase search | Embedding similarity must not replace exact search |
| Object storage | Keep content-addressed local vault; add explicit derivative namespaces and manifests | Immutable bytes and reproducibility |
| Model serving | Provider interfaces; Ollama first adapter; model identity pinned by digest | Local-first and vendor independence |
| Desktop | PySide6/QML shell over local API | UI must not perform heavyweight work or direct SQL |
| Jobs | Durable state machine and transactional lease/admission protocol | Restart-safe work and workspace exclusion |
| Network | Loopback-only by default; acquisition separately enabled and audited | Local privacy and least privilege |

## 2. Current repository facts

The current repository implements:
- SQLite source catalog with stable IDs, idempotent exact inserts and conflict detection.
- Operation-specific fail-closed rights checks.
- Strict JSONL source-manifest import.
- SHA-256 content-addressed immutable blob storage.
- PDF inspection and bounded page rendering.
- Page-artifact manifests with source hash, page hash, renderer/config identity and transactional persistence.
- CLI entry points, tests and CI.

The current repository does **not** implement production PostgreSQL persistence, Qdrant, OCR/HTR, VLM processing, hybrid retrieval, a canonical knowledge graph, research agents, durable job orchestration, local API, desktop UI, model registry, or backup/restore certification. Those remain future work until code and tests prove otherwise.

## 3. Required domain model evolution

The current `SourceRecord` combines source/catalog identity and an edition label. The production model must separate these concepts.

### Canonical entities

- **Work** — intellectual work independent of a particular publication or scan.
- **Edition** — a particular publication, translation, editorial state or edition identity.
- **SourceRecord** — external catalog listing or acquisition locator; not itself a rights grant.
- **Asset** — exact immutable byte object with SHA-256, media type, size and acquisition provenance.
- **Page** — page in a specific asset/edition rendition, with stable identity and page-label metadata.
- **Region** — normalized polygon/box in a page coordinate space, tied to a specific page image derivative.
- **ProcessingRun** — one versioned operation over immutable inputs and explicit configuration.
- **Artifact** — output of a processing run, with hash, lineage and lifecycle status.
- **Evidence** — source-grounded material: page region, transcription span, visual observation or external archival record.
- **Assertion** — typed statement with evidence links, method, model/human authorship, uncertainty and review state.
- **Claim** — proposition under investigation, with support/contradiction/qualification evidence and verification status.
- **Entity** — normalized person, place, object, text, concept or institution with aliases and source-specific labels.
- **Relationship** — typed, directed or undirected edge with temporal scope, evidence, provenance class and uncertainty.
- **RightsDecision** — edition/asset-specific decision with operation scopes, evidence, jurisdiction, reviewer and review date.
- **ResearchCase** — question, scope, plan, corpus snapshot, execution trace, claim ledger and dossier.
- **ModelProfile** — immutable model/runtime identity, license, digest, modalities, resource profile and measured evaluations.
- **Job** — durable task state, dependencies, attempt budget, lease owner and recovery metadata.
- **AuditEvent** — append-only security- and governance-relevant event.

### Identity invariants

1. A Work can have many Editions; an Edition can have many Assets; an Asset can have many processing renditions.
2. A title, filename, URL or catalog position is never the authoritative identity of an asset.
3. Page evidence always resolves to an exact edition/asset/page and, when applicable, a page-image hash and region coordinate frame.
4. Derived artifacts never overwrite source bytes or reviewed artifacts.
5. Raw model output, normalized machine output and human-reviewed output are distinct immutable versions.
6. Claims and relationships are not canonical facts merely because an agent generated them.
7. Rights are checked against the exact asset/edition and requested operation, not inferred from the ancient work's age.

## 4. Rights model expansion

The existing rights enum and operation set are a useful fail-closed start but are too coarse for the production specification.

Required states:
`unknown`, `review_required`, `cleared_limited`, `cleared_full`, `restricted`, `denied`, `expired_or_revoked`.

Required independently gateable operations:
`acquire`, `store_original`, `render_pages`, `ocr_transcribe`, `vision_analyze`, `create_embeddings`, `internal_search`, `internal_research`, `fine_tune_model`, `export_text`, `export_images`, `share_with_third_parties`, `redistribute_original`, `commercial_use`.

Every decision records the subject asset/edition, evidence source, license/version/URL or permission artifact, relevant excerpt, jurisdiction, allowed and prohibited operations, expiry/review date, reviewer, timestamp, confidence and unresolved questions.

**Migration rule:** Existing `public_domain` or `licensed` records must not be silently mapped to broad permission. Map uncertain legacy decisions to `review_required` or `unknown`; require explicit operation scopes. Fine-tuning remains denied unless separately approved.

## 5. Processing and evidence lineage

Every processing output must carry:
- stable run ID and input asset/page/region identifiers;
- SHA-256 for each immutable input and output;
- processor name, exact version and configuration digest;
- model identifier and digest when applicable;
- language/script and task profile;
- start/end timestamps, resource profile and execution host profile;
- status, warnings, error classification and retry history;
- rights decision ID and operation authorization snapshot;
- parent artifact IDs and coordinate transforms for crops/tiles.

Page-level states must be explicit: `pending`, `queued`, `running`, `completed`, `held`, `failed`, `review_required`, `cancelled`. No silent page omission. A page is not complete merely because a batch process returned successfully.

## 6. Retrieval and knowledge rules

Retrieval must combine:
1. exact phrase, identifier and bibliographic search;
2. lexical/BM25-style search;
3. dense text retrieval;
4. sparse/multi-vector retrieval where justified;
5. page-image and region visual retrieval;
6. metadata, chronology, geography and rights filters;
7. bounded graph expansion.

Rank fusion and reranking must be measured on curated gold queries. Store index version, embedding model digest and source artifact version. An index is disposable and rebuildable.

Every answer claim must resolve to evidence records. Distinguish:
- source-stated;
- translator/editorial;
- machine transcription/translation;
- literal visual observation;
- historical contextualization;
- model inference;
- comparative hypothesis;
- human-reviewed conclusion.

A visual similarity match is a discovery aid, not proof of shared origin, contact, transmission or influence. Such a relationship requires chronology, geography, plausible contact/transmission pathways and independent evidence; otherwise it remains a hypothesis.

## 7. Agent security and verification

Agents receive typed, task-scoped tools—not unrestricted shell, filesystem or network access. Imported documents, OCR, websites and model outputs are untrusted data and must never become instructions.

The research workflow must:
1. capture the question and scope;
2. create a bounded plan and show it for approval where needed;
3. snapshot corpus/index/model versions;
4. retrieve evidence across modalities;
5. create a claim ledger with supporting, contradicting and qualifying evidence;
6. identify gaps and alternative explanations;
7. run independent verification;
8. generate a dossier with resolvable citations and explicit uncertainty.

The synthesis agent cannot self-certify. Deterministic validators must check citation resolution, evidence/claim linkage, rights, schema validity and prohibited inference classes. A separate verifier reviews sufficiency, contradiction handling and unsupported claims.

## 8. Local hardware and resource policy

AKI must profile hardware at runtime; never hardcode a single GPU model or assume a model fits.

- Default to one GPU-heavy task at a time.
- CPU metadata, hashing and permitted CPU OCR may run concurrently under budgets.
- Reserve configurable VRAM/RAM headroom for the OS and desktop.
- Measure free/total VRAM, RAM, CPU, disk, model load time and inference latency before scheduling.
- Model profiles include modality support, quantization, context, image limits, license, digest, measured peak VRAM/RAM, throughput and quality evaluation.
- Provide explicit queued/deferred/CPU-fallback/insufficient-resource states; no silent OOM loops.
- Context length is selected from a measured memory budget, not maximized by default.

## 9. Security, operations and UX invariants

- Local API binds to loopback; mutation routes require an installation-scoped random token.
- Validate Origin/Host; no permissive CORS; no public listener by default.
- Acquisition is a separately enabled capability with allowlist, rate limit, request log, terms/robots review and rights gate.
- Parse untrusted documents in bounded worker processes; never execute embedded scripts, macros, attachments or links.
- File paths are generated internally; API clients cannot supply arbitrary vault paths.
- Audit rights changes, exports, model changes, approvals, deletion and recovery.
- Backup/restore must be tested, not merely documented.
- Desktop navigation: Library, Page Lab, Knowledge Graph, Research, Review Queue, Models & System.
- Every visual status/animation reflects real state; no fabricated AI activity, confidence, collaboration or telemetry.
- Accessibility includes keyboard navigation, visible focus, scalable text, non-color-only status, reduced motion and high contrast.

## 10. Acceptance targets from the architecture baseline

These are targets for evaluation, not current claims:
- 100% generated corpus citations resolve to stored evidence records.
- Automated locator regression: 0 failures in deterministic contract fixtures; human-audited locator accuracy >=99.5% with a reported failure analysis.
- Gold-query Recall@20 >=0.90 on the curated set, stratified by language/script/source class.
- nDCG@10 >=0.80 on the curated set, stratified by corpus segment.
- Clean-print OCR CER <=2% only on a specifically defined clean-print subset.
- Historical/manuscript OCR has script/source-specific baselines; no universal claim.
- Every page reaches an explicit terminal or review state; no silent omission.
- Restart tests produce no lost committed artifacts or duplicate logical records.
- Local-only mode works with network disabled for indexed corpus query/view/analysis.

## 11. Implementation sequencing constraints

Do not begin 256-item bulk acquisition or model fine-tuning as the first milestone. First prove one rights-cleared test edition through immutable acquisition, preflight, rendering, OCR, visual observation, evidence citation, retrieval and recovery.

See:
- [Implementation backlog](IMPLEMENTATION_BACKLOG.md)
- [Freebuff local execution handoff](FREEBUFF_LOCAL_HANDOFF.md)
- [Original architecture specification](../research/AKI_ARCH_001_SOURCE_SPECIFICATION.md) when the source PDF is added to the repository by the maintainer.
