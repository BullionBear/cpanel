"""Transfer API tests against two fake connectors (FAKE / FAKE2)."""
from decimal import Decimal

import httpx
import pytest_asyncio

from app.connectors.fake import FakeConnector
from app.connectors.registry import connectors
from app.core.audit import Actor, current_actor
from app.core.db import get_db
from app.main import app
from app.services import accounts as accounts_svc


def make_fake(exchange: str) -> FakeConnector:
    connector = FakeConnector()
    connector.exchange = exchange
    return connector


@pytest_asyncio.fixture
async def client(session_factory):
    fake = make_fake("FAKE")
    fake2 = make_fake("FAKE2")
    connectors.register(fake)
    connectors.register(fake2)

    async with session_factory() as db:
        main = await accounts_svc.register_account(db, "FAKE", "main", "main")
        await accounts_svc.add_credential(db, fake, main, "HMAC", "key-1", "secret-1")
        sub = await accounts_svc.register_account(
            db, "FAKE", "sub-1", "sub", parent_id=main.id, native_uid="uid-9"
        )
        dest = await accounts_svc.register_account(db, "FAKE2", "main", "main")
        await accounts_svc.add_credential(db, fake2, dest, "HMAC", "key-2", "secret-2")
        ids = {"main": main.id, "sub": sub.id, "dest": dest.id}

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_actor] = lambda: Actor(type="user", id="42", name="t")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.ids = ids  # type: ignore[attr-defined]
        c.fakes = (fake, fake2)  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


async def test_requires_auth(session_factory):
    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/transfers")).status_code == 401
    app.dependency_overrides.clear()


async def test_intra_create_and_read(client):
    resp = await client.post(
        "/transfers",
        json={
            "from_account_id": client.ids["main"],
            "to_account_id": client.ids["sub"],
            "asset": "USDT",
            "amount": "25",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "intra"
    assert body["status"] == "completed"
    assert [s["name"] for s in body["steps"]] == ["submit", "confirm"]

    listed = (await client.get("/transfers")).json()
    assert len(listed) == 1
    detail = (await client.get(f"/transfers/{body['id']}")).json()
    assert detail["native_transfer_id"]


async def test_cross_requires_confirm(client):
    resp = await client.post(
        "/transfers",
        json={
            "from_account_id": client.ids["main"],
            "to_account_id": client.ids["dest"],
            "asset": "USDT",
            "amount": "100",
            "network": "TESTNET",
        },
    )
    assert resp.status_code == 422
    assert "confirm" in resp.json()["detail"]


async def test_cross_preview_then_create(client):
    preview = await client.post(
        "/transfers/preview",
        json={
            "from_account_id": client.ids["main"],
            "to_account_id": client.ids["dest"],
            "asset": "USDT",
            "amount": "100",
            "network": "TESTNET",
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["ok"] is True
    assert preview.json()["expected_fee"] == "0.5"

    resp = await client.post(
        "/transfers",
        json={
            "from_account_id": client.ids["main"],
            "to_account_id": client.ids["dest"],
            "asset": "USDT",
            "amount": "100",
            "network": "TESTNET",
            "confirm": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "cross"
    assert body["status"] == "in_progress"
    assert body["address"] == "fake-USDT-TESTNET-self"
    statuses = {s["name"]: s["status"] for s in body["steps"]}
    assert statuses["submit_withdrawal"] == "succeeded"
    assert statuses["await_withdrawal"] == "running"


async def test_fail_endpoint(client):
    created = (
        await client.post(
            "/transfers",
            json={
                "from_account_id": client.ids["main"],
                "to_account_id": client.ids["dest"],
                "asset": "USDT",
                "amount": "100",
                "network": "TESTNET",
                "confirm": True,
            },
        )
    ).json()
    resp = await client.post(
        f"/transfers/{created['id']}/fail", json={"reason": "operator cancelled"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    # A completed/failed transfer cannot be retried.
    retry = await client.post(f"/transfers/{created['id']}/retry")
    assert retry.status_code == 422


async def test_withdraw_preview_and_create(client):
    preview = await client.post(
        "/transfers/withdraw/preview",
        json={
            "from_account_id": client.ids["main"],
            "asset": "USDT",
            "amount": "50",
            "network": "TESTNET",
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["ok"] is True

    # Confirmation is mandatory for irreversible withdrawals.
    unconfirmed = await client.post(
        "/transfers/withdraw",
        json={
            "from_account_id": client.ids["main"],
            "asset": "USDT",
            "amount": "50",
            "address": "TExternal123",
            "network": "TESTNET",
        },
    )
    assert unconfirmed.status_code == 422

    resp = await client.post(
        "/transfers/withdraw",
        json={
            "from_account_id": client.ids["main"],
            "asset": "USDT",
            "amount": "50",
            "address": "TExternal123",
            "network": "TESTNET",
            "confirm": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "withdrawal"
    assert body["to_account_id"] is None
    assert body["address"] == "TExternal123"
    statuses = {s["name"]: s["status"] for s in body["steps"]}
    assert statuses["await_withdrawal"] == "running"


async def test_deposit_address_and_recent(client):
    fake, _ = client.fakes
    resp = await client.get(
        "/transfers/deposit-address",
        params={"account_id": client.ids["main"], "asset": "USDT", "network": "TESTNET"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["address"] == "fake-USDT-TESTNET-self"

    fake.credit_deposit("USDT", Decimal("25"), tx_id="0xd")
    deposits = await client.get(
        "/transfers/deposits", params={"account_id": client.ids["main"], "asset": "USDT"}
    )
    assert deposits.status_code == 200
    assert len(deposits.json()) == 1
    assert deposits.json()[0]["status"] == "CREDITED"


async def test_validation_maps_to_422(client):
    resp = await client.post(
        "/transfers",
        json={
            "from_account_id": client.ids["main"],
            "to_account_id": client.ids["main"],
            "asset": "USDT",
            "amount": "10",
        },
    )
    assert resp.status_code == 422
    assert "wallet" in resp.json()["detail"].lower()
