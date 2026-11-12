# Ancient Knowledge Intelligence Engine (AKI)

**11vatedTech** — A provenance-first, multimodal research system for historical texts, manuscripts, diagrams, and material culture.

AKI preserves the distinction between visible source content, proposed transcription, translation, visual observation, and researcher inference. Every derived assertion must remain traceable to a specific source edition and page-level evidence.

## Current implementation

- SQLite source catalog with immutable source IDs and conflict detection.
- Operation-specific, fail-closed rights enforcement.
- Strict JSONL catalog manifests.
- SHA-256 content-addressed blob storage with integrity verification.
- Local PDF inspection and page rendering through optional PyMuPDF support.
- Page artifacts record source PDF hash, page number/label, pixel dimensions, page dimensions, DPI, and image hash.
- GitHub Actions lint/test workflow.

The nominated collection is described as 256 works, but that inventory and its availability are **not verified**. See the source snapshot in the research directory.

## Development

Requires Python 3.11+.

Create and activate a virtual environment, then run:

    py -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -e ".[dev]"
    pytest
    ruff check src tests
    aki --help

PDF support is included in the development extra. For a runtime installation with PDF support:

    python -m pip install -e ".[documents]"

## First-run workflow

Initialize the catalog:

    aki init

Inspect a local PDF without adding it to the archive:

    aki pdf-inspect "D:\Sources\edition.pdf"

Import a reviewed JSONL manifest, then inspect its source ID:

    aki catalog-import .\sources.jsonl
    aki catalog-list

Only after the edition record has a documented rights grant permitting STORE, render pages into .aki/blobs:

    aki pdf-render edition-001 "D:\Sources\edition.pdf" --dpi 200

PDF page images are stored as immutable PNG blobs under .aki/blobs/sha256/. Do not commit source scans, derived page images, private research data, or local databases to Git.

## Engineering principles

- **Evidence before synthesis:** generated interpretation is never represented as primary-source fact.
- **Page-level provenance:** derived records resolve to edition, page, and optional region.
- **Rights-aware processing:** acquisition, storage, OCR, model processing, display, and redistribution are independent permissions.
- **Reproducible processing:** runs retain input hashes, processor/model versions, configuration, and outputs.
- **Local-first execution:** local inference and storage are first-class; remote services are explicit opt-ins.
- **Uncertainty is data:** confidence, review state, alternative readings, and contradictions are preserved.
- **No silent source mutation:** source files are content-addressed and never overwritten in place.

See docs/ROADMAP.md, docs/ARCHITECTURE.md, and docs/RIGHTS_AND_PROVENANCE.md.

AKI is a research and archival tool, not an authority that settles historical disputes. Similar motifs, words, or cosmological descriptions do not by themselves establish contact, borrowing, or shared origin.
