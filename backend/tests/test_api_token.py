"""Panel API key mint/revoke and auth acceptance tests."""
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.audit import Actor, current_actor, current_ui_actor
from app.core.db import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.audit_log import AuditLog
from app.models.panel_access_token import PanelAccessToken

UI_ACTOR = Actor(type="user", id="99", name="discord-user", auth_method="discord_oauth")


def _ui_token(sub: str = "99", name: str = "discord-user") -> str:
    return create_access_token(
        data={"sub": sub, "name": name, "type": "ui_access"},
        expires_delta=timedelta(days=1),
        prefix="ui_",
    )


@pytest_asyncio.fixture
async def client(session_factory):
    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.state.session_factory = session_factory
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    app.state.session_factory = None


@pytest_asyncio.fixture
async def ui_client(client, monkeypatch):
    """Authenticated Discord UI session for token management endpoints."""
    app.dependency_overrides[current_ui_actor] = lambda: UI_ACTOR
    app.dependency_overrides[current_actor] = lambda: UI_ACTOR
    yield client
    app.dependency_overrides.pop(current_ui_actor, None)
    app.dependency_overrides.pop(current_actor, None)


async def test_api_token_requires_auth(client):
    assert (await client.get("/auth/api-token")).status_code == 401
    assert (await client.post("/auth/api-token")).status_code == 401
    assert (await client.delete("/auth/api-token")).status_code == 401


async def test_create_get_delete_api_token(ui_client, session_factory):
    missing = await ui_client.get("/auth/api-token")
    assert missing.status_code == 404

    created = await ui_client.post("/auth/api-token")
    assert created.status_code == 200
    token = created.json()["access_token"]
    assert token.startswith("api_")

    fetched = await ui_client.get("/auth/api-token")
    assert fetched.status_code == 200
    assert fetched.json()["access_token"] == token

    async with session_factory() as session:
        rows = (await session.execute(select(PanelAccessToken))).scalars().all()
        assert len(rows) == 1
        assert rows[0].actor_id == UI_ACTOR.id

        audits = (await session.execute(select(AuditLog))).scalars().all()
        assert any(a.action == "auth.api_token.create" for a in audits)

    # Real api_ token authenticates against protected routes.
    app.dependency_overrides.pop(current_actor, None)
    me = await ui_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["id"] == UI_ACTOR.id

    app.dependency_overrides[current_ui_actor] = lambda: UI_ACTOR
    deleted = await ui_client.delete("/auth/api-token")
    assert deleted.status_code == 200

    app.dependency_overrides.pop(current_actor, None)
    rejected = await ui_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert rejected.status_code == 401


async def test_api_token_cannot_mint_with_api_key(client, session_factory):
    """Minting requires a Discord UI session, not an existing API key."""
    api_token = create_access_token(
        data={"sub": "99", "name": "bot", "type": "api_access"},
        expires_delta=timedelta(days=1),
        prefix="api_",
    )
    async with session_factory() as session:
        session.add(
            PanelAccessToken(
                actor_id="99",
                actor_name="bot",
                access_token=api_token,
                expired_at=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                )
                + timedelta(days=1),
            )
        )
        await session.commit()

    response = await client.post(
        "/auth/api-token",
        headers={"Authorization": f"Bearer {api_token}"},
    )
    assert response.status_code in (400, 401)


async def test_audit_scoped_to_actor(ui_client, session_factory):
    async with session_factory() as session:
        session.add(
            AuditLog(
                actor_type="user",
                actor_id="99",
                actor_name="discord-user",
                action="orders.place",
                target="BTC",
                status="succeeded",
                ip_address="1.2.3.4",
                user_agent="curl/8.0",
                auth_method="api_key",
                return_code=200,
            )
        )
        session.add(
            AuditLog(
                actor_type="user",
                actor_id="other",
                action="orders.place",
                status="succeeded",
                return_code=200,
            )
        )
        await session.commit()

    response = await ui_client.get("/audit")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["actor_id"] == "99"
    assert body[0]["ip_address"] == "1.2.3.4"
    assert body[0]["auth_method"] == "api_key"
    assert body[0]["return_code"] == 200
    assert body[0]["user_agent"] == "curl/8.0"


async def test_activity_middleware_records_request(client, session_factory):
    token = _ui_token()
    response = await client.get(
        "/symbols",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0 Chrome/120.0",
            "X-Forwarded-For": "203.0.113.9",
        },
    )
    assert response.status_code == 200

    async with session_factory() as session:
        rows = (
            await session.execute(select(AuditLog).where(AuditLog.action == "http.request"))
        ).scalars().all()
    assert len(rows) >= 1
    entry = rows[0]
    assert entry.ip_address == "203.0.113.9"
    assert entry.auth_method == "discord_oauth"
    assert entry.return_code == 200
    assert entry.target == "GET /symbols"
    assert "Chrome" in (entry.user_agent or "")
