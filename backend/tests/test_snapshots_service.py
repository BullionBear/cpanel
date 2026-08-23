"""Snapshot service tests: capture, valuation, and read paths against the fake."""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.connectors.fake import FakeConnector
from app.connectors.types import Balance, Position, WalletType
from app.models.snapshot import BalanceSnapshot, PositionSnapshot
from app.services import accounts as accounts_svc
from app.services import snapshots as svc
from app.services.symbols import refresh_instruments, refresh_prices


@pytest.fixture
def connector():
    c = FakeConnector()
    # A spot wallet holding quote + a priced asset, and one open position.
    c.balances = [
        Balance(wallet=WalletType.SPOT, asset="USDT", free=Decimal("10000"), locked=Decimal("0")),
        Balance(wallet=WalletType.SPOT, asset="BTC", free=Decimal("0.5"), locked=Decimal("0")),
    ]
    c.positions = [
        Position(
            universal_symbol="FAKE_PERP_ETHUSDT",
            side="long",
            size=Decimal("2"),
            entry_price=Decimal("3000"),
            mark_price=Decimal("3100"),
            unrealized_pnl=Decimal("200"),
            margin=Decimal("600"),
        )
    ]
    return c


async def _account_with_key(db, connector):
    account = await accounts_svc.register_account(db, "FAKE", "main", "main")
    await accounts_svc.add_credential(db, connector, account, "HMAC", "api-key-1", "secret-1")
    return account


async def _seed_prices(db, connector):
    await refresh_instruments(db, connector)
    await refresh_prices(db, connector)  # FAKE_SPOT_BTCUSDT -> 60000


async def test_capture_writes_balances_and_positions(db, connector):
    await _seed_prices(db, connector)
    account = await _account_with_key(db, connector)

    written = await svc.capture_snapshots(db, connector, account)
    assert written == 3  # 2 balances + 1 position

    balances = (await db.execute(select(BalanceSnapshot))).scalars().all()
    positions = (await db.execute(select(PositionSnapshot))).scalars().all()
    assert {b.asset for b in balances} == {"USDT", "BTC"}
    assert len(positions) == 1
    # Every row of the pass shares one batch stamp.
    assert len({b.batch_id for b in balances} | {p.batch_id for p in positions}) == 1


async def test_valuation_uses_reference_prices(db, connector):
    await _seed_prices(db, connector)
    account = await _account_with_key(db, connector)
    await svc.capture_snapshots(db, connector, account)

    rows = {b.asset: b for b in (await db.execute(select(BalanceSnapshot))).scalars()}
    assert rows["USDT"].value_quote == Decimal("10000")   # stable, 1:1
    assert rows["BTC"].value_quote == Decimal("30000")    # 0.5 * 60000
    assert rows["BTC"].quote_asset == "USDT"

    # Asset value = spot value (40000) + position unrealized PnL (200).
    _, total = await svc.account_value(db, account.id)
    assert total == Decimal("40200")


async def test_position_notional_valued(db, connector):
    await _seed_prices(db, connector)
    account = await _account_with_key(db, connector)
    await svc.capture_snapshots(db, connector, account)

    position = (await db.execute(select(PositionSnapshot))).scalars().one()
    assert position.side == "long"
    assert position.value_quote == Decimal("6200")  # 2 * 3100


async def test_holdings_returns_latest_batch(db, connector):
    await _seed_prices(db, connector)
    account = await _account_with_key(db, connector)
    await svc.capture_snapshots(db, connector, account)

    # A second capture with a smaller book must fully replace the view.
    connector.balances = [
        Balance(wallet=WalletType.SPOT, asset="USDT", free=Decimal("5000"), locked=Decimal("0")),
    ]
    connector.positions = []
    await svc.capture_snapshots(db, connector, account)

    balances, positions, taken_at = await svc.get_holdings(db, account.id)
    assert [b.asset for b in balances] == ["USDT"]
    assert balances[0].total == Decimal("5000")
    assert positions == []
    assert taken_at is not None


async def test_value_history_one_point_per_batch(db, connector):
    await _seed_prices(db, connector)
    account = await _account_with_key(db, connector)
    await svc.capture_snapshots(db, connector, account)
    await svc.capture_snapshots(db, connector, account)

    history = await svc.value_history(db, account.id)
    assert len(history) == 2
    # spot value (40000) + position unrealized PnL (200)
    assert all(total == Decimal("40200") for _, _, total in history)


async def test_pnl_history_one_point_per_batch(db, connector):
    await _seed_prices(db, connector)
    account = await _account_with_key(db, connector)
    await svc.capture_snapshots(db, connector, account)
    await svc.capture_snapshots(db, connector, account)

    history = await svc.pnl_history(db, account.id)
    assert len(history) == 2
    # Only the position's unrealized PnL (200); balances do not contribute.
    assert all(pnl == Decimal("200") for _, _, pnl in history)


async def test_unpriced_asset_has_null_value(db, connector):
    await _seed_prices(db, connector)
    account = await _account_with_key(db, connector)
    connector.balances = [
        Balance(wallet=WalletType.SPOT, asset="WEIRD", free=Decimal("3"), locked=Decimal("0")),
    ]
    connector.positions = []
    await svc.capture_snapshots(db, connector, account)

    row = (await db.execute(select(BalanceSnapshot))).scalars().one()
    assert row.value_quote is None
    assert row.quote_asset is None
    _, total = await svc.account_value(db, account.id)
    assert total == Decimal("0")


async def test_capture_requires_active_credential(db, connector):
    await _seed_prices(db, connector)
    account = await accounts_svc.register_account(db, "FAKE", "keyless", "main")
    with pytest.raises(svc.AccountError):
        await svc.capture_snapshots(db, connector, account)


async def test_empty_capture_records_a_batch(db, connector):
    """An account drained to zero must report zero, not its last funded state."""
    await _seed_prices(db, connector)
    account = await _account_with_key(db, connector)
    await svc.capture_snapshots(db, connector, account)
    funded_at = (await svc.latest_batch(db, account.id))[1]

    # Everything moved out: the venue now returns nothing at all.
    connector.balances = []
    connector.positions = []
    written = await svc.capture_snapshots(db, connector, account)
    assert written == 0

    batch = await svc.latest_batch(db, account.id)
    assert batch is not None
    assert batch[1] > funded_at  # the view advanced past the funded capture

    balances, positions, taken_at = await svc.get_holdings(db, account.id)
    assert balances == [] and positions == []
    assert taken_at == batch[1]

    stamped_at, total = await svc.account_value(db, account.id)
    assert total == Decimal("0")
    assert stamped_at == batch[1]


async def test_never_captured_account_has_no_batch(db, connector):
    """No capture at all stays distinguishable from a capture that found none."""
    await _seed_prices(db, connector)
    account = await _account_with_key(db, connector)

    assert await svc.latest_batch(db, account.id) is None
    assert await svc.get_holdings(db, account.id) == ([], [], None)
    assert await svc.account_value(db, account.id) == (None, Decimal("0"))


async def test_history_includes_empty_batches_as_zero(db, connector):
    await _seed_prices(db, connector)
    account = await _account_with_key(db, connector)
    await svc.capture_snapshots(db, connector, account)

    connector.balances = []
    connector.positions = []
    await svc.capture_snapshots(db, connector, account)

    # Newest first: the empty capture is a zero point, not a missing one.
    assert [total for _, _, total in await svc.value_history(db, account.id)] == [
        Decimal("0"),
        Decimal("40200"),
    ]
    assert [pnl for _, _, pnl in await svc.pnl_history(db, account.id)] == [
        Decimal("0"),
        Decimal("200"),
    ]
