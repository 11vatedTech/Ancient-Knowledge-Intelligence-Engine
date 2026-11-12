# Rights and Provenance Policy

## Edition-specific and operation-specific rights
A historical text may be public domain while a modern translation, critical edition, introduction,
annotation, typesetting, or scan has separate rights. AKI records rights against the edition/asset
and never infers permission from the age of the underlying work alone.

Operations are independent permissions: acquire, store, OCR/transcribe, local or remote model
processing, display, and redistribute. Unknown, restricted, and permission-required statuses deny
processing by default. A grant identifies permitted operations and links to evidence where available.

## Provenance requirements
Every derived artifact retains source work and edition identifier; page and optional normalized
region; original artifact SHA-256; processor/model name and exact version; prompt/template and
configuration hash; timestamp and pipeline run identifier; raw and normalized output; review state;
confidence and alternate readings where meaningful.

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
The initial importer accepts reviewed JSON Lines manifests only. Automated web acquisition is
excluded from this release. Before enabling a connector, verify site terms, robots directives,
rate limits, source rights, and whether page-level downloads are permitted.
