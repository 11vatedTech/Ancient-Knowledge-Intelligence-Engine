# Deployment Runbook

Local-first deployment on Windows 11. All state lives under the project directory except the Python toolchain.

---

## 0. Prerequisites

| Requirement | Check |
|---|---|
| Python 3.11+ (3.14.6 verified) | `py --version` |
| Git | `git --version` |
| ~2 GB free disk for the environment and vault | `Get-PSDrive C` |

Optional: NVIDIA driver + `nvidia-smi` (detected, not required), Ollama (detected, not required — one GPU-heavy job at a time is the default policy).

---

## 1. Install

```powershell
.\scripts\aki.ps1 -Command setup
```

Creates `.venv`, installs the package editable with the `dev` extra. Equivalent manual form:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

---

## 2. Environment verification

```powershell
.\scripts\aki.ps1 -Command doctor
```

Reports OS, Python, CPU, RAM, GPU/VRAM, disk, and tool versions. Anything not measurable is `null` — never guessed. Also prints the derived scheduling budget.

---

## 3. Initialise the database

```powershell
.\scripts\aki.ps1 -Command init
```

Creates `.aki/aki.sqlite3` and applies migrations. Safe to re-run: `applied` will be empty.

---

## 4. Catalog credentials (only for live census)

Create `.aki/holyword.json` (gitignored, never commit):

```json
{
  "base_url": "https://<project-ref>.supabase.co",
  "anon_key": "<public anon key>",
  "table": "books",
  "page_size": 200,
  "request_interval_seconds": 1.0
}
```

The `anon_key` is the key the public site itself ships to anonymous visitors in its own client bundle; it grants only the reads that unauthenticated visitors already perform. It is still treated as a local credential and kept out of version control.

Alternatively, skip this step entirely and import a snapshot:

```powershell
.\scripts\aki.ps1 -Command inventory
# or
.\.venv\Scripts\aki.exe inventory import data\holyword\inventory_snapshot.json
```

---

## 5. Census and rights

```powershell
.\scripts\aki.ps1 -Command inventory     # enumerate + import
.\.venv\Scripts\aki.exe inventory report  # factual acquisition report
```

Every imported record lands in `review_required` with **no permitted operations**. Nothing is downloaded.

To clear an edition you hold rights for:

```powershell
.\.venv\Scripts\aki.exe rights grant `
  --edition <edition-id> `
  --state cleared_limited `
  --operations store_original,render_pages,ocr_transcribe,internal_search,internal_research `
  --reviewer "<your name>" `
  --license-name "<licence>" `
  --evidence-uri "<url-or-file>" `
  --notes "<what was granted, for which operations>"
```

The grant is re-checked on every processing run. Removing an operation blocks it immediately for future runs.

---

## 6. Ingest a cleared edition

Direct:

```powershell
.\.venv\Scripts\aki.exe ingest <edition-id> "D:\Sources\edition.pdf" --dpi 300
```

Durable queue (survives process exit):

```powershell
.\.venv\Scripts\aki.exe jobs enqueue-ingest --edition-id <edition-id> --pdf "D:\Sources\edition.pdf"
.\.venv\Scripts\aki.exe jobs run --max-jobs 25
```

`--dpi` defaults to 300. Preflight rejects encrypted PDFs, executables, oversize files, and page-count overflow before anything is archived.

---

## 7. Search and cite

```powershell
.\.venv\Scripts\aki.exe search "geneva bible" --limit 10
.\.venv\Scripts\aki.exe search "psalms" --mode phrase
.\.venv\Scripts\aki.exe search "archive" --edition <edition-id>
.\.venv\Scripts\aki.exe cite <evidence-id>
```

---

## 8. Research

```powershell
.\.venv\Scripts\aki.exe research "What does the source say about <topic>?" `
  --limit 12 --out-dir .aki\dossiers --show-dossier
```

Exit code 1 means the independent verifier rejected at least one claim (typically an unresolvable citation). The dossier is still written, with the failure visible.

Re-verify a case later:

```powershell
.\.venv\Scripts\aki.exe case <case-id>
.\.venv\Scripts\aki.exe cases --limit 20
```

---

## 9. Backup

```powershell
.\scripts\aki.ps1 -Command backup
# or to a specific path
.\scripts\aki.ps1 -Command backup -Path D:\Backups\aki.sqlite3
```

Uses SQLite's online backup API, then reopens the copy and runs `quick_check` + `foreign_key_check`. The SHA-256 of the backup is printed. A backup that fails verification raises.

**The vault is not in the database backup.** Back up `.aki/blobs/` separately (it is append-only and content-addressed, so a file-sync copy is safe).

---

## 10. Restore (destructive — verify first)

```powershell
.\scripts\aki.ps1 -Command verify
.\scripts\aki.ps1 -Command restore -Path .aki\backups\aki-20261112T120000Z.sqlite3 -Confirm
```

Restore refuses without `-Confirm`, validates the backup's integrity before touching the live file, and writes a timestamped `.pre-restore-*` safety copy of the current database.

---

## 11. Health checks

```powershell
.\scripts\aki.ps1 -Command verify     # DB + vault integrity
.\scripts\aki.ps1 -Command coverage   # corpus counters
.\scripts\aki.ps1 -Command test       # pytest + ruff
.\.venv\Scripts\aki.exe jobs status
```

`verify` exits non-zero if any archived asset is missing or fails its hash.

---

## 12. Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| `PermissionError: rights state 'review_required' does not permit ...` | Correct fail-closed behaviour | Record a rights decision (§5) |
| `PolicyViolation: host resolves to non-public address` | SSRF guard | Use a public URL or a local file |
| `PDF support is optional; install AKI with the 'documents' extra` | PyMuPDF missing | `pip install -e ".[documents]"` |
| `database schema version N does not match required M` | Stale database | `aki migrate` |
| `migration checksum mismatch` | Schema tampered with | Restore from backup; do not edit `schema_migrations` |
| `UnicodeEncodeError` in a third-party pipe | Consumer is not UTF-8 | Data files are UTF-8; the CLI forces UTF-8 on its own streams |
| Empty search results after ingest | PDF had no text layer (image-only scan) | OCR is not implemented; see `KNOWN_LIMITATIONS.md` §2 |

---

## 13. Where state lives

| Item | Path | In git? |
|---|---|---|
| Database | `.aki/aki.sqlite3` | no |
| Vault | `.aki/blobs/sha256/` | no |
| Dossiers | `.aki/dossiers/` | no |
| Backups | `.aki/backups/` | no |
| Catalog credentials | `.aki/holyword.json` | no |
| Census snapshot | `data/holyword/inventory_snapshot.json` | no: kept on the owner's computer |
| Raw listing cache | `data/holyword/books_raw.json` | no |
