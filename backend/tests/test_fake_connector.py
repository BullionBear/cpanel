"""Interface test suite for the connector surface, run against FakeConnector.

Real connectors (Phase 1+) should be able to reuse the same assertions where
capabilities overlap.
"""
from decimal import Decimal

import pytest

from app.connectors.base import Capability
from app.connectors.fake import FakeConnector
from app.connectors.types import (
    KeyScheme,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    WalletType,
)

CRED = {"api_key": "k", "secret": "s"}


@pytest.fixture
def connector():
    return FakeConnector()


def test_declares_all_capabilities(connector):
    for capability in Capability:
        assert connector.supports(capability)


async def test_instruments_are_normalized(connector):
    instruments = await connector.fetch_instruments()
    assert instruments
    for inst in instruments:
        assert inst.universal_symbol.startswith(f"{connector.exchange}_")
        assert inst.filters.price_tick > 0
        assert inst.filters.qty_step > 0


async def test_reference_prices_cover_instruments(connector):
    instruments = await connector.fetch_instruments()
    prices = await connector.fetch_reference_prices()
    for inst in instruments:
        assert inst.universal_symbol in prices


async def test_balances(connector):
    balances = await connector.fetch_balances(CRED)
    assert balances[0].wallet == WalletType.SPOT
    assert balances[0].total == balances[0].free + balances[0].locked


async def test_order_lifecycle(connector):
    request = OrderRequest(
        universal_symbol="FAKE_SPOT_BTCUSDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        qty=Decimal("0.5"),
        price=Decimal("50000"),
        client_order_id="cpanel-abc",
    )
    order = await connector.place_order(CRED, request)
    assert order.status == OrderStatus.OPEN
    assert order.client_order_id == "cpanel-abc"

    open_orders = await connector.fetch_open_orders(CRED)
    assert [o.native_order_id for o in open_orders] == [order.native_order_id]

    # Idempotent recovery path: find by client order id
    found = await connector.fetch_order(
        CRED, "FAKE_SPOT_BTCUSDT", client_order_id="cpanel-abc"
    )
    assert found is not None and found.native_order_id == order.native_order_id

    cancelled = await connector.cancel_order(CRED, "FAKE_SPOT_BTCUSDT", order.native_order_id)
    assert cancelled.status == OrderStatus.CANCELLED
    assert await connector.fetch_open_orders(CRED) == []


async def test_fill_produces_trade_history(connector):
    order = await connector.place_order(
        CRED,
        OrderRequest(
            universal_symbol="FAKE_PERP_ETHUSDT",
            side=OrderSide.SELL,
            type=OrderType.LIMIT,
            qty=Decimal("2"),
            price=Decimal("3100"),
        ),
    )
    trade = connector.fill_order(order.native_order_id)
    assert trade.native_order_id == order.native_order_id

    page = await connector.fetch_trades(CRED, "account", cursor=None)
    assert len(page.items) == 1
    assert page.next_cursor is None
    assert page.items[0].qty == Decimal("2")


async def test_trade_pagination_cursor(connector):
    for i in range(150):
        order = await connector.place_order(
            CRED,
            OrderRequest(
                universal_symbol="FAKE_SPOT_BTCUSDT",
                side=OrderSide.BUY,
                type=OrderType.LIMIT,
                qty=Decimal("0.001"),
                price=Decimal("60000"),
            ),
        )
        connector.fill_order(order.native_order_id)

    page1 = await connector.fetch_trades(CRED, "account", cursor=None)
    assert len(page1.items) == 100 and page1.next_cursor is not None
    page2 = await connector.fetch_trades(CRED, "account", cursor=page1.next_cursor)
    assert len(page2.items) == 50 and page2.next_cursor is None

    # Re-fetching the same page returns the same items (idempotent ingestion)
    again = await connector.fetch_trades(CRED, "account", cursor=page1.next_cursor)
    assert [t.native_trade_id for t in again.items] == [
        t.native_trade_id for t in page2.items
    ]


async def test_provisioning(connector):
    sub = await connector.create_subaccount(CRED, "mm-01")
    assert sub in connector.subaccounts

    key = await connector.create_api_key(CRED, sub, KeyScheme.ED25519, ["read", "trade"])
    assert key.scheme == KeyScheme.ED25519
    assert key.secret  # plaintext available exactly once, at creation

    await connector.revoke_api_key(CRED, sub, key.api_key)
    assert key.api_key in connector.revoked_keys


async def test_transfer(connector):
    result = await connector.transfer(
        CRED, "USDT", Decimal("100"),
        from_wallet=WalletType.SPOT, to_wallet=WalletType.DERIVATIVES,
    )
    assert result.status == "confirmed"
    assert connector.transfers[0]["asset"] == "USDT"
