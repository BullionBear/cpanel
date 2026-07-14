"""Portfolio views over asset snapshots.

Reads are served from the local snapshot history (never a live venue call):
the latest batch per account is "current holdings", older batches are history.
The one write here is an on-demand snapshot, which is audited like any other
state-changing action and captures a fresh batch through the connector.
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


# -- helpers ------------------------------------------------------------------

async def _get_account(db: AsyncSession, account_id: int) -> Account:
    account = await db.get(Account, account_id)
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


@router.get("/{account_id}", response_model=HoldingsOut)
async def account_holdings(account_id: int, db: AsyncSession = Depends(get_db)):
    """Current holdings (balances + positions) from the latest batch."""
    account = await _get_account(db, account_id)
    balances, positions, taken_at = await svc.get_holdings(db, account_id)
    total = sum((b.value_quote for b in balances if b.value_quote is not None), Decimal("0"))
    return HoldingsOut(
        account_id=account.id,
        exchange=account.exchange,
        label=account.label,
        taken_at=taken_at,
        quote_asset=svc.QUOTE_ASSET,
        total_value=total,
        balances=[BalanceOut.model_validate(b) for b in balances],
        positions=[PositionOut.model_validate(p) for p in positions],
    )


@router.get("/{account_id}/history", response_model=list[HistoryPoint])
async def account_history(
    account_id: int,
    limit: int = Query(default=90, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Total portfolio value over time, one point per capture batch."""
    await _get_account(db, account_id)
    points = await svc.balance_history(db, account_id, limit=limit)
    return [HistoryPoint(taken_at=taken_at, total_value=total) for _, taken_at, total in points]


@router.post("/{account_id}/snapshot", response_model=HoldingsOut, status_code=201)
async def trigger_snapshot(
    account_id: int,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Capture a fresh snapshot for one account on demand."""
    account = await _get_account(db, account_id)
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
            written = await svc.capture_snapshots(db, connector, account)
        except svc.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        entry.params = {"rows": written}
    balances, positions, taken_at = await svc.get_holdings(db, account_id)
    total = sum((b.value_quote for b in balances if b.value_quote is not None), Decimal("0"))
    return HoldingsOut(
        account_id=account.id,
        exchange=account.exchange,
        label=account.label,
        taken_at=taken_at,
        quote_asset=svc.QUOTE_ASSET,
        total_value=total,
        balances=[BalanceOut.model_validate(b) for b in balances],
        positions=[PositionOut.model_validate(p) for p in positions],
    )
