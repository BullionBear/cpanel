"""Order management: validation, guardrails, idempotent placement, sync.

Placement is deliberately ordered for safety (see ARCHITECTURE.md "Order
Management"):

1. Validate/round the request against the instrument's ``universal_ticker``
   filters and enforce guardrails (symbol allow-list, max notional).
2. Write the ``trade_order`` row as ``SUBMITTING`` with a panel-generated
   ``client_order_id`` — durable *before* the venue is called.
3. Call the connector. On success record the native id and status. If the call
   raises, try to recover the order by its client id (the venue may have
   accepted it before the response was lost) rather than risk a double-place.

State afterwards is advanced by the open-order poller (:func:`sync_open_orders`).
"""
import logging
import uuid
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import Capability, ExchangeConnector
from app.connectors.types import (
    Order as ConnOrder,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
)
from app.core.config import settings
from app.models.account import Account
from app.models.order import Order
from app.models.universal_ticker import UniversalTicker
from app.services.accounts import (
    AccountError,
    decrypt_credential,
    get_active_credential,
)
from app.services.balance_refresh import schedule_balance_refresh

logger = logging.getLogger(__name__)

__all__ = [
    "OrderError",
    "OrderValidationError",
    "GuardrailError",
    "OPEN_STATES",
    "place_order",
    "cancel_order",
    "place_orders",
    "cancel_orders",
    "sync_open_orders",
    "list_orders",
]

# Local lifecycle states considered "still live" (may still change on the venue).
OPEN_STATES = (
    OrderStatus.SUBMITTING.value,
    OrderStatus.OPEN.value,
    OrderStatus.PARTIALLY_FILLED.value,
)


class OrderError(Exception):
    """Base class for order failures surfaced to the caller."""


class OrderValidationError(OrderError):
    """Request violates an instrument filter (tick/step/min qty/min notional)."""


class GuardrailError(OrderError):
    """Request is rejected by a configured guardrail (allow-list / max notional)."""


# -- validation / guardrails --------------------------------------------------

def _quantize(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    if step is None or step == 0:
        return value
    return (value / step).to_integral_value(rounding=rounding) * step


def _allowlist() -> set[str]:
    raw = settings.ORDER_SYMBOL_ALLOWLIST or ""
    return {s.strip() for s in raw.split(",") if s.strip()}


def validate_and_round(
    ticker: UniversalTicker,
    otype: OrderType,
    qty: Decimal,
    price: Decimal | None,
) -> tuple[Decimal, Decimal | None]:
    """Round to the instrument's tick/step and enforce min/max filters."""
    if ticker.status != "active":
        raise OrderValidationError(f"{ticker.universal_symbol} is {ticker.status}, not tradable")

    qty = _quantize(qty, ticker.qty_step, ROUND_DOWN)
    if qty <= 0 or qty < ticker.min_qty:
        raise OrderValidationError(
            f"Quantity below the minimum ({ticker.min_qty}) after rounding to step {ticker.qty_step}"
        )
    if ticker.max_qty is not None and qty > ticker.max_qty:
        raise OrderValidationError(f"Quantity above the maximum ({ticker.max_qty})")

    if otype == OrderType.LIMIT:
        if price is None or price <= 0:
            raise OrderValidationError("A limit order requires a positive price")
        price = _quantize(price, ticker.price_tick, ROUND_HALF_UP)
        if price <= 0:
            raise OrderValidationError("Price rounds to zero at this tick size")
    else:
        price = None

    ref_price = price if price is not None else ticker.last_price
    if ticker.min_notional is not None and ref_price is not None:
        if ref_price * qty < ticker.min_notional:
            raise OrderValidationError(
                f"Notional below the minimum ({ticker.min_notional}) for this instrument"
            )
    return qty, price


def check_guardrails(
    universal_symbol: str, qty: Decimal, ref_price: Decimal | None
) -> None:
    allow = _allowlist()
    if allow and universal_symbol not in allow:
        raise GuardrailError(f"{universal_symbol} is not on the order allow-list")
    max_notional = Decimal(str(settings.ORDER_MAX_NOTIONAL or 0))
    if max_notional > 0:
        if ref_price is None:
            raise GuardrailError(
                "Cannot enforce the max-notional guardrail without a reference price"
            )
        if ref_price * qty > max_notional:
            raise GuardrailError(
                f"Order notional {ref_price * qty} exceeds the max ({max_notional})"
            )


# -- helpers ------------------------------------------------------------------

def _new_client_order_id() -> str:
    return f"cpanel-{uuid.uuid4().hex[:16]}"


async def _ticker(db: AsyncSession, exchange: str, universal_symbol: str) -> UniversalTicker:
    ticker = await db.get(UniversalTicker, universal_symbol)
    if ticker is None or ticker.exchange != exchange.upper():
        raise OrderValidationError(f"Unknown instrument {universal_symbol!r} for {exchange}")
    return ticker


def _apply_connector_order(row: Order, result: ConnOrder) -> None:
    row.native_order_id = result.native_order_id
    row.status = result.status.value
    row.filled_qty = result.filled_qty
    if result.price is not None:
        row.price = result.price
    row.updated_at = result.updated_at or datetime.now(timezone.utc)


# -- write paths --------------------------------------------------------------

async def place_order(
    db: AsyncSession,
    connector: ExchangeConnector,
    account: Account,
    universal_symbol: str,
    side: OrderSide,
    otype: OrderType,
    qty: Decimal,
    price: Decimal | None = None,
    time_in_force: TimeInForce | None = None,
    reduce_only: bool = False,
    position_side: PositionSide | None = None,
) -> Order:
    """Validate, guardrail, and place one order idempotently. Returns the row."""
    connector.require(Capability.ORDERS)
    ticker = await _ticker(db, account.exchange, universal_symbol)
    qty, price = validate_and_round(ticker, otype, qty, price)
    ref_price = price if price is not None else ticker.last_price
    check_guardrails(universal_symbol, qty, ref_price)

    credential = decrypt_credential(await get_active_credential(db, account))

    client_order_id = _new_client_order_id()
    row = Order(
        account_id=account.id,
        exchange=account.exchange,
        universal_symbol=universal_symbol,
        client_order_id=client_order_id,
        side=side.value,
        type=otype.value,
        status=OrderStatus.SUBMITTING.value,
        qty=qty,
        price=price,
        filled_qty=Decimal("0"),
        external=False,
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    request = OrderRequest(
        universal_symbol=universal_symbol,
        side=side,
        type=otype,
        qty=qty,
        price=price,
        client_order_id=client_order_id,
        time_in_force=time_in_force,
        reduce_only=reduce_only,
        position_side=position_side,
    )
    try:
        result = await connector.place_order(credential, request)
    except Exception as exc:  # noqa: BLE001 - classify, then recover or reject
        recovered = await _recover(connector, credential, universal_symbol, client_order_id)
        if recovered is not None:
            _apply_connector_order(row, recovered)
            await db.commit()
            await db.refresh(row)
            return row
        row.status = OrderStatus.REJECTED.value
        row.reject_reason = str(exc)[:500]
        row.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(row)
        logger.warning("Order placement failed for %s: %s", universal_symbol, exc)
        raise OrderError(row.reject_reason) from exc

    _apply_connector_order(row, result)
    await db.commit()
    await db.refresh(row)
    return row


async def _recover(
    connector: ExchangeConnector,
    credential: dict,
    universal_symbol: str,
    client_order_id: str,
) -> ConnOrder | None:
    """After a failed placement, see whether the venue actually accepted it."""
    try:
        return await connector.fetch_order(
            credential, universal_symbol, client_order_id=client_order_id
        )
    except Exception:  # noqa: BLE001 - recovery is best-effort
        return None


async def cancel_order(
    db: AsyncSession,
    connector: ExchangeConnector,
    account: Account,
    order: Order,
) -> Order:
    """Cancel one order on the venue and record the new state."""
    connector.require(Capability.ORDERS)
    if order.native_order_id is None:
        raise OrderError("Order has no venue id yet; cannot cancel")
    if order.status not in OPEN_STATES:
        raise OrderError(f"Order is {order.status}; nothing to cancel")
    credential = decrypt_credential(await get_active_credential(db, account))
    result = await connector.cancel_order(
        credential, order.universal_symbol, order.native_order_id
    )
    _apply_connector_order(order, result)
    await db.commit()
    await db.refresh(order)
    return order


async def place_orders(
    db: AsyncSession,
    connector: ExchangeConnector,
    account: Account,
    requests: list[dict],
) -> list[dict]:
    """Place a batch, reporting a per-item outcome (never all-or-nothing)."""
    outcomes: list[dict] = []
    for req in requests:
        try:
            row = await place_order(
                db, connector, account,
                universal_symbol=req["universal_symbol"],
                side=OrderSide(req["side"]),
                otype=OrderType(req["type"]),
                qty=Decimal(str(req["qty"])),
                price=Decimal(str(req["price"])) if req.get("price") is not None else None,
                time_in_force=TimeInForce(req["time_in_force"]) if req.get("time_in_force") else None,
                reduce_only=bool(req.get("reduce_only", False)),
                position_side=PositionSide(req["position_side"]) if req.get("position_side") else None,
            )
            outcomes.append({"ok": True, "order_id": row.id, "status": row.status})
        except (OrderError, AccountError) as exc:
            outcomes.append({"ok": False, "error": str(exc), "request": req})
    return outcomes


async def cancel_orders(
    db: AsyncSession,
    connector: ExchangeConnector,
    account: Account,
    orders: list[Order],
) -> list[dict]:
    outcomes: list[dict] = []
    for order in orders:
        try:
            await cancel_order(db, connector, account, order)
            outcomes.append({"ok": True, "order_id": order.id, "status": order.status})
        except (OrderError, AccountError) as exc:
            outcomes.append({"ok": False, "order_id": order.id, "error": str(exc)})
    return outcomes


# -- sync / read paths --------------------------------------------------------

async def sync_open_orders(
    db: AsyncSession, connector: ExchangeConnector, account: Account
) -> int:
    """Reconcile local open orders with the venue. Returns rows changed.

    Venue open orders update (or create, if external) the matching local row;
    local orders the venue no longer lists are resolved to a terminal state via
    a per-order lookup. Polling is the source of truth per the architecture.
    """
    connector.require(Capability.ORDERS)
    credential = decrypt_credential(await get_active_credential(db, account))
    venue_orders = await connector.fetch_open_orders(credential)

    local = list(
        (
            await db.execute(
                select(Order).where(
                    Order.account_id == account.id, Order.status.in_(OPEN_STATES)
                )
            )
        ).scalars()
    )
    by_native = {o.native_order_id: o for o in local if o.native_order_id}
    by_client = {o.client_order_id: o for o in local if o.client_order_id}

    changed = 0
    balance_touch = False
    seen_ids: set[str] = set()
    for vo in venue_orders:
        seen_ids.add(vo.native_order_id)
        row = by_native.get(vo.native_order_id) or (
            by_client.get(vo.client_order_id) if vo.client_order_id else None
        )
        if row is None:
            db.add(_external_row(account, vo))
            changed += 1
            if vo.filled_qty and vo.filled_qty > 0:
                balance_touch = True
            continue
        fill_or_status = (
            row.status != vo.status.value or row.filled_qty != vo.filled_qty
        )
        if _apply_if_changed(row, vo):
            changed += 1
            if fill_or_status:
                balance_touch = True

    # Local opens the venue no longer reports: resolve to a terminal state.
    for row in local:
        if row.native_order_id in seen_ids:
            continue
        terminal = await _resolve_terminal(connector, credential, row)
        if terminal is not None:
            fill_or_status = (
                row.status != terminal.status.value
                or row.filled_qty != terminal.filled_qty
            )
            if _apply_if_changed(row, terminal):
                changed += 1
                if fill_or_status:
                    balance_touch = True

    if changed:
        await db.commit()
    if balance_touch:
        schedule_balance_refresh(account.id, "order_sync")
    return changed


def _external_row(account: Account, vo: ConnOrder) -> Order:
    return Order(
        account_id=account.id,
        exchange=account.exchange,
        universal_symbol=vo.universal_symbol,
        client_order_id=vo.client_order_id,
        native_order_id=vo.native_order_id,
        side=vo.side.value,
        type=vo.type.value,
        status=vo.status.value,
        qty=vo.qty,
        price=vo.price,
        filled_qty=vo.filled_qty,
        external=True,
        submitted_at=vo.created_at,
        updated_at=vo.updated_at or datetime.now(timezone.utc),
    )


def _apply_if_changed(row: Order, vo: ConnOrder) -> bool:
    changed = (
        row.status != vo.status.value
        or row.filled_qty != vo.filled_qty
        or (row.native_order_id is None and vo.native_order_id is not None)
    )
    if changed:
        _apply_connector_order(row, vo)
    return changed


async def _resolve_terminal(
    connector: ExchangeConnector, credential: dict, row: Order
) -> ConnOrder | None:
    try:
        return await connector.fetch_order(
            credential,
            row.universal_symbol,
            native_order_id=row.native_order_id,
            client_order_id=row.client_order_id,
        )
    except Exception:  # noqa: BLE001 - best-effort; leave state for next poll
        return None


async def list_orders(
    db: AsyncSession,
    *,
    exchange: str | None = None,
    account_id: int | None = None,
    universal_symbol: str | None = None,
    open_only: bool = False,
    limit: int = 200,
) -> list[Order]:
    query = select(Order).order_by(Order.created_at.desc(), Order.id.desc())
    if exchange:
        query = query.where(Order.exchange == exchange.upper())
    if account_id is not None:
        query = query.where(Order.account_id == account_id)
    if universal_symbol:
        query = query.where(Order.universal_symbol == universal_symbol)
    if open_only:
        query = query.where(Order.status.in_(OPEN_STATES))
    query = query.limit(limit)
    return list((await db.execute(query)).scalars())
