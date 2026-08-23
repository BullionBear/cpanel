"""Asset snapshot service: capture balances/positions and read them back.

A capture is append-only (see ARCHITECTURE.md "Asset Snapshots"): one pass per
account fetches the current balances and positions through the connector and
inserts a fresh set of rows sharing a ``batch_id`` and ``taken_at``. Nothing is
ever updated in place, so history accumulates and the latest batch is the
current holdings.

Every pass also writes one ``snapshot_batch`` row, including a pass that finds
nothing to record. An account drained to zero produces no balance or position
rows at all, so without that row the "latest batch" would still be its last
funded one and the account would appear frozen at a holding it no longer has.

Valuation into a common quote asset (USDT) uses the reference prices the
symbol module keeps on ``universal_ticker`` — no extra venue calls, and prices
are already refreshed on a short cadence so they are close to snapshot time.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import Capability, ExchangeConnector
from app.connectors.types import Balance, Position
from app.models.account import Account
from app.models.snapshot import BalanceSnapshot, PositionSnapshot, SnapshotBatch
from app.models.universal_ticker import UniversalTicker
from app.services.accounts import AccountError, decrypt_credential, get_active_credential

__all__ = [
    "AccountError",
    "QUOTE_ASSET",
    "capture_snapshots",
    "latest_batch",
    "get_holdings",
    "holdings_total",
    "account_value",
    "value_history",
    "pnl_history",
]

# Common quote asset for portfolio valuation.
QUOTE_ASSET = "USDT"

# Assets treated as 1:1 with the quote asset when no direct market exists.
_STABLE_ASSETS = {"USDT", "USDC", "USD", "BUSD", "FDUSD", "DAI", "TUSD"}


async def _price_map(db: AsyncSession, exchange: str) -> dict[str, Decimal]:
    """base asset -> last spot price in the quote asset, for one exchange."""
    result = await db.execute(
        select(UniversalTicker.base_asset, UniversalTicker.last_price).where(
            UniversalTicker.exchange == exchange,
            UniversalTicker.market_type == "SPOT",
            UniversalTicker.quote_asset == QUOTE_ASSET,
            UniversalTicker.last_price.is_not(None),
        )
    )
    return {base: price for base, price in result if price is not None}


def _value_in_quote(asset: str, amount: Decimal, prices: dict[str, Decimal]) -> Decimal | None:
    if amount == 0:
        return Decimal("0")
    if asset == QUOTE_ASSET or asset in _STABLE_ASSETS:
        return amount
    price = prices.get(asset)
    return amount * price if price is not None else None


async def capture_snapshots(
    db: AsyncSession,
    connector: ExchangeConnector,
    account: Account,
    *,
    balances: list[Balance] | None = None,
    positions: list[Position] | None = None,
) -> int:
    """Capture one balance+position batch for an account. Returns rows written.

    Balances and positions are gated on the connector's capabilities so a venue
    that only exposes one still snapshots what it can.

    Optional ``balances`` / ``positions`` skip the venue fetch (used when the
    caller already fetched, e.g. post-transfer refresh with a lag retry).
    """
    if balances is None or positions is None:
        credential = await get_active_credential(db, account)
        decrypted = decrypt_credential(credential)
        if balances is None:
            balances = (
                await connector.fetch_balances(decrypted)
                if connector.supports(Capability.BALANCES)
                else []
            )
        if positions is None:
            positions = (
                await connector.fetch_positions(decrypted)
                if connector.supports(Capability.POSITIONS)
                else []
            )
    else:
        # Prefetched path still needs a credential check so accounts without
        # keys fail the same way as a live capture.
        await get_active_credential(db, account)

    prices = await _price_map(db, account.exchange)
    batch_id = uuid.uuid4().hex
    taken_at = datetime.now(timezone.utc)

    # Recorded unconditionally: an empty account still captured successfully.
    db.add(
        SnapshotBatch(
            batch_id=batch_id,
            account_id=account.id,
            exchange=account.exchange,
            taken_at=taken_at,
        )
    )

    written = 0
    for balance in balances:
        value = _value_in_quote(balance.asset, balance.total, prices)
        db.add(
            BalanceSnapshot(
                batch_id=batch_id,
                account_id=account.id,
                exchange=account.exchange,
                wallet=balance.wallet.value,
                asset=balance.asset,
                free=balance.free,
                locked=balance.locked,
                total=balance.total,
                value_quote=value,
                quote_asset=QUOTE_ASSET if value is not None else None,
                taken_at=taken_at,
            )
        )
        written += 1

    for position in positions:
        value = position.size * position.mark_price if position.mark_price else None
        db.add(
            PositionSnapshot(
                batch_id=batch_id,
                account_id=account.id,
                exchange=account.exchange,
                universal_symbol=position.universal_symbol,
                side=position.side,
                size=position.size,
                entry_price=position.entry_price,
                mark_price=position.mark_price,
                unrealized_pnl=position.unrealized_pnl,
                margin=position.margin,
                value_quote=value,
                quote_asset=QUOTE_ASSET if value is not None else None,
                taken_at=taken_at,
            )
        )
        written += 1

    await db.commit()
    return written


# -- read paths ---------------------------------------------------------------

# Asset value is account equity: spot balance value (holdings valued in the
# quote asset) plus the unrealized PnL of open positions. Position *notional*
# is exposure, not value, so it is deliberately not summed here.


def holdings_total(balances, positions) -> Decimal:
    """Total asset value = spot balance value + position unrealized PnL."""
    total = Decimal("0")
    for balance in balances:
        if balance.value_quote is not None:
            total += balance.value_quote
    for position in positions:
        if position.unrealized_pnl is not None:
            total += position.unrealized_pnl
    return total


async def latest_batch(db: AsyncSession, account_id: int) -> tuple[str, datetime] | None:
    """(batch_id, taken_at) of the most recent capture for an account, or None.

    Read from ``snapshot_batch`` rather than from the snapshot rows, so a
    capture that found an empty account still advances the account's view.
    ``None`` means the account has never been captured — distinct from a
    capture that found nothing.
    """
    batches = await _recent_batches(db, account_id, limit=1)
    return batches[0] if batches else None


async def _recent_batches(
    db: AsyncSession, account_id: int, limit: int
) -> list[tuple[str, datetime]]:
    """The account's most recent capture batches, newest first."""
    rows = (
        await db.execute(
            select(SnapshotBatch.batch_id, SnapshotBatch.taken_at)
            .where(SnapshotBatch.account_id == account_id)
            .order_by(SnapshotBatch.taken_at.desc(), SnapshotBatch.batch_id.desc())
            .limit(limit)
        )
    ).all()
    return [(row.batch_id, row.taken_at) for row in rows]


async def _sum_by_batch(
    db: AsyncSession, model, column, batch_ids: list[str]
) -> dict[str, Decimal]:
    """Sum ``model.column`` per batch, in Python.

    ``PreciseDecimal`` is TEXT on SQLite, where a SQL ``sum()`` would go
    through float, so the addition happens here on exact Decimals instead.
    """
    if not batch_ids:
        return {}
    rows = await db.execute(
        select(model.batch_id, column).where(model.batch_id.in_(batch_ids))
    )
    totals: dict[str, Decimal] = {}
    for batch_id, value in rows:
        totals[batch_id] = totals.get(batch_id, Decimal("0")) + (value or Decimal("0"))
    return totals


async def get_holdings(
    db: AsyncSession, account_id: int
) -> tuple[list[BalanceSnapshot], list[PositionSnapshot], datetime | None]:
    """Balances and positions of the latest batch for an account."""
    batch = await latest_batch(db, account_id)
    if batch is None:
        return [], [], None
    batch_id, taken_at = batch
    balances = (
        await db.execute(
            select(BalanceSnapshot)
            .where(BalanceSnapshot.batch_id == batch_id)
            .order_by(BalanceSnapshot.value_quote.desc().nullslast(), BalanceSnapshot.asset)
        )
    ).scalars().all()
    positions = (
        await db.execute(
            select(PositionSnapshot)
            .where(PositionSnapshot.batch_id == batch_id)
            .order_by(PositionSnapshot.universal_symbol)
        )
    ).scalars().all()
    return list(balances), list(positions), taken_at


async def account_value(
    db: AsyncSession, account_id: int
) -> tuple[datetime | None, Decimal]:
    """(taken_at, total asset value) of the latest batch.

    Asset value is spot balance value plus position unrealized PnL; unpriced
    balances and PnL-less positions contribute nothing.
    """
    batch = await latest_batch(db, account_id)
    if batch is None:
        return None, Decimal("0")
    batch_id, taken_at = batch
    total = Decimal("0")
    balances = await db.execute(
        select(BalanceSnapshot.value_quote).where(BalanceSnapshot.batch_id == batch_id)
    )
    for value in balances.scalars():
        if value is not None:
            total += value
    positions = await db.execute(
        select(PositionSnapshot.unrealized_pnl).where(PositionSnapshot.batch_id == batch_id)
    )
    for pnl in positions.scalars():
        if pnl is not None:
            total += pnl
    return taken_at, total


async def value_history(
    db: AsyncSession, account_id: int, limit: int = 90
) -> list[tuple[str, datetime, Decimal]]:
    """Per-batch total asset value in the quote asset, most recent first.

    One point per capture batch: that batch's spot balance value plus its
    positions' unrealized PnL. Unpriced rows contribute nothing, and a batch
    that captured an empty account is a genuine zero rather than a gap.
    """
    batches = await _recent_batches(db, account_id, limit)
    batch_ids = [batch_id for batch_id, _ in batches]
    balances = await _sum_by_batch(
        db, BalanceSnapshot, BalanceSnapshot.value_quote, batch_ids
    )
    positions = await _sum_by_batch(
        db, PositionSnapshot, PositionSnapshot.unrealized_pnl, batch_ids
    )
    return [
        (
            batch_id,
            taken_at,
            balances.get(batch_id, Decimal("0")) + positions.get(batch_id, Decimal("0")),
        )
        for batch_id, taken_at in batches
    ]


async def pnl_history(
    db: AsyncSession, account_id: int, limit: int = 90
) -> list[tuple[str, datetime, Decimal]]:
    """Per-batch total position unrealized PnL, most recent first.

    One point per capture batch: the summed ``unrealized_pnl`` of that batch.
    PnL can be negative, and a batch with no open positions is zero — closing
    a book flattens the line rather than ending it.
    """
    batches = await _recent_batches(db, account_id, limit)
    batch_ids = [batch_id for batch_id, _ in batches]
    positions = await _sum_by_batch(
        db, PositionSnapshot, PositionSnapshot.unrealized_pnl, batch_ids
    )
    return [
        (batch_id, taken_at, positions.get(batch_id, Decimal("0")))
        for batch_id, taken_at in batches
    ]
