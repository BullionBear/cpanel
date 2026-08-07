"""Portfolio views over asset snapshots.

Reads are served from the local snapshot history (never a live venue call):
the latest batch per account is "current holdings", older batches are history.

Freshness is driven by background hooks (order sync, history, transfers, the
hourly job) plus ``POST .../refresh``, which enqueues a debounced capture.
``POST .../snapshot`` remains for agents/ops but is deprecated for the UI.
"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import Capability, ExchangeConnector
from app.connectors.registry import connectors
from app.core.audit import Actor, audit, current_actor
from app.core.db import get_db
from app.models.account import Account
from app.services import balance_refresh
from app.services import snapshots as svc

router = APIRouter(
    prefix="/portfolio", tags=["portfolio"], dependencies=[Depends(current_actor)]
)


# -- schemas ------------------------------------------------------------------

class BalanceOut(BaseModel):
    wallet: str
    asset: str
    free: Decimal
    locked: Decimal
    total: Decimal
    value_quote: Decimal | None
    quote_asset: str | None

    model_config = {"from_attributes": True}


class PositionOut(BaseModel):
    universal_symbol: str
    side: str
    size: Decimal
    entry_price: Decimal
    mark_price: Decimal | None
    unrealized_pnl: Decimal | None
    margin: Decimal | None
    value_quote: Decimal | None
    quote_asset: str | None

    model_config = {"from_attributes": True}


class HoldingsOut(BaseModel):
    account_id: int
    exchange: str
    label: str
    taken_at: datetime | None
    quote_asset: str
    total_value: Decimal
    balances: list[BalanceOut]
    positions: list[PositionOut]


class OverviewItem(BaseModel):
    account_id: int
    exchange: str
    label: str
    account_type: str
    parent_id: int | None
    taken_at: datetime | None
    quote_asset: str
    total_value: Decimal


class HistoryPoint(BaseModel):
    taken_at: datetime
    total_value: Decimal


class PnlPoint(BaseModel):
    taken_at: datetime
    pnl: Decimal


# -- helpers ------------------------------------------------------------------

async def _account_by_ref(db: AsyncSession, exchange: str, label: str) -> Account:
    """Resolve an account by its natural key (exchange + nickname)."""
    result = await db.execute(
        select(Account).where(
            Account.exchange == exchange.upper(), Account.label == label
        )
    )
    account = result.scalars().first()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def _get_connector(exchange: str) -> ExchangeConnector:
    try:
        return connectors.get(exchange)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown exchange {exchange!r}")


# -- endpoints ----------------------------------------------------------------

@router.get("", response_model=list[OverviewItem])
async def portfolio_overview(db: AsyncSession = Depends(get_db)):
    """Latest snapshot value per account, across every exchange."""
    accounts = (
        await db.execute(
            select(Account)
            .where(Account.status != "removed")
            .order_by(Account.exchange, Account.label)
        )
    ).scalars().all()
    items: list[OverviewItem] = []
    for account in accounts:
        taken_at, total = await svc.account_value(db, account.id)
        items.append(
            OverviewItem(
                account_id=account.id,
                exchange=account.exchange,
                label=account.label,
                account_type=account.account_type,
                parent_id=account.parent_id,
                taken_at=taken_at,
                quote_asset=svc.QUOTE_ASSET,
                total_value=total,
            )
        )
    return items


@router.get("/{exchange}/{label}", response_model=HoldingsOut)
async def account_holdings(
    exchange: str, label: str, db: AsyncSession = Depends(get_db)
):
    """Current holdings (balances + positions) from the latest batch."""
    account = await _account_by_ref(db, exchange, label)
    balances, positions, taken_at = await svc.get_holdings(db, account.id)
    return HoldingsOut(
        account_id=account.id,
        exchange=account.exchange,
        label=account.label,
        taken_at=taken_at,
        quote_asset=svc.QUOTE_ASSET,
        total_value=svc.holdings_total(balances, positions),
        balances=[BalanceOut.model_validate(b) for b in balances],
        positions=[PositionOut.model_validate(p) for p in positions],
    )


@router.get("/{exchange}/{label}/history", response_model=list[HistoryPoint])
async def account_history(
    exchange: str,
    label: str,
    limit: int = Query(default=90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Total portfolio value over time, one point per capture batch."""
    account = await _account_by_ref(db, exchange, label)
    points = await svc.value_history(db, account.id, limit=limit)
    return [HistoryPoint(taken_at=taken_at, total_value=total) for _, taken_at, total in points]


@router.get("/{exchange}/{label}/pnl-history", response_model=list[PnlPoint])
async def account_pnl_history(
    exchange: str,
    label: str,
    limit: int = Query(default=90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Total position unrealized PnL over time, one point per capture batch."""
    account = await _account_by_ref(db, exchange, label)
    points = await svc.pnl_history(db, account.id, limit=limit)
    return [PnlPoint(taken_at=taken_at, pnl=pnl) for _, taken_at, pnl in points]


class RefreshOut(BaseModel):
    account_id: int
    exchange: str
    label: str
    scheduled: bool = True


@router.post(
    "/{exchange}/{label}/refresh",
    response_model=RefreshOut,
    status_code=202,
)
async def request_refresh(
    exchange: str,
    label: str,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue a debounced balance/position capture (does not await the venue).

    The UI should keep reading ``GET`` holdings from the database and wait for
    a ``balance.updated`` SSE (or poll ``taken_at``) after calling this.
    """
    account = await _account_by_ref(db, exchange, label)
    connector = _get_connector(account.exchange)
    if not (
        connector.supports(Capability.BALANCES)
        or connector.supports(Capability.POSITIONS)
    ):
        raise HTTPException(
            status_code=409,
            detail=f"{connector.exchange} does not support balance/position reads",
        )
    async with audit(
        db, actor, "snapshots.refresh",
        target=f"{account.exchange}:{account.label}",
    ):
        balance_refresh.schedule_balance_refresh(
            account.id, "manual", debounce_seconds=0.5
        )
    return RefreshOut(
        account_id=account.id,
        exchange=account.exchange,
        label=account.label,
        scheduled=True,
    )


@router.post(
    "/{exchange}/{label}/snapshot",
    response_model=HoldingsOut,
    status_code=201,
    deprecated=True,
)
async def trigger_snapshot(
    exchange: str,
    label: str,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Deprecated: synchronous live capture for agents/ops only.

    The panel UI should use ``POST .../refresh`` and read holdings from the DB
    after the ``balance.updated`` SSE notification.
    """
    account = await _account_by_ref(db, exchange, label)
    connector = _get_connector(account.exchange)
    if not (
        connector.supports(Capability.BALANCES)
        or connector.supports(Capability.POSITIONS)
    ):
        raise HTTPException(
            status_code=409,
            detail=f"{connector.exchange} does not support balance/position reads",
        )
    async with audit(
        db, actor, "snapshots.capture",
        target=f"{account.exchange}:{account.label}",
    ) as entry:
        try:
            written = await balance_refresh.capture_and_publish(
                db, connector, account, "manual_sync"
            )
        except svc.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        entry.params = {"rows": written}
    balances, positions, taken_at = await svc.get_holdings(db, account.id)
    return HoldingsOut(
        account_id=account.id,
        exchange=account.exchange,
        label=account.label,
        taken_at=taken_at,
        quote_asset=svc.QUOTE_ASSET,
        total_value=svc.holdings_total(balances, positions),
        balances=[BalanceOut.model_validate(b) for b in balances],
        positions=[PositionOut.model_validate(p) for p in positions],
    )
