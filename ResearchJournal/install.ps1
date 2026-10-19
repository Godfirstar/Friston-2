# Run ONCE with PowerShell inside an activated conda environment if desired.
# No administrator rights, no Git commit, no modification of Friston-2 source files.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$agent = Join-Path $here 'journal.py'

$pyExe = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source

if (-not (Test-Path -LiteralPath $pyExe)) {
    throw "Python executable not found: $pyExe"
}$pyExe = $pyExe.Trim()
$pySilent = Join-Path (Split-Path $pyExe -Parent) 'pythonw.exe'
if (-not (Test-Path $pySilent)) { $pySilent = $pyExe }

$startup = [Environment]::GetFolderPath('Startup')
if (-not (Test-Path $startup)) { throw '无法定位当前用户的 Startup 启动目录。' }
$linkPath = Join-Path $startup 'Friston2-ResearchJournal.lnk'
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath = $pySilent
$link.Arguments = '"' + $agent + '" watch'
$link.WorkingDirectory = $here
$link.Description = 'Friston-2 source snapshot -> automatic Obsidian research logs'
$link.WindowStyle = 7
$link.Save()

Write-Host ('开机自启已安装: ' + $linkPath) -ForegroundColor Green
Write-Host ('Python interpreter: ' + $pySilent)
Write-Host '每次启动电脑后运行，无需手动 Git commit。'

# Launch immediately. journal.py uses an instance lock to avoid duplicates.
Start-Process -FilePath $pySilent -WorkingDirectory $here -ArgumentList ('"' + $agent + '" watch') -WindowStyle Hidden
Write-Host ('现在开始后台监听。Obsidian 可打开: ' + (Join-Path $here 'vault')) -ForegroundColor Green
Write-Host '首次运行先建立源码基线；之后代码连续约 5 分钟不再变化时自动生成日志。'
