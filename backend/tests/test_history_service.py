"""History service tests: cursor seeding, idempotent ingestion, upserts."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.connectors.base import ExchangeConnector
from app.connectors.fake import FakeConnector
from app.connectors.types import OrderRequest, OrderSide, OrderType
from app.models.history import (
    DepositHistory,
    OrderHistory,
    SyncCursor,
    TradeHistory,
    WithdrawalHistory,
)
from app.services import accounts as accounts_svc
from app.services import history as svc


@pytest.fixture
def connector():
    return FakeConnector()


async def _account_with_key(db, connector):
    account = await accounts_svc.register_account(db, "FAKE", "main", "main")
    await accounts_svc.add_credential(db, connector, account, "HMAC", "api-key-1", "secret-1")
    return account


async def _fill_orders(connector, count, symbol="FAKE_SPOT_BTCUSDT"):
    trades = []
    for _ in range(count):
        order = await connector.place_order(
            {"api_key": "k", "secret": "s"},
            OrderRequest(
                universal_symbol=symbol,
                side=OrderSide.BUY,
                type=OrderType.LIMIT,
                qty=Decimal("0.001"),
                price=Decimal("60000"),
            ),
        )
        trades.append(connector.fill_order(order.native_order_id))
    return trades


# -- new-stream seeding (3-day backfill) ---------------------------------------

def test_base_initial_cursor_is_epoch_ms():
    start = datetime(2026, 7, 12, tzinfo=timezone.utc)
    cursor = ExchangeConnector().initial_history_cursor(start)
    assert cursor == str(int(start.timestamp() * 1000))


async def test_new_stream_seeds_three_day_backfill(db):
    """An account without a sync_cursor backfills the past 3 days first."""

    class SeededFake(FakeConnector):
        seed_start: datetime | None = None

        def initial_history_cursor(self, start):
            self.seed_start = start
            return ExchangeConnector.initial_history_cursor(self, start)

    connector = SeededFake()
    account = await _account_with_key(db, connector)

    before = datetime.now(timezone.utc)
    await svc.sync_stream(db, connector, account, "trades", "account")

    assert connector.seed_start is not None
    expected = before - svc.NEW_STREAM_BACKFILL
    assert abs((connector.seed_start - expected).total_seconds()) < 60

    row = (await db.execute(select(SyncCursor))).scalars().one()
    assert row.data_kind == "trades"
    assert row.stream_key == "account"
    seeded_ms = int(row.cursor)
    assert abs(seeded_ms - int(expected.timestamp() * 1000)) < 60_000


async def test_existing_cursor_is_not_reseeded(db, connector):
    account = await _account_with_key(db, connector)
    await svc.sync_stream(db, connector, account, "trades", "account")
    first = (await db.execute(select(SyncCursor))).scalars().one()

    await _fill_orders(connector, 120)
    await svc.sync_stream(db, connector, account, "trades", "account")
    rows = (await db.execute(select(SyncCursor))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == first.id


# -- generic sync worker --------------------------------------------------------

async def test_sync_trades_is_idempotent(db, connector):
    account = await _account_with_key(db, connector)
    await _fill_orders(connector, 3)

    assert await svc.sync_stream(db, connector, account, "trades", "account") == 3
    assert await svc.sync_stream(db, connector, account, "trades", "account") == 0

    rows = (await db.execute(select(TradeHistory))).scalars().all()
    assert len(rows) == 3
    assert {r.exchange for r in rows} == {"FAKE"}
    assert all(r.native_order_id for r in rows)


async def test_cursor_advances_and_resumes(db, connector):
    account = await _account_with_key(db, connector)
    await _fill_orders(connector, 150)

    assert await svc.sync_stream(db, connector, account, "trades", "account") == 150
    cursor = (await db.execute(select(SyncCursor))).scalars().one()
    # The fake pages by 100; the last stored cursor is the final page boundary.
    assert cursor.cursor == "100"
    assert cursor.last_synced_at is not None
    assert cursor.last_error is None

    # New activity resumes from the stored cursor, re-ingesting only the new fills.
    await _fill_orders(connector, 10)
    assert await svc.sync_stream(db, connector, account, "trades", "account") == 10
    assert len((await db.execute(select(TradeHistory))).scalars().all()) == 160


async def test_order_history_upsert_advances_state(db, connector):
    account = await _account_with_key(db, connector)
    order = await connector.place_order(
        {"api_key": "k", "secret": "s"},
        OrderRequest(
            universal_symbol="FAKE_PERP_ETHUSDT",
            side=OrderSide.SELL,
            type=OrderType.LIMIT,
            qty=Decimal("2"),
            price=Decimal("3100"),
        ),
    )
    assert await svc.sync_stream(db, connector, account, "orders", "account") == 1
    row = (await db.execute(select(OrderHistory))).scalars().one()
    assert row.status == "OPEN"

    connector.fill_order(order.native_order_id)
    assert await svc.sync_stream(db, connector, account, "orders", "account") == 1
    row = (await db.execute(select(OrderHistory))).scalars().one()
    assert row.status == "FILLED"
    assert row.filled_qty == Decimal("2")


async def test_sync_account_history_covers_both_kinds(db, connector):
    account = await _account_with_key(db, connector)
    await _fill_orders(connector, 2)

    changed = await svc.sync_account_history(db, connector, account)
    assert changed == 4  # 2 trades + 2 orders (no fund rows seeded)

    assert len((await db.execute(select(TradeHistory))).scalars().all()) == 2
    assert len((await db.execute(select(OrderHistory))).scalars().all()) == 2
    # Deposit/withdrawal streams still sync (empty) and leave cursor rows.
    kinds = {
        row.data_kind
        for row in (await db.execute(select(SyncCursor))).scalars().all()
    }
    assert kinds == {"trades", "orders", "deposits", "withdrawals"}


async def test_sync_deposits_and_withdrawals_from_venue(db, connector):
    """Fund history is ingested from the exchange, not panel transfer rows."""
    account = await _account_with_key(db, connector)
    connector.credit_deposit("USDT", Decimal("100"), tx_id="0xd1")
    connector.credit_deposit("BTC", Decimal("0.5"), tx_id="0xd2")
    wd = await connector.withdraw(
        {"api_key": "k", "secret": "s"},
        "USDT",
        Decimal("40"),
        address="TAddr",
        network="TRX",
    )

    assert await svc.sync_stream(db, connector, account, "deposits", "account") == 2
    assert await svc.sync_stream(db, connector, account, "deposits", "account") == 0
    deposits = (await db.execute(select(DepositHistory))).scalars().all()
    assert len(deposits) == 2
    assert {d.asset for d in deposits} == {"USDT", "BTC"}

    assert await svc.sync_stream(db, connector, account, "withdrawals", "account") == 1
    row = (await db.execute(select(WithdrawalHistory))).scalars().one()
    assert row.status == "PENDING"
    assert row.native_withdrawal_id == wd.native_id

    connector.complete_withdrawal(wd.native_id, tx_id="0xw", fee=Decimal("0.5"))
    assert await svc.sync_stream(db, connector, account, "withdrawals", "account") == 1
    row = (await db.execute(select(WithdrawalHistory))).scalars().one()
    assert row.status == "COMPLETED"
    assert row.tx_id == "0xw"
    assert row.fee == Decimal("0.5")

    total, listed = await svc.list_deposits(db, asset="USDT")
    assert total == 1
    assert listed[0].amount == Decimal("100")
    total, listed = await svc.list_withdrawals(db, account_id=account.id)
    assert total == 1
    assert listed[0].status == "COMPLETED"


async def test_stream_failure_recorded_on_cursor(db):
    class BrokenFake(FakeConnector):
        async def fetch_trades(self, credential, stream_key, cursor):
            raise RuntimeError("venue exploded")

    connector = BrokenFake()
    account = await _account_with_key(db, connector)

    with pytest.raises(RuntimeError):
        await svc.sync_stream(db, connector, account, "trades", "account")

    row = (await db.execute(select(SyncCursor))).scalars().one()
    assert "venue exploded" in row.last_error
    assert row.last_synced_at is not None

    # A later healthy run clears the error.
    healthy = FakeConnector()
    assert await svc.sync_stream(db, healthy, account, "trades", "account") == 0
    row = (await db.execute(select(SyncCursor))).scalars().one()
    assert row.last_error is None


# -- stream discovery ------------------------------------------------------------

async def test_active_symbols_union(db, connector):
    account = await _account_with_key(db, connector)
    await _fill_orders(connector, 1, symbol="FAKE_SPOT_BTCUSDT")
    await svc.sync_stream(db, connector, account, "orders", "account")

    symbols = await svc.active_symbols(db, account)
    assert symbols == ["FAKE_SPOT_BTCUSDT"]
