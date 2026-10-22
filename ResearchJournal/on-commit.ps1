# This is called by Git's post-commit hook. Never abort a successful Git commit.
param([Parameter(Position=0, Mandatory=$true)][string]$Commit)
$ErrorActionPreference = 'Stop'
$homeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$stateDir = Join-Path $homeDir 'state'
# Leave any third-party Git hooks intact, but never enqueue while timer mode is active.
try {
    $settingsPath = Join-Path $homeDir 'config.json'
    $settings = [System.IO.File]::ReadAllText($settingsPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($settings.trigger_mode -ne 'git') { exit 0 }
} catch {
    # Fail closed: an unreadable config must not queue Git jobs.
    exit 0
}
try {
    $pythonFile = Join-Path $stateDir 'git-python-path.txt'
    if (-not (Test-Path -LiteralPath $pythonFile)) { throw 'Python path not registered; rerun git-install.ps1' }
    $pythonExe = ([System.IO.File]::ReadAllText($pythonFile, [System.Text.Encoding]::UTF8)).Trim()
    if (-not (Test-Path -LiteralPath $pythonExe)) { throw "Python interpreter missing: $pythonExe" }
    $workerScript = Join-Path $homeDir 'git_journal.py'
    & $pythonExe $workerScript enqueue $Commit | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not durably enqueue the commit' }
    $silent = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $silent)) { $silent = $pythonExe }
    $arguments = '"' + $workerScript + '" worker'
    Start-Process -FilePath $silent -WorkingDirectory $homeDir -ArgumentList $arguments -WindowStyle Hidden | Out-Null
    Write-Host ('[ResearchJournal] queued Git commit ' + $Commit.Substring(0, [Math]::Min(12, $Commit.Length)))
} catch {
    [void][System.IO.Directory]::CreateDirectory($stateDir)
    $errlog = Join-Path $stateDir 'git-hook-errors.log'
    $line = [DateTime]::Now.ToString('s') + ' post-commit hook error: ' + $_.Exception.Message + [Environment]::NewLine
    [System.IO.File]::AppendAllText($errlog, $line, [System.Text.Encoding]::UTF8)
    Write-Warning '[ResearchJournal] failed to queue commit; see state/git-hook-errors.log'
}
# Do not interfere with the Git commit even when documentation generation fails.
exit 0
