# VPS deploy (PowerShell) — pull, install, restart, verify props build.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "==> git pull"
git pull origin main

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating .venv..."
    & py -3.12 -m venv .venv
}
Write-Host "==> pip install"
& $venvPython -m pip install -q -r requirements.txt

Write-Host "==> restart parlay-builder (if service exists)"
try {
    & sc.exe query parlay-builder 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Restart-Service parlay-builder -ErrorAction SilentlyContinue
    }
} catch { }

Start-Sleep -Seconds 2
$port = if ($env:PORT) { $env:PORT } else { "8000" }
$base = if ($env:DEPLOY_URL) { $env:DEPLOY_URL } else { "http://127.0.0.1:$port" }

Write-Host "==> verify /api/build"
try {
    $build = Invoke-RestMethod -Uri "$base/api/build" -TimeoutSec 10
    $build | ConvertTo-Json -Compress
} catch {
    Write-Warning "Could not reach $base/api/build — is the server running?"
}

Write-Host "==> verify /api/daily/props (must not be 401)"
try {
    Invoke-RestMethod -Uri "$base/api/daily/props?limit=1" -TimeoutSec 15 | Out-Null
    Write-Host "  ok props API reachable"
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 401) {
        Write-Error "Props API returned 401 — auth fix not deployed"
    }
    throw
}

$required = @(
    "app\services\props_mlb.py",
    "app\services\prop_scoring.py",
    "static\index.html",
    "static\app.js",
    "data\processed\props_repository"
)
foreach ($rel in $required) {
    if (-not (Test-Path (Join-Path $ProjectRoot $rel))) {
        throw "Missing required path: $rel"
    }
    Write-Host "  ok $rel"
}

Write-Host "Deploy complete. Hard-refresh browser. Check $base/api/build"
