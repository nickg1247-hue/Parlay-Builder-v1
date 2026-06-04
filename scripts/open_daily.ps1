$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Run scripts/dev.ps1 first to create .venv"
    exit 1
}

$url = "http://127.0.0.1:8000"
Write-Host "Starting server at $url ..."
Start-Process $venvPython -ArgumentList @(
    "-m", "uvicorn", "app.main:app",
    "--reload", "--host", "127.0.0.1", "--port", "8000"
) -WorkingDirectory $ProjectRoot

Start-Sleep -Seconds 3
Start-Process $url
Write-Host "Opened browser. Press Ctrl+C in the server window to stop."
