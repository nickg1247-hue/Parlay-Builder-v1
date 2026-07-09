$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$logPath = Join-Path $ProjectRoot "data\processed\weekly_ufc_clv.log"
$logDir = Split-Path $logPath -Parent
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"=== Weekly UFC CLV started $timestamp ===" | Out-File -Append -Encoding utf8 $logPath

& $venvPython scripts/backfill_forward_clv.py --sport ufc *>&1 | Out-File -Append -Encoding utf8 $logPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $venvPython -c "from app.services.ufc_forward_clv import grade_pick_results, summarize_clv; print('Grade:', grade_pick_results()); print('Summary:', summarize_clv(days=30))" *>&1 | Out-File -Append -Encoding utf8 $logPath
exit $LASTEXITCODE
