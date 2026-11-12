# Rights and Provenance Policy

## Edition-specific and operation-specific rights
A historical text may be public domain while a modern translation, critical edition, introduction,
annotation, typesetting, or scan has separate rights. AKI records rights against the edition/asset
and never infers permission from the age of the underlying work alone.

Operations are independent permissions: acquire, store, OCR/transcribe, local or remote model
processing, display, and redistribute. Unknown, restricted, and permission-required statuses deny
processing by default. A grant identifies permitted operations and links to evidence where available.

PDF rendering creates a derivative page image and is therefore gated by the recorded STORE
permission. pdf-inspect reads metadata and page count locally but does not persist page images.
Encrypted PDFs are not processed through implicit password guessing or password collection.

## Provenance requirements
Every derived artifact retains source work and edition identifier; page and optional normalized
region; original artifact SHA-256; processor identity and exact version; configuration hash;
timestamp and pipeline run identifier; raw and normalized output; review state; confidence and
alternate readings where meaningful.

The PDF renderer records source PDF SHA-256, rendered PNG SHA-256, one-based page number, PDF page
label, source page dimensions, raster dimensions, DPI, PyMuPDF version, and rendering-configuration
hash. A SQLite page manifest is committed transactionally only after all referenced blobs pass
integrity and size checks. Exact re-imports are idempotent; conflicting metadata is rejected.

A full processing-run record, resumable per-page scheduling, and orphan-blob reconciliation are
not yet implemented.

## Epistemic labels
- Transcription: proposed reading of visible glyphs.
- Translation: rendering of a transcription into another language.
- Visual observation: description of visible marks, layout, or depicted objects.
- Historical claim: proposition about events, people, chronology, influence, or context.
- Hypothesis: explanatory model open to revision.

A model confidence score is not a calibrated probability unless validated for the exact task and
dataset. Similarity is a retrieval signal, not evidence of historical transmission. Repetition by
multiple model agents does not independently verify a claim.

## Source acquisition
The importer accepts reviewed JSON Lines manifests only. Automated web acquisition is excluded
from this release. Before enabling a connector, verify site terms, robots directives, rate limits,
source rights, and whether page-level downloads are permitted.
