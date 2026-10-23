param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$journalRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $journalRoot 'state/watch.pid'
if (Test-Path -LiteralPath $pidFile) {
    $pidValue = 0
    [void][int]::TryParse(((Get-Content -LiteralPath $pidFile -Raw).Trim()), [ref] $pidValue)
    if ($pidValue -gt 0) {
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
        if ($processInfo -and $processInfo.Name -match '^pythonw?\.exe$' -and
            $processInfo.CommandLine -match '(?i)ResearchJournal[\\/]journal\.py' -and
            $processInfo.CommandLine -match '(?i)\bwatch\b') {
            Stop-Process -Id $pidValue -Force
            Write-Host "Stopped ResearchJournal watcher PID: $pidValue"
        } else { Write-Warning 'Saved PID is not a verified ResearchJournal watcher. No process stopped.' }
    }
}
if ($Uninstall) {
    $startup = [Environment]::GetFolderPath('Startup')
    $shell = New-Object -ComObject WScript.Shell
    foreach ($name in @('Friston2-ResearchJournal.lnk','Friston3-ResearchJournal.lnk')) {
        $linkPath = Join-Path $startup $name
        if (Test-Path -LiteralPath $linkPath) {
            $link = $shell.CreateShortcut($linkPath)
            if ($link.Arguments -match '(?i)ResearchJournal[\\/]journal\.py') {
                Remove-Item -LiteralPath $linkPath -Force
                Write-Host "Removed ResearchJournal startup shortcut: $linkPath"
            }
        }
    }
}
