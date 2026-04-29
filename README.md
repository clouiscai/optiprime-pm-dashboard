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

## Security Checklist Before Sharing

Run these before handing off:

```powershell
git status --short
git ls-files | Select-String -Pattern '(^|/)(\.env|.*\.db|.*\.sqlite|.*\.sqlite3|.*\.pem|.*\.key|.*\.crt)$'
git grep -n -I -E '(TOKEN|PASSWORD|SECRET|authtoken|Bearer)' -- . ':!frontend/package-lock.json'
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
