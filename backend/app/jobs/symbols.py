"""Scheduled instrument and reference-price refresh jobs, one set per exchange."""
from app.connectors.registry import connectors
from app.core.db import get_session_factory
from app.services.symbols import refresh_instruments, refresh_prices


def make_instrument_refresh(exchange: str):
    async def job() -> int:
        connector = connectors.get(exchange)
        async with get_session_factory()() as db:
            return await refresh_instruments(db, connector)

    return job


def make_price_refresh(exchange: str):
    async def job() -> int:
        connector = connectors.get(exchange)
        async with get_session_factory()() as db:
            return await refresh_prices(db, connector)

    return job
