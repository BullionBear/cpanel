from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.connectors.binance import BinanceConnector
from app.connectors.bitget import BitgetConnector
from app.connectors.bybit import BybitConnector
from app.connectors.gateio import GateIOConnector
from app.connectors.registry import connectors
from app.core.config import settings
from app.core.db import dispose_engine, get_session_factory
from app.core.ratelimit import rate_limiters
from app.jobs.history import make_history_sync_job
from app.jobs.orders import make_open_order_poll_job
from app.jobs.scheduler import JobDefinition, scheduler
from app.jobs.snapshots import make_snapshot_job
from app.jobs.symbols import make_instrument_refresh, make_price_refresh
from app.jobs.transfers import make_transfer_progress_job
from app.middleware.activity_audit import ActivityAuditMiddleware

EXCHANGES = ["BINANCE", "BYBIT", "GATEIO", "BITGET"]


def setup_connectors() -> None:
    """Register connectors and their rate-limit budgets."""
    binance_bucket = rate_limiters.configure("BINANCE", rate=5, capacity=10)
    bybit_bucket = rate_limiters.configure("BYBIT", rate=5, capacity=10)
    gateio_bucket = rate_limiters.configure("GATEIO", rate=5, capacity=10)
    bitget_bucket = rate_limiters.configure("BITGET", rate=5, capacity=10)
    connectors.register(BinanceConnector(bucket=binance_bucket))
    connectors.register(BybitConnector(bucket=bybit_bucket))
    connectors.register(GateIOConnector(bucket=gateio_bucket))
    connectors.register(BitgetConnector(bucket=bitget_bucket))


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
        scheduler.register(
            JobDefinition(
                name="snapshots.capture",
                interval_seconds=3600,
                func=make_snapshot_job(exchange),
                exchange=exchange,
            )
        )
        scheduler.register(
            JobDefinition(
                name="orders.poll_open",
                interval_seconds=30,
                func=make_open_order_poll_job(exchange),
                exchange=exchange,
            )
        )
        scheduler.register(
            JobDefinition(
                name="history.sync",
                interval_seconds=120,
                func=make_history_sync_job(exchange),
                exchange=exchange,
            )
        )
    # Transfers span exchanges, so progress tracking is one global job.
    scheduler.register(
        JobDefinition(
            name="transfers.progress",
            interval_seconds=60,
            func=make_transfer_progress_job(),
        )
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_connectors()
    setup_jobs()
    if settings.DATABASE_URL:
        app.state.session_factory = get_session_factory()
    if settings.SCHEDULER_ENABLED and settings.DATABASE_URL:
        await scheduler.start(get_session_factory())
    yield
    if scheduler.running:
        await scheduler.stop()
    await dispose_engine()
    app.state.session_factory = None


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

app.add_middleware(ActivityAuditMiddleware)
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
