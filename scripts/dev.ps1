$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment (.venv)..."
    python -m venv .venv
}

Write-Host "Installing dependencies..."
& $venvPython -m pip install -q -r requirements.txt

Write-Host ""
Write-Host "Parlay Builder v1 — local server"
Write-Host "  Home:  http://127.0.0.1:8000/"
Write-Host "  MLB:   http://127.0.0.1:8000/mlb"
Write-Host "  Health: http://127.0.0.1:8000/health"
Write-Host ""

& $venvPython -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
