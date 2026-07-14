"""Order API tests against the fake connector."""
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select

from app.connectors.fake import FakeConnector
from app.connectors.registry import connectors
from app.core.audit import Actor, current_actor
from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.audit_log import AuditLog
from app.services import accounts as accounts_svc
from app.services.symbols import refresh_instruments, refresh_prices

SYMBOL = "FAKE_SPOT_BTCUSDT"


@pytest_asyncio.fixture
async def client(session_factory):
    connector = FakeConnector()
    connectors.register(connector)

    async with session_factory() as db:
        await refresh_instruments(db, connector)
        await refresh_prices(db, connector)
        account = await accounts_svc.register_account(db, "FAKE", "main", "main")
        await accounts_svc.add_credential(db, connector, account, "HMAC", "api-key-1", "secret-1")
        account_id = account.id

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_actor] = lambda: Actor(type="user", id="42", name="t")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.account_id = account_id  # type: ignore[attr-defined]
        c.session_factory = session_factory  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


def _leg(**overrides):
    body = {
        "account_id": None,
        "universal_symbol": SYMBOL,
        "side": "BUY",
        "type": "LIMIT",
        "qty": "0.01",
        "price": "60000",
    }
    body.update(overrides)
    return body


async def test_requires_auth(session_factory):
    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/orders")).status_code == 401
    app.dependency_overrides.clear()


async def test_place_and_list(client):
    resp = await client.post("/orders", json=_leg(account_id=client.account_id))
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "OPEN"
    assert body["client_order_id"].startswith("cpanel-")
    assert Decimal(body["qty"]) == Decimal("0.01")

    listed = (await client.get("/orders")).json()
    assert len(listed) == 1
    assert listed[0]["universal_symbol"] == SYMBOL

    open_only = (await client.get("/orders", params={"open_only": True})).json()
    assert len(open_only) == 1


async def test_place_validation_error(client):
    resp = await client.post(
        "/orders", json=_leg(account_id=client.account_id, qty="0.00001", price="1")
    )
    assert resp.status_code == 422


async def test_guardrail_forbidden(client, monkeypatch):
    monkeypatch.setattr(settings, "ORDER_MAX_NOTIONAL", 10)
    resp = await client.post(
        "/orders", json=_leg(account_id=client.account_id, qty="1", price="60000")
    )
    assert resp.status_code == 403


async def test_cancel_order(client):
    placed = (await client.post("/orders", json=_leg(account_id=client.account_id))).json()
    resp = await client.post(f"/orders/{placed['id']}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


async def test_bulk_place(client):
    body = {
        "account_id": client.account_id,
        "orders": [
            {"universal_symbol": SYMBOL, "side": "BUY", "type": "LIMIT", "qty": "0.01", "price": "60000"},
            {"universal_symbol": SYMBOL, "side": "BUY", "type": "LIMIT", "qty": "0.00001", "price": "1"},
        ],
    }
    resp = await client.post("/orders/bulk", json=body)
    assert resp.status_code == 201
    outcomes = resp.json()
    assert outcomes[0]["ok"] is True
    assert outcomes[1]["ok"] is False  # min-notional violation


async def test_place_is_audited(client):
    await client.post("/orders", json=_leg(account_id=client.account_id))
    async with client.session_factory() as db:
        rows = (
            await db.execute(select(AuditLog).where(AuditLog.action == "orders.place"))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "succeeded"
