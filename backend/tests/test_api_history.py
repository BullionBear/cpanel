"""History API tests against the fake connector."""
from decimal import Decimal

import httpx
import pytest_asyncio

from app.connectors.fake import FakeConnector
from app.connectors.registry import connectors
from app.connectors.types import OrderRequest, OrderSide, OrderType
from app.core.audit import Actor, current_actor
from app.core.db import get_db
from app.main import app
from app.services import accounts as accounts_svc

SYMBOL = "FAKE_SPOT_BTCUSDT"
CRED = {"api_key": "k", "secret": "s"}


@pytest_asyncio.fixture
async def client(session_factory):
    connector = FakeConnector()
    connectors.register(connector)

    async with session_factory() as db:
        account = await accounts_svc.register_account(db, "FAKE", "main", "main")
        await accounts_svc.add_credential(db, connector, account, "HMAC", "api-key-1", "secret-1")
        account_id = account.id

    # One filled order so both history kinds have content.
    order = await connector.place_order(
        CRED,
        OrderRequest(
            universal_symbol=SYMBOL,
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            qty=Decimal("0.01"),
            price=Decimal("60000"),
        ),
    )
    connector.fill_order(order.native_order_id)

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_actor] = lambda: Actor(type="user", id="42", name="t")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.account_id = account_id  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


async def test_requires_auth(session_factory):
    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/history/trades")).status_code == 401
    app.dependency_overrides.clear()


async def test_sync_then_explore(client):
    # Seed venue deposit/withdrawal history (exchange-sourced, not panel transfers).
    from app.connectors.registry import connectors

    fake = connectors.get("FAKE")
    fake.credit_deposit("USDT", Decimal("25"), tx_id="0xd")
    wd = await fake.withdraw(
        CRED, "USDT", Decimal("10"), address="TExternal", network="TRX"
    )
    fake.complete_withdrawal(wd.native_id, tx_id="0xw", fee=Decimal("0.5"))
    fake.credit_funding_fee(
        "FAKE_PERP_ETHUSDT", "USDT", Decimal("-0.75"), funding_rate=Decimal("0.0001")
    )

    resp = await client.post(f"/history/sync/{client.account_id}")
    assert resp.status_code == 200
    # 1 trade + 1 order + 1 deposit + 1 withdrawal + 1 funding
    assert resp.json()["ingested"] == 5

    trades = (await client.get("/history/trades")).json()
    assert trades["total"] == 1
    trade = trades["items"][0]
    assert trade["universal_symbol"] == SYMBOL
    assert Decimal(trade["qty"]) == Decimal("0.01")

    orders = (await client.get("/history/orders")).json()
    assert orders["total"] == 1
    assert orders["items"][0]["status"] == "FILLED"

    deposits = (await client.get("/history/deposits")).json()
    assert deposits["total"] == 1
    assert deposits["items"][0]["asset"] == "USDT"
    assert Decimal(deposits["items"][0]["amount"]) == Decimal("25")

    withdrawals = (await client.get("/history/withdrawals")).json()
    assert withdrawals["total"] == 1
    assert withdrawals["items"][0]["status"] == "COMPLETED"
    assert Decimal(withdrawals["items"][0]["fee"]) == Decimal("0.5")

    funding = (await client.get("/history/funding")).json()
    assert funding["total"] == 1
    assert funding["items"][0]["universal_symbol"] == "FAKE_PERP_ETHUSDT"
    assert Decimal(funding["items"][0]["amount"]) == Decimal("-0.75")
    assert Decimal(funding["items"][0]["funding_rate"]) == Decimal("0.0001")

    # Filters narrow, unknown values exclude.
    assert (await client.get("/history/trades", params={"exchange": "FAKE"})).json()["total"] == 1
    assert (await client.get("/history/trades", params={"universal_symbol": "NOPE"})).json()["total"] == 0
    assert (await client.get("/history/deposits", params={"asset": "USDT"})).json()["total"] == 1
    assert (await client.get("/history/deposits", params={"asset": "BTC"})).json()["total"] == 0
    assert (
        await client.get("/history/funding", params={"universal_symbol": "FAKE_PERP_ETHUSDT"})
    ).json()["total"] == 1
    assert (
        await client.get("/history/funding", params={"universal_symbol": "NOPE"})
    ).json()["total"] == 0


async def test_stream_health_listed(client):
    await client.post(f"/history/sync/{client.account_id}")
    streams = (await client.get("/history/streams")).json()
    assert {(s["data_kind"], s["stream_key"]) for s in streams} == {
        ("trades", "account"),
        ("orders", "account"),
        ("deposits", "account"),
        ("withdrawals", "account"),
        ("funding", "account"),
    }
    for stream in streams:
        assert stream["account_id"] == client.account_id
        assert stream["last_synced_at"] is not None
        assert stream["last_error"] is None


async def test_sync_unknown_account_404(client):
    assert (await client.post("/history/sync/9999")).status_code == 404
