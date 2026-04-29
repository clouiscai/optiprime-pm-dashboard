$ErrorActionPreference = "Stop"
Set-Location (Join-Path (Split-Path -Parent $PSScriptRoot) "frontend")

if (-not (Test-Path "node_modules")) {
  npm install
}

$port = if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { "5173" }
if (-not $env:VITE_API_URL) {
  $repoRoot = Split-Path -Parent $PSScriptRoot
  $backendPortFile = Join-Path $repoRoot ".backend-port"
  $backendPort = if (Test-Path $backendPortFile) { (Get-Content $backendPortFile -Raw).Trim() } else { "8010" }
  $env:VITE_API_URL = "http://127.0.0.1:$backendPort/api"
}

Write-Host ""
Write-Host "OptiPrime frontend starting on http://localhost:$port"
Write-Host "Using backend: $env:VITE_API_URL"
Write-Host ""
npm run dev -- --host 0.0.0.0 --port $port
