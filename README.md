# OptiPrime PM Dashboard

Local-first project management dashboard for OptiPrime UAV, USV, and UUV workstreams. The app uses FastAPI, React, and SQLite for local development, with environment variables for secrets and runtime configuration.

## Repository Safety

This repository should contain source code only.

Do not commit:

- `.env`
- `database/robotx.db`
- any `*.db`, `*.sqlite`, or `*.sqlite3` files
- ngrok auth/config files
- private keys, certificates, or secrets
- virtual environments or `node_modules`

These are already covered by `.gitignore`. The tracked `.env.example` file uses placeholder values only.

## Project Structure

```text
api/                  FastAPI routes
backend/              FastAPI app entrypoint
database/             SQLAlchemy session, migrations, seed data
frontend/             React/Vite frontend
models/               SQLAlchemy and Pydantic models
scripts/              PowerShell helper scripts
services/             Auth, dashboard calculations, realtime services
```

## Environment Setup

Create a local `.env` from the example:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` with local-only values:

```text
ROBOTX_TOKEN=replace-with-a-long-random-token
OPTIPRIME_SESSION_SECRET=replace-with-an-independent-random-signing-secret
OPTIPRIME_SESSION_MINUTES=120
OPTIPRIME_ALLOW_STATIC_TOKENS=false
OPTIPRIME_USERNAME=OptiPrime
OPTIPRIME_PASSWORD=replace-with-admin-password
OPTIPRIME_VIEWER_TOKEN=replace-with-a-different-long-random-token
OPTIPRIME_VIEWER_USERNAME=replace-with-viewer-username
OPTIPRIME_VIEWER_PASSWORD=replace-with-viewer-password
DATABASE_URL=sqlite:///./database/robotx.db
```

## Run Locally

Terminal 1:

```powershell
.\scripts\start-backend.ps1
```

If an old backend is already running and you want to reload code changes:

```powershell
.\scripts\start-backend.ps1 -Restart
```

Terminal 2:

```powershell
.\scripts\start-frontend.ps1
```

Open:

```text
http://localhost:5173
```

The backend defaults to port `8010`. The frontend reads `.backend-port` to find the backend.

## Build Frontend

```powershell
.\scripts\build-frontend.ps1
```

Or directly:

```powershell
cd frontend
npm install
npm run build
```

## Deploy To Vercel With Supabase

This repository is configured for Vercel hosting through `vercel.json`. Vercel serves the React/Vite frontend and routes `/api/*` to the FastAPI app in `app.py`.

Important: use Supabase Postgres or another hosted Postgres database for production. Do not use SQLite on Vercel because serverless file storage is temporary. Invoice PDFs are stored in the database with their invoice records and are limited to 10 MB each.

### Finance Data Model

Finance records follow this hierarchy:

1. Vendor
2. Invoice number and PDF
3. Purchase lines under that invoice, including materials, shipping, tax, fees, and services

Invoice headers store the vendor, invoice number, date, base currency, SGD exchange rate, sponsorship status, sponsor name, description, and PDF. Each purchase line records what the charge is, its type (such as material, shipping, tax, fee, discount, or service), and its price in the invoice base currency. Discounts can be entered as negative purchase lines. Actual spending is calculated only from non-sponsored invoice purchase lines, not from the invoice header or legacy standalone expenses, so invoice totals are never counted twice. An invoice PDF can be replaced or removed without deleting the invoice and its purchase lines.

### 1. Create Supabase Database

1. Create a Supabase project.
2. Open Project Settings -> Database.
3. Copy the Postgres connection string. Prefer the pooled connection string for serverless.
4. Add `?sslmode=require` if it is not already included.

Example format:

```text
postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres?sslmode=require
```

Use the real password from Supabase. Do not commit it.

Do not use the Supabase Next.js quickstart for this app. That guide asks you to install `@supabase/supabase-js`, `@supabase/ssr`, and create `page.tsx` / `utils/supabase/*.ts` files. This project is not Next.js; it is React/Vite plus FastAPI. Supabase is used as the Postgres database behind FastAPI through `DATABASE_URL`.

You do not need these Vercel variables for the current app:

```text
NEXT_PUBLIC_SUPABASE_URL
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
```

Instead, the required Supabase variable is:

```text
DATABASE_URL=postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres?sslmode=require
```

### 2. Import the repo in Vercel

1. Go to [Vercel](https://vercel.com).
2. Add New Project.
3. Import `clouiscai/optiprime-pm-dashboard`.
4. Keep the project root as the repository root.
5. Vercel will use:

```text
Install Command: python -m pip install -r requirements.txt && cd frontend && npm install
Build Command: cd frontend && npm run build
Output Directory: frontend/dist
```

These are already defined in `vercel.json`.

### 3. Add Vercel environment variables

In Vercel Project Settings -> Environment Variables, add:

```text
DATABASE_URL=postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres?sslmode=require
ROBOTX_TOKEN=replace-with-a-long-random-token
OPTIPRIME_SESSION_SECRET=replace-with-an-independent-random-signing-secret
OPTIPRIME_SESSION_MINUTES=120
OPTIPRIME_ALLOW_STATIC_TOKENS=false
OPTIPRIME_USERNAME=OptiPrime
OPTIPRIME_PASSWORD=replace-with-admin-password
OPTIPRIME_VIEWER_TOKEN=replace-with-a-different-long-random-token
OPTIPRIME_VIEWER_USERNAME=replace-with-viewer-username
OPTIPRIME_VIEWER_PASSWORD=replace-with-viewer-password
FRONTEND_ORIGINS=https://your-vercel-project.vercel.app
VITE_API_URL=https://your-vercel-project.vercel.app/api
VITE_ENABLE_REALTIME=false
```

Then redeploy the Vercel project. Vite only exposes environment variables that start with `VITE_` to the browser, so keep backend secrets without that prefix.

You can also set the Vercel environment variables from PowerShell after logging in:

```powershell
.\scripts\setup-vercel-supabase.ps1 `
  -DatabaseUrl "postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres?sslmode=require" `
  -VercelUrl "https://your-vercel-project.vercel.app" `
  -AdminPassword "replace-with-admin-password" `
  -ViewerPassword "replace-with-viewer-password"
```

The script will:

- verify/login to Vercel through `npx vercel login`
- link this folder to your Vercel project through `npx vercel link`
- generate secure API tokens if you do not pass them
- generate a separate session-signing secret
- disable permanent static bearer-token access
- add the backend and frontend env vars to production, preview, and development

Then deploy:

```powershell
.\scripts\deploy-vercel.ps1
```

### 4. Verify

After Vercel deploys:

1. Open `https://your-vercel-project.vercel.app/health`.
2. Confirm it returns `{"ok":true}`.
3. Open the Vercel app URL.
4. Log in with the admin credentials.
5. Confirm Dashboard, Tasks, BOM, Finance, Sponsors, and Equipments/Asset load.

If login fails, check that `VITE_API_URL` ends with `/api`, `DATABASE_URL` is the Supabase Postgres URL, and `FRONTEND_ORIGINS` matches the Vercel domain.

### 5. Existing Local Data

The current local SQLite database is not uploaded to Supabase automatically. If you need to migrate real local data, export it from `database/robotx.db` and import it privately into Supabase. Do not commit the database file to GitHub.

## Database

Local SQLite database:

```text
database/robotx.db
```

This file is intentionally ignored by Git. Each developer can create their own local database by running the backend. Seed data lives in `database/seed.py`.

For a team or Azure deployment, prefer a managed database such as Azure PostgreSQL. Set `DATABASE_URL` in the Azure environment instead of committing a database file.

## Azure Handoff Notes

For Azure, the team should configure secrets in Azure App Service settings, Azure Container Apps secrets, or Key Vault. Do not put production secrets in GitHub.

Minimum environment variables:

```text
ROBOTX_TOKEN
OPTIPRIME_SESSION_SECRET
OPTIPRIME_SESSION_MINUTES
OPTIPRIME_ALLOW_STATIC_TOKENS
OPTIPRIME_USERNAME
OPTIPRIME_PASSWORD
OPTIPRIME_VIEWER_TOKEN
OPTIPRIME_VIEWER_USERNAME
OPTIPRIME_VIEWER_PASSWORD
DATABASE_URL
```

Recommended deployment approach:

1. Use GitHub as the source of truth.
2. Keep `.env` local only.
3. Use Azure-managed environment variables for secrets.
4. Use Azure PostgreSQL or another managed database for shared team data.
5. Build the frontend with `VITE_API_URL` pointing to the deployed API URL.
6. Run the FastAPI backend behind HTTPS.
7. Restrict CORS origins to the deployed frontend domain.

## Authentication Security

The web client keeps its signed session token in memory only. Refreshing or reopening the site requires the user to enter their password again. Session tokens also expire on the backend after `OPTIPRIME_SESSION_MINUTES` and cannot be replaced with the old permanent API tokens while `OPTIPRIME_ALLOW_STATIC_TOKENS=false`.

For production:

- use unique admin and viewer passwords of at least 16 characters
- keep `OPTIPRIME_SESSION_SECRET` independent from `ROBOTX_TOKEN`
- leave `OPTIPRIME_ALLOW_STATIC_TOKENS=false`
- rotate passwords and database credentials whenever they are shared outside the password manager
- use a least-privileged PostgreSQL role instead of the Supabase `postgres` owner account

To create the restricted Supabase runtime role, review and run `database/create_runtime_role.sql` in the Supabase SQL Editor. Then change Vercel's `DATABASE_URL` username to `optiprime_runtime`, set `OPTIPRIME_SKIP_STARTUP_DB=true`, and redeploy. Schema migrations must be run separately with the database-owner account before deploying code that changes tables.

## Security Checklist Before Sharing

Run these before handing off:

```powershell
git status --short
git ls-files | Select-String -Pattern '(^|/)(\.env|.*\.db|.*\.sqlite|.*\.sqlite3|.*\.pem|.*\.key|.*\.crt)$'
git grep -n -I -E '(TOKEN|PASSWORD|SECRET|authtoken|Bearer)' -- . ':!frontend/package-lock.json'
cd frontend; npm audit; cd ..
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt
```

Expected result:

- no `.env` tracked
- no database file tracked
- only placeholder secrets in `.env.example`

If a real secret was ever committed, rotate it immediately and remove it from Git history before making the repository public.

## Team Workflow

Suggested contribution flow:

```powershell
git checkout -b feature/short-description
git add .
git commit -m "Describe the change"
git push origin feature/short-description
```

Then open a pull request on GitHub.

## API Notes

The API expects bearer token authentication:

```text
Authorization: Bearer <ROBOTX_TOKEN>
```

The local WebSocket endpoint is:

```text
ws://localhost:8010/api/ws?token=<ROBOTX_TOKEN>
```
