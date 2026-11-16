#!/usr/bin/env pwsh
<#
.SYNOPSIS
    One-command entry points for the AKI research engine.
.EXAMPLE
    .\scripts\aki.ps1 -Command setup
    .\scripts\aki.ps1 -Command inventory
    .\scripts\aki.ps1 -Command verify
    .\scripts\aki.ps1 -Command relocate   # move the archive out of OneDrive to C:\AKI (verified copy)
    .\scripts\aki.ps1 -Command library    # readable copies of the originals, by shelf
    .\scripts\aki.ps1 -Command finish     # backup, then read every remaining book and build search
    .\scripts\aki.ps1 -Command library-sync  # copy the readable Library into the project folder
    .\scripts\aki.ps1 -Command serve      # open the Felz Reading Room in your browser
.NOTES
    The archive home (database, vault, logs, backups) is read from aki.config.json, written by
    'relocate'. Without it, the archive is the .aki folder inside the project.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('setup', 'doctor', 'init', 'migrate', 'inventory', 'test', 'verify', 'backup', 'restore', 'coverage', 'jobs', 'finish', 'serve', 'library', 'library-sync', 'relocate', 'preflight', 'archive-backup', 'verify-backup')]
    [string]$Command,

    [string]$Path = '',
    [switch]$Confirm,
    [int]$Workers = 0,
    [int]$Port = 8765,
    [string]$To = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# AKI_VENV lets the launcher tests run a sandbox copy of the project with the real
# environment; normal use never sets it.
$venv = if ($env:AKI_VENV) { $env:AKI_VENV } else { Join-Path $root '.venv' }
$py = Join-Path $venv 'Scripts\python.exe'
$aki = Join-Path $venv 'Scripts\aki.exe'

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

function Get-AkiProcesses {
    # Any AKI work still running (ingest workers, finish, a reading room) blocks a move.
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='aki.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -match '(aki(\.exe)?["\s]|aki\.cli)' -and
                       $_.CommandLine -match '(ingest|finish|serve|relocate|library|media|semantic|concordance|plates)' -and
                       $_.ProcessId -ne $PID }
}

function Invoke-Preflight([string]$label) {
    $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $report = Join-Path $logs "preflight-$label-$stamp.json"
    $running = @(Get-AkiProcesses)
    if ($running.Count -gt 0) {
        $running | ForEach-Object { Write-Host "  still running: PID $($_.ProcessId) $($_.CommandLine)" }
        throw "AKI work is still running ($($running.Count) process(es)). Let it finish or close it, then try again."
    }
    & $aki preflight --out $report | Out-Null
    $code = $LASTEXITCODE
    $facts = Get-Content $report -Raw | ConvertFrom-Json
    Write-Host ("Archive: {0}  originals {1:N1} GiB, vault {2:N0} files / {3:N1} GiB, database {4:N1} GiB, free {5:N1} GiB" -f `
        $facts.archive_home, ($facts.originals_bytes / 1GB), $facts.vault_files, ($facts.vault_bytes / 1GB),
        ($facts.database_bytes / 1GB), ($facts.free_bytes_at_archive / 1GB))
    if ($code -eq 2) { throw "The archive looks busy (active job leases or ingestion within the last 2 minutes). See $report" }
    if ($code -ne 0) { throw "Preflight failed; see $report" }
    return $facts
}

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

    'preflight' {
        Assert-Venv
        $null = Invoke-Preflight 'manual'
    }

    'archive-backup' {
        Assert-Venv
        if ([string]::IsNullOrWhiteSpace($To)) { throw 'Choose where the backup goes: -To <folder> (ideally another drive)' }
        $null = Invoke-Preflight 'backup'
        & $aki archive-backup --to $To
        if ($LASTEXITCODE -ne 0) { throw 'The archive backup failed or did not pass its restore test. Nothing was changed.' }
    }

    'verify-backup' {
        Assert-Venv
        if ([string]::IsNullOrWhiteSpace($Path)) { throw 'Restore-test which backup? -Path <backup folder>' }
        & $aki verify-backup $Path
        if ($LASTEXITCODE -ne 0) { throw 'The backup did not pass its restore test.' }
    }

    'relocate' {
        Assert-Venv
        if ($relocated) {
            Write-Host "The archive already lives at $archive. Nothing to move."
            return
        }
        $target = if (-not [string]::IsNullOrWhiteSpace($To)) { $To } elseif ($env:AKI_RELOCATE_TO) { $env:AKI_RELOCATE_TO } else { 'C:\AKI' }
        $null = Invoke-Preflight 'relocate'
        Write-Host "Moving the archive out of OneDrive to $target."
        Write-Host 'Every file is copied and re-hashed; nothing in OneDrive is deleted.'
        & $aki relocate --to $target
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
        Assert-Venv
        if (-not $relocated) { Write-Host 'The Library is already in the project folder.'; return }
        Write-Host 'Checking every file in the project Library by hash, copying what is missing...'
        & $aki library-sync --to (Join-Path $root 'Library')
        if ($LASTEXITCODE -ne 0) { throw 'Library sync failed; see the library-sync report in the logs folder' }
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

    'doctor' {
        Assert-Venv
        & $aki doctor
    }

    'init' {
        Assert-Venv
        & $aki init
    }

    'migrate' {
        Assert-Venv
        & $aki migrate
    }

    'inventory' {
        Assert-Venv
        $config = Join-Path $archive 'holyword.json'
        if (-not (Test-Path $config)) {
            throw "Catalog config not found at $config. See docs/execution/DEPLOYMENT_RUNBOOK.md."
        }
        & $aki inventory sync --config $config
        & $aki inventory report
    }

    'coverage' {
        Assert-Venv
        & $aki coverage
    }

    'jobs' {
        Assert-Venv
        & $aki jobs status
        & $aki jobs recover
        & $aki jobs run --max-jobs 25
    }

    'test' {
        Assert-Venv
        & $py -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw 'Test suite failed' }
        & $py -m ruff check src tests
        if ($LASTEXITCODE -ne 0) { throw 'Lint failed' }
        Write-Host 'Tests and lint passed.'
    }

    'verify' {
        Assert-Venv
        & $aki verify
        if ($LASTEXITCODE -ne 0) { throw 'Integrity verification failed' }
        & $aki coverage
    }

    'backup' {
        Assert-Venv
        if ([string]::IsNullOrWhiteSpace($Path)) {
            $stamp = Get-Date -Format 'yyyyMMddTHHmmssZ'
            $Path = Join-Path $archive "backups\aki-$stamp.sqlite3"
        }
        & $aki backup $Path
    }

    'restore' {
        Assert-Venv
        if ([string]::IsNullOrWhiteSpace($Path)) {
            throw 'Restore requires -Path <backup-file> -Confirm'
        }
        if (-not $Confirm) {
            throw 'Restore is destructive. Re-run with -Confirm after verifying the backup path.'
        }
        & $aki restore $Path --confirm
    }
}

exit 0
