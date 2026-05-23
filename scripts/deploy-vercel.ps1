$ErrorActionPreference = "Stop"

if (-not (Get-Command npx -ErrorAction SilentlyContinue)) {
  throw "npx was not found. Install Node.js first, then rerun this script."
}

if (-not (Test-Path ".vercel\project.json")) {
  Write-Host "This repo is not linked to Vercel yet. Run:"
  Write-Host "  .\scripts\setup-vercel-supabase.ps1 -DatabaseUrl '<supabase-url>' -VercelUrl 'https://your-project.vercel.app' -AdminPassword '<password>' -ViewerPassword '<password>'"
  exit 1
}

npx vercel --prod
