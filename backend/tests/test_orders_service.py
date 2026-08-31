"""Order service tests: validation, guardrails, placement, recovery, sync."""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.connectors.fake import FakeConnector
from app.connectors.types import OrderRequest, OrderSide, OrderStatus, OrderType
from app.core.config import settings
from app.models.order import Order
from app.services import accounts as accounts_svc
from app.services import orders as svc
from app.services.symbols import refresh_instruments, refresh_prices

SYMBOL = "FAKE_SPOT_BTCUSDT"


@pytest.fixture
def connector():
    return FakeConnector()


async def _setup(db, connector):
    await refresh_instruments(db, connector)
    await refresh_prices(db, connector)
    account = await accounts_svc.register_account(db, "FAKE", "main", "main")
    await accounts_svc.add_credential(db, connector, account, "HMAC", "api-key-1", "secret-1")
    return account


async def test_place_order_rounds_and_records(db, connector):
    account = await _setup(db, connector)
    row = await svc.place_order(
        db, connector, account, SYMBOL,
        OrderSide.BUY, OrderType.LIMIT,
        qty=Decimal("0.123456"), price=Decimal("60000.005"),
    )
    assert row.status == OrderStatus.OPEN.value
    assert row.qty == Decimal("0.12345")       # rounded down to step 0.00001
    assert row.price == Decimal("60000.01")    # rounded to tick 0.01
    assert row.native_order_id is not None
    assert row.client_order_id.startswith("cpanel")
    # OKX rejects a clOrdId with any non-alphanumeric character.
    assert row.client_order_id.isalnum()


async def test_market_order_has_no_price(db, connector):
    account = await _setup(db, connector)
    row = await svc.place_order(
        db, connector, account, SYMBOL,
        OrderSide.SELL, OrderType.MARKET, qty=Decimal("0.01"), price=None,
    )
    assert row.type == OrderType.MARKET.value
    assert row.price is None
    assert row.status == OrderStatus.OPEN.value


async def test_below_min_notional_rejected(db, connector):
    account = await _setup(db, connector)
    with pytest.raises(svc.OrderValidationError):
        await svc.place_order(
            db, connector, account, SYMBOL,
            OrderSide.BUY, OrderType.LIMIT, qty=Decimal("0.00001"), price=Decimal("1"),
        )


async def test_below_min_qty_rejected(db, connector):
    account = await _setup(db, connector)
    with pytest.raises(svc.OrderValidationError):
        await svc.place_order(
            db, connector, account, SYMBOL,
            OrderSide.BUY, OrderType.LIMIT, qty=Decimal("0.000001"), price=Decimal("60000"),
        )


async def test_guardrail_max_notional(db, connector, monkeypatch):
    account = await _setup(db, connector)
    monkeypatch.setattr(settings, "ORDER_MAX_NOTIONAL", 10)
    with pytest.raises(svc.GuardrailError):
        await svc.place_order(
            db, connector, account, SYMBOL,
            OrderSide.BUY, OrderType.LIMIT, qty=Decimal("1"), price=Decimal("60000"),
        )


async def test_guardrail_allowlist(db, connector, monkeypatch):
    account = await _setup(db, connector)
    monkeypatch.setattr(settings, "ORDER_SYMBOL_ALLOWLIST", "FAKE_PERP_ETHUSDT")
    with pytest.raises(svc.GuardrailError):
        await svc.place_order(
            db, connector, account, SYMBOL,
            OrderSide.BUY, OrderType.LIMIT, qty=Decimal("0.01"), price=Decimal("60000"),
        )


async def test_placement_recovers_after_lost_response(db, connector):
    """The venue accepted the order but the response was lost — recover by client id."""
    account = await _setup(db, connector)

    class _AcceptThenRaise(FakeConnector):
        async def place_order(self, credential, request):
            await super().place_order(credential, request)  # venue stored it
            raise RuntimeError("connection reset")

    recovering = _AcceptThenRaise()
    row = await svc.place_order(
        db, recovering, account, SYMBOL,
        OrderSide.BUY, OrderType.LIMIT, qty=Decimal("0.01"), price=Decimal("60000"),
    )
    assert row.status == OrderStatus.OPEN.value       # recovered, not rejected
    assert row.native_order_id is not None


async def test_placement_rejected_when_unrecoverable(db, connector):
    account = await _setup(db, connector)

    class _Reject(FakeConnector):
        async def place_order(self, credential, request):
            raise RuntimeError("rejected by venue")

    with pytest.raises(svc.OrderError):
        await svc.place_order(
            db, _Reject(), account, SYMBOL,
            OrderSide.BUY, OrderType.LIMIT, qty=Decimal("0.01"), price=Decimal("60000"),
        )
    row = (await db.execute(select(Order))).scalars().one()
    assert row.status == OrderStatus.REJECTED.value
    assert row.reject_reason


async def test_extra_options_reach_connector(db, connector):
    """Post-only / reduce-only / position-side flow through to the connector."""
    from app.connectors.types import PositionSide, TimeInForce

    account = await _setup(db, connector)

    class _Capturing(FakeConnector):
        captured = None

        async def place_order(self, credential, request):
            _Capturing.captured = request
            return await super().place_order(credential, request)

    cap = _Capturing()
    await svc.place_order(
        db, cap, account, "FAKE_PERP_ETHUSDT",
        OrderSide.SELL, OrderType.LIMIT, qty=Decimal("0.01"), price=Decimal("3000"),
        time_in_force=TimeInForce.POST_ONLY, reduce_only=True, position_side=PositionSide.SHORT,
    )
    assert cap.captured.time_in_force == TimeInForce.POST_ONLY
    assert cap.captured.reduce_only is True
    assert cap.captured.position_side == PositionSide.SHORT


async def test_cancel_order(db, connector):
    account = await _setup(db, connector)
    row = await svc.place_order(
        db, connector, account, SYMBOL,
        OrderSide.BUY, OrderType.LIMIT, qty=Decimal("0.01"), price=Decimal("60000"),
    )
    cancelled = await svc.cancel_order(db, connector, account, row)
    assert cancelled.status == OrderStatus.CANCELLED.value


async def test_sync_marks_filled(db, connector):
    account = await _setup(db, connector)
    row = await svc.place_order(
        db, connector, account, SYMBOL,
        OrderSide.BUY, OrderType.LIMIT, qty=Decimal("0.01"), price=Decimal("60000"),
    )
    connector.fill_order(row.native_order_id)  # venue-side fill
    changed = await svc.sync_open_orders(db, connector, account)
    assert changed == 1
    refreshed = await db.get(Order, row.id)
    assert refreshed.status == OrderStatus.FILLED.value
    assert refreshed.filled_qty == Decimal("0.01")


async def test_sync_discovers_external_order(db, connector):
    account = await _setup(db, connector)
    # An order placed entirely outside the panel (no client id).
    await connector.place_order(
        {}, OrderRequest(
            universal_symbol=SYMBOL, side=OrderSide.SELL, type=OrderType.LIMIT,
            qty=Decimal("0.02"), price=Decimal("61000"),
        )
    )
    changed = await svc.sync_open_orders(db, connector, account)
    assert changed == 1
    row = (await db.execute(select(Order).where(Order.external.is_(True)))).scalars().one()
    assert row.universal_symbol == SYMBOL
    assert row.status == OrderStatus.OPEN.value
