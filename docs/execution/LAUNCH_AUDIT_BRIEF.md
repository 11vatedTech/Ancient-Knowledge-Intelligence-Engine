# Launch Audit Brief — Felz Reading Room, before the full-corpus run (revision 3)

**Prepared:** 2026-11-13 (by Claude, for a read-only audit by Freebuff or a person)
**State at writing:** `Finish the reading room.cmd` has **not** been run. No `finish-report.json`
exists. The database is still at schema version 2 on disk; migration 3 is applied by the
launcher. Library export: 109 of 257 files copied (106 PDF, 3 images); no partial `.part` files.

The audit should be **read-only**: do not run the launcher, `finish`, `migrate` or
`restore` against `.aki/aki.sqlite3`. Use a copy of the database for anything that writes.

---

## 00. Revision 3 — gated on Windows execution (2026-11-13, late evening)

The owner's verdict: approve the direction; keep the real migration gated on Windows
execution and a retrospective audit of the 109 pre-existing Library files. The approved
sequence and its state:

| # | Step | State |
|---|---|---|
| 1 | Nine-defect audit against the actual changed files | Delivered files re-hashed on the device against the tested build (see §01) |
| 2 | Launcher and PowerShell tests on Windows | **Harness built, not yet run**: `Test the launchers.cmd` → `scripts/test-launcher.ps1` |
| 3 | Audit the 109 existing Library files | **Done**: 110/110 file–edition rows authorized and byte-identical (`LIBRARY_RETROSPECTIVE_AUDIT.md/.csv`) |
| 4 | Check for active workers; fresh size and space measurement | Built into `relocate` and `archive-backup` (`aki preflight` + Windows process check) |
| 5 | Create and verify a recoverable backup | `Back up the archive.cmd` → `aki archive-backup` with restore test (ADR-021) |
| 6 | Move to `C:\AKI`, verify, retain source | `relocate` refuses without step 5; checks completeness and hashes; source kept |
| 7 | Library export, rights and hashes | Export gated on `store_original`; sync hash-checks every destination file (ADR-022) |
| 8 | Process the remaining corpus | `finish`, unchanged from revision 2 apart from EPUB resume |
| 9 | Full integrity, citation and reading-room checks | `serve --check`, `aki verify`, and the §5.6 queries must be re-run after step 8 |

### What the Windows harness executes (Windows PowerShell 5.1, sandbox paths with spaces)

| Scenario | Failure injected | Must hold |
|---|---|---|
| Environment | — | Project environment imports every component |
| 1 Complete run | none | Backup + restore test pass; all five steps; config switched; old archive kept with MOVED.txt; Library files hash-identical; serve check passes |
| 2 Step 1 | no verified backup | Exit ≠ 0; archive not moved and intact; reading room not opened |
| 3 Step 1 | target already holds a database | Exit ≠ 0; source vault unchanged; no config; not opened |
| 4 Step 2 | Library folder unwritable | Exit ≠ 0; no pages processed; not opened |
| 5 Step 3 | pre-processing backup cannot be written | Exit ≠ 0; no pages processed; no report; not opened |
| 6 Step 3 | a book that cannot be read (encrypted PDF) | Exit ≠ 0; report lists it; step 4 not run; not opened |
| 7 Step 4 | project Library unwritable | Exit ≠ 0 after successful processing; not opened |
| 8 Interruption | processing killed mid-book (400-page synthetic) | Re-run exits 0; every page and transcription present; integrity ok |
| 9 Interruption | move left partial: truncated file, `.part`, half database | Re-run exits 0; every vault file hash-identical |
| 10 Hash check | same-size damaged Library copy | Detected, quarantined, replaced with a verified copy |
| 11 Busy archive | a reading room left running | Preflight refuses |

Test-only switches, unused in normal operation: `AKI_VENV` (sandbox uses the real
environment), `AKI_NO_PAUSE`, `AKI_SERVE_CHECK` (serve exercises its routes and exits, no
browser), `AKI_RELOCATE_TO`.

### Changed in revision 3
New: `src/aki/archive_backup.py`, `src/aki/library_sync.py`, `scripts/make_test_archive.py`,
`scripts/test-launcher.ps1`, `Back up the archive.cmd`, `Test the launchers.cmd`,
`docs/execution/LIBRARY_RETROSPECTIVE_AUDIT.md/.csv`, ADR-021/022.
Modified: `src/aki/home.py` (backup gate, database last, completeness check, resume),
`src/aki/cli_room.py` (archive-backup, verify-backup, library-sync, preflight, serve --check),
`src/aki/library_export.py` (edition ids in catalogue), `src/aki/media.py` (EPUB resume),
`scripts/aki.ps1`, both earlier launchers, tests (73 passing).

---

## 0. Revision 2 — defects fixed (2026-11-13, evening)

All nine defects in §4 were fixed before any launch. Tests: 67 passing (58 earlier + 9 in
`tests/test_launch_safety.py`). The launcher was still **not** run.

| ID | Fix | Where | Tested by |
|---|---|---|---|
| D1 | Library export gated on `store_original`, SHA-256 verified per copy, missing originals reported (exit 1), one `library_export` audit event per run; ADR-016 | `src/aki/library_export.py`, `aki library-export`; `scripts/build_library.py` is now a wrapper | `test_library_export_is_gated_idempotent_and_audited`, `test_library_export_replaces_a_damaged_copy` |
| D2 | Launcher checks `errorlevel` after every step and stops before `serve` | `Finish the reading room.cmd` | Read-only review |
| D3 | `finish` exits 1 on failed workers, failed image/EPUB books, missing meaning index or failed integrity check, and lists them | `cli_room._problems` | `test_finish_reports_worker_and_stage_failures`; real failing workers on a sample copy exited 1 |
| D4 | Research findings filtered by `internal_research` | `app/api.py` | `test_api_enforces_render_and_research_rights` |
| D5 | Thumbnails require `render_pages` | `app/server.py` | same test |
| D6 | Vault manifest written with every pre-finish backup; backups documented as retention under the archive's scope; ADR-018 | `aki vault-manifest`, `aki.ps1 finish` | Manual run on sample |
| D7 | Progress log `logs/finish-<stamp>.log`, per-worker logs `logs/finish-<stamp>-worker<n>.log`, timestamped reports, backup JSON kept | `cli_room.py`, `aki.ps1` | Sample run produced all files |
| D8 | Launcher parses the backup JSON and stops unless integrity is `ok` with 0 foreign-key violations (in addition to `Database.backup` refusing a corrupt copy) | `aki.ps1 finish` | Read-only review |
| D9 | `aki relocate --to C:\AKI`: online DB backup, every vault file re-hashed, refuses cloud/nested targets, existing DB or low disk space (needs archive size + 15 GiB), writes `aki.config.json`, leaves old `.aki` with `MOVED.txt`; all commands follow the config; ADR-017 | `src/aki/home.py`, `cli.py` defaults, `aki.ps1` | `test_relocate_*` (4 tests), `test_env_overrides_config`; end-to-end on a sample copy |

New launcher flow: **relocate → library (local) → finish → library-sync (only files the
project `Library/` lacks; robocopy `/XC /XN /XO`) → serve.**

Not verifiable here and worth checking on the Windows machine: the PowerShell script was
reviewed by reading but not executed (no PowerShell available in the build environment);
robocopy behaviour; free space on `C:` (expected archive size ~15–17 GiB before the
remaining books, plus the 15 GiB headroom the relocation requires).

---

## 1. What changed

### New files
| Path | Purpose |
|---|---|
| `Finish the reading room.cmd` | Launcher: library export → finish → serve |
| `Open the reading room.cmd` | Launcher: serve only |
| `scripts/build_library.py` | Readable copies of originals into `Library/<shelf>/<title>.<ext>` + `_catalog.csv` |
| `src/aki/gate.py` | Edition-level rights check for derived operations |
| `src/aki/media.py` | Ingestion of the 8 non-PDF editions (image OCR, EPUB reflow) |
| `src/aki/semantic.py` | Meaning index: model2vec, fallback corpus LSA; derived files in `.aki/derived/semantic` |
| `src/aki/concordance.py`, `src/aki/concordance_terms.py` | Curated term list → `entities`, `entity_aliases`, `mentions` |
| `src/aki/plates.py` | Plate heuristic → `visual_observations` (+ evidence rows for plates) |
| `src/aki/cli_room.py` | CLI: `catalog-enrich`, `media-ingest`, `semantic`, `concordance`, `plates`, `serve`, `finish` |
| `src/aki/app/__init__.py`, `api.py`, `server.py`, `memorial.json` | Loopback-only FastAPI service |
| `src/aki/app/static/index.html`, `app.css`, `app.js`, `sun.svg` | Reading room UI (no third-party code) |
| `tests/test_reading_room.py` | 11 tests for the above |
| `src/aki/home.py` | Archive home (`aki.config.json`, `AKI_HOME`) and verified relocation (rev. 2) |
| `src/aki/library_export.py` | Rights-gated, audited Library export (rev. 2) |
| `tests/test_launch_safety.py` | 9 tests for relocation, export, failure reporting, API rights (rev. 2) |

### Modified files
| Path | Change |
|---|---|
| `src/aki/db.py` | Migration 3: indexes only (mentions, aliases, entities, observations, pages, chunks). No data shape change |
| `src/aki/cli.py` | Registers the commands in `cli_room.py` (3 lines) |
| `src/aki/research.py` | Question words (what, did, the …) dropped from research search terms |
| `scripts/aki.ps1` | New commands `finish`, `serve`, `library`; `setup` installs `.[dev,room]` |
| `pyproject.toml` | `room` extra; `httpx` in `dev`; package data for the UI |
| `tests/test_canonical_store.py` | Schema version assertion follows `SCHEMA_VERSION` |
| `scripts/build_library.py` | Now a thin wrapper over `aki library-export` (rev. 2) |
| `docs/execution/DECISION_LOG.md` | ADR-016 to ADR-020 (rev. 2) |
| `README.md`, `docs/execution/KNOWN_LIMITATIONS.md` | Documentation of the above |

### Leftovers safe to delete
`.aki/tmp/devsubset.tar` (52 MB test copy of 5 books' pages).

---

## 2. The launchers, verbatim (revision 2)

`Finish the reading room.cmd`
```bat
@echo off
setlocal
title Finishing the Felz Reading Room
cd /d "%~dp0"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\aki.ps1""

echo Step 1 of 5: moving the archive out of OneDrive to C:\AKI (copied and verified; nothing is deleted)
%PS% -Command relocate
if errorlevel 1 goto failed

echo.
echo Step 2 of 5: writing readable copies of the books
%PS% -Command library
if errorlevel 1 goto failed

echo.
echo Step 3 of 5: reading every remaining book and building search. This takes hours.
echo You can close this window at any time; running this file again resumes where it stopped.
%PS% -Command finish
if errorlevel 1 goto failed

echo.
echo Step 4 of 5: copying the readable Library into the project folder for OneDrive
%PS% -Command library-sync
if errorlevel 1 goto failed

echo.
echo Step 5 of 5: opening the reading room
%PS% -Command serve
goto end

:failed
echo.
echo The run stopped at the step above. Nothing was deleted, and the reading room was not opened.
echo The logs are in the archive's logs folder (C:\AKI\logs once the archive has moved).
echo Fix the problem it describes, then run this file again to resume.

:end
pause
```

`Open the reading room.cmd`
```bat
@echo off
setlocal
title The Felz Reading Room
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\aki.ps1" -Command serve
if errorlevel 1 echo The reading room could not start. Run "Finish the reading room.cmd" first.
pause
```

`scripts/aki.ps1`, from the top of the script body to the new commands (the remaining
commands are unchanged apart from reading the archive home)
```powershell
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root '.venv\Scripts\python.exe'
$aki = Join-Path $root '.venv\Scripts\aki.exe'

function Get-ArchiveHome {
    if ($env:AKI_HOME) { return $env:AKI_HOME }
    $configFile = Join-Path $root 'aki.config.json'
    if (Test-Path $configFile) {
        $config = Get-Content $configFile -Raw | ConvertFrom-Json
        if ($config.home) { return $config.home }
    }
    return (Join-Path $root '.aki')
}
$archive = Get-ArchiveHome
$database = Join-Path $archive 'aki.sqlite3'
$logs = Join-Path $archive 'logs'
$relocated = $archive -ne (Join-Path $root '.aki')

function Write-Log([string]$name, [string]$text) {
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    Add-Content -Path (Join-Path $logs $name) -Value $text -Encoding UTF8
}

function Assert-Venv {
    if (-not (Test-Path $py)) {
        throw "Virtual environment missing. Run: .\scripts\aki.ps1 -Command setup"
    }
}

function Invoke-Setup {
    if (-not (Test-Path $py)) {
        Write-Host 'Creating virtual environment (.venv)...'
        py -m venv .venv
    }
    & $py -m pip install --upgrade pip
    & $py -m pip install -e '.[dev,room]'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    Write-Host 'Environment ready.'
}

function Assert-Room {
    Assert-Venv
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $py -c "import fastapi, uvicorn, sklearn, model2vec" *> $null
    $missing = $LASTEXITCODE -ne 0
    $ErrorActionPreference = $previous
    if ($missing) {
        Write-Host 'Installing the reading room components (one time)...'
        & $py -m pip install -e '.[dev,room]'
        if ($LASTEXITCODE -ne 0) { throw 'Could not install the reading room components' }
    }
}

switch ($Command) {
    'setup' { Invoke-Setup }

    'relocate' {
        Assert-Venv
        if ($relocated) {
            Write-Host "The archive already lives at $archive. Nothing to move."
            return
        }
        Write-Host "Moving the archive out of OneDrive to $To."
        Write-Host 'Every file is copied and re-hashed; nothing in OneDrive is deleted.'
        & $aki relocate --to $To
        if ($LASTEXITCODE -ne 0) { throw 'The archive was not moved. The project still uses .aki in OneDrive.' }
    }

    'library' {
        Assert-Venv
        $target = if ($relocated) { Join-Path $archive 'Library' } else { Join-Path $root 'Library' }
        Write-Host "Writing readable copies to $target"
        & $aki library-export --out $target
        if ($LASTEXITCODE -ne 0) { throw 'The Library export stopped with an error' }
    }

    'library-sync' {
        if (-not $relocated) { Write-Host 'The Library is already in the project folder.'; return }
        $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
        $log = Join-Path $logs "library-sync-$stamp.log"
        New-Item -ItemType Directory -Force -Path $logs | Out-Null
        Write-Host 'Copying books the project Library does not have yet (existing copies are left alone)...'
        robocopy (Join-Path $archive 'Library') (Join-Path $root 'Library') /E /XC /XN /XO /R:2 /W:5 /NP /NFL /NDL /LOG:$log | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Library sync failed; see $log" }
        $global:LASTEXITCODE = 0
        Write-Host "Library synced. Log: $log"
    }

    'finish' {
        Assert-Room
        $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
        $backups = Join-Path $archive 'backups'
        Write-Host 'Taking a verified backup of the database before processing...'
        $backupJson = (& $aki backup (Join-Path $backups "aki-before-finish-$stamp.sqlite3")) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw 'Backup failed; nothing was changed' }
        Write-Log "finish-$stamp-backup.json" $backupJson
        $backup = $backupJson | ConvertFrom-Json
        if ($backup.integrity.quick_check -ne 'ok' -or $backup.integrity.foreign_key_violations -ne 0) {
            throw "Backup did not pass its integrity check; nothing was changed. See logs\finish-$stamp-backup.json"
        }
        Write-Host "Backup verified: $($backup.path)"
        & $aki vault-manifest (Join-Path $backups "vault-manifest-$stamp.json") | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not write the vault manifest; nothing was changed' }
        $finishArgs = @('finish', '--log-file', (Join-Path $logs "finish-$stamp.log"))
        if ($Workers -gt 0) { $finishArgs += @('--workers', "$Workers") }
        & $aki @finishArgs
        if ($LASTEXITCODE -ne 0) { throw "Processing finished with problems. Read $logs\finish-$stamp.log, then re-run to resume." }
        Write-Host ''
        Write-Host "Finished. Log: $logs\finish-$stamp.log"
    }

    'serve' {
        Assert-Room
        & $aki serve --port $Port --open
    }

```

`aki finish` (in `src/aki/cli_room.py`) runs, in order: `initialize` (migration 3) →
`catalog-enrich` → N parallel `ingest-batch --ocr` workers (default min(6, CPUs − 2)) →
`media-ingest` → `semantic build --provider auto` → `concordance build` → `plates` →
integrity check → report; exit 1 if any problem was found.

---

## 3. Rights: the record, and what each operation checks

**The record.** `docs/execution/PERMISSION_ATTESTATION.md`, attestation
`456d3244-229c-484a-8d3c-b30cd06d3bbc`, recorded 2026-11-12T16:24:53Z, state
`cleared_limited` on 257 editions. Basis: a person stated in the session that they are The
Holy Word and a friend of [name withheld] and Joe Felz. It is **self-attested**: no signed licence,
no verified identity. It was recorded *after* `research/THE_HOLY_WORD_CATALOG_SNAPSHOT.md`
(which correctly said no bulk download was authorised at that point) and *before*
acquisition. That ordering is the reconciliation: no permission existed, then this
assertion was recorded, then acquisition ran under it.

Granted: `acquire, store_original, render_pages, ocr_transcribe, vision_analyze,
create_embeddings, internal_search, internal_research`.
Denied: `export_text, export_images, share_with_third_parties, redistribute_original,
commercial_use, fine_tune_model`.

| Operation asked about | Recorded operation | Enforced in code? |
|---|---|---|
| Download and retain source files | `acquire`, `store_original` | Yes (`acquire.py`) |
| Local archival copies (vault) | `store_original` | Yes |
| **Readable Library copies** | none fits exactly; nearest is `store_original`, arguably `export_*` | Yes since rev. 2: `store_original`, audited (ADR-016; was defect D1) |
| OCR and text extraction | `ocr_transcribe` (+ `render_pages`) | Yes (`ingest._require`; `media.py` reuses it) |
| Image processing, plate detection | `render_pages`, `vision_analyze` | Yes (`plates.py` via `gate.py`); thumbnails also require `render_pages` since rev. 2 (was D5) |
| Local indexing, meaning search | `internal_search`, `create_embeddings` | Yes (`semantic.py` via `gate.py`; `api.py`) |
| Derived research indexes and citations | `internal_research` | Yes: concordance and, since rev. 2, the research API (was D4) |
| Local backups of the database | none listed | Not an operation in the model; documented as retention under the archive's scope (ADR-018; was D6) |

Two facts the audit should weigh rather than resolve: the whole project, vault included,
sits in a OneDrive-synchronised folder, so originals and derived text are already stored
by a cloud provider; and localhost-only serving limits *who can reach* the reading room,
but does not by itself establish permission for any operation above.

---

## 4. Defects found in self-review of revision 1 (all fixed in revision 2, see §0)

| ID | Severity | Defect | Proposed fix |
|---|---|---|---|
| D1 | High | Library export bypasses the rights gate and the audit log | Gate on `store_original`; record an `audit_events` row per run; or record a new explicit decision for "local readable copy" |
| D2 | High | The `.cmd` launcher does not stop when a step fails: a failed `finish` still proceeds to `serve` | `if errorlevel 1 goto failed` after each step |
| D3 | High | `aki finish` exits 0 even when an ingest shard exits non-zero; codes are only recorded | Non-zero exit and a clear message if any shard fails |
| D4 | Medium | `/api/research` does not check `internal_research` | Filter findings through `permitted_editions(…, "internal_research")` |
| D5 | Low | Thumbnail cache is derived image output, gated only by `internal_search` | Also require `render_pages` |
| D6 | Medium | Backups are not a rights operation; backup covers the database only, never the vault | Document; optionally add a vault manifest backup |
| D7 | Medium | No preserved execution log: console output is not saved and shard logs are overwritten (`finish-shard{n}.out`, mode `w`) each run | `Start-Transcript` to `.aki/logs/finish-<stamp>.log`; timestamped shard logs |
| D8 | Medium | The launcher checks the backup's exit code but not its `integrity == ok` field | Parse the JSON and stop unless integrity is `ok` |
| D9 | Medium | OneDrive sync overhead and file locking on `.aki/` (WAL database, `derived/semantic` directory swap, thousands of thumbnail files) | Relocate the canonical archive outside OneDrive; see §6 |

---

## 5. The six audit checks, with how to verify each

1. **Launchers.** Read §2. Confirm D2, D7, D8. Check `-ExecutionPolicy Bypass` is acceptable to the owner.
2. **Backup and restore.** `Database.backup` (online backup, reopened, integrity-checked, SHA-256) and `Database.restore` (requires `--confirm`, verifies the candidate, keeps a `.pre-restore-<stamp>` safety copy) in `src/aki/db.py`. Exercise on a **copy**: `aki --database <copy> backup <dest>` then `aki --database <copy2> restore <dest> --confirm`.
3. **Rights checks.** Walk the table in §3 against `gate.py`, `ingest._require`, `media.py`, `semantic.build_index`, `concordance.build`, `plates.detect`, `app/server.Library.editions`, `app/api.py`, `scripts/build_library.py`.
4. **Library idempotency and paths.** `build_library.py` skips a file whose size already matches (size, not hash), writes via `.part` then `os.replace`, and numbers duplicate titles deterministically (rows sorted by source URL). Longest current target is 223 characters, under 260. Confirm D1.
5. **Model download and offline behaviour.** `semantic.resolve_embedder("auto")` tries `model2vec` (`minishlab/potion-retrieval-32M`, Hugging Face cache in the user profile) and falls back to LSA on any load error. The provider used is written to `.aki/derived/semantic/meta.json` and `model_runs`. Verify by running `semantic build --provider lsa` on a database copy with networking off.
6. **Citation resolution across the corpus.** Read-only check on the real database: every `evidence` row with `evidence_kind='passage'` should resolve via `aki cite <id>` to edition → page → page image → SHA-256, and every `pages.page_image_key` should exist under `.aki/blobs`. A query to start:
   `SELECT COUNT(*) FROM evidence e LEFT JOIN pages p ON p.id=e.page_id WHERE e.evidence_kind='passage' AND p.id IS NULL;` (expect 0).

   **Already measured read-only on 2026-11-13 (current, pre-launch corpus):**

   | Check | Result |
   |---|---|
   | Passage evidence rows | 34,370 |
   | Passages whose page is missing | 0 |
   | Search chunks whose evidence is missing | 0 |
   | Pages whose asset is missing | 0 |
   | Distinct page images referenced | 34,433 (35,257 pages; identical pages share one content-addressed image) |
   | Page images missing from the vault | 0 |
   | Originals re-hashed (all 258) | 258 match their recorded SHA-256 |

   This covers the 140+ editions already ingested. It must be repeated after the launcher
   runs, because the remaining ~105 PDF editions and the 8 media editions do not exist yet.

---

## 6. Archive location (OneDrive)

The engine already accepts `--database` and `--blob-root`, so the canonical archive can live
outside OneDrive (for example `C:\AKI\`) without code changes beyond the launcher and
`aki.ps1` passing those paths. Suggested order: verified backup → copy `.aki` to the new
location with robocopy → `aki verify` there (all 258 SHA-256 must match) → point the
launcher at it → only then retire the OneDrive copy. `Library/` can be generated locally
and synchronised afterwards, or not at all.

## 7. Immutability boundary

Originals in `.aki/blobs/sha256/…` are never modified by any new code: image editions use
the original bytes as their page image (read only), and the Library export copies them out.
Everything else (OCR text, EPUB layout pages, embeddings, concordance rows, plate
observations, thumbnails, research cases) is derived, rebuildable, and points back to an
edition and page.

## 8. Not yet proven

Full-corpus processing; OCR accuracy (no human validation, no CER/WER); performance at
~50,000+ pages (meaning index memory, concordance build time, co-occurrence queries for very
common names such as "God"); the reading room on real data beyond the 5-book sample.

## 9. Content note for curation

Titles are shown as listed by The Holy Word. The corpus includes material such as *The
Protocols of the Learned Elders of Zion*, a documented antisemitic forgery, filed under
"Ancient Texts". The archive preserves it as listed; whether the reading room should carry
context notes on such items is a curation decision for the owner.
