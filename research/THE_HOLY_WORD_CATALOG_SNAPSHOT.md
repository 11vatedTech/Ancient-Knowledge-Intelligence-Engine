# The Holy Word — Catalog Reconciliation Snapshot

**Research date:** 2026-11-12  
**Target site:** https://theholyword.net/  
**Purpose:** Record what can currently be established before any automated acquisition is enabled.

## Findings

| Surface | Observed claim | Interpretation |
|---|---|---|
| /blog | Search-indexed page advertises “256 books available” | Evidence that the site advertised a 256-item collection |
| /library | Search-indexed page says “The library awaits its first books” | The current catalog surface is inconsistent with the blog snapshot |
| Indexed book detail pages | At least two /bible/{UUID} pages are indexed | Individual book routes exist in the index, but this does not establish current download availability |

The 256 count is **advertised**, not independently enumerated or acquired. The /library surface and /blog surface conflict. AKI must not silently resolve this discrepancy by treating search snippets as a complete catalog.

## Candidate titles surfaced by the indexed blog

These are discovery candidates only. Metadata below is limited to what the indexed page exposed.

| Candidate title | Creator shown | Advertised file size |
|---|---|---:|
| Ancient Magic Magnetism and Psychic Forces | LH Anderson | 23.8 MB |
| (1884) Great Expose of Mystic Arts Sciences | LH Anderson | 23.2 MB |
| (1897) The Key to Mental and Physical Power by LH Anderson | LH Anderson | 39.5 MB |
| How to Win (1896) | LH Andreson | 29.4 MB |
| the 1560 geneva bible (the real one) | Not shown in snippet | 45.4 MB |
| Hypnotism, Its Uses and Abuses Dr. L.H. Anderson | LH Anderson | 4.8 MB |
| Telekinesis Power Secrets | Adrian P Cooper | 316.8 KB |
| Gospel of Mary Magdalene | Not shown in snippet | 291.4 KB |
| Wisdom 1850 | Not shown in snippet | 9.1 MB |
| Radium and the secret of life | Not shown in snippet | 1.8 MB |
| A New Medicine Based on the 5 Biological Laws | Not shown in snippet | 6.1 MB |
| Pistis of Sophia | Not shown in snippet | 20.0 MB |

## Source evidence

- Blog search snapshot: https://theholyword.net/blog — indexed result advertised 256 books and exposed the candidate titles above.
- Library search snapshot: https://theholyword.net/library — indexed result stated that the library awaits its first books.
- Example indexed book route: https://theholyword.net/bible/5cbded79-b2e5-4853-8fa4-31399be9d53c

## Acquisition decision

**No bulk download is authorized by this snapshot.** A catalog listing, file-size label, or historical-text title is not evidence that the current edition is downloadable or that its scan, translation, or editorial matter may be copied or processed.

Before acquisition, AKI must:
1. enumerate live item records through the site's current public interface;
2. record exact item URL, edition metadata, file format, and current availability;
3. inspect the site's terms and each item's rights/permission statement;
4. separate underlying-work status from scan, translation, and editorial rights;
5. require an explicit operation-level grant for acquire, store, OCR, model analysis, display, and redistribution;
6. hash every acquired original and preserve the source URL and retrieval timestamp.

No missing author, date, edition, identifier, or rights fact may be inferred from a title alone.
