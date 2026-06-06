$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$logPath = Join-Path $ProjectRoot "data\processed\morning_refresh.log"
$logDir = Split-Path $logPath -Parent
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"=== Morning refresh started $timestamp ===" | Out-File -Append -Encoding utf8 $logPath

& $venvPython scripts/morning_refresh.py *>&1 | Out-File -Append -Encoding utf8 $logPath
exit $LASTEXITCODE
