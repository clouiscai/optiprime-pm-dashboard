$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$credentialPath = Join-Path $root "secrets\optiprime-admin-password.dpapi"

if (-not (Test-Path -LiteralPath $credentialPath)) {
  throw "No encrypted OptiPrime admin password was found on this PC. Rotate the Vercel OPTIPRIME_PASSWORD first."
}

$securePassword = (Get-Content -LiteralPath $credentialPath -Raw).Trim() | ConvertTo-SecureString
$credential = New-Object System.Management.Automation.PSCredential("OptiPrime", $securePassword)
$credential.GetNetworkCredential().Password | Set-Clipboard

Write-Host "OptiPrime admin password copied to the Windows clipboard."
Write-Host "Username: OptiPrime"
