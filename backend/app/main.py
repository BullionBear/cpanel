from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.connectors.fake import FakeConnector
from app.connectors.registry import connectors
from app.core.config import settings
from app.core.db import dispose_engine, get_session_factory
from app.core.ratelimit import rate_limiters
from app.jobs.demo import heartbeat
from app.jobs.scheduler import JobDefinition, scheduler


def setup_connectors() -> None:
    """Register connectors and their rate-limit budgets.

    Real exchange connectors are added here as they are implemented
    (Phase 1+). The FAKE connector backs tests and the Phase 0 demo job.
    """
    connectors.register(FakeConnector())
    rate_limiters.configure("FAKE", rate=10, capacity=20)


def setup_jobs() -> None:
    scheduler.register(
        JobDefinition(
            name="demo.heartbeat",
            interval_seconds=60,
            func=heartbeat,
            exchange="FAKE",
            run_on_start=True,
        )
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_connectors()
    setup_jobs()
    if settings.SCHEDULER_ENABLED and settings.DATABASE_URL:
        await scheduler.start(get_session_factory())
    yield
    if scheduler.running:
        await scheduler.stop()
    await dispose_engine()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan,
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
