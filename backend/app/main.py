from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    debug=settings.DEBUG,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
async def root():
    return {
        "message": "CPanel API",
        "version": settings.VERSION,
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/status")
async def status():
    return {
        "version": settings.VERSION,
        "git_commit": settings.GIT_COMMIT,
        "build_time": settings.BUILD_TIME,
    }
