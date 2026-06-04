$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Run scripts/dev.ps1 first to create .venv"
    exit 1
}

$homeUrl = "http://127.0.0.1:8000/"
$mlbUrl = "http://127.0.0.1:8000/mlb"
Write-Host "Starting server (Home + MLB on one port)..."
Start-Process $venvPython -ArgumentList @(
    "-m", "uvicorn", "app.main:app",
    "--reload", "--host", "127.0.0.1", "--port", "8000"
) -WorkingDirectory $ProjectRoot

Start-Sleep -Seconds 3
Start-Process $homeUrl
Start-Process $mlbUrl
Write-Host "Opened Home and MLB in browser."
Write-Host "  Home: $homeUrl"
Write-Host "  MLB:  $mlbUrl"
Write-Host "Close the uvicorn window to stop the server."
