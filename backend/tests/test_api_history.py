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
    resp = await client.post(f"/history/sync/{client.account_id}")
    assert resp.status_code == 200
    assert resp.json()["ingested"] == 2  # 1 trade + 1 order

    trades = (await client.get("/history/trades")).json()
    assert trades["total"] == 1
    trade = trades["items"][0]
    assert trade["universal_symbol"] == SYMBOL
    assert Decimal(trade["qty"]) == Decimal("0.01")

    orders = (await client.get("/history/orders")).json()
    assert orders["total"] == 1
    assert orders["items"][0]["status"] == "FILLED"

    # Filters narrow, unknown values exclude.
    assert (await client.get("/history/trades", params={"exchange": "FAKE"})).json()["total"] == 1
    assert (await client.get("/history/trades", params={"universal_symbol": "NOPE"})).json()["total"] == 0


async def test_stream_health_listed(client):
    await client.post(f"/history/sync/{client.account_id}")
    streams = (await client.get("/history/streams")).json()
    assert {(s["data_kind"], s["stream_key"]) for s in streams} == {
        ("trades", "account"),
        ("orders", "account"),
    }
    for stream in streams:
        assert stream["account_id"] == client.account_id
        assert stream["last_synced_at"] is not None
        assert stream["last_error"] is None


async def test_sync_unknown_account_404(client):
    assert (await client.post("/history/sync/9999")).status_code == 404
