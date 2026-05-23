import os
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router
from database.seed import seed
from database.session import init_db


logger = logging.getLogger(__name__)
app = FastAPI(title="OptiPrime Project OS", version="0.1.0")
ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = ROOT / "frontend" / "dist"

allowed_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
allowed_origins.extend([origin.strip() for origin in os.getenv("FRONTEND_ORIGINS", "").split(",") if origin.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"https://.*\.(ngrok-free\.app|vercel\.app)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    if os.getenv("OPTIPRIME_SKIP_STARTUP_DB", "").lower() in {"1", "true", "yes"}:
        logger.info("Skipping startup database maintenance.")
        return
    try:
        init_db()
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
