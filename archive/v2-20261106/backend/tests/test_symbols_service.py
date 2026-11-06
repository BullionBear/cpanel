"""Refresh service tests: upsert, filter updates, delist marking, prices."""
from decimal import Decimal

from sqlalchemy import select

from app.connectors.fake import FakeConnector
from app.connectors.types import Instrument, InstrumentFilters, MarketType
from app.models.universal_ticker import UniversalTicker
from app.services.symbols import refresh_instruments, refresh_prices


def make_instrument(universal="FAKE_SPOT_BTCUSDT", tick="0.01", status="active"):
    return Instrument(
        universal_symbol=universal,
        exchange="FAKE",
        market_type=MarketType.SPOT,
        native_symbol=universal.split("_")[-1],
        base_asset="BTC",
        quote_asset="USDT",
        filters=InstrumentFilters(
            price_tick=Decimal(tick),
            qty_step=Decimal("0.00001"),
            min_qty=Decimal("0.00001"),
            min_notional=Decimal("5"),
        ),
        status=status,
    )


async def test_initial_refresh_inserts(db):
    connector = FakeConnector()
    touched = await refresh_instruments(db, connector)
    assert touched == 2  # FakeConnector ships two instruments

    rows = (await db.execute(select(UniversalTicker))).scalars().all()
    assert {r.universal_symbol for r in rows} == {"FAKE_SPOT_BTCUSDT", "FAKE_PERP_ETHUSDT"}
    assert all(r.status == "active" for r in rows)


async def test_filter_change_updates_row(db):
    connector = FakeConnector()
    await refresh_instruments(db, connector)

    # Venue retunes the tick size
    connector.instruments = [make_instrument(tick="0.10"), connector.instruments[1]]
    await refresh_instruments(db, connector)

    row = await db.get(UniversalTicker, "FAKE_SPOT_BTCUSDT")
    assert row.price_tick == Decimal("0.10")


async def test_disappeared_instrument_marked_delisted_not_deleted(db):
    connector = FakeConnector()
    await refresh_instruments(db, connector)

    connector.instruments = [connector.instruments[0]]  # ETH perp disappears
    await refresh_instruments(db, connector)

    row = await db.get(UniversalTicker, "FAKE_PERP_ETHUSDT")
    assert row is not None
    assert row.status == "delisted"
    assert (await db.get(UniversalTicker, "FAKE_SPOT_BTCUSDT")).status == "active"


async def test_relisted_instrument_reactivates(db):
    connector = FakeConnector()
    await refresh_instruments(db, connector)
    saved = list(connector.instruments)

    connector.instruments = [saved[0]]
    await refresh_instruments(db, connector)
    connector.instruments = saved
    await refresh_instruments(db, connector)

    assert (await db.get(UniversalTicker, "FAKE_PERP_ETHUSDT")).status == "active"


async def test_refresh_prices_updates_known_symbols_only(db):
    connector = FakeConnector()
    await refresh_instruments(db, connector)
    connector.prices["FAKE_SPOT_UNKNOWNUSDT"] = Decimal("1")  # not in the DB

    updated = await refresh_prices(db, connector)
    assert updated == 2

    row = await db.get(UniversalTicker, "FAKE_SPOT_BTCUSDT")
    assert row.last_price == Decimal("60000")
    assert row.price_updated_at is not None
