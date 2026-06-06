# Hourly odds repository refresh (quota-gated)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

& .\.venv\Scripts\python.exe scripts\refresh_odds_hourly.py
exit $LASTEXITCODE
