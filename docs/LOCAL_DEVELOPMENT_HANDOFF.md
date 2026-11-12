# Local Development Handoff — AKI

**Handoff milestone:** Archive + rights + deterministic PDF page-ingestion foundation  
**Verified CI commit:** 31af073d172de20cc4d49ad378075ce2fb3085cc  
**Verification:** GitHub Actions quality job passed; Ruff passed; 23 tests passed; 88% total statement coverage (Python 3.11).

## 1. What is implemented

### Archive and rights
- SQLite source catalog with stable edition/source identifiers.
- Exact duplicate inserts are idempotent; reusing an identifier with changed metadata raises a conflict.
- Rights are recorded per edition and per operation.
- Unknown, restricted, and permission-required rights fail closed.
- Strict JSONL source-manifest parser with path and line-number diagnostics.

### Immutable artifact storage
- SHA-256 content-addressed blob storage.
- Duplicate payloads resolve to the same blob.
- Writes use exclusive insertion and verify bytes after writing.
- Reads verify the digest; a corrupted blob raises an integrity error.

### PDF ingestion
- Local PDF inspection captures source SHA-256, file size, page count, and metadata.
- Encrypted PDFs are rejected; no implicit password handling.
- Page count, DPI, and rendered pixel count are bounded.
- Page rendering uses PyMuPDF and produces PNG page artifacts.
- Each page record includes source ID, one-based page number, PDF page label, source PDF hash, image hash, pixel and point dimensions, DPI, renderer version, and rendering configuration hash.
- Rendering requires explicit STORE permission.
- Page manifests are persisted transactionally only after all referenced blobs pass integrity and size checks.
- Re-importing identical page records is idempotent; conflicting provenance is rejected.
- Source PDF is re-hashed after rendering; changed source content prevents manifest persistence.

### CLI
- aki init
- aki catalog-import
- aki catalog-list
- aki pdf-inspect
- aki pdf-render

## 2. Clone and install on Windows

From PowerShell:

    git clone https://github.com/11vatedTech/Ancient-Knowledge-Intelligence-Engine.git
    cd Ancient-Knowledge-Intelligence-Engine
    py -3.12 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    python -m pip install -e ".[dev]"

The development extra installs PyMuPDF for PDF tests and local rendering. A runtime install with PDF support but without development tools can use:

    python -m pip install -e ".[documents]"

## 3. Verify the checkout

Run:

    ruff check src tests
    pytest --cov=aki --cov-report=term-missing
    aki --version
    aki --help

The hosted baseline has passed on Python 3.11. Run the same gates on Python 3.12 locally before changing the supported runtime matrix.

## 4. First local workflow

Initialize a workspace:

    aki init

Inspect a local edition without storing rendered pages:

    aki pdf-inspect "D:\Library\edition.pdf"

Create a reviewed source manifest in JSON Lines format and import it:

    aki catalog-import .\sources.jsonl
    aki catalog-list

The manifest must describe the specific edition/scan. Do not infer scan rights from the age or public-domain status of the underlying work. Unknown rights remain non-permissive.

After verifying and recording explicit STORE permission for that edition:

    aki pdf-render edition-id "D:\Library\edition.pdf" --dpi 200

Rendered PNG blobs are stored under .aki/blobs/sha256/. The SQLite catalog and page manifest are stored under .aki/catalog.sqlite3 by default. Keep source scans, local databases, rendered images, and private research data out of Git.

## 5. Immediate next engineering milestones

1. **Page-level resumability:** persist per-page job state, reclaim abandoned work safely, and reconcile orphaned blobs after interruption.
2. **OCR evaluation harness:** define reviewed ground truth across representative scripts, compare Tesseract and PaddleOCR, retain raw OCR output, word/line boxes, confidence, engine version, and configuration. Do not treat OCR confidence as calibrated truth.
3. **Human review workflow:** side-by-side scan and transcription, correction history, alternate readings, reviewer identity, and immutable raw model output.
4. **Local VLM adapter:** connect to the user's local Ollama runtime behind a provider interface; record model digest, prompt hash, options, hardware/resource profile, and raw response.
5. **Visual region analysis:** normalized page coordinates, crops linked to original page hashes, diagram/table/marginalia observations, and region-level citations.
6. **Research retrieval:** only after the corpus and reviewed text are sound, add hybrid lexical/vector retrieval and a typed, provenance-backed claim graph.
7. **Desktop shell:** build the research workspace after stable service APIs exist; do not bind the UI directly to database internals.

## 6. Deliberate constraints

- No automated acquisition or bulk downloading is implemented.
- No OCR, translation, VLM analysis, embeddings, vector database, knowledge graph, or desktop UI is claimed implemented.
- The 256-work count remains an advertised candidate count, not a verified, downloadable, rights-cleared inventory.
- Page-manifest persistence is transactional, but crash-resumable per-page scheduling and orphan-blob cleanup remain future work.
- Similar symbols or motifs are not evidence of historical contact without independent contextual support.

## 7. Repository verification record

The verified CI run used Python 3.11 and completed:
- Ruff: all checks passed.
- Pytest: 23 passed.
- Total statement coverage: 88%.

Verification is tied to commit 31af073d172de20cc4d49ad378075ce2fb3085cc. Any subsequent code change must pass CI again before this milestone is considered verified.
