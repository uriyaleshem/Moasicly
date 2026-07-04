$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

python -m PyInstaller --clean --noconfirm Moasicly.spec

Write-Host ""
Write-Host "Built: $PSScriptRoot\dist\Moasicly.exe"
Write-Host "Put .env next to Moasicly.exe to configure AI keys for the packaged app."
