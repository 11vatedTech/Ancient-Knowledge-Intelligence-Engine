# Ancient Knowledge Intelligence Engine (AKI)

**11vatedTech** — A provenance-first, multimodal research system for the study of historical texts, manuscripts, diagrams, and material culture.

AKI is designed to preserve the distinction between what a source visibly contains, what a transcription proposes, what a translation interprets, and what a researcher infers. Every derived assertion must remain traceable to a source edition and page-level evidence.

## Project status

The repository is being initialized as a production-oriented Python application. The first vertical slice establishes the source archive domain, rights-aware ingestion policy, immutable source identity, and machine-verifiable provenance. The initial target corpus is 256 candidate works from the user's nominated collection; the count and availability are **not considered verified** until catalog reconciliation completes.

## Engineering principles

- **Evidence before synthesis:** no generated interpretation is represented as primary-source fact.
- **Page-level provenance:** every transcription, translation, visual annotation, and claim resolves to a stable edition/page/region reference.
- **Rights-aware processing:** acquisition, storage, OCR, model processing, display, and redistribution are separately recorded permissions.
- **Reproducible processing:** pipeline runs record tool/model versions, configuration hashes, input hashes, and outputs.
- **Local-first execution:** local inference and storage are first-class; remote services are explicit opt-ins.
- **Uncertainty is data:** confidence, review state, alternative readings, and contradictions are preserved.
- **No silent source mutation:** source files are content-addressed and never overwritten in place.

## Initial architecture

```text
src/aki/
  domain/       Source, edition, rights, page, provenance value objects
  infrastructure/ SQLite persistence and content-addressed storage
  services/     Catalog ingestion and rights enforcement
  cli.py        Operator entry point
tests/          Deterministic domain and service tests
```

The first release deliberately avoids prematurely committing to a specific OCR/VLM vendor or vector database. Those adapters will be introduced behind tested interfaces after the source and provenance contracts are stable.

## Development

Requires Python 3.11+.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
aki --help
```

## Status and scope

See [docs/ROADMAP.md](docs/ROADMAP.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and [docs/RIGHTS_AND_PROVENANCE.md](docs/RIGHTS_AND_PROVENANCE.md).

## Disclaimer

AKI is a research and archival tool, not an authority that settles historical disputes. Similar motifs, words, or cosmological descriptions do not by themselves establish contact, borrowing, or a shared origin. All historical conclusions require inspectable evidence and explicit reasoning.
