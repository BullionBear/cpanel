"""Portfolio API tests against the fake connector."""
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select

from app.connectors.fake import FakeConnector
from app.connectors.registry import connectors
from app.connectors.types import Balance, Position, WalletType
from app.core.audit import Actor, current_actor
from app.core.db import get_db
from app.main import app
from app.models.audit_log import AuditLog
from app.services import accounts as accounts_svc
from app.services.symbols import refresh_instruments, refresh_prices


def _fake_connector() -> FakeConnector:
    c = FakeConnector()
    c.balances = [
        Balance(wallet=WalletType.SPOT, asset="USDT", free=Decimal("10000"), locked=Decimal("0")),
        Balance(wallet=WalletType.SPOT, asset="BTC", free=Decimal("0.5"), locked=Decimal("0")),
    ]
    c.positions = [
        Position(
            universal_symbol="FAKE_PERP_ETHUSDT",
            side="long",
            size=Decimal("2"),
            entry_price=Decimal("3000"),
            mark_price=Decimal("3100"),
            unrealized_pnl=Decimal("200"),
            margin=Decimal("600"),
        )
    ]
    return c


@pytest_asyncio.fixture
async def client(session_factory):
    connector = _fake_connector()
    connectors.register(connector)

    async with session_factory() as db:
        await refresh_instruments(db, connector)
        await refresh_prices(db, connector)
        account = await accounts_svc.register_account(db, "FAKE", "main", "main")
        await accounts_svc.add_credential(db, connector, account, "HMAC", "api-key-1", "secret-1")
        base = f"/portfolio/{account.exchange}/{account.label}"

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_actor] = lambda: Actor(type="user", id="42", name="t")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.base = base  # type: ignore[attr-defined]
        c.session_factory = session_factory  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


async def test_requires_auth(session_factory):
    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/portfolio")).status_code == 401
    app.dependency_overrides.clear()


async def test_overview_empty_before_snapshot(client):
    body = (await client.get("/portfolio")).json()
    assert len(body) == 1
    assert Decimal(body[0]["total_value"]) == 0
    assert body[0]["taken_at"] is None


async def test_trigger_snapshot_then_holdings(client):
    resp = await client.post(f"{client.base}/snapshot")
    assert resp.status_code == 201
    body = resp.json()
    # Asset value = spot value (40000) + position notional (6200).
    assert Decimal(body["total_value"]) == Decimal("46200")
    assert {b["asset"] for b in body["balances"]} == {"USDT", "BTC"}
    assert body["positions"][0]["universal_symbol"] == "FAKE_PERP_ETHUSDT"
    assert Decimal(body["positions"][0]["value_quote"]) == Decimal("6200")

    # Overview now reflects the captured value.
    overview = (await client.get("/portfolio")).json()
    assert Decimal(overview[0]["total_value"]) == Decimal("46200")
    assert overview[0]["taken_at"] is not None

    # Holdings endpoint returns the same latest batch.
    holdings = (await client.get(client.base)).json()
    assert Decimal(holdings["total_value"]) == Decimal("46200")


async def test_snapshot_is_audited(client):
    await client.post(f"{client.base}/snapshot")
    async with client.session_factory() as db:
        rows = (await db.execute(select(AuditLog).where(AuditLog.action == "snapshots.capture"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "succeeded"
    assert rows[0].params == {"rows": 3}


async def test_history_after_two_snapshots(client):
    await client.post(f"{client.base}/snapshot")
    await client.post(f"{client.base}/snapshot")
    history = (await client.get(f"{client.base}/history")).json()
    assert len(history) == 2
    assert all(Decimal(point["total_value"]) == Decimal("46200") for point in history)


async def test_holdings_unknown_account_404(client):
    assert (await client.get("/portfolio/FAKE/ghost")).status_code == 404
