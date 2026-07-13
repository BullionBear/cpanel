"""FastAPI application main file."""
import subprocess
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI

from app.api.v1 import api_router as api_router_v1
from app.api.v2 import api_router as api_router_v2
from app.core.config import settings
from app.middleware.cors import setup_cors
from app.middleware.logging import setup_logging


@lru_cache(maxsize=1)
def _resolve_git_commit() -> str:
    """Return short git commit hash, falling back to local `git` for dev runs."""
    if settings.GIT_COMMIT and settings.GIT_COMMIT != "unknown":
        return settings.GIT_COMMIT[:7]
    try:
        repo_root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        return result.stdout.strip() or "unknown"
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return "unknown"


# Create FastAPI app
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    debug=settings.DEBUG,
)

# Setup middleware
setup_cors(app)
setup_logging(app)

# Include API routers
app.include_router(api_router_v1, prefix=f"{settings.API_PREFIX}/v1")
app.include_router(api_router_v2, prefix=f"{settings.API_PREFIX}/v2")



@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "CPanel API",
        "version": settings.VERSION,
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/status")
async def status():
    """Build/version status for developers.

    Returns the running version, the short git commit hash the image was
    built from, and the build timestamp injected at docker build time.
    """
    return {
        "version": settings.VERSION,
        "git_commit": _resolve_git_commit(),
        "build_time": settings.BUILD_TIME,
    }

