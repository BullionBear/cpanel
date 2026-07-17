"""History explorer endpoints.

Reads serve the locally ingested history tables (trades, orders, deposits,
withdrawals) — never a live venue call. Rows are filled by syncing venue
history endpoints. ``/history/streams`` exposes per-stream sync health from
the cursor table; the on-demand sync trigger is audited like every other
state-changing operation.
"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import ExchangeConnector
from app.connectors.registry import connectors
from app.core.audit import Actor, audit, current_actor
from app.core.db import get_db
from app.models.account import Account
from app.services import history as svc
from app.services.accounts import AccountError

router = APIRouter(prefix="/history", tags=["history"], dependencies=[Depends(current_actor)])


# -- schemas ------------------------------------------------------------------

class TradeOut(BaseModel):
    id: int
    account_id: int
    exchange: str
    universal_symbol: str
    native_trade_id: str
    native_order_id: str
    side: str
    price: Decimal
    qty: Decimal
    fee: Decimal
    fee_asset: str
    traded_at: datetime | None
    ingested_at: datetime

    model_config = {"from_attributes": True}


class OrderHistoryOut(BaseModel):
    id: int
    account_id: int
    exchange: str
    universal_symbol: str
    native_order_id: str
    client_order_id: str | None
    side: str
    type: str
    status: str
    qty: Decimal
    price: Decimal | None
    filled_qty: Decimal
    placed_at: datetime | None
    updated_at: datetime | None
    ingested_at: datetime

    model_config = {"from_attributes": True}


class DepositHistoryOut(BaseModel):
    id: int
    account_id: int
    exchange: str
    native_deposit_id: str
    asset: str
    amount: Decimal
    status: str
    network: str | None
    tx_id: str | None
    credited_at: datetime | None
    created_at: datetime | None
    ingested_at: datetime

    model_config = {"from_attributes": True}


class WithdrawalHistoryOut(BaseModel):
    id: int
    account_id: int
    exchange: str
    native_withdrawal_id: str
    asset: str
    amount: Decimal
    status: str
    address: str | None
    network: str | None
    tx_id: str | None
    fee: Decimal | None
    fee_asset: str | None
    created_at: datetime | None
    ingested_at: datetime

    model_config = {"from_attributes": True}


class TradePage(BaseModel):
    total: int
    items: list[TradeOut]


class OrderHistoryPage(BaseModel):
    total: int
    items: list[OrderHistoryOut]


class DepositHistoryPage(BaseModel):
    total: int
    items: list[DepositHistoryOut]


class WithdrawalHistoryPage(BaseModel):
    total: int
    items: list[WithdrawalHistoryOut]


class StreamOut(BaseModel):
    account_id: int
    account_label: str
    exchange: str
    data_kind: str
    stream_key: str
    cursor: str | None
    last_synced_at: datetime | None
    last_error: str | None


# -- helpers ------------------------------------------------------------------

async def _account(db: AsyncSession, account_id: int) -> Account:
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def _connector(exchange: str) -> ExchangeConnector:
    try:
        return connectors.get(exchange)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown exchange {exchange!r}")


# -- endpoints ----------------------------------------------------------------

@router.get("/trades", response_model=TradePage)
async def list_trades(
    exchange: str | None = None,
    account_id: int | None = None,
    universal_symbol: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    total, rows = await svc.list_trades(
        db,
        exchange=exchange,
        account_id=account_id,
        universal_symbol=universal_symbol,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    return TradePage(total=total, items=[TradeOut.model_validate(r) for r in rows])


@router.get("/orders", response_model=OrderHistoryPage)
async def list_order_history(
    exchange: str | None = None,
    account_id: int | None = None,
    universal_symbol: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    total, rows = await svc.list_order_history(
        db,
        exchange=exchange,
        account_id=account_id,
        universal_symbol=universal_symbol,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    return OrderHistoryPage(
        total=total, items=[OrderHistoryOut.model_validate(r) for r in rows]
    )


@router.get("/deposits", response_model=DepositHistoryPage)
async def list_deposits(
    exchange: str | None = None,
    account_id: int | None = None,
    asset: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    total, rows = await svc.list_deposits(
        db,
        exchange=exchange,
        account_id=account_id,
        asset=asset,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    return DepositHistoryPage(
        total=total, items=[DepositHistoryOut.model_validate(r) for r in rows]
    )


@router.get("/withdrawals", response_model=WithdrawalHistoryPage)
async def list_withdrawals(
    exchange: str | None = None,
    account_id: int | None = None,
    asset: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    total, rows = await svc.list_withdrawals(
        db,
        exchange=exchange,
        account_id=account_id,
        asset=asset,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    return WithdrawalHistoryPage(
        total=total, items=[WithdrawalHistoryOut.model_validate(r) for r in rows]
    )


@router.get("/streams", response_model=list[StreamOut])
async def list_streams(
    exchange: str | None = None,
    account_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Per-stream sync health: cursor position, last run, last error."""
    pairs = await svc.list_sync_cursors(db, exchange=exchange, account_id=account_id)
    return [
        StreamOut(
            account_id=cursor.account_id,
            account_label=account.label,
            exchange=cursor.exchange,
            data_kind=cursor.data_kind,
            stream_key=cursor.stream_key,
            cursor=cursor.cursor,
            last_synced_at=cursor.last_synced_at,
            last_error=cursor.last_error,
        )
        for cursor, account in pairs
    ]


@router.post("/sync/{account_id}", response_model=dict)
async def sync_account(
    account_id: int,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Run one account's history sync now (new accounts backfill 3 days)."""
    account = await _account(db, account_id)
    connector = _connector(account.exchange)
    async with audit(
        db, actor, "history.sync",
        target=f"{account.exchange}:{account.label}",
    ) as entry:
        try:
            ingested = await svc.sync_account_history(db, connector, account)
        except AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        entry.params = {"ingested": ingested}
    return {"ingested": ingested}
