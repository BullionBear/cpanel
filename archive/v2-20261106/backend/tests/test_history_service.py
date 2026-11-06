"""History service tests: cursor seeding, idempotent ingestion, upserts."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.connectors.base import ExchangeConnector
from app.connectors.fake import FakeConnector
from app.connectors.types import (
    InternalTransfer,
    InternalTransferDirection,
    InternalTransferScope,
    InternalTransferStatus,
    MarketType,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    WalletType,
)
from app.models.history import (
    DepositHistory,
    FundingFeeHistory,
    InternalTransferHistory,
    OrderHistory,
    SyncCursor,
    TradeHistory,
    WithdrawalHistory,
)
from app.models.snapshot import BalanceSnapshot, PositionSnapshot, SnapshotBatch
from app.models.universal_ticker import UniversalTicker
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


async def test_order_history_skips_pure_cancels(db, connector):
    """Cancelled with no fills is noise — never store it; drop if already OPEN."""
    account = await _account_with_key(db, connector)
    cred = {"api_key": "k", "secret": "s"}
    never_synced = await connector.place_order(
        cred,
        OrderRequest(
            universal_symbol="FAKE_PERP_ETHUSDT",
            side=OrderSide.SELL,
            type=OrderType.LIMIT,
            qty=Decimal("2"),
            price=Decimal("3100"),
        ),
    )
    await connector.cancel_order(cred, never_synced.universal_symbol, never_synced.native_order_id)
    assert await svc.sync_stream(db, connector, account, "orders", "account") == 0
    assert (await db.execute(select(OrderHistory))).scalars().all() == []

    open_then_cancel = await connector.place_order(
        cred,
        OrderRequest(
            universal_symbol="FAKE_PERP_ETHUSDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            qty=Decimal("1"),
            price=Decimal("3000"),
        ),
    )
    assert await svc.sync_stream(db, connector, account, "orders", "account") == 1
    assert (await db.execute(select(OrderHistory))).scalars().one().status == "OPEN"

    await connector.cancel_order(
        cred, open_then_cancel.universal_symbol, open_then_cancel.native_order_id
    )
    assert await svc.sync_stream(db, connector, account, "orders", "account") == 1
    assert (await db.execute(select(OrderHistory))).scalars().all() == []


async def test_order_history_keeps_partial_then_cancel(db, connector):
    """Cancel after a partial fill is real activity and must stay in history."""
    account = await _account_with_key(db, connector)
    cred = {"api_key": "k", "secret": "s"}
    order = await connector.place_order(
        cred,
        OrderRequest(
            universal_symbol="FAKE_PERP_ETHUSDT",
            side=OrderSide.SELL,
            type=OrderType.LIMIT,
            qty=Decimal("2"),
            price=Decimal("3100"),
        ),
    )
    resting = connector.orders[order.native_order_id]
    connector.orders[order.native_order_id] = Order(
        **{
            **resting.__dict__,
            "status": OrderStatus.PARTIALLY_FILLED,
            "filled_qty": Decimal("0.5"),
        }
    )
    await connector.cancel_order(cred, order.universal_symbol, order.native_order_id)

    assert await svc.sync_stream(db, connector, account, "orders", "account") == 1
    row = (await db.execute(select(OrderHistory))).scalars().one()
    assert row.status == "CANCELLED"
    assert row.filled_qty == Decimal("0.5")


async def test_sync_account_history_covers_both_kinds(db, connector):
    account = await _account_with_key(db, connector)
    await _fill_orders(connector, 2)

    changed = await svc.sync_account_history(db, connector, account)
    assert changed == 4  # 2 trades + 2 orders (no fund/funding rows seeded)

    assert len((await db.execute(select(TradeHistory))).scalars().all()) == 2
    assert len((await db.execute(select(OrderHistory))).scalars().all()) == 2
    # Deposit/withdrawal/funding streams still sync (empty) and leave cursor rows.
    kinds = {
        row.data_kind
        for row in (await db.execute(select(SyncCursor))).scalars().all()
    }
    assert kinds == {
        "trades", "orders", "deposits", "withdrawals", "funding", "internal_transfers",
    }


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


async def test_sync_funding_fees_from_venue(db, connector):
    """Funding settlements are ingested from the exchange income/transaction log."""
    account = await _account_with_key(db, connector)
    connector.credit_funding_fee(
        "FAKE_PERP_ETHUSDT",
        "USDT",
        Decimal("-1.25"),
        funding_rate=Decimal("0.0001"),
    )
    connector.credit_funding_fee(
        "FAKE_PERP_ETHUSDT",
        "USDT",
        Decimal("0.5"),
        funding_rate=Decimal("-0.00005"),
    )

    assert await svc.sync_stream(db, connector, account, "funding", "account") == 2
    assert await svc.sync_stream(db, connector, account, "funding", "account") == 0
    rows = (await db.execute(select(FundingFeeHistory))).scalars().all()
    assert len(rows) == 2
    assert {r.universal_symbol for r in rows} == {"FAKE_PERP_ETHUSDT"}
    assert sum(r.amount for r in rows) == Decimal("-0.75")

    total, listed = await svc.list_funding_fees(
        db, account_id=account.id, universal_symbol="FAKE_PERP_ETHUSDT"
    )
    assert total == 2
    assert listed[0].funding_rate is not None


def _wallet_move(native_id, amount, status=InternalTransferStatus.CONFIRMED):
    return InternalTransfer(
        native_id=native_id,
        asset="USDT",
        amount=Decimal(amount),
        status=status,
        scope=InternalTransferScope.WALLET,
        from_wallet=WalletType.UMARGIN,
        to_wallet=WalletType.SPOT,
        created_at=datetime(2026, 7, 30, 13, 45, tzinfo=timezone.utc),
    )


def _account_move(native_id, amount, status=InternalTransferStatus.CONFIRMED):
    return InternalTransfer(
        native_id=native_id,
        asset="USDT",
        amount=Decimal(amount),
        status=status,
        scope=InternalTransferScope.ACCOUNT,
        direction=InternalTransferDirection.OUT,
        from_wallet=WalletType.SPOT,
        to_wallet=WalletType.SPOT,
        counterparty="master@example.com",
        created_at=datetime(2026, 7, 30, 13, 46, tzinfo=timezone.utc),
    )


def test_every_data_kind_fits_its_column():
    """A kind name longer than the column silently passes on SQLite.

    PostgreSQL raises StringDataRightTruncation instead, which poisons the
    session and aborts the rest of that account's sync — so the length is
    asserted here rather than discovered in production.
    """
    limit = SyncCursor.__table__.c.data_kind.type.length
    too_long = [kind for kind in svc.DATA_KINDS if len(kind) > limit]
    assert not too_long, f"data kinds exceed varchar({limit}): {too_long}"


def test_stream_key_lengths_fit_their_column():
    """Same trap, one column over: connector stream keys are venue-defined."""
    limit = SyncCursor.__table__.c.stream_key.type.length
    connector = FakeConnector()
    keys = [
        *connector.fund_history_stream_keys(),
        *connector.funding_history_stream_keys(),
        *connector.internal_transfer_stream_keys("sub"),
    ]
    assert all(len(key) <= limit for key in keys)


async def test_sync_internal_transfers_from_venue(db, connector):
    """The two legs that empty a subaccount, neither of which is on a chain."""
    account = await _account_with_key(db, connector)
    connector.internal_transfers = [
        _wallet_move("1001", "91.84137568"),
        _account_move("2001", "91.84137568"),
    ]

    assert await svc.sync_stream(db, connector, account, "internal_transfers", "account") == 2
    # Re-ingesting the same window changes nothing.
    assert await svc.sync_stream(db, connector, account, "internal_transfers", "account") == 0

    rows = (await db.execute(select(InternalTransferHistory))).scalars().all()
    assert {r.scope for r in rows} == {"WALLET", "ACCOUNT"}
    wallet_row = next(r for r in rows if r.scope == "WALLET")
    assert wallet_row.from_wallet == "UMARGIN"
    assert wallet_row.to_wallet == "SPOT"
    assert wallet_row.direction is None
    account_row = next(r for r in rows if r.scope == "ACCOUNT")
    assert account_row.direction == "OUT"
    assert account_row.counterparty == "master@example.com"

    total, listed = await svc.list_internal_transfers(db, account_id=account.id)
    assert total == 2
    _, wallet_only = await svc.list_internal_transfers(
        db, account_id=account.id, scope="wallet"
    )
    assert [r.scope for r in wallet_only] == ["WALLET"]


async def test_internal_transfer_scope_separates_colliding_ids(db, connector):
    """Wallet and account transfer ids live in different venue spaces."""
    account = await _account_with_key(db, connector)
    connector.internal_transfers = [
        _wallet_move("777", "10"),
        _account_move("777", "10"),
    ]

    assert await svc.sync_stream(db, connector, account, "internal_transfers", "account") == 2
    rows = (await db.execute(select(InternalTransferHistory))).scalars().all()
    assert len(rows) == 2
    assert {r.native_transfer_id for r in rows} == {"777"}


async def test_internal_transfer_status_advances(db, connector):
    account = await _account_with_key(db, connector)
    connector.internal_transfers = [
        _wallet_move("3001", "5", status=InternalTransferStatus.PENDING)
    ]
    assert await svc.sync_stream(db, connector, account, "internal_transfers", "account") == 1

    connector.internal_transfers = [
        _wallet_move("3001", "5", status=InternalTransferStatus.CONFIRMED)
    ]
    assert await svc.sync_stream(db, connector, account, "internal_transfers", "account") == 1

    row = (await db.execute(select(InternalTransferHistory))).scalars().one()
    assert row.status == "CONFIRMED"


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


def _ticker(
    symbol: str,
    *,
    market: MarketType,
    base: str,
    quote: str,
    settle: str | None = None,
    exchange: str = "FAKE",
) -> UniversalTicker:
    return UniversalTicker(
        universal_symbol=symbol,
        exchange=exchange,
        market_type=market.value,
        native_symbol=f"{base}{quote}",
        base_asset=base,
        quote_asset=quote,
        price_tick=Decimal("0.01"),
        qty_step=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        settle_asset=settle,
        status="active",
    )


async def _write_holdings(
    db,
    account,
    balances: list[tuple[WalletType | str, str, str]],
    positions: list[str] | None = None,
):
    """One snapshot batch: ``(wallet, asset, total)`` plus optional position symbols."""
    batch_id = f"batch-{account.id}-{len(balances)}"
    taken_at = datetime.now(timezone.utc)
    for wallet, asset, total in balances:
        amount = Decimal(total)
        db.add(
            BalanceSnapshot(
                batch_id=batch_id,
                account_id=account.id,
                exchange=account.exchange,
                wallet=wallet.value if isinstance(wallet, WalletType) else wallet,
                asset=asset,
                free=amount,
                locked=Decimal("0"),
                total=amount,
                taken_at=taken_at,
            )
        )
    for symbol in positions or []:
        db.add(
            PositionSnapshot(
                batch_id=batch_id,
                account_id=account.id,
                exchange=account.exchange,
                universal_symbol=symbol,
                side="long",
                size=Decimal("1"),
                entry_price=Decimal("1"),
                taken_at=taken_at,
            )
        )
    db.add(
        SnapshotBatch(
            batch_id=batch_id,
            account_id=account.id,
            exchange=account.exchange,
            balance_rows=len(balances),
            position_rows=len(positions or []),
            taken_at=taken_at,
        )
    )
    await db.flush()


async def test_active_symbols_spot_bipartite_from_balances(db, connector):
    """Classic spot: stables × coins, verified against listed instruments."""
    account = await _account_with_key(db, connector)
    db.add_all(
        [
            _ticker("FAKE_SPOT_BTCUSDT", market=MarketType.SPOT, base="BTC", quote="USDT"),
            _ticker("FAKE_SPOT_BTCUSDC", market=MarketType.SPOT, base="BTC", quote="USDC"),
            _ticker("FAKE_SPOT_ETHUSDT", market=MarketType.SPOT, base="ETH", quote="USDT"),
            _ticker("FAKE_SPOT_ETHUSDC", market=MarketType.SPOT, base="ETH", quote="USDC"),
            _ticker("FAKE_SPOT_ETHBTC", market=MarketType.SPOT, base="ETH", quote="BTC"),
        ]
    )
    await _write_holdings(
        db,
        account,
        [
            (WalletType.SPOT, "BTC", "0.5"),
            (WalletType.SPOT, "ETH", "2"),
            (WalletType.SPOT, "USDT", "100"),
            (WalletType.SPOT, "USDC", "50"),
        ],
    )

    symbols = await svc.active_symbols(db, account)
    assert symbols == [
        "FAKE_SPOT_BTCUSDC",
        "FAKE_SPOT_BTCUSDT",
        "FAKE_SPOT_ETHUSDC",
        "FAKE_SPOT_ETHUSDT",
    ]


async def test_active_symbols_spot_ignores_funding_and_unlisted(db, connector):
    """FUNDING stables do not complete the bipartite; unlisted pairs stay out."""
    account = await _account_with_key(db, connector)
    db.add(_ticker("FAKE_SPOT_BTCUSDT", market=MarketType.SPOT, base="BTC", quote="USDT"))
    await _write_holdings(
        db,
        account,
        [
            (WalletType.SPOT, "BTC", "0.1"),
            (WalletType.FUNDING, "USDT", "950"),
        ],
    )

    assert await svc.active_symbols(db, account) == []


async def test_active_symbols_um_from_positions_not_umargin_wallet(db, connector):
    """USD-M streams come from positions, not from holding USDT in UMARGIN."""
    account = await _account_with_key(db, connector)
    db.add_all(
        [
            _ticker(
                "FAKE_PERP_BTCUSDT",
                market=MarketType.PERP,
                base="BTC",
                quote="USDT",
                settle="USDT",
            ),
            _ticker(
                "FAKE_PERP_ETHUSDT",
                market=MarketType.PERP,
                base="ETH",
                quote="USDT",
                settle="USDT",
            ),
        ]
    )
    await _write_holdings(
        db,
        account,
        [(WalletType.UMARGIN, "USDT", "5000")],
        positions=["FAKE_PERP_ETHUSDT"],
    )

    assert await svc.active_symbols(db, account) == ["FAKE_PERP_ETHUSDT"]


async def test_active_symbols_cm_from_cmargin_balance(db, connector):
    """Classic CM: coin in the CMARGIN wallet → listed inverse instruments."""
    account = await _account_with_key(db, connector)
    db.add_all(
        [
            _ticker(
                "FAKE_CMARGIN_BTCUSD",
                market=MarketType.CMARGIN,
                base="BTC",
                quote="USD",
                settle="BTC",
            ),
            _ticker(
                "FAKE_CMARGIN_ETHUSD",
                market=MarketType.CMARGIN,
                base="ETH",
                quote="USD",
                settle="ETH",
            ),
            _ticker("FAKE_SPOT_BTCUSDT", market=MarketType.SPOT, base="BTC", quote="USDT"),
        ]
    )
    await _write_holdings(db, account, [(WalletType.CMARGIN, "BTC", "0.2")])

    assert await svc.active_symbols(db, account) == ["FAKE_CMARGIN_BTCUSD"]


async def test_active_symbols_classic_spot_does_not_imply_cm(db, connector):
    """Spot BTC is not CM collateral on a classic account."""
    account = await _account_with_key(db, connector)
    db.add_all(
        [
            _ticker("FAKE_SPOT_BTCUSDT", market=MarketType.SPOT, base="BTC", quote="USDT"),
            _ticker(
                "FAKE_CMARGIN_BTCUSD",
                market=MarketType.CMARGIN,
                base="BTC",
                quote="USD",
                settle="BTC",
            ),
        ]
    )
    await _write_holdings(
        db,
        account,
        [(WalletType.SPOT, "BTC", "0.1"), (WalletType.SPOT, "USDT", "10")],
    )

    assert await svc.active_symbols(db, account) == ["FAKE_SPOT_BTCUSDT"]


async def test_active_symbols_unified_applies_spot_and_cm(db, connector):
    """Unified book: spot bipartite plus CM lookup on the same coins."""
    account = await _account_with_key(db, connector)
    db.add_all(
        [
            _ticker("FAKE_SPOT_BTCUSDT", market=MarketType.SPOT, base="BTC", quote="USDT"),
            _ticker("FAKE_SPOT_ETHUSDT", market=MarketType.SPOT, base="ETH", quote="USDT"),
            _ticker("FAKE_SPOT_ETHBTC", market=MarketType.SPOT, base="ETH", quote="BTC"),
            _ticker(
                "FAKE_CMARGIN_BTCUSD",
                market=MarketType.CMARGIN,
                base="BTC",
                quote="USD",
                settle="BTC",
            ),
            _ticker(
                "FAKE_PERP_BTCUSDT",
                market=MarketType.PERP,
                base="BTC",
                quote="USDT",
                settle="USDT",
            ),
        ]
    )
    await _write_holdings(
        db,
        account,
        [
            (WalletType.UNIFIED, "BTC", "0.4"),
            (WalletType.UNIFIED, "ETH", "1"),
            (WalletType.UNIFIED, "USDT", "200"),
        ],
        positions=["FAKE_PERP_BTCUSDT"],
    )

    assert await svc.active_symbols(db, account) == [
        "FAKE_CMARGIN_BTCUSD",
        "FAKE_PERP_BTCUSDT",
        "FAKE_SPOT_BTCUSDT",
        "FAKE_SPOT_ETHUSDT",
    ]


async def test_active_symbols_skips_zero_balances(db, connector):
    account = await _account_with_key(db, connector)
    db.add(_ticker("FAKE_SPOT_BTCUSDT", market=MarketType.SPOT, base="BTC", quote="USDT"))
    await _write_holdings(
        db,
        account,
        [
            (WalletType.SPOT, "BTC", "0"),
            (WalletType.SPOT, "USDT", "100"),
        ],
    )

    assert await svc.active_symbols(db, account) == []


async def test_active_symbols_keeps_traded_cross_pair(db, connector):
    """ETHBTC is excluded from the bipartite unless already traded."""
    account = await _account_with_key(db, connector)
    db.add_all(
        [
            _ticker("FAKE_SPOT_BTCUSDT", market=MarketType.SPOT, base="BTC", quote="USDT"),
            _ticker("FAKE_SPOT_ETHBTC", market=MarketType.SPOT, base="ETH", quote="BTC"),
        ]
    )
    await _write_holdings(
        db,
        account,
        [
            (WalletType.SPOT, "BTC", "1"),
            (WalletType.SPOT, "ETH", "1"),
            (WalletType.SPOT, "USDT", "10"),
        ],
    )
    await _fill_orders(connector, 1, symbol="FAKE_SPOT_ETHBTC")
    await svc.sync_stream(db, connector, account, "orders", "account")

    assert await svc.active_symbols(db, account) == [
        "FAKE_SPOT_BTCUSDT",
        "FAKE_SPOT_ETHBTC",
    ]
