from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.connectors.binance import BinanceConnector
from app.connectors.bybit import BybitConnector
from app.connectors.registry import connectors
from app.core.config import settings
from app.core.db import dispose_engine, get_session_factory
from app.core.ratelimit import rate_limiters
from app.jobs.scheduler import JobDefinition, scheduler
from app.jobs.symbols import make_instrument_refresh, make_price_refresh

EXCHANGES = ["BINANCE", "BYBIT"]


def setup_connectors() -> None:
    """Register connectors and their rate-limit budgets."""
    binance_bucket = rate_limiters.configure("BINANCE", rate=5, capacity=10)
    bybit_bucket = rate_limiters.configure("BYBIT", rate=5, capacity=10)
    connectors.register(BinanceConnector(bucket=binance_bucket))
    connectors.register(BybitConnector(bucket=bybit_bucket))


def setup_jobs() -> None:
    for exchange in EXCHANGES:
        scheduler.register(
            JobDefinition(
                name="symbols.refresh_instruments",
                interval_seconds=3600,
                func=make_instrument_refresh(exchange),
                exchange=exchange,
                run_on_start=True,
            )
        )
        scheduler.register(
            JobDefinition(
                name="symbols.refresh_prices",
                interval_seconds=300,
                func=make_price_refresh(exchange),
                exchange=exchange,
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
