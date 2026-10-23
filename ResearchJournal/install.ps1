# Installs a per-user login shortcut for the CURRENT ResearchJournal folder.
# Doesn't touch app source, does not run tests or Git commands.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$agent = Join-Path $here 'journal.py'
if (-not (Test-Path -LiteralPath $agent)) { throw "Cannot find journal.py: $agent" }
$pyCmd = Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1
$pyExe = [string] $pyCmd.Source
if (-not (Test-Path -LiteralPath $pyExe)) { throw "Cannot find Python: $pyExe" }
$pythonw = Join-Path (Split-Path $pyExe -Parent) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = $pyExe }
$startup = [Environment]::GetFolderPath('Startup')
if (-not (Test-Path -LiteralPath $startup)) { throw 'Windows Startup folder was not found.' }
$shortcut = Join-Path $startup 'Friston3-ResearchJournal.lnk'
$legacyShortcut = Join-Path $startup 'Friston2-ResearchJournal.lnk'
$shell = New-Object -ComObject WScript.Shell
# Remove only the legacy shortcut owned by ResearchJournal.
if (Test-Path -LiteralPath $legacyShortcut) {
    $legacy = $shell.CreateShortcut($legacyShortcut)
    if ($legacy.Arguments -match '(?i)ResearchJournal[\\/]journal\.py') {
        Remove-Item -LiteralPath $legacyShortcut -Force
    } else {
        Write-Warning "Legacy shortcut was not recognized; please inspect it: $legacyShortcut"
    }
}
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = $pythonw
$link.Arguments = '"' + $agent + '" watch'
$link.WorkingDirectory = $here
$link.WindowStyle = 7
$link.Description = 'Friston-3 ResearchJournal 10-minute source watcher'
$link.Save()
Write-Host "Startup shortcut created: $shortcut" -ForegroundColor Green
Write-Host "Python executable: $pythonw"
Start-Process -FilePath $pythonw -ArgumentList ('"' + $agent + '" watch') -WorkingDirectory $here -WindowStyle Hidden
Write-Host 'Watcher launch requested. Verify with: python .\ResearchJournal\journal.py status'
