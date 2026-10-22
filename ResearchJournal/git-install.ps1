# Install Git post-commit for ResearchJournal, without overwriting other hooks.
# Requires an existing Git repo whose root equals Friston-2.
param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$homeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = (Resolve-Path (Join-Path $homeDir '..')).Path
$gitCmd = Get-Command git -CommandType Application -ErrorAction Stop | Select-Object -First 1
$gitExe = $gitCmd.Source
# Capture native Git output without a Select-Object -First 1 pipeline.
$repoRoot = & $gitExe -C $project rev-parse --show-toplevel
$gitExitCode = $LASTEXITCODE
if ($gitExitCode -ne 0 -or [string]::IsNullOrWhiteSpace([string]$repoRoot)) {
    throw 'Friston-2 is not a Git repository. Initialize Git first; installation made no changes.'
}
$repoRoot = ([System.IO.Path]::GetFullPath($repoRoot)).TrimEnd('\','/')
$projectCanonical = ([System.IO.Path]::GetFullPath($project)).TrimEnd('\','/')
if (-not [string]::Equals($repoRoot, $projectCanonical, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Git root mismatch. Expected $projectCanonical but Git found $repoRoot"
}
$customHooks = & $gitExe -C $project config --get core.hooksPath
if (-not [string]::IsNullOrWhiteSpace($customHooks)) {
    throw "Custom core.hooksPath detected: $customHooks. Manually integrate ResearchJournal instead of replacing hooks."
}
$hookDirectory = & $gitExe -C $project rev-parse --git-path hooks
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($hookDirectory)) { throw 'Could not locate Git hooks directory.' }
if (-not [System.IO.Path]::IsPathRooted($hookDirectory)) {
    $hookDirectory = Join-Path $project $hookDirectory
}
$hookPath = Join-Path $hookDirectory 'post-commit'
$marker = 'RJ33_GIT_HOOK'
if (Test-Path -LiteralPath $hookPath) {
    $beginning = [System.IO.File]::ReadAllText($hookPath, [System.Text.Encoding]::UTF8)
    if (-not $beginning.Contains($marker)) {
        throw "A post-commit hook already exists: $hookPath. It will not be overwritten."
    }
}
$pythonExe = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Current Python executable was not found' }
if ($CheckOnly) {
    Write-Host ('Git hook preflight passed: ' + $hookPath)
    Write-Host ('Python: ' + $pythonExe)
    exit 0
}
[void][System.IO.Directory]::CreateDirectory($hookDirectory)
$hook = @'
#!/bin/sh
# RJ33_GIT_HOOK - local ResearchJournal Git post-commit entry
sha="$(git rev-parse HEAD)" || exit 0
root="$(git rev-parse --show-toplevel)" || exit 0
script="$root/ResearchJournal/on-commit.ps1"
if command -v cygpath >/dev/null 2>&1; then
    script="$(cygpath -w "$script")"
fi
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$script" "$sha"
exit 0
'@
$utf8Plain = New-Object System.Text.UTF8Encoding($false)
if (-not (Test-Path -LiteralPath $hookPath)) {
    [System.IO.File]::WriteAllText($hookPath, ($hook.TrimEnd() + "`n"), $utf8Plain)
}
$state = Join-Path $homeDir 'state'
[void][System.IO.Directory]::CreateDirectory($state)
[System.IO.File]::WriteAllText((Join-Path $state 'git-python-path.txt'), $pythonExe, $utf8Plain)

# Hide local/private knowledge artifacts from FUTURE git add operations.
# This does not untrack files already committed by the user.
$exclude = & $gitExe -C $project rev-parse --git-path info/exclude
if (-not [System.IO.Path]::IsPathRooted($exclude)) { $exclude = Join-Path $project $exclude }
[void][System.IO.Directory]::CreateDirectory((Split-Path -Parent $exclude))
$existing = if (Test-Path -LiteralPath $exclude) { [System.IO.File]::ReadAllText($exclude, [System.Text.Encoding]::UTF8) } else { '' }
$owned = @('ResearchJournal/state/', 'ResearchJournal/inbox/', 'ResearchJournal/vault/')
$extras = @($owned | Where-Object { -not (($existing -split "`r?`n") -contains $_) })
if ($extras.Count -gt 0) {
    [System.IO.File]::AppendAllText($exclude, "`n# ResearchJournal local/private artifacts`n" + ($extras -join "`n") + "`n", $utf8Plain)
}
Write-Host ('Installed Git post-commit hook: ' + $hookPath) -ForegroundColor Green
Write-Host ('Python interpreter: ' + $pythonExe)
Write-Host 'Notes are generated only for committed robot source changes.'
$tracked = @(& $gitExe -C $project ls-files -- ResearchJournal/state ResearchJournal/inbox ResearchJournal/vault)
if ($tracked.Count -gt 0) {
    Write-Warning ('Git already tracks ' + $tracked.Count + ' private journal files; local exclude does not untrack existing files. Review before pushing.')
}
$startup = [Environment]::GetFolderPath('Startup')
if (Test-Path -LiteralPath $startup) {
    $silent = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $silent)) { $silent = $pythonExe }
    $shortcutPath = Join-Path $startup 'Friston2-ResearchJournal-GitQueue.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $silent
    $workerScript = Join-Path $homeDir 'git_journal.py'
    $shortcut.Arguments = '"' + $workerScript + '" worker'
    $shortcut.WorkingDirectory = $homeDir
    $shortcut.Description = 'Resume pending committed code summaries; no source scanner'
    $shortcut.WindowStyle = 7
    $shortcut.Save()
    Write-Host ('Pending-commit recovery on next login: ' + $shortcutPath)
}
Write-Host 'Existing vault/state/inbox files were not modified.' 
