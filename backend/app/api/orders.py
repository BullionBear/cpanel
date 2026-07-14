"""Order management endpoints.

Placement and cancellation are state-changing and audited. Reads are served
from the local ``trade_order`` table (kept current by the open-order poller),
never a live venue call.
"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import ExchangeConnector, NotSupported
from app.connectors.registry import connectors
from app.connectors.types import OrderSide, OrderType
from app.core.audit import Actor, audit, current_actor
from app.core.db import get_db
from app.models.account import Account
from app.models.order import Order
from app.services import orders as svc
from app.services.accounts import AccountError

router = APIRouter(prefix="/orders", tags=["orders"], dependencies=[Depends(current_actor)])


# -- schemas ------------------------------------------------------------------

class OrderOut(BaseModel):
    id: int
    account_id: int
    exchange: str
    universal_symbol: str
    client_order_id: str | None
    native_order_id: str | None
    side: str
    type: str
    status: str
    qty: Decimal
    price: Decimal | None
    filled_qty: Decimal
    external: bool
    reject_reason: str | None
    created_at: datetime
    submitted_at: datetime | None
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class OrderLeg(BaseModel):
    universal_symbol: str
    side: str
    type: str
    qty: Decimal
    price: Decimal | None = None


class PlaceOrderIn(OrderLeg):
    account_id: int


class BulkPlaceIn(BaseModel):
    account_id: int
    orders: list[OrderLeg]


class CancelBulkIn(BaseModel):
    order_ids: list[int]


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


def _side(value: str) -> OrderSide:
    try:
        return OrderSide(value.upper())
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid side {value!r}")


def _type(value: str) -> OrderType:
    try:
        return OrderType(value.upper())
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid order type {value!r}")


def _map_order_error(exc: Exception) -> HTTPException:
    if isinstance(exc, svc.OrderValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, svc.GuardrailError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, NotSupported):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


# -- endpoints ----------------------------------------------------------------

@router.get("", response_model=list[OrderOut])
async def list_orders(
    exchange: str | None = None,
    account_id: int | None = None,
    universal_symbol: str | None = None,
    open_only: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """Orders from the local store, newest first, with optional filters."""
    rows = await svc.list_orders(
        db,
        exchange=exchange,
        account_id=account_id,
        universal_symbol=universal_symbol,
        open_only=open_only,
        limit=limit,
    )
    return [OrderOut.model_validate(r) for r in rows]


@router.post("", response_model=OrderOut, status_code=201)
async def place_order(
    body: PlaceOrderIn,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Validate, guardrail, and place a single order."""
    account = await _account(db, body.account_id)
    connector = _connector(account.exchange)
    async with audit(
        db, actor, "orders.place",
        target=f"{account.exchange}:{account.label}",
        params={"symbol": body.universal_symbol, "side": body.side, "type": body.type},
    ):
        try:
            row = await svc.place_order(
                db, connector, account,
                universal_symbol=body.universal_symbol,
                side=_side(body.side),
                otype=_type(body.type),
                qty=body.qty,
                price=body.price,
            )
        except (svc.OrderError, AccountError, NotSupported) as exc:
            raise _map_order_error(exc)
    return OrderOut.model_validate(row)


@router.post("/bulk", response_model=list[dict], status_code=201)
async def place_orders_bulk(
    body: BulkPlaceIn,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Place a batch of orders, returning a per-item outcome."""
    account = await _account(db, body.account_id)
    connector = _connector(account.exchange)
    requests = [leg.model_dump() for leg in body.orders]
    async with audit(
        db, actor, "orders.place_bulk",
        target=f"{account.exchange}:{account.label}",
        params={"count": len(requests)},
    ) as entry:
        outcomes = await svc.place_orders(db, connector, account, requests)
        entry.params = {"count": len(requests), "placed": sum(1 for o in outcomes if o["ok"])}
    return outcomes


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(
    order_id: int,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    order = await db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    account = await _account(db, order.account_id)
    connector = _connector(account.exchange)
    async with audit(
        db, actor, "orders.cancel",
        target=f"{account.exchange}:{account.label}",
        params={"order_id": order_id},
    ):
        try:
            row = await svc.cancel_order(db, connector, account, order)
        except (svc.OrderError, AccountError, NotSupported) as exc:
            raise _map_order_error(exc)
    return OrderOut.model_validate(row)


@router.post("/cancel", response_model=list[dict])
async def cancel_orders_bulk(
    body: CancelBulkIn,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    if not body.order_ids:
        return []
    rows = list(
        (await db.execute(select(Order).where(Order.id.in_(body.order_ids)))).scalars()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No matching orders")
    # A cancel batch is scoped to a single account (one connector/credential).
    account = await _account(db, rows[0].account_id)
    if any(r.account_id != account.id for r in rows):
        raise HTTPException(status_code=400, detail="All orders must belong to one account")
    connector = _connector(account.exchange)
    async with audit(
        db, actor, "orders.cancel_bulk",
        target=f"{account.exchange}:{account.label}",
        params={"count": len(rows)},
    ) as entry:
        outcomes = await svc.cancel_orders(db, connector, account, rows)
        entry.params = {"count": len(rows), "cancelled": sum(1 for o in outcomes if o["ok"])}
    return outcomes


@router.post("/sync/{account_id}", response_model=dict)
async def sync_account_orders(
    account_id: int,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Force an open-order reconciliation for one account."""
    account = await _account(db, account_id)
    connector = _connector(account.exchange)
    async with audit(
        db, actor, "orders.sync",
        target=f"{account.exchange}:{account.label}",
    ) as entry:
        try:
            changed = await svc.sync_open_orders(db, connector, account)
        except (svc.OrderError, AccountError, NotSupported) as exc:
            raise _map_order_error(exc)
        entry.params = {"changed": changed}
    return {"changed": changed}
