# Enable live Odds API - verification checklist (does not edit .env)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$envFile = Join-Path $ProjectRoot ".env"
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Write-Host ""
Write-Host "=== Parlay Builder - Enable Live Odds (checklist) ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Before running, set these in .env (edit manually; this script does not change .env):"
Write-Host "  USE_LIVE_ODDS=true"
Write-Host "  ODDS_API_KEY=<your key from the-odds-api.com>"
Write-Host "  ODDS_HOURLY_REFRESH=true          # optional: in-app hourly refresh"
Write-Host "  ODDS_API_MAX_PER_HOUR=25          # UTC hour cap"
Write-Host "  ODDS_API_MAX_PER_DAY=500          # UTC day cap"
Write-Host ""

if (-not (Test-Path $envFile)) {
    Write-Host "ERROR: .env not found. Copy .env.example to .env and add your settings." -ForegroundColor Red
    exit 1
}

function Get-EnvValue([string]$Name) {
    $line = Get-Content $envFile -Encoding UTF8 | Where-Object {
        $_ -match "^\s*$([regex]::Escape($Name))\s*="
    } | Select-Object -First 1
    if (-not $line) { return $null }
    $parts = $line -split "=", 2
    if ($parts.Count -lt 2) { return "" }
    return $parts[1].Trim().Trim('"').Trim("'")
}

$useLive = (Get-EnvValue "USE_LIVE_ODDS").ToLower()
$key = Get-EnvValue "ODDS_API_KEY"
$hourly = Get-EnvValue "ODDS_HOURLY_REFRESH"
$maxHour = Get-EnvValue "ODDS_API_MAX_PER_HOUR"
$maxDay = Get-EnvValue "ODDS_API_MAX_PER_DAY"

$ok = $true
if ($useLive -notin @("true", "1", "yes", "on")) {
    Write-Host "FAIL: USE_LIVE_ODDS is not true in .env (current: '$useLive')" -ForegroundColor Red
    $ok = $false
} else {
    Write-Host "OK:   USE_LIVE_ODDS=true"
}

if ([string]::IsNullOrWhiteSpace($key)) {
    Write-Host "FAIL: ODDS_API_KEY is empty in .env" -ForegroundColor Red
    $ok = $false
} else {
    Write-Host "OK:   ODDS_API_KEY is set (value hidden)"
}

if ($maxHour) { Write-Host "      ODDS_API_MAX_PER_HOUR=$maxHour" }
if ($maxDay)  { Write-Host "      ODDS_API_MAX_PER_DAY=$maxDay" }
if ($hourly)  { Write-Host "      ODDS_HOURLY_REFRESH=$hourly" }

if (-not $ok) {
    Write-Host ""
    Write-Host "Fix .env and re-run: .\scripts\enable_live_odds.ps1" -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: .venv not found. Run: python -m venv .venv; pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Running morning refresh (quota-gated API call when refresh needed)..." -ForegroundColor Cyan
& $venvPython scripts/morning_refresh.py
$refreshExit = $LASTEXITCODE

Write-Host ""
if ($refreshExit -eq 0) {
    Write-Host "Morning refresh completed." -ForegroundColor Green
} else {
    Write-Host "Morning refresh returned exit code $refreshExit - check data/processed/morning_refresh.log" -ForegroundColor Yellow
}

$hostPort = Get-EnvValue "PORT"
if (-not $hostPort) { $hostPort = "8000" }
$base = "http://127.0.0.1:$hostPort"

Write-Host ""
Write-Host "=== Verify (with dev server running: .\scripts\dev.ps1) ===" -ForegroundColor Cyan
Write-Host "  Quota and today lines:  $base/api/odds/today"
Write-Host "    Check: quota.hour_count, quota.day_count, quota.hour_max (20), quota.day_max (500)"
Write-Host "    Check: fetched_at, games[] populated after successful fetch"
Write-Host "  Refresh status:         $base/api/status/refresh"
Write-Host "  Game page:              $base/mlb  -> click a game -> market boxes show ML/O/U"
Write-Host "  On-disk quota:          data/processed/odds_repository/quota.json"
Write-Host "  On-disk snapshot:       data/processed/odds_repository/YYYY-MM-DD.json"
Write-Host ""
Write-Host "Game pages poll /api/odds/today every 60s (no refresh=true) - 0 extra API credits." -ForegroundColor DarkGray
Write-Host "Hourly refresh: scripts/refresh_odds_hourly.ps1 or ODDS_HOURLY_REFRESH=true in dev server." -ForegroundColor DarkGray
Write-Host ""

exit $refreshExit
