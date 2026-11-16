<#
.SYNOPSIS
    Execute the real launchers on Windows against throwaway synthetic archives.
.DESCRIPTION
    Every scenario builds its own small archive (scripts/make_test_archive.py) in a sandbox
    under %TEMP% whose paths contain spaces, runs the real .cmd launchers and aki.ps1 with
    the project's own Python environment, injects one failure, and checks what happened.
    The real archive, database and Library are never read or written.

    Results: test-results\launcher-test-<stamp>.md (+ .json and the run logs).
#>
[CmdletBinding()]
param([int]$LongPages = 400)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $repo '.venv'
$py = Join-Path $venv 'Scripts\python.exe'
$aki = Join-Path $venv 'Scripts\aki.exe'
$stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$base = Join-Path $env:TEMP "AKI launcher test $stamp"
$outDir = Join-Path $repo 'test-results'
$logDir = Join-Path $outDir "launcher-test-$stamp"
New-Item -ItemType Directory -Force -Path $base, $logDir | Out-Null
$results = New-Object System.Collections.ArrayList

Remove-Item Env:AKI_HOME -ErrorAction SilentlyContinue
$env:AKI_VENV = $venv
$env:AKI_NO_PAUSE = '1'
$env:AKI_SERVE_CHECK = '1'

function Add-Result([string]$scenario, [bool]$passed, [string]$detail) {
    [void]$results.Add([pscustomobject]@{ Scenario = $scenario; Passed = $passed; Detail = $detail })
    $mark = if ($passed) { 'PASS' } else { 'FAIL' }
    Write-Host ("[{0}] {1}: {2}" -f $mark, $scenario, $detail)
}

function New-Sandbox([string]$name, [int]$long = 12, [switch]$BadBook) {
    $root = Join-Path $base $name
    $project = Join-Path $root 'project with spaces'
    New-Item -ItemType Directory -Force -Path (Join-Path $project 'scripts') | Out-Null
    Copy-Item (Join-Path $repo 'scripts\aki.ps1') (Join-Path $project 'scripts\aki.ps1')
    Get-ChildItem -Path $repo -Filter '*.cmd' | Copy-Item -Destination $project
    $build = @((Join-Path $repo 'scripts\make_test_archive.py'), $project, '--long-pages', "$long")
    if ($BadBook) { $build += '--bad-book' }
    & $py @build | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "could not build the test archive for $name" }
    return [pscustomobject]@{
        Name = $name; Root = $root; Project = $project
        Source = (Join-Path $project '.aki'); Home = (Join-Path $root 'AKI home')
        Backup = (Join-Path $root 'backup drive\AKI backup')
    }
}

function Invoke-Launcher($sb, [string]$file, [string[]]$extra = @()) {
    $env:AKI_RELOCATE_TO = $sb.Home
    $log = Join-Path $logDir ("{0}-{1}-{2}.log" -f $sb.Name, ($file -replace '[^A-Za-z]', ''), (Get-Date -Format 'HHmmssfff'))
    $quoted = ($extra | ForEach-Object { ' "' + $_ + '"' }) -join ''
    $line = '/c ""' + (Join-Path $sb.Project $file) + '"' + $quoted + ' > "' + $log + '" 2>&1"'
    $process = Start-Process -FilePath 'cmd.exe' -ArgumentList $line -WorkingDirectory $sb.Project -Wait -PassThru -WindowStyle Hidden
    return [pscustomobject]@{ Code = $process.ExitCode; Log = $log }
}

function Invoke-Script($sb, [string[]]$scriptArgs, [switch]$NoWait) {
    $env:AKI_RELOCATE_TO = $sb.Home
    $tag = "{0}-ps-{1}-{2}" -f $sb.Name, $scriptArgs[1], (Get-Date -Format 'HHmmssfff')
    $out = Join-Path $logDir "$tag.out.log"
    $err = Join-Path $logDir "$tag.err.log"
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + (Join-Path $sb.Project 'scripts\aki.ps1') + '"')) + $scriptArgs
    if ($NoWait) {
        return Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -WorkingDirectory $sb.Project -PassThru -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
    }
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -WorkingDirectory $sb.Project -Wait -PassThru -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
    return [pscustomobject]@{ Code = $process.ExitCode; Log = $out }
}

function Get-Scalar([string]$database, [string]$sql) {
    $value = & $py -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute(sys.argv[2]).fetchone()[0])" $database $sql
    return $value
}

function Get-QuickCheck([string]$database) { return (Get-Scalar $database 'PRAGMA quick_check') }

function Get-Sha([string]$path) { return (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() }

function Test-ServeRan($sb) {
    return (Test-Path (Join-Path $sb.Home 'logs\serve-check.json')) -or (Test-Path (Join-Path $sb.Source 'logs\serve-check.json'))
}

function Test-NotMoved($sb) {
    $config = Test-Path (Join-Path $sb.Project 'aki.config.json')
    $check = Get-QuickCheck (Join-Path $sb.Source 'aki.sqlite3')
    return (-not $config) -and ($check -eq 'ok')
}

Write-Host "AKI launcher tests. Sandbox: $base"
Write-Host "Windows PowerShell $($PSVersionTable.PSVersion) on $([Environment]::OSVersion.VersionString)"

# ---------------------------------------------------------------- environment
# The sandbox projects hold no pyproject.toml, so install the reading room components from
# the real project once, here, before any launcher runs.
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $py -c "import aki.home, aki.archive_backup, aki.library_sync, pymupdf, fastapi, uvicorn, sklearn, model2vec, httpx" *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing the reading room components into the project environment (one time)...'
    & $py -m pip install -e ("{0}[dev,room]" -f $repo) *> (Join-Path $logDir 'pip-install.log')
}
& $py -c "import aki.home, aki.archive_backup, aki.library_sync, pymupdf, fastapi, uvicorn, sklearn, model2vec, httpx" *> $null
$envOk = $LASTEXITCODE -eq 0
$ErrorActionPreference = $previous
Add-Result 'Environment' $envOk "PowerShell $($PSVersionTable.PSVersion); project environment has every component"
if (-not $envOk) { throw "The project environment is missing components; see $logDir\pip-install.log" }

# ---------------------------------------------------------------- 1. complete run
try {
    $sb = New-Sandbox 'A complete run'
    $b = Invoke-Launcher $sb 'Back up the archive.cmd' @($sb.Backup)
    $backupOk = ($b.Code -eq 0) -and ((Get-Content (Join-Path $sb.Backup 'BACKUP.json') -Raw | ConvertFrom-Json).restore_test.ok)
    $r = Invoke-Launcher $sb 'Finish the reading room.cmd'
    $config = Get-Content (Join-Path $sb.Project 'aki.config.json') -Raw | ConvertFrom-Json
    $serve = Get-Content (Join-Path $sb.Home 'logs\serve-check.json') -Raw | ConvertFrom-Json
    $report = Get-Content (Join-Path $sb.Home 'finish-report.json') -Raw | ConvertFrom-Json
    $catalog = Import-Csv (Join-Path $sb.Project 'Library\_catalog.csv')
    $hashesOk = @($catalog | Where-Object { (Get-Sha (Join-Path $sb.Project ('Library\' + $_.file))) -ne $_.sha256 }).Count -eq 0
    $retained = (Test-Path (Join-Path $sb.Source 'aki.sqlite3')) -and (Test-Path (Join-Path $sb.Source 'MOVED.txt'))
    $ok = $backupOk -and $r.Code -eq 0 -and $config.home -eq $sb.Home -and $serve.ok -and $report.ok -and $hashesOk -and $retained -and $catalog.Count -eq 4
    Add-Result '1. Complete run, paths with spaces' $ok ("backup+restore test {0}; launcher exit {1}; moved {2}; report ok {3}; serve check {4}; {5} Library files hash-identical {6}; old archive kept {7}" -f $backupOk, $r.Code, ($config.home -eq $sb.Home), $report.ok, $serve.ok, $catalog.Count, $hashesOk, $retained)
    $sbComplete = $sb
} catch { Add-Result '1. Complete run, paths with spaces' $false $_.Exception.Message }

# ---------------------------------------------------------------- 2. step 1 without a backup
try {
    $sb = New-Sandbox 'B no backup'
    $r = Invoke-Launcher $sb 'Finish the reading room.cmd'
    $ok = $r.Code -ne 0 -and (Test-NotMoved $sb) -and -not (Test-ServeRan $sb) -and (Select-String -Path $r.Log -Pattern 'no verified archive backup' -Quiet)
    Add-Result '2. Step 1 refuses without a verified backup' $ok "exit $($r.Code); archive not moved, intact; reading room not opened"
} catch { Add-Result '2. Step 1 refuses without a verified backup' $false $_.Exception.Message }

# ---------------------------------------------------------------- 3. step 1 migration failure
try {
    $sb = New-Sandbox 'C move fails'
    $null = Invoke-Launcher $sb 'Back up the archive.cmd' @($sb.Backup)
    New-Item -ItemType Directory -Force -Path $sb.Home | Out-Null
    Set-Content -Path (Join-Path $sb.Home 'aki.sqlite3') -Value 'an older copy'
    $before = @(Get-ChildItem -Recurse -File (Join-Path $sb.Source 'blobs')).Count
    $r = Invoke-Launcher $sb 'Finish the reading room.cmd'
    $after = @(Get-ChildItem -Recurse -File (Join-Path $sb.Source 'blobs')).Count
    $ok = $r.Code -ne 0 -and (Test-NotMoved $sb) -and $before -eq $after -and -not (Test-ServeRan $sb)
    Add-Result '3. Migration failure preserves the old archive' $ok "exit $($r.Code); vault files before/after $before/$after; config not written; reading room not opened"
} catch { Add-Result '3. Migration failure preserves the old archive' $false $_.Exception.Message }

# ---------------------------------------------------------------- 4. step 2 failure
try {
    $sb = New-Sandbox 'D library fails'
    $null = Invoke-Launcher $sb 'Back up the archive.cmd' @($sb.Backup)
    $m = Invoke-Script $sb @('-Command', 'relocate')
    Set-Content -Path (Join-Path $sb.Home 'Library') -Value 'a file where the Library folder should be'
    $r = Invoke-Launcher $sb 'Finish the reading room.cmd'
    $pages = [int](Get-Scalar (Join-Path $sb.Home 'aki.sqlite3') 'SELECT COUNT(*) FROM pages')
    $ok = $m.Code -eq 0 -and $r.Code -ne 0 -and $pages -eq 0 -and -not (Test-ServeRan $sb)
    Add-Result '4. Step 2 (Library) failure stops the run' $ok "move exit $($m.Code); launcher exit $($r.Code); pages processed $pages (expected 0); reading room not opened"
} catch { Add-Result '4. Step 2 (Library) failure stops the run' $false $_.Exception.Message }

# ---------------------------------------------------------------- 5. step 3 backup failure
try {
    $sb = New-Sandbox 'E backup fails'
    $null = Invoke-Launcher $sb 'Back up the archive.cmd' @($sb.Backup)
    $null = Invoke-Script $sb @('-Command', 'relocate')
    Set-Content -Path (Join-Path $sb.Home 'backups') -Value 'a file where the backups folder should be'
    $r = Invoke-Launcher $sb 'Finish the reading room.cmd'
    $pages = [int](Get-Scalar (Join-Path $sb.Home 'aki.sqlite3') 'SELECT COUNT(*) FROM pages')
    $ok = $r.Code -ne 0 -and $pages -eq 0 -and -not (Test-Path (Join-Path $sb.Home 'finish-report.json')) -and -not (Test-ServeRan $sb)
    Add-Result '5. Backup failure stops before processing' $ok "exit $($r.Code); pages processed $pages (expected 0); no finish report; reading room not opened"
} catch { Add-Result '5. Backup failure stops before processing' $false $_.Exception.Message }

# ---------------------------------------------------------------- 6. step 3 processing failure
try {
    $sb = New-Sandbox 'F book fails' -BadBook
    $null = Invoke-Launcher $sb 'Back up the archive.cmd' @($sb.Backup)
    $r = Invoke-Launcher $sb 'Finish the reading room.cmd'
    $report = Get-Content (Join-Path $sb.Home 'finish-report.json') -Raw | ConvertFrom-Json
    $synced = Test-Path (Join-Path $sb.Project 'Library')
    $ok = $r.Code -ne 0 -and -not $report.ok -and -not $synced -and -not (Test-ServeRan $sb)
    Add-Result '6. Step 3 processing failure stops the run' $ok ("exit {0}; problems: {1}; step 4 not run {2}; reading room not opened" -f $r.Code, ($report.problems -join ' | '), (-not $synced))
} catch { Add-Result '6. Step 3 processing failure stops the run' $false $_.Exception.Message }

# ---------------------------------------------------------------- 7. step 4 failure
try {
    $sb = New-Sandbox 'G sync fails'
    $null = Invoke-Launcher $sb 'Back up the archive.cmd' @($sb.Backup)
    Set-Content -Path (Join-Path $sb.Project 'Library') -Value 'a file where the Library folder should be'
    $r = Invoke-Launcher $sb 'Finish the reading room.cmd'
    $report = Get-Content (Join-Path $sb.Home 'finish-report.json') -Raw | ConvertFrom-Json
    $ok = $r.Code -ne 0 -and $report.ok -and -not (Test-ServeRan $sb)
    Add-Result '7. Step 4 (sync) failure stops before the reading room' $ok "exit $($r.Code); processing had succeeded ($($report.ok)); reading room not opened"
} catch { Add-Result '7. Step 4 (sync) failure stops before the reading room' $false $_.Exception.Message }

# ---------------------------------------------------------------- 8. interrupted processing
try {
    $sb = New-Sandbox 'H interrupted processing' -long $LongPages
    $null = Invoke-Launcher $sb 'Back up the archive.cmd' @($sb.Backup)
    $null = Invoke-Script $sb @('-Command', 'relocate')
    $db = Join-Path $sb.Home 'aki.sqlite3'
    $sql = "SELECT COUNT(*) FROM pages p JOIN assets a ON a.id = p.asset_id WHERE a.edition_id = 'edt-test-t2'"
    $process = Invoke-Script $sb @('-Command', 'finish') -NoWait
    $deadline = (Get-Date).AddMinutes(10)
    $atKill = 0
    while ((Get-Date) -lt $deadline -and -not $process.HasExited) {
        Start-Sleep -Seconds 2
        try { $atKill = [int](Get-Scalar $db $sql) } catch { $atKill = 0 }
        if ($atKill -ge 20) { break }
    }
    if (-not $process.HasExited) { & taskkill.exe /PID $process.Id /T /F | Out-Null }
    Start-Sleep -Seconds 3
    $interrupted = $atKill -lt $LongPages
    $r = Invoke-Script $sb @('-Command', 'finish')
    $final = [int](Get-Scalar $db $sql)
    $texts = [int](Get-Scalar $db "SELECT COUNT(*) FROM transcriptions t JOIN pages p ON p.id = t.page_id JOIN assets a ON a.id = p.asset_id WHERE a.edition_id = 'edt-test-t2'")
    $ok = $interrupted -and $r.Code -eq 0 -and $final -eq $LongPages -and $texts -eq $LongPages -and (Get-QuickCheck $db) -eq 'ok'
    Add-Result '8. Interrupted processing resumes' $ok "killed at $atKill of $LongPages pages; re-run exit $($r.Code); pages $final, transcriptions $texts; database check ok"
} catch { Add-Result '8. Interrupted processing resumes' $false $_.Exception.Message }

# ---------------------------------------------------------------- 9. interrupted move
try {
    $sb = New-Sandbox 'I interrupted move'
    $null = Invoke-Launcher $sb 'Back up the archive.cmd' @($sb.Backup)
    $vault = @(Get-ChildItem -Recurse -File (Join-Path $sb.Source 'blobs'))
    foreach ($i in 0, 1) {
        $relative = $vault[$i].FullName.Substring((Join-Path $sb.Source 'blobs').Length + 1)
        $dest = Join-Path (Join-Path $sb.Home 'blobs') $relative
        New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
        Copy-Item -LiteralPath $vault[$i].FullName -Destination $dest
        if ($i -eq 1) { $bytes = [IO.File]::ReadAllBytes($dest); [IO.File]::WriteAllBytes($dest, $bytes[0..9]) }
    }
    Set-Content -Path ($dest + '.part') -Value 'half'
    Set-Content -Path (Join-Path $sb.Home 'aki.sqlite3.relocating') -Value 'half a database'
    $r = Invoke-Script $sb @('-Command', 'relocate')
    $mismatch = @($vault | Where-Object {
        $relative = $_.FullName.Substring((Join-Path $sb.Source 'blobs').Length + 1)
        (Get-Sha $_.FullName) -ne (Get-Sha (Join-Path (Join-Path $sb.Home 'blobs') $relative)) }).Count
    $ok = $r.Code -eq 0 -and $mismatch -eq 0 -and (Get-QuickCheck (Join-Path $sb.Home 'aki.sqlite3')) -eq 'ok'
    Add-Result '9. Interrupted move resumes and verifies' $ok "exit $($r.Code); vault files differing after resume: $mismatch of $($vault.Count)"
} catch { Add-Result '9. Interrupted move resumes and verifies' $false $_.Exception.Message }

# ---------------------------------------------------------------- 10. hash reconciliation
try {
    if (-not $sbComplete) { throw 'scenario 1 did not complete' }
    $sb = $sbComplete
    $victim = Get-ChildItem -Recurse -File (Join-Path $sb.Project 'Library') -Filter '*.pdf' | Select-Object -First 1
    $size = $victim.Length
    [IO.File]::WriteAllBytes($victim.FullName, (New-Object byte[] $size))
    $r = Invoke-Script $sb @('-Command', 'library-sync')
    $catalogRow = Import-Csv (Join-Path $sb.Project 'Library\_catalog.csv') | Where-Object { $victim.FullName.EndsWith($_.file.Replace('/', '\')) }
    $restored = (Get-Sha $victim.FullName) -eq $catalogRow.sha256
    $quarantined = @(Get-ChildItem -Recurse -File (Join-Path $sb.Project 'Library\_quarantine') -ErrorAction SilentlyContinue).Count
    $ok = $r.Code -eq 0 -and $restored -and $quarantined -eq 1
    Add-Result '10. Same-size damaged Library copy is detected by hash' $ok "exit $($r.Code); restored $restored; files moved to quarantine $quarantined"
} catch { Add-Result '10. Same-size damaged Library copy is detected by hash' $false $_.Exception.Message }

# ---------------------------------------------------------------- 11. busy archive
try {
    if (-not $sbComplete) { throw 'scenario 1 did not complete' }
    $sb = $sbComplete
    Remove-Item Env:AKI_SERVE_CHECK
    $server = Start-Process -FilePath $aki -ArgumentList @('serve', '--port', '8799') -WorkingDirectory $sb.Project -PassThru -WindowStyle Hidden
    $env:AKI_SERVE_CHECK = '1'
    Start-Sleep -Seconds 4
    $r = Invoke-Script $sb @('-Command', 'preflight')
    & taskkill.exe /PID $server.Id /T /F | Out-Null
    $err = Get-Content ($r.Log -replace '\.out\.log$', '.err.log') -Raw
    $ok = $r.Code -ne 0 -and ($err + (Get-Content $r.Log -Raw)) -match 'still running'
    Add-Result '11. Preflight refuses while AKI is running' $ok "exit $($r.Code) with a reading room open"
} catch { Add-Result '11. Preflight refuses while AKI is running' $false $_.Exception.Message }

# ---------------------------------------------------------------- report
$passed = @($results | Where-Object { $_.Passed }).Count
$lines = @(
    "# Launcher test run $stamp", '',
    "Windows PowerShell $($PSVersionTable.PSVersion), $([Environment]::OSVersion.VersionString).",
    "Sandbox: ``$base`` (synthetic archives only; the real archive was not touched).", '',
    "**$passed of $($results.Count) passed.**", '',
    '| Scenario | Result | Detail |', '|---|---|---|'
)
foreach ($item in $results) {
    $lines += "| $($item.Scenario) | $(if ($item.Passed) { 'PASS' } else { '**FAIL**' }) | $($item.Detail -replace '\|', '/') |"
}
$lines += '', "Run logs: ``test-results\launcher-test-$stamp\``."
$markdown = Join-Path $outDir "launcher-test-$stamp.md"
$lines | Set-Content -Path $markdown -Encoding UTF8
$results | ConvertTo-Json | Set-Content -Path (Join-Path $outDir "launcher-test-$stamp.json") -Encoding UTF8
Write-Host ''
Write-Host "$passed of $($results.Count) passed. Report: $markdown"
if ($passed -ne $results.Count) { exit 1 }
exit 0
