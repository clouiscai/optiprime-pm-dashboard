$ErrorActionPreference = "Stop"
Set-Location (Join-Path (Split-Path -Parent $PSScriptRoot) "frontend")

if (-not (Test-Path "node_modules")) {
  npm install
}

Remove-Item Env:\VITE_API_URL -ErrorAction SilentlyContinue
npm run build

Write-Host ""
Write-Host "Frontend built into public/."
Write-Host "The backend can now serve the whole app from one URL."
Write-Host ""
