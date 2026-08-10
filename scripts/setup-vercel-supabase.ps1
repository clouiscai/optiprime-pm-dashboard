param(
  [Parameter(Mandatory = $true)]
  [string]$DatabaseUrl,

  [Parameter(Mandatory = $true)]
  [string]$VercelUrl,

  [string]$AdminUsername = "OptiPrime",
  [Parameter(Mandatory = $true)]
  [string]$AdminPassword,

  [string]$ViewerUsername = "viewer",
  [Parameter(Mandatory = $true)]
  [string]$ViewerPassword,

  [string]$RobotxToken,
  [string]$ViewerToken,
  [string]$SessionSecret
)

$ErrorActionPreference = "Stop"

function New-SecretToken {
  $bytes = New-Object byte[] 32
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($bytes)
  } finally {
    $rng.Dispose()
  }
  return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Set-VercelEnv {
  param(
    [string]$Name,
    [string]$Value
  )

  Write-Host "Setting $Name for production, preview, and development..."
  $oldPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    npx vercel env rm $Name production --yes 2>$null | Out-Null
    npx vercel env rm $Name preview --yes 2>$null | Out-Null
    npx vercel env rm $Name development --yes 2>$null | Out-Null

    $Value | npx vercel env add $Name production
    if ($LASTEXITCODE -ne 0) { throw "Failed to add $Name to production." }
    $Value | npx vercel env add $Name preview
    if ($LASTEXITCODE -ne 0) { throw "Failed to add $Name to preview." }
    $Value | npx vercel env add $Name development
    if ($LASTEXITCODE -ne 0) { throw "Failed to add $Name to development." }
  } finally {
    $ErrorActionPreference = $oldPreference
  }
}

if (-not $RobotxToken) {
  $RobotxToken = New-SecretToken
}
if (-not $ViewerToken) {
  $ViewerToken = New-SecretToken
}
if (-not $SessionSecret) {
  $SessionSecret = New-SecretToken
}

if ($DatabaseUrl -notmatch "^postgres(ql)?://") {
  throw "DatabaseUrl must be a Supabase Postgres URL starting with postgresql://"
}
if ($DatabaseUrl -notmatch "sslmode=require") {
  $separator = if ($DatabaseUrl.Contains("?")) { "&" } else { "?" }
  $DatabaseUrl = "$DatabaseUrl${separator}sslmode=require"
}

$VercelUrl = $VercelUrl.TrimEnd("/")

if (-not (Get-Command npx -ErrorAction SilentlyContinue)) {
  throw "npx was not found. Install Node.js first, then rerun this script."
}

Write-Host "Checking Vercel login..."
npx vercel whoami
if ($LASTEXITCODE -ne 0) {
  Write-Host "Opening Vercel login..."
  npx vercel login
}

if (-not (Test-Path ".vercel\project.json")) {
  Write-Host "Linking this folder to your existing Vercel project..."
  npx vercel link
  if ($LASTEXITCODE -ne 0) {
    throw "Vercel link failed. Run 'npx vercel link' manually, choose the existing optiprime project, then rerun this script."
  }
}

if (-not (Test-Path ".vercel\project.json")) {
  throw "Vercel is still not linked. Run 'npx vercel link' manually and choose your existing Vercel project. The link is complete only when .vercel\project.json exists."
}

Set-VercelEnv "DATABASE_URL" $DatabaseUrl
Set-VercelEnv "ROBOTX_TOKEN" $RobotxToken
Set-VercelEnv "OPTIPRIME_USERNAME" $AdminUsername
Set-VercelEnv "OPTIPRIME_PASSWORD" $AdminPassword
Set-VercelEnv "OPTIPRIME_VIEWER_TOKEN" $ViewerToken
Set-VercelEnv "OPTIPRIME_VIEWER_USERNAME" $ViewerUsername
Set-VercelEnv "OPTIPRIME_VIEWER_PASSWORD" $ViewerPassword
Set-VercelEnv "OPTIPRIME_SESSION_SECRET" $SessionSecret
Set-VercelEnv "OPTIPRIME_SESSION_MINUTES" "120"
Set-VercelEnv "OPTIPRIME_ALLOW_STATIC_TOKENS" "false"
Set-VercelEnv "FRONTEND_ORIGINS" $VercelUrl
Set-VercelEnv "VITE_ENABLE_REALTIME" "false"

Write-Host ""
Write-Host "Done. Deploy with:"
Write-Host "  npx vercel --prod"
Write-Host ""
Write-Host "After deploy, test:"
Write-Host "  $VercelUrl/health"
