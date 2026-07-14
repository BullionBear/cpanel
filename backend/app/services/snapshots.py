"""Asset snapshot service: capture balances/positions and read them back.

A capture is append-only (see ARCHITECTURE.md "Asset Snapshots"): one pass per
account fetches the current balances and positions through the connector and
inserts a fresh set of rows sharing a ``batch_id`` and ``taken_at``. Nothing is
ever updated in place, so history accumulates and the latest batch is the
current holdings.

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
from app.models.snapshot import BalanceSnapshot, PositionSnapshot
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
    db: AsyncSession, connector: ExchangeConnector, account: Account
) -> int:
    """Capture one balance+position batch for an account. Returns rows written.

    Balances and positions are gated on the connector's capabilities so a venue
    that only exposes one still snapshots what it can.
    """
    credential = await get_active_credential(db, account)
    decrypted = decrypt_credential(credential)

    balances: list[Balance] = []
    positions: list[Position] = []
    if connector.supports(Capability.BALANCES):
        balances = await connector.fetch_balances(decrypted)
    if connector.supports(Capability.POSITIONS):
        positions = await connector.fetch_positions(decrypted)

    prices = await _price_map(db, account.exchange)
    batch_id = uuid.uuid4().hex
    taken_at = datetime.now(timezone.utc)

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

# Asset value is spot value + position notional: every snapshot row (balance or
# position) already carries its ``value_quote`` in the quote asset, so a total
# is just the sum of both across a batch.


def holdings_total(balances, positions) -> Decimal:
    """Total asset value = spot balance value + position notional value."""
    total = Decimal("0")
    for row in (*balances, *positions):
        if row.value_quote is not None:
            total += row.value_quote
    return total


async def latest_batch(db: AsyncSession, account_id: int) -> tuple[str, datetime] | None:
    """(batch_id, taken_at) of the most recent capture for an account, or None."""
    latest: tuple[str, datetime] | None = None
    for model in (BalanceSnapshot, PositionSnapshot):
        row = (
            await db.execute(
                select(model.batch_id, model.taken_at)
                .where(model.account_id == account_id)
                .order_by(model.taken_at.desc(), model.id.desc())
                .limit(1)
            )
        ).first()
        if row is not None and (latest is None or row.taken_at > latest[1]):
            latest = (row.batch_id, row.taken_at)
    return latest


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

    Asset value is spot balance value plus position notional value; unpriced
    rows contribute nothing.
    """
    batch = await latest_batch(db, account_id)
    if batch is None:
        return None, Decimal("0")
    batch_id, taken_at = batch
    total = Decimal("0")
    for model in (BalanceSnapshot, PositionSnapshot):
        result = await db.execute(
            select(model.value_quote).where(model.batch_id == batch_id)
        )
        for value in result.scalars():
            if value is not None:
                total += value
    return taken_at, total


async def value_history(
    db: AsyncSession, account_id: int, limit: int = 90
) -> list[tuple[str, datetime, Decimal]]:
    """Per-batch total asset value in the quote asset, most recent first.

    One point per capture batch: the summed ``value_quote`` of that batch's
    balances *and* positions (spot value + position notional). Unpriced rows
    contribute nothing.
    """
    totals: dict[str, tuple[datetime, Decimal]] = {}
    for model in (BalanceSnapshot, PositionSnapshot):
        result = await db.execute(
            select(model.batch_id, model.taken_at, model.value_quote).where(
                model.account_id == account_id
            )
        )
        for batch_id, taken_at, value in result:
            prev = totals.get(batch_id)
            running = (prev[1] if prev else Decimal("0")) + (value or Decimal("0"))
            totals[batch_id] = (taken_at, running)
    points = [
        (batch_id, taken_at, total) for batch_id, (taken_at, total) in totals.items()
    ]
    points.sort(key=lambda p: p[1], reverse=True)
    return points[:limit]
