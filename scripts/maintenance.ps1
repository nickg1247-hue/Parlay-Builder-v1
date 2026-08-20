# Toggle NTG Sports construction mode.
# On the VPS, this flips the live website with no restart.
# From Windows, set VPS_SSH in .env to toggle ntgsports.com over SSH.
#
#   .\scripts\maintenance.ps1 on
#   .\scripts\maintenance.ps1 off
#   .\scripts\maintenance.ps1 status

param(
    [Parameter(Position = 0)]
    [string]$Action = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Get-DotEnvValue([string]$Key) {
    $envFile = Join-Path $ProjectRoot ".env"
    if (-not (Test-Path $envFile)) {
        return $null
    }
    foreach ($line in Get-Content $envFile) {
        $trim = $line.Trim()
        if ($trim.StartsWith("#") -or -not $trim.Contains("=")) {
            continue
        }
        $name, $value = $trim.Split("=", 2)
        if ($name.Trim() -eq $Key) {
            return $value.Trim().Trim("'").Trim('"')
        }
    }
    return $null
}

$Action = $Action.Trim().ToLowerInvariant()
if ($Action -notin @("on", "off", "status")) {
    Write-Host "Usage: .\scripts\maintenance.ps1 on|off|status"
    exit 1
}

$vpsSsh = if ($env:VPS_SSH) { $env:VPS_SSH } else { Get-DotEnvValue "VPS_SSH" }
$vpsDir = if ($env:VPS_APP_DIR) { $env:VPS_APP_DIR } else { Get-DotEnvValue "VPS_APP_DIR" }
if (-not $vpsDir) {
    $vpsDir = "/var/www/parlay-builder"
}

if ($vpsSsh) {
    Write-Host "==> $Action construction on $vpsSsh ($vpsDir)"
    ssh $vpsSsh "cd '$vpsDir' && bash scripts/maintenance.sh $Action"
    exit $LASTEXITCODE
}

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython (Join-Path $ProjectRoot "scripts\maintenance.py") $Action
} else {
    python (Join-Path $ProjectRoot "scripts\maintenance.py") $Action
}

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "That toggled THIS folder, not ntgsports.com."
Write-Host "On the VPS run:  bash scripts/maintenance.sh $Action"
Write-Host "Or set VPS_SSH=user@your-vps in .env and rerun this command."
