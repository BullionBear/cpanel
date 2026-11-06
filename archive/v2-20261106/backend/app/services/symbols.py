"""Symbol unification service: keeps universal_ticker in sync with venues.

Rules (see ARCHITECTURE.md):
- New instruments are inserted; changed filters are updated.
- Instruments that disappear from a venue's listing are marked delisted,
  never deleted — history keeps referencing them, and universal symbols are
  never reused.
"""
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import ExchangeConnector
from app.connectors.types import Instrument
from app.models.universal_ticker import UniversalTicker


def _apply(row: UniversalTicker, instrument: Instrument, now: datetime) -> None:
    f = instrument.filters
    row.exchange = instrument.exchange
    row.market_type = instrument.market_type.value
    row.native_symbol = instrument.native_symbol
    row.base_asset = instrument.base_asset
    row.quote_asset = instrument.quote_asset
    row.price_tick = f.price_tick
    row.qty_step = f.qty_step
    row.min_qty = f.min_qty
    row.max_qty = f.max_qty
    row.min_notional = f.min_notional
    row.contract_size = instrument.contract_size
    row.settle_asset = instrument.settle_asset
    row.expiry = instrument.expiry
    row.status = instrument.status
    row.last_seen_at = now


async def refresh_instruments(db: AsyncSession, connector: ExchangeConnector) -> int:
    """One refresh pass for one exchange. Returns number of rows touched."""
    instruments = await connector.fetch_instruments()
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(UniversalTicker).where(UniversalTicker.exchange == connector.exchange)
    )
    existing = {row.universal_symbol: row for row in result.scalars()}

    touched = 0
    seen: set[str] = set()
    for instrument in instruments:
        seen.add(instrument.universal_symbol)
        row = existing.get(instrument.universal_symbol)
        if row is None:
            row = UniversalTicker(
                universal_symbol=instrument.universal_symbol,
                first_seen_at=now,
            )
            _apply(row, instrument, now)
            db.add(row)
        else:
            _apply(row, instrument, now)
        touched += 1

    # Anything previously listed but absent from this pass is delisted.
    for universal_symbol, row in existing.items():
        if universal_symbol not in seen and row.status != "delisted":
            row.status = "delisted"
            touched += 1

    await db.commit()
    return touched


async def refresh_prices(db: AsyncSession, connector: ExchangeConnector) -> int:
    """Update reference prices for one exchange. Returns rows updated."""
    prices = await connector.fetch_reference_prices()
    if not prices:
        return 0
    now = datetime.now(timezone.utc)

    # Only update rows that exist; prices for unknown symbols are dropped
    result = await db.execute(
        select(UniversalTicker.universal_symbol).where(
            UniversalTicker.exchange == connector.exchange
        )
    )
    known = set(result.scalars())
    params = [
        {"universal_symbol": symbol, "last_price": price, "price_updated_at": now}
        for symbol, price in prices.items()
        if symbol in known
    ]
    if not params:
        return 0
    await db.execute(update(UniversalTicker), params)  # bulk UPDATE by primary key
    await db.commit()
    return len(params)
