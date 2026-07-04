$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not $env:CLASS_BALANCER_DB) {
    $env:CLASS_BALANCER_DB = Join-Path $env:USERPROFILE ".class_balancer\class_balancer.sqlite3"
}

Write-Host "Starting Mosaicly / Shibutz Hacham..."
python -m class_balancer
