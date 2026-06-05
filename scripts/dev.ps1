$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment (.venv) with Python 3.12..."
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed. Install Python 3.12: https://www.python.org/downloads/"
        Write-Host "Or run: py install 3.12"
        exit 1
    }
} else {
    $minor = & $venvPython -c "import sys; print(sys.version_info.minor)"
    if ($minor -ne "12") {
        Write-Host "WARNING: .venv uses Python 3.$minor but this project needs 3.12."
        Write-Host "Recreate: Remove-Item -Recurse -Force .venv"
        Write-Host "Then run: .\scripts\dev.ps1"
        exit 1
    }
}

Write-Host "Installing dependencies..."
# Skip pip wheel cache — avoids 'Cache entry deserialization failed' spam on Windows.
& $venvPython -m pip install -q --no-cache-dir -r requirements.txt

Write-Host ""
Write-Host "Parlay Builder v1 - local server"
Write-Host "  Home:  http://127.0.0.1:8000/"
Write-Host "  MLB:   http://127.0.0.1:8000/mlb"
Write-Host "  Health: http://127.0.0.1:8000/health"
Write-Host ""

& $venvPython -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
