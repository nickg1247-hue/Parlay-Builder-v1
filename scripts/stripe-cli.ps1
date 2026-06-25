# Stripe CLI helper — works even if `stripe` isn't on PATH yet.
$stripeDir = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Stripe.StripeCli_Microsoft.Winget.Source_8wekyb3d8bbwe"
$stripeExe = Join-Path $stripeDir "stripe.exe"

if (-not (Test-Path $stripeExe)) {
    Write-Host "Stripe CLI not found. Install with: winget install Stripe.StripeCli" -ForegroundColor Red
    exit 1
}

$cmd = $args[0]
if (-not $cmd) {
    Write-Host @"
Stripe CLI helper

  .\scripts\stripe-cli.ps1 login
  .\scripts\stripe-cli.ps1 listen

After 'listen' starts, copy the whsec_... line into .env as STRIPE_WEBHOOK_SECRET
"@
    exit 0
}

switch ($cmd) {
    "login" {
        & $stripeExe login
    }
    "listen" {
        Write-Host "Forwarding webhooks to http://127.0.0.1:8000/api/billing/webhook" -ForegroundColor Cyan
        Write-Host "Copy the whsec_... secret below into .env -> STRIPE_WEBHOOK_SECRET" -ForegroundColor Yellow
        & $stripeExe listen --forward-to localhost:8000/api/billing/webhook
    }
    default {
        & $stripeExe @args
    }
}
