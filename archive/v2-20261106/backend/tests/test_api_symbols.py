import httpx
import pytest_asyncio

from app.connectors.fake import FakeConnector
from app.core.audit import Actor, current_actor
from app.core.db import get_db
from app.main import app
from app.services.symbols import refresh_instruments, refresh_prices


@pytest_asyncio.fixture
async def client(session_factory):
    async with session_factory() as db:
        connector = FakeConnector()
        await refresh_instruments(db, connector)
        await refresh_prices(db, connector)

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_actor] = lambda: Actor(type="user", id="42", name="t")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_requires_auth(session_factory):
    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/symbols")).status_code == 401
    app.dependency_overrides.clear()


async def test_list_all(client):
    body = (await client.get("/symbols")).json()
    assert body["total"] == 2
    assert {s["universal_symbol"] for s in body["items"]} == {
        "FAKE_SPOT_BTCUSDT",
        "FAKE_PERP_ETHUSDT",
    }


async def test_filter_by_market_type(client):
    body = (await client.get("/symbols", params={"market_type": "perp"})).json()
    assert body["total"] == 1
    assert body["items"][0]["universal_symbol"] == "FAKE_PERP_ETHUSDT"


async def test_search(client):
    body = (await client.get("/symbols", params={"search": "btc"})).json()
    assert body["total"] == 1


async def test_get_single_symbol_with_filters_and_price(client):
    body = (await client.get("/symbols/fake_spot_btcusdt")).json()
    assert body["price_tick"] == "0.01"
    assert body["qty_step"] == "0.00001"
    assert body["min_notional"] == "5"
    assert body["last_price"] == "60000"


async def test_unknown_symbol_404(client):
    assert (await client.get("/symbols/NOPE_SPOT_X")).status_code == 404
