"""API tests for account management, using the fake connector as a venue."""
import httpx
import pytest_asyncio
from sqlalchemy import select

from app.connectors.fake import FakeConnector
from app.connectors.registry import connectors
from app.core.audit import Actor, current_actor
from app.core.db import get_db
from app.main import app
from app.models.audit_log import AuditLog

TEST_ACTOR = Actor(type="user", id="42", name="tester")


@pytest_asyncio.fixture
async def fake():
    connector = FakeConnector()
    connectors.register(connector)
    yield connector
    connectors._connectors.pop("FAKE", None)


@pytest_asyncio.fixture
async def client(session_factory, fake):
    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_actor] = lambda: TEST_ACTOR
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def create_main(client) -> dict:
    response = await client.post(
        "/accounts", json={"exchange": "FAKE", "label": "main", "account_type": "main"}
    )
    assert response.status_code == 201
    return response.json()


async def add_key(client, account_id: int) -> dict:
    response = await client.post(
        f"/accounts/{account_id}/credentials",
        json={"scheme": "HMAC", "api_key": "k" * 16, "secret": "s" * 16},
    )
    assert response.status_code == 201
    return response.json()


async def test_requires_auth(session_factory):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/accounts/tree")).status_code == 401


async def test_create_account_unknown_exchange(client):
    response = await client.post(
        "/accounts", json={"exchange": "NOPE", "label": "x", "account_type": "main"}
    )
    assert response.status_code == 404


async def test_register_and_tree(client):
    main = await create_main(client)
    credential = await add_key(client, main["id"])
    assert credential["status"] == "active"
    assert "secret" not in credential  # never echoed back

    tree = (await client.get("/accounts/tree")).json()
    assert len(tree) == 1
    assert tree[0]["label"] == "main"
    assert tree[0]["native_uid"] == "uid-1"
    assert len(tree[0]["credentials"]) == 1


async def test_bad_credential_rejected(client):
    main = await create_main(client)
    response = await client.post(
        f"/accounts/{main['id']}/credentials",
        json={"scheme": "HMAC", "api_key": "k" * 16, "secret": "wrong-pad"},
    )
    assert response.status_code == 400


async def test_exchange_capabilities(client):
    caps = (await client.get("/accounts/exchanges")).json()
    fake_caps = next(c for c in caps if c["exchange"] == "FAKE")
    assert fake_caps["subaccount_create"] is True
    assert fake_caps["api_key_create"] is True


async def test_subaccount_create_and_sync(client, fake):
    main = await create_main(client)
    await add_key(client, main["id"])

    response = await client.post(
        f"/accounts/{main['id']}/subaccounts", json={"label": "alpha"}
    )
    assert response.status_code == 201
    assert response.json()["account_type"] == "sub"

    # A subaccount created directly on the venue appears after sync
    await fake.create_subaccount({}, "beta")
    synced = (await client.post(f"/accounts/{main['id']}/subaccounts/sync")).json()
    assert {s["label"] for s in synced} == {"alpha", "beta"}


async def test_sub_api_key_returns_secret_once(client):
    main = await create_main(client)
    await add_key(client, main["id"])
    sub = (
        await client.post(f"/accounts/{main['id']}/subaccounts", json={"label": "gamma"})
    ).json()

    response = await client.post(
        f"/accounts/{main['id']}/api-keys",
        json={"sub_account_id": sub["id"], "permissions": ["read", "trade"]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["secret"].startswith("fake-secret")
    assert body["credential"]["api_key"].startswith("fake-key")

    # The tree exposes only credential metadata, never the secret
    tree = (await client.get("/accounts/tree")).json()
    child = tree[0]["children"][0]
    assert len(child["credentials"]) == 1


async def test_revoke_credential_removes_it(client, fake):
    main = await create_main(client)
    credential = await add_key(client, main["id"])
    response = await client.post(
        f"/accounts/{main['id']}/credentials/{credential['id']}/revoke"
    )
    assert response.status_code == 204

    tree = (await client.get("/accounts/tree")).json()
    assert tree[0]["credentials"] == []


async def test_delete_credential(client):
    main = await create_main(client)
    credential = await add_key(client, main["id"])
    response = await client.delete(
        f"/accounts/{main['id']}/credentials/{credential['id']}"
    )
    assert response.status_code == 204

    tree = (await client.get("/accounts/tree")).json()
    assert tree[0]["credentials"] == []

    # Deleting a missing credential is a 404
    missing = await client.delete(f"/accounts/{main['id']}/credentials/999999")
    assert missing.status_code == 404


async def test_register_combined(client):
    response = await client.post(
        "/accounts/register",
        json={
            "exchange": "FAKE",
            "label": "combined",
            "account_type": "main",
            "api_key": "k" * 16,
            "secret": "s" * 16,
            "tags": ["spot", "primary"],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["label"] == "combined"
    assert body["native_uid"] == "uid-1"
    assert len(body["credentials"]) == 1
    assert body["credentials"][0]["tags"] == ["spot", "primary"]
    assert "secret" not in body["credentials"][0]


async def test_register_rejects_bad_key(client):
    response = await client.post(
        "/accounts/register",
        json={
            "exchange": "FAKE", "label": "bad", "account_type": "main",
            "api_key": "k" * 16, "secret": "wrong-pad",
        },
    )
    assert response.status_code == 400


async def test_rename_account(client):
    main = await create_main(client)
    response = await client.patch(f"/accounts/{main['id']}", json={"label": "renamed"})
    assert response.status_code == 200
    assert response.json()["label"] == "renamed"

    tree = (await client.get("/accounts/tree")).json()
    assert tree[0]["label"] == "renamed"


async def test_rename_duplicate_rejected(client):
    a = await create_main(client)
    await client.post(
        "/accounts", json={"exchange": "FAKE", "label": "other", "account_type": "main"}
    )
    response = await client.patch(f"/accounts/{a['id']}", json={"label": "other"})
    assert response.status_code == 400


async def test_reveal_secret_is_audited(client, session_factory):
    main = await create_main(client)
    cred = await add_key(client, main["id"])

    response = await client.post(
        f"/accounts/{main['id']}/credentials/{cred['id']}/reveal"
    )
    assert response.status_code == 200
    assert response.json()["secret"] == "s" * 16

    async with session_factory() as session:
        entries = (await session.execute(select(AuditLog))).scalars().all()
    reveals = [e for e in entries if e.action == "accounts.reveal_secret"]
    assert len(reveals) == 1
    # The plaintext secret must never land in the audit params
    for entry in entries:
        assert "s" * 16 not in str(entry.params)


async def test_update_tags(client):
    main = await create_main(client)
    cred = await add_key(client, main["id"])
    response = await client.put(
        f"/accounts/{main['id']}/credentials/{cred['id']}/tags",
        json={"tags": ["read-only", "eu"]},
    )
    assert response.status_code == 200
    assert response.json()["tags"] == ["read-only", "eu"]


async def test_modify_sub_key(client, fake):
    main = await create_main(client)
    await add_key(client, main["id"])
    sub = (
        await client.post(f"/accounts/{main['id']}/subaccounts", json={"label": "s1"})
    ).json()
    created = (
        await client.post(
            f"/accounts/{main['id']}/api-keys",
            json={"sub_account_id": sub["id"], "permissions": ["read", "trade"]},
        )
    ).json()
    cred_id = created["credential"]["id"]

    response = await client.post(
        f"/accounts/{sub['id']}/credentials/{cred_id}/modify",
        json={"read_only": True, "permissions": ["read"]},
    )
    assert response.status_code == 200
    assert response.json()["read_only"] is True
    assert len(fake.modified_keys) == 1


async def test_freeze_subaccount(client, fake):
    main = await create_main(client)
    await add_key(client, main["id"])
    sub = (
        await client.post(f"/accounts/{main['id']}/subaccounts", json={"label": "s2"})
    ).json()

    response = await client.post(
        f"/accounts/{main['id']}/subaccounts/{sub['id']}/freeze", json={"frozen": True}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "frozen"
    assert fake.frozen  # venue was told to freeze

    # A frozen sub is still visible in the tree (not removed)
    tree = (await client.get("/accounts/tree")).json()
    child = tree[0]["children"][0]
    assert child["status"] == "frozen"


async def test_capabilities_include_new_flags(client):
    caps = (await client.get("/accounts/exchanges")).json()
    fake_caps = next(c for c in caps if c["exchange"] == "FAKE")
    assert fake_caps["subaccount_freeze"] is True
    assert fake_caps["api_key_modify"] is True
    assert fake_caps["registration_only"] is False


async def test_actions_are_audited(client, session_factory):
    main = await create_main(client)
    await add_key(client, main["id"])

    async with session_factory() as session:
        actions = {
            a.action for a in (await session.execute(select(AuditLog))).scalars()
        }
    assert {"accounts.create", "accounts.add_credential"} <= actions

    # Secrets must never reach the audit log
    async with session_factory() as session:
        entries = (await session.execute(select(AuditLog))).scalars().all()
    for entry in entries:
        assert "s" * 16 not in str(entry.params)
