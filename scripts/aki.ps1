#!/usr/bin/env pwsh
<#
.SYNOPSIS
    One-command entry points for the AKI research engine.
.EXAMPLE
    .\scripts\aki.ps1 -Command setup
    .\scripts\aki.ps1 -Command inventory
    .\scripts\aki.ps1 -Command verify
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('setup', 'doctor', 'init', 'migrate', 'inventory', 'test', 'verify', 'backup', 'restore', 'coverage', 'jobs')]
    [string]$Command,

    [string]$Path = '',
    [switch]$Confirm
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root '.venv\Scripts\python.exe'
$aki = Join-Path $root '.venv\Scripts\aki.exe'
$database = Join-Path $root '.aki\aki.sqlite3'

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
    & $py -m pip install -e '.[dev]'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    Write-Host 'Environment ready.'
}

switch ($Command) {
    'setup' { Invoke-Setup }

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
        $config = Join-Path $root '.aki\holyword.json'
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
            $Path = Join-Path $root ".aki\backups\aki-$stamp.sqlite3"
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
