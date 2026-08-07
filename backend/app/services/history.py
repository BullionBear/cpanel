"""History ingestion: the generic sync worker over connector-owned streams.

The worker knows nothing about any venue's pagination. Per stream it runs the
loop from ARCHITECTURE.md "History Ingestion": load cursor → fetch a page
through the connector → upsert → advance the cursor **only after** the rows
are stored. A crash between fetch and store re-fetches the same page next
run, and upsert-by-native-id makes that harmless.

Backfill for a freshly connected account is deliberately simple: a stream
with no ``sync_cursor`` row yet is seeded ``NEW_STREAM_BACKFILL`` (3 days)
into the past, so the first sync ingests that window before incremental sync
takes over. There is no separate backward-walking stream — recent history is
what reconciliation needs, and anything older can be re-seeded by hand.

Stream sets are connector strategy: Bybit returns its fixed account-wide
streams for trades/orders; Binance returns one stream per *active* symbol,
where activity is derived from panel orders, previously ingested order
history, and snapshotted positions (querying every listed instrument would
waste the rate budget on thousands of empty calls). Deposit and withdrawal
history is always account-wide (``fund_history_stream_keys``). Funding-fee
history is per derivatives surface (``funding_history_stream_keys``).
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import (
    DEFAULT_HISTORY_BACKFILL,
    Capability,
    ExchangeConnector,
)
from app.connectors.types import (
    Deposit as ConnDeposit,
    FundingFee as ConnFundingFee,
    Order as ConnOrder,
    OrderStatus,
    Trade as ConnTrade,
    Withdrawal as ConnWithdrawal,
)
from app.models.account import Account
from app.models.history import (
    DepositHistory,
    FundingFeeHistory,
    OrderHistory,
    SyncCursor,
    TradeHistory,
    WithdrawalHistory,
)
from app.models.order import Order
from app.models.snapshot import PositionSnapshot
from app.services.accounts import AccountError, decrypt_credential, get_active_credential

logger = logging.getLogger(__name__)

__all__ = [
    "AccountError",
    "NEW_STREAM_BACKFILL",
    "DATA_KINDS",
    "active_symbols",
    "sync_stream",
    "sync_account_history",
    "list_trades",
    "list_order_history",
    "list_deposits",
    "list_withdrawals",
    "list_funding_fees",
    "list_sync_cursors",
]

# New accounts (streams without a cursor yet) backfill this far first.
NEW_STREAM_BACKFILL = DEFAULT_HISTORY_BACKFILL

DATA_KINDS = ("trades", "orders", "deposits", "withdrawals", "funding")

# Pages ingested per stream per run. Bounds one scheduler tick; the next run
# resumes from the stored cursor, so a large backlog just takes several runs.
MAX_PAGES_PER_RUN = 20


# -- stream discovery ----------------------------------------------------------

async def active_symbols(db: AsyncSession, account: Account) -> list[str]:
    """Symbols this account has shown activity on (for per-symbol venues).

    Union of panel orders, already-ingested order history, and the latest
    position snapshot. Order history feeding back into the set means a symbol
    discovered once keeps syncing even after its panel orders age out.
    """
    symbols: set[str] = set()
    for column, condition in (
        (Order.universal_symbol, Order.account_id == account.id),
        (OrderHistory.universal_symbol, OrderHistory.account_id == account.id),
        (PositionSnapshot.universal_symbol, PositionSnapshot.account_id == account.id),
    ):
        result = await db.execute(select(column).where(condition).distinct())
        symbols.update(result.scalars())
    return sorted(symbols)


# -- cursor bookkeeping ---------------------------------------------------------

async def _get_or_create_cursor(
    db: AsyncSession,
    connector: ExchangeConnector,
    account: Account,
    data_kind: str,
    stream_key: str,
) -> SyncCursor:
    row = (
        await db.execute(
            select(SyncCursor).where(
                SyncCursor.account_id == account.id,
                SyncCursor.data_kind == data_kind,
                SyncCursor.stream_key == stream_key,
            )
        )
    ).scalars().first()
    if row is None:
        # New stream: seed the cursor NEW_STREAM_BACKFILL into the past so the
        # first sync backfills recent history. The connector builds the value
        # because cursor encoding is venue-owned.
        start = datetime.now(timezone.utc) - NEW_STREAM_BACKFILL
        row = SyncCursor(
            account_id=account.id,
            exchange=account.exchange,
            data_kind=data_kind,
            stream_key=stream_key,
            cursor=connector.initial_history_cursor(start),
        )
        db.add(row)
        await db.flush()
    return row


# -- upserts --------------------------------------------------------------------

async def _upsert_trades(
    db: AsyncSession, account: Account, trades: list[ConnTrade]
) -> int:
    """Insert trades not seen before. Trades are immutable, so no updates."""
    if not trades:
        return 0
    existing: set[tuple[str, str]] = set(
        (
            await db.execute(
                select(TradeHistory.universal_symbol, TradeHistory.native_trade_id).where(
                    TradeHistory.account_id == account.id,
                    TradeHistory.native_trade_id.in_({t.native_trade_id for t in trades}),
                )
            )
        ).all()
    )
    written = 0
    for trade in trades:
        key = (trade.universal_symbol, trade.native_trade_id)
        if key in existing:
            continue
        existing.add(key)
        db.add(
            TradeHistory(
                account_id=account.id,
                exchange=account.exchange,
                universal_symbol=trade.universal_symbol,
                native_trade_id=trade.native_trade_id,
                native_order_id=trade.native_order_id,
                side=trade.side.value,
                price=trade.price,
                qty=trade.qty,
                fee=trade.fee,
                fee_asset=trade.fee_asset,
                traded_at=trade.traded_at,
            )
        )
        written += 1
    return written


def _is_pure_cancel(order: ConnOrder) -> bool:
    """True when the order was cancelled with no fills — not worth storing."""
    return order.status is OrderStatus.CANCELLED and order.filled_qty == 0


async def _upsert_orders(
    db: AsyncSession, account: Account, orders: list[ConnOrder]
) -> int:
    """Insert unseen orders; advance the state of known ones. Returns changes.

    Pure cancels (``CANCELLED`` with zero ``filled_qty``) are skipped. If a
    previously stored open order later cancels without filling, its row is
    removed. Partially filled then cancelled orders are kept.
    """
    if not orders:
        return 0
    rows = (
        await db.execute(
            select(OrderHistory).where(
                OrderHistory.account_id == account.id,
                OrderHistory.native_order_id.in_({o.native_order_id for o in orders}),
            )
        )
    ).scalars()
    known = {(r.universal_symbol, r.native_order_id): r for r in rows}
    changed = 0
    for order in orders:
        key = (order.universal_symbol, order.native_order_id)
        row = known.get(key)
        if _is_pure_cancel(order):
            if row is not None:
                await db.delete(row)
                del known[key]
                changed += 1
            continue
        if row is None:
            row = OrderHistory(
                account_id=account.id,
                exchange=account.exchange,
                universal_symbol=order.universal_symbol,
                native_order_id=order.native_order_id,
                client_order_id=order.client_order_id,
                side=order.side.value,
                type=order.type.value,
                status=order.status.value,
                qty=order.qty,
                price=order.price,
                filled_qty=order.filled_qty,
                placed_at=order.created_at,
                updated_at=order.updated_at,
            )
            db.add(row)
            known[key] = row
            changed += 1
        elif row.status != order.status.value or row.filled_qty != order.filled_qty:
            row.status = order.status.value
            row.filled_qty = order.filled_qty
            row.updated_at = order.updated_at or datetime.now(timezone.utc)
            changed += 1
    return changed


async def _upsert_deposits(
    db: AsyncSession, account: Account, deposits: list[ConnDeposit]
) -> int:
    """Insert unseen deposits; advance status/tx of known ones."""
    if not deposits:
        return 0
    # Skip venue rows with empty native ids — they cannot be upsert-keyed.
    deposits = [d for d in deposits if d.native_id]
    if not deposits:
        return 0
    rows = (
        await db.execute(
            select(DepositHistory).where(
                DepositHistory.account_id == account.id,
                DepositHistory.native_deposit_id.in_({d.native_id for d in deposits}),
            )
        )
    ).scalars()
    known = {r.native_deposit_id: r for r in rows}
    changed = 0
    for deposit in deposits:
        row = known.get(deposit.native_id)
        if row is None:
            row = DepositHistory(
                account_id=account.id,
                exchange=account.exchange,
                native_deposit_id=deposit.native_id,
                asset=deposit.asset,
                amount=deposit.amount,
                status=deposit.status.value,
                network=deposit.network,
                tx_id=deposit.tx_id,
                credited_at=deposit.credited_at,
                created_at=deposit.created_at or deposit.credited_at,
            )
            db.add(row)
            known[deposit.native_id] = row
            changed += 1
        elif row.status != deposit.status.value or row.tx_id != deposit.tx_id:
            # Status/tx drive updates; do not compare datetimes (SQLite round-trips
            # can shift tz/µs and would make every re-sync look like a change).
            row.status = deposit.status.value
            row.tx_id = deposit.tx_id
            row.credited_at = deposit.credited_at
            if deposit.created_at and row.created_at is None:
                row.created_at = deposit.created_at
            changed += 1
    return changed


async def _upsert_withdrawals(
    db: AsyncSession, account: Account, withdrawals: list[ConnWithdrawal]
) -> int:
    """Insert unseen withdrawals; advance status/tx/fee of known ones."""
    if not withdrawals:
        return 0
    withdrawals = [w for w in withdrawals if w.native_id]
    if not withdrawals:
        return 0
    rows = (
        await db.execute(
            select(WithdrawalHistory).where(
                WithdrawalHistory.account_id == account.id,
                WithdrawalHistory.native_withdrawal_id.in_(
                    {w.native_id for w in withdrawals}
                ),
            )
        )
    ).scalars()
    known = {r.native_withdrawal_id: r for r in rows}
    changed = 0
    for withdrawal in withdrawals:
        row = known.get(withdrawal.native_id)
        if row is None:
            row = WithdrawalHistory(
                account_id=account.id,
                exchange=account.exchange,
                native_withdrawal_id=withdrawal.native_id,
                asset=withdrawal.asset,
                amount=withdrawal.amount,
                status=withdrawal.status.value,
                address=withdrawal.address,
                network=withdrawal.network,
                tx_id=withdrawal.tx_id,
                fee=withdrawal.fee,
                fee_asset=withdrawal.asset if withdrawal.fee is not None else None,
                created_at=withdrawal.created_at,
            )
            db.add(row)
            known[withdrawal.native_id] = row
            changed += 1
        elif (
            row.status != withdrawal.status.value
            or row.tx_id != withdrawal.tx_id
            or row.fee != withdrawal.fee
        ):
            row.status = withdrawal.status.value
            row.tx_id = withdrawal.tx_id
            row.fee = withdrawal.fee
            if withdrawal.fee is not None:
                row.fee_asset = withdrawal.asset
            if withdrawal.created_at and row.created_at is None:
                row.created_at = withdrawal.created_at
            changed += 1
    return changed


async def _upsert_funding_fees(
    db: AsyncSession, account: Account, fees: list[ConnFundingFee]
) -> int:
    """Insert funding settlements not seen before. Settlements are immutable."""
    if not fees:
        return 0
    fees = [f for f in fees if f.native_id]
    if not fees:
        return 0
    existing: set[str] = set(
        (
            await db.execute(
                select(FundingFeeHistory.native_funding_id).where(
                    FundingFeeHistory.account_id == account.id,
                    FundingFeeHistory.native_funding_id.in_(
                        {f.native_id for f in fees}
                    ),
                )
            )
        ).scalars()
    )
    written = 0
    for fee in fees:
        if fee.native_id in existing:
            continue
        existing.add(fee.native_id)
        db.add(
            FundingFeeHistory(
                account_id=account.id,
                exchange=account.exchange,
                universal_symbol=fee.universal_symbol,
                native_funding_id=fee.native_id,
                asset=fee.asset,
                amount=fee.amount,
                funding_rate=fee.funding_rate,
                settled_at=fee.settled_at,
            )
        )
        written += 1
    return written


_FETCHERS = {
    "trades": "fetch_trades",
    "orders": "fetch_orders_history",
    "deposits": "fetch_deposits_history",
    "withdrawals": "fetch_withdrawals_history",
    "funding": "fetch_funding_fees_history",
}

_UPSERTERS = {
    "trades": _upsert_trades,
    "orders": _upsert_orders,
    "deposits": _upsert_deposits,
    "withdrawals": _upsert_withdrawals,
    "funding": _upsert_funding_fees,
}


# -- the generic sync worker ----------------------------------------------------

async def sync_stream(
    db: AsyncSession,
    connector: ExchangeConnector,
    account: Account,
    data_kind: str,
    stream_key: str,
) -> int:
    """Ingest one stream up to MAX_PAGES_PER_RUN pages. Returns rows changed.

    Each page is committed together with its cursor advance; on failure the
    error is recorded on the cursor row and re-raised for the caller to log.
    """
    if data_kind not in DATA_KINDS:
        raise ValueError(f"Unknown history data kind {data_kind!r}")
    cursor_row = await _get_or_create_cursor(db, connector, account, data_kind, stream_key)
    credential = decrypt_credential(await get_active_credential(db, account))
    fetch = getattr(connector, _FETCHERS[data_kind])
    upsert = _UPSERTERS[data_kind]

    total = 0
    try:
        for _ in range(MAX_PAGES_PER_RUN):
            page = await fetch(credential, stream_key, cursor_row.cursor)
            total += await upsert(db, account, page.items)
            if page.next_cursor is not None:
                cursor_row.cursor = page.next_cursor
            cursor_row.last_synced_at = datetime.now(timezone.utc)
            cursor_row.last_error = None
            # Rows and cursor advance land atomically: a crash re-fetches the
            # same page next run instead of skipping it.
            await db.commit()
            if page.next_cursor is None:
                break
    except Exception as exc:  # noqa: BLE001 - record on the stream, then surface
        cursor_row.last_error = str(exc)[:2000]
        cursor_row.last_synced_at = datetime.now(timezone.utc)
        await db.commit()
        raise
    if data_kind in ("deposits", "withdrawals") and total > 0:
        from app.services.balance_refresh import schedule_balance_refresh

        schedule_balance_refresh(account.id, f"history_{data_kind}")
    return total


async def sync_account_history(
    db: AsyncSession, connector: ExchangeConnector, account: Account
) -> int:
    """Sync every history stream of one account. Returns rows changed.

    Streams are isolated: one failing stream (recorded on its cursor row) does
    not stop the others.
    """
    trade_order_kinds = [
        kind
        for kind, capability in (
            ("trades", Capability.TRADE_HISTORY),
            ("orders", Capability.ORDER_HISTORY),
        )
        if connector.supports(capability)
    ]
    fund_kinds = [
        kind
        for kind, capability in (
            ("deposits", Capability.DEPOSITS),
            ("withdrawals", Capability.WITHDRAWALS),
        )
        if connector.supports(capability)
    ]
    funding_kinds = (
        ["funding"] if connector.supports(Capability.FUNDING_HISTORY) else []
    )
    if not trade_order_kinds and not fund_kinds and not funding_kinds:
        return 0

    total = 0
    if trade_order_kinds:
        streams = connector.history_stream_keys(await active_symbols(db, account))
        for data_kind in trade_order_kinds:
            for stream_key in streams:
                try:
                    total += await sync_stream(
                        db, connector, account, data_kind, stream_key
                    )
                except AccountError:
                    raise  # no usable credential — pointless to try other streams
                except Exception:
                    logger.exception(
                        "History sync failed for %s account %s stream %s/%s",
                        account.exchange, account.id, data_kind, stream_key,
                    )
    if fund_kinds:
        for data_kind in fund_kinds:
            for stream_key in connector.fund_history_stream_keys():
                try:
                    total += await sync_stream(
                        db, connector, account, data_kind, stream_key
                    )
                except AccountError:
                    raise
                except Exception:
                    logger.exception(
                        "History sync failed for %s account %s stream %s/%s",
                        account.exchange, account.id, data_kind, stream_key,
                    )
    if funding_kinds:
        for data_kind in funding_kinds:
            for stream_key in connector.funding_history_stream_keys():
                try:
                    total += await sync_stream(
                        db, connector, account, data_kind, stream_key
                    )
                except AccountError:
                    raise
                except Exception:
                    logger.exception(
                        "History sync failed for %s account %s stream %s/%s",
                        account.exchange, account.id, data_kind, stream_key,
                    )
    return total


# -- read paths -------------------------------------------------------------------

def _apply_filters(
    query,
    model,
    *,
    exchange: str | None,
    account_id: int | None,
    universal_symbol: str | None = None,
    asset: str | None = None,
    start: datetime | None,
    end: datetime | None,
    time_column,
):
    if exchange:
        query = query.where(model.exchange == exchange.upper())
    if account_id is not None:
        query = query.where(model.account_id == account_id)
    if universal_symbol:
        query = query.where(model.universal_symbol == universal_symbol)
    if asset:
        query = query.where(model.asset == asset.upper())
    if start is not None:
        query = query.where(time_column >= start)
    if end is not None:
        query = query.where(time_column <= end)
    return query


async def list_trades(
    db: AsyncSession,
    *,
    exchange: str | None = None,
    account_id: int | None = None,
    universal_symbol: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[int, list[TradeHistory]]:
    filters = dict(
        exchange=exchange, account_id=account_id, universal_symbol=universal_symbol,
        start=start, end=end, time_column=TradeHistory.traded_at,
    )
    total = (
        await db.execute(
            _apply_filters(select(func.count(TradeHistory.id)), TradeHistory, **filters)
        )
    ).scalar_one()
    rows = (
        await db.execute(
            _apply_filters(select(TradeHistory), TradeHistory, **filters)
            .order_by(TradeHistory.traded_at.desc().nullslast(), TradeHistory.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return total, list(rows)


async def list_order_history(
    db: AsyncSession,
    *,
    exchange: str | None = None,
    account_id: int | None = None,
    universal_symbol: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[int, list[OrderHistory]]:
    filters = dict(
        exchange=exchange, account_id=account_id, universal_symbol=universal_symbol,
        start=start, end=end, time_column=OrderHistory.placed_at,
    )
    total = (
        await db.execute(
            _apply_filters(select(func.count(OrderHistory.id)), OrderHistory, **filters)
        )
    ).scalar_one()
    rows = (
        await db.execute(
            _apply_filters(select(OrderHistory), OrderHistory, **filters)
            .order_by(OrderHistory.placed_at.desc().nullslast(), OrderHistory.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return total, list(rows)


async def list_deposits(
    db: AsyncSession,
    *,
    exchange: str | None = None,
    account_id: int | None = None,
    asset: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[int, list[DepositHistory]]:
    filters = dict(
        exchange=exchange, account_id=account_id, asset=asset,
        start=start, end=end, time_column=DepositHistory.created_at,
    )
    total = (
        await db.execute(
            _apply_filters(select(func.count(DepositHistory.id)), DepositHistory, **filters)
        )
    ).scalar_one()
    rows = (
        await db.execute(
            _apply_filters(select(DepositHistory), DepositHistory, **filters)
            .order_by(
                DepositHistory.created_at.desc().nullslast(), DepositHistory.id.desc()
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return total, list(rows)


async def list_withdrawals(
    db: AsyncSession,
    *,
    exchange: str | None = None,
    account_id: int | None = None,
    asset: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[int, list[WithdrawalHistory]]:
    filters = dict(
        exchange=exchange, account_id=account_id, asset=asset,
        start=start, end=end, time_column=WithdrawalHistory.created_at,
    )
    total = (
        await db.execute(
            _apply_filters(
                select(func.count(WithdrawalHistory.id)), WithdrawalHistory, **filters
            )
        )
    ).scalar_one()
    rows = (
        await db.execute(
            _apply_filters(select(WithdrawalHistory), WithdrawalHistory, **filters)
            .order_by(
                WithdrawalHistory.created_at.desc().nullslast(),
                WithdrawalHistory.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return total, list(rows)


async def list_funding_fees(
    db: AsyncSession,
    *,
    exchange: str | None = None,
    account_id: int | None = None,
    universal_symbol: str | None = None,
    asset: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[int, list[FundingFeeHistory]]:
    filters = dict(
        exchange=exchange,
        account_id=account_id,
        universal_symbol=universal_symbol,
        asset=asset,
        start=start,
        end=end,
        time_column=FundingFeeHistory.settled_at,
    )
    total = (
        await db.execute(
            _apply_filters(
                select(func.count(FundingFeeHistory.id)), FundingFeeHistory, **filters
            )
        )
    ).scalar_one()
    rows = (
        await db.execute(
            _apply_filters(select(FundingFeeHistory), FundingFeeHistory, **filters)
            .order_by(
                FundingFeeHistory.settled_at.desc().nullslast(),
                FundingFeeHistory.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return total, list(rows)


async def list_sync_cursors(
    db: AsyncSession,
    *,
    exchange: str | None = None,
    account_id: int | None = None,
) -> list[tuple[SyncCursor, Account]]:
    """Cursor rows with their accounts, for the sync-health view."""
    query = (
        select(SyncCursor, Account)
        .join(Account, Account.id == SyncCursor.account_id)
        .order_by(SyncCursor.exchange, Account.label, SyncCursor.data_kind, SyncCursor.stream_key)
    )
    if exchange:
        query = query.where(SyncCursor.exchange == exchange.upper())
    if account_id is not None:
        query = query.where(SyncCursor.account_id == account_id)
    return [(cursor, account) for cursor, account in (await db.execute(query)).all()]
