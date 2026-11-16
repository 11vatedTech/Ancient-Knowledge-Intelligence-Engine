# Visual Canon: The Felz Reading Room

The gated concept is in `concept.json` beside this file. This page is the working summary:
what the room is, what it is made of, and what must not creep in.

## Product thesis

**By day the cell gathers; by night the lamp gives back.** Joe Felz wired solar panels and
photographed old books. Both catch light. The reading room is lit by the reader's own sun,
and everything in it is set the way the books in the library were set.

## Audience and emotion

- **Audience:** Joe's friends and the owner of this archive, reading alone on one Windows
  computer, usually for a long sitting.
- **Primary emotion:** quiet, sunlit attention.
- **Secondary emotion:** being let into a private library by someone who loved the books.
- **Trust signal:** every machine-made thing says it is machine-made, in plain words, beside
  the thing itself (the transcription's source line, the margin's "found by likeness of
  meaning, by a machine", the chart's "nothing about why").

## Visual identity

- **Core metaphor:** an open book in a room whose light changes with the hour.
- **What must never appear:** cards, pills, icon circles, glass, glow for its own sake, fake
  wear on paper, stock or generated imagery, a photograph of Joe we do not have.
- **Signature moments**
  1. Home opens as a book: a plate from the library as frontispiece, the room's title page
     with the sun device, then a contents leaf that is also the navigation.
  2. The light follows the clock (`light.js`): dawn, noon, golden hour, dusk, night.
  3. The reader's margin: index names on the page and near passages in other books, set as
     side-notes.
  4. The chart: the index names as an engraved star chart of shared pages.

## Composition rules

- **Structure is typographic.** Hairline rules, double rules, dot leaders and columns are
  the only containers. If a design needs a box, it needs rethinking.
- **Centre for ceremony, left for reading.** Page heads, the title page and the memorial are
  centred like title pages; reading text, results and passages are flush left at a book
  measure (about 34 to 36rem).
- **Paper is always the brightest surface.** Scans and the home spread use `.paper` /
  `.scan`, which keep day ink whatever the hour.
- **Small windows:** the spread becomes one leaf with the title page first; the reader
  stacks scan, transcription, margin; the head keeps the sundial and search and lets the
  section names scroll sideways.

## Art direction

- **Colour.** Thirteen tokens (`--ground`, `--ink`, `--accent`, `--gilt`, `--paper`, the sky
  and sun, …) are written by `light.js`, blended in OKLab between five keyed palettes. Light
  phases: ink on sunlit plaster, **solar-cell blue as accent**. Dark phases: lamplight on
  cell blue, **gilt as accent**. The two never blend across the light/dark line; the room
  crosses it in one slow fade. `--cell` (#14233F) is the one constant.
- **Type.** Titles: IM Fell French Canon. Sub-heads and book titles: IM Fell Double Pica.
  Title-page capitals: IM Fell English SC. Everything else, including the interface: EB
  Garamond, with letterspaced small capitals for labels and old-style figures. Monospace only
  for hashes, model names and commands. All SIL OFL 1.1, bundled in `static/fonts/` with
  their licences. Base size is 18px because Garamond's x-height is small.
- **The device.** `art.js: sunDevice()`: a woodcut sun, twelve straight and twelve flaming
  rays, its disc ruled like a solar cell with busbars. It is the mark in the head, the
  emblem on the title page and the memorial. It is never animated.
- **Ornament.** One: the swelled rule with a lozenge (`swell()`).
- **Texture.** A faint fixed grain on the ground so it reads as plaster. Nothing on paper.
- **Imagery.** Only the library's own pages and plates.

## Motion

- The spread rises once when home opens (0.9s). Views fade in (0.35s). The frontispiece
  cross-fades when turned. Spines and covers lift a few pixels on hover. The light changes
  over 0.9s.
- Nothing loops and nothing moves while you read.
- `prefers-reduced-motion` removes all of it.

## Voice

Plain words a reader would use, never the machine's. "Read by a machine", not "OCR";
"the meaning", not "semantic"; "side-notes", not "annotations"; "plates", not "images".
Say what a thing does not show as plainly as what it shows.

## Assets

| Asset | Purpose | Medium | Source and licence | Status |
|---|---|---|---|---|
| EB Garamond (roman, italic, variable) | Text and interface | WOFF2 | Georg Duffner, Octavio Pardo; SIL OFL 1.1 | Final |
| IM Fell French Canon, Double Pica, English SC | Titles | WOFF2 | Igino Marini; SIL OFL 1.1 | Final |
| Sun device, swelled rule, chart stars | Mark and ornament | Inline SVG | Drawn for this project | Final |
| Page scans and plates | All imagery | The archive | The Holy Word library, under the recorded permission; never leaves the machine | As ingested |

## Visual QA record (14 November 2026)

Inspected as rendered pages in Microsoft Edge (headless, driven by Playwright from a
scratch environment), over a working copy of the real archive: 169 readable books, 35,257
pages, 235 index entries, 339 plates.

- **Desktop 1440×900:** home (noon, night), library, reader (noon, night), search, index,
  index entry (dawn), chart (noon, night, with a star in focus), plates, research (dusk),
  Joe (golden hour), colophon, sundial menu open.
- **Phone 390×844:** every section loaded and measured for sideways overflow (none); home,
  reader and the sundial menu inspected.
- **Tablet 900×1000:** home inspected; reader loaded and measured (no overflow).
- **States:** the curator's note above a book, the plate viewer open, a book not yet read.
- **Console and network:** no errors, warnings or failed requests on any page shot.
- **Corrected after the first look:** the title page ran below the fold (tightened, the
  epigraph moved under the spread); a drop capital split a verse number (now only on a word);
  verses ran together (paragraphs are now recovered from short lines); noise from unsure
  machine readings fed the margin (now withheld and said so); the chart's focus ring filled
  solid; the sundial vanished on phones; the page-turner overflowed on phones.

### Re-running the inspection

`scripts/room_walk.py` opens every section at desktop and phone width in day and night
light, fails on any console error, failed request or sideways scroll, and exercises the
keyboard and timing behaviours an independent review found broken and that were then fixed
(a slow page drawing over a newer one, focus lost in the sundial and the plate viewer, a
search choice forgotten, small words highlighted). `--shots <folder>` saves the pictures.

No screenshots are kept in this repository on purpose: they contain page scans, the rights
record denies exporting images, and this folder is synchronised to OneDrive. The record
above is therefore regenerated, not archived.

### Open issues

- No by-eye pass at tablet width beyond the reader, and none on a real phone.
- Contrast was computed for the five keyed palettes: body text 13.8 to 15.2, secondary
  text 5.2 to 7.8, accent 9.1 to 10.3, gilt 4.8 to 8.2 (the light-phase gilt was darkened
  from 4.1 to pass 4.5). The blended in-between states were not measured; they lie between
  two passing palettes of the same polarity.
- The frontispiece is drawn from machine-found plates, so some days it is a modern book
  cover and not an engraving.
- No screen-reader pass has been done.
