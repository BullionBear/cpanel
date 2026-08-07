"""Debounced balance refresh, SSE hub, and portfolio refresh API."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import httpx
import pytest
import pytest_asyncio

from app.connectors.fake import FakeConnector
from app.connectors.registry import connectors
from app.connectors.types import (
    Balance,
    OrderSide,
    OrderType,
    Position,
    WalletType,
)
from app.core.audit import Actor, current_actor
from app.core.db import get_db
from app.core.events import BalanceUpdatedEvent, event_hub
from app.main import app
from app.services import accounts as accounts_svc
from app.services import balance_refresh
from app.services import history as history_svc
from app.services import orders as orders_svc
from app.services.symbols import refresh_instruments, refresh_prices


@pytest.fixture(autouse=True)
def _clean_hub_and_refresh():
    event_hub.clear()
    yield
    event_hub.clear()


@pytest_asyncio.fixture
async def configured(session_factory):
    balance_refresh.configure(session_factory)
    connectors.register(FakeConnector())
    yield session_factory
    await balance_refresh.shutdown()


async def _account(db, connector: FakeConnector, label: str = "main"):
    account = await accounts_svc.register_account(db, "FAKE", label, "main")
    await accounts_svc.add_credential(
        db, connector, account, "HMAC", "api-key-1", "secret-1"
    )
    return account


def test_event_hub_publish_delivers_to_subscribers():
    queue = event_hub.subscribe()
    event = BalanceUpdatedEvent(
        account_id=1,
        exchange="FAKE",
        label="main",
        reason="test",
        taken_at=datetime.now(timezone.utc),
    )
    event_hub.publish(event)
    assert queue.get_nowait() == event
    event_hub.unsubscribe(queue)


async def test_debounce_coalesces_to_one_capture(configured, monkeypatch):
    connector = FakeConnector()
    connector.balances = [
        Balance(
            wallet=WalletType.SPOT,
            asset="USDT",
            free=Decimal("100"),
            locked=Decimal("0"),
        )
    ]
    connectors.register(connector)

    async with configured() as db:
        account = await _account(db, connector)

    calls: list[str] = []

    async def _capture(db, conn, account, reason, **kwargs):
        calls.append(reason)
        return 1

    monkeypatch.setattr(balance_refresh, "capture_and_publish", _capture)

    balance_refresh.schedule_balance_refresh(account.id, "a", debounce_seconds=0.05)
    balance_refresh.schedule_balance_refresh(account.id, "b", debounce_seconds=0.05)
    balance_refresh.schedule_balance_refresh(account.id, "c", debounce_seconds=0.05)
    await asyncio.sleep(0.2)

    assert calls == ["c"]


async def test_capture_and_publish_emits_sse(configured):
    connector = FakeConnector()
    connector.balances = [
        Balance(
            wallet=WalletType.SPOT,
            asset="USDT",
            free=Decimal("50"),
            locked=Decimal("0"),
        )
    ]
    connectors.register(connector)
    queue = event_hub.subscribe()

    async with configured() as db:
        account = await _account(db, connector)
        written = await balance_refresh.capture_and_publish(
            db, connector, account, "manual"
        )

    assert written == 1
    event = queue.get_nowait()
    assert event.type == "balance.updated"
    assert event.account_id == account.id
    assert event.reason == "manual"
    assert event.taken_at is not None
    event_hub.unsubscribe(queue)


async def test_order_sync_fill_schedules_refresh(configured, monkeypatch):
    connector = FakeConnector()
    connectors.register(connector)
    scheduled: list[tuple[int, str]] = []

    def _schedule(account_id, reason, **kwargs):
        scheduled.append((account_id, reason))

    monkeypatch.setattr(orders_svc, "schedule_balance_refresh", _schedule)

    async with configured() as db:
        await refresh_instruments(db, connector)
        await refresh_prices(db, connector)
        account = await _account(db, connector)
        row = await orders_svc.place_order(
            db,
            connector,
            account,
            "FAKE_SPOT_BTCUSDT",
            OrderSide.BUY,
            OrderType.LIMIT,
            qty=Decimal("0.01"),
            price=Decimal("60000"),
        )
        connector.fill_order(row.native_order_id)
        changed = await orders_svc.sync_open_orders(db, connector, account)

    assert changed == 1
    assert scheduled == [(account.id, "order_sync")]


async def test_history_deposit_sync_schedules_refresh(configured, monkeypatch):
    connector = FakeConnector()
    connector.credit_deposit("USDT", Decimal("10"), tx_id="tx1")
    connectors.register(connector)
    scheduled: list[tuple[int, str]] = []

    def _schedule(account_id, reason, **kwargs):
        scheduled.append((account_id, reason))

    monkeypatch.setattr(
        "app.services.balance_refresh.schedule_balance_refresh", _schedule
    )

    async with configured() as db:
        account = await _account(db, connector)
        written = await history_svc.sync_stream(
            db, connector, account, "deposits", "account"
        )

    assert written == 1
    assert scheduled == [(account.id, "history_deposits")]


@pytest_asyncio.fixture
async def portfolio_client(session_factory):
    connector = FakeConnector()
    connector.balances = [
        Balance(
            wallet=WalletType.SPOT,
            asset="USDT",
            free=Decimal("10000"),
            locked=Decimal("0"),
        ),
        Balance(
            wallet=WalletType.SPOT,
            asset="BTC",
            free=Decimal("0.5"),
            locked=Decimal("0"),
        ),
    ]
    connector.positions = [
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
    connectors.register(connector)
    balance_refresh.configure(session_factory)

    async with session_factory() as db:
        await refresh_instruments(db, connector)
        await refresh_prices(db, connector)
        account = await accounts_svc.register_account(db, "FAKE", "main", "main")
        await accounts_svc.add_credential(
            db, connector, account, "HMAC", "api-key-1", "secret-1"
        )
        base = f"/portfolio/{account.exchange}/{account.label}"
        account_id = account.id

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_actor] = lambda: Actor(
        type="user", id="42", name="t"
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.base = base  # type: ignore[attr-defined]
        c.account_id = account_id  # type: ignore[attr-defined]
        c.session_factory = session_factory  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()
    await balance_refresh.shutdown()
    event_hub.clear()


async def test_refresh_returns_202_and_schedules(portfolio_client, monkeypatch):
    scheduled: list[tuple[int, str]] = []

    def _schedule(account_id, reason, **kwargs):
        scheduled.append((account_id, reason))

    monkeypatch.setattr(balance_refresh, "schedule_balance_refresh", _schedule)

    resp = await portfolio_client.post(f"{portfolio_client.base}/refresh")
    assert resp.status_code == 202
    body = resp.json()
    assert body["scheduled"] is True
    assert body["account_id"] == portfolio_client.account_id
    assert scheduled == [(portfolio_client.account_id, "manual")]


async def test_deprecated_snapshot_still_works_and_publishes(portfolio_client):
    queue = event_hub.subscribe()
    resp = await portfolio_client.post(f"{portfolio_client.base}/snapshot")
    assert resp.status_code == 201
    assert Decimal(resp.json()["total_value"]) == Decimal("40200")
    event = queue.get_nowait()
    assert event.account_id == portfolio_client.account_id
    assert event.reason == "manual_sync"
    event_hub.unsubscribe(queue)
