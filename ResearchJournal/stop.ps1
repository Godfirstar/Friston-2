# Stop only this journal watcher. Removes startup shortcut only when -Uninstall is set.
param([switch] $Uninstall)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $here 'state/watch.pid'
if (Test-Path $pidFile) {
    $saved = 0
    [void][int]::TryParse((Get-Content $pidFile -Raw).Trim(), [ref] $saved)
    if ($saved -gt 0) {
        $procInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$saved" -ErrorAction SilentlyContinue
        if ($procInfo -and $procInfo.CommandLine -and $procInfo.CommandLine.Contains('journal.py')) {
            Stop-Process -Id $saved -Force
            Write-Host 'ResearchJournal 监听已停止。'
        } else {
            Write-Warning '未能确认属于 ResearchJournal 的进程，未结束任何进程。'
        }
    }
}
if ($Uninstall) {
    $link = Join-Path ([Environment]::GetFolderPath('Startup')) 'Friston2-ResearchJournal.lnk'
    if (Test-Path $link) { Remove-Item $link -Force }
    Write-Host '开机自启已关闭，研发笔记未删除。'
}
