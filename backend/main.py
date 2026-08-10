import os
import logging
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from api.routes import router
from database.seed import seed
from database.session import init_db


logger = logging.getLogger(__name__)
is_production = bool(os.getenv("VERCEL")) or os.getenv("OPTIPRIME_ENV", "").lower() == "production"
app = FastAPI(
    title="OptiPrime Project OS",
    version="0.1.0",
    docs_url=None if is_production else "/docs",
    redoc_url=None if is_production else "/redoc",
    openapi_url=None if is_production else "/openapi.json",
)
ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIR = ROOT / "public"
LEGACY_FRONTEND_DIST = ROOT / "frontend" / "dist"
FRONTEND_DIST = PUBLIC_DIR if PUBLIC_DIR.exists() else LEGACY_FRONTEND_DIST

allowed_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
allowed_origins.extend([origin.strip() for origin in os.getenv("FRONTEND_ORIGINS", "").split(",") if origin.strip()])
trusted_hosts = {"localhost", "127.0.0.1", "*.vercel.app", "*.ngrok-free.app"}
trusted_hosts.update(urlparse(origin).hostname for origin in allowed_origins if urlparse(origin).hostname)
trusted_hosts.update(host.strip() for host in os.getenv("TRUSTED_HOSTS", "").split(",") if host.strip())

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(trusted_hosts))


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self' wss:; font-src 'self' data:; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    if request.url.path.startswith("/api/") or request.url.path == "/health":
        response.headers["Cache-Control"] = "no-store"
    if is_production:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    return response


@app.on_event("startup")
def startup():
    if os.getenv("OPTIPRIME_SKIP_STARTUP_DB", "").lower() in {"1", "true", "yes"}:
        logger.info("Skipping startup database maintenance.")
        return
    try:
        init_db()
        if not is_production or os.getenv("OPTIPRIME_RUN_STARTUP_SEED", "").lower() in {"1", "true", "yes"}:
            seed()
    except Exception:
        logger.exception("Database startup failed; serving non-database routes while configuration is fixed.")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def root():
    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "app": "OptiPrime Project OS",
        "ok": True,
        "message": "Backend is running. Open the frontend URL to use the app.",
        "docs": "/docs",
        "health": "/health",
    }


app.include_router(router, prefix="/api")

assets_dir = FRONTEND_DIST / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend_fallback(path: str):
    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists() and not path.startswith("api/"):
        return FileResponse(index_file)
    return {
        "app": "OptiPrime Project OS",
        "ok": True,
        "message": "Backend is running. Build the frontend with `npm run build` to serve the app here.",
    }
