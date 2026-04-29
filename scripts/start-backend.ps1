param(
  [switch]$Restart
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".venv")) {
  python -m venv .venv
}

$requestedPort = if ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 8010 }
$port = $requestedPort

function Test-OptiPrimeBackend {
  param([int]$Port)
  try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
    return $health.ok -eq $true
  } catch {
    return $false
  }
}

function Stop-OptiPrimeBackend {
  param([int]$Port)

  Write-Host "Stopping existing OptiPrime backend on http://localhost:$Port ..."

  for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $connections) {
      return
    }

    $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($pid in $pids) {
      if (-not $pid) {
        continue
      }
      try {
        Stop-Process -Id $pid -Force -ErrorAction Stop
      } catch [Microsoft.PowerShell.Commands.ProcessCommandException] {
        # The reload process can exit between the port lookup and Stop-Process.
      }
    }

    Start-Sleep -Milliseconds 250
  }

  throw "Could not stop the existing backend on port $Port."
}

if (-not $env:BACKEND_PORT) {
  while (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    if (Test-OptiPrimeBackend -Port $port) {
      if ($Restart) {
        Stop-OptiPrimeBackend -Port $port
        break
      }
      $port | Set-Content -Path ".backend-port"
      Write-Host ""
      Write-Host "OptiPrime backend is already running on http://localhost:$port"
      Write-Host "Restart with: .\scripts\start-backend.ps1 -Restart"
      Write-Host "Start the frontend with: .\scripts\start-frontend.ps1"
      Write-Host ""
      exit 0
    }
    $port++
    if ($port -gt 8025) {
      throw "No free backend port found between 8010 and 8025. Stop an old backend or set BACKEND_PORT manually."
    }
  }
} elseif (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
  if (Test-OptiPrimeBackend -Port $port) {
    if ($Restart) {
      Stop-OptiPrimeBackend -Port $port
    } else {
      $port | Set-Content -Path ".backend-port"
      Write-Host ""
      Write-Host "OptiPrime backend is already running on http://localhost:$port"
      Write-Host "Restart with: .\scripts\start-backend.ps1 -Restart"
      Write-Host "Start the frontend with: .\scripts\start-frontend.ps1"
      Write-Host ""
      exit 0
    }
  } else {
    throw "Port $port is already in use by another app. Pick another port: `$env:BACKEND_PORT='8011'; .\scripts\start-backend.ps1"
  }
}

$port | Set-Content -Path ".backend-port"
.\.venv\Scripts\python -m pip install -r requirements.txt
Write-Host ""
Write-Host "OptiPrime backend starting on http://localhost:$port"
Write-Host "Frontend should use: `$env:VITE_API_URL='http://127.0.0.1:$port/api'"
Write-Host ""
.\.venv\Scripts\python -m uvicorn backend.main:app --host 0.0.0.0 --port $port --reload
