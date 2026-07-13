"""Account registry and credential lifecycle against the fake connector."""
import pytest
from sqlalchemy import select

from app.connectors.fake import FakeConnector
from app.core.crypto import SecretBox
from app.models.account import Account, ApiCredential
from app.services import accounts as svc


@pytest.fixture
def connector():
    return FakeConnector()


async def make_main(db, exchange="FAKE", label="main"):
    return await svc.register_account(db, exchange, label, "main")


async def test_register_main_and_sub(db):
    main = await make_main(db)
    assert main.exchange == "FAKE"
    assert main.account_type == "main"

    sub = await svc.register_account(db, "FAKE", "sub-1", "sub", parent_id=main.id)
    assert sub.parent_id == main.id


async def test_register_validation(db):
    with pytest.raises(svc.AccountError):
        await svc.register_account(db, "FAKE", "orphan", "sub")
    main = await make_main(db)
    with pytest.raises(svc.AccountError):
        await svc.register_account(db, "FAKE", "bad", "main", parent_id=main.id)


async def test_add_credential_verifies_and_encrypts(db, connector):
    main = await make_main(db)
    credential, info = await svc.add_credential(
        db, connector, main, "HMAC", "my-api-key", "my-secret"
    )
    assert info.valid
    assert credential.verified_at is not None
    assert credential.read_only is False
    # Secret is stored encrypted, round-trips through the box
    assert "my-secret" not in credential.secret_enc
    assert SecretBox().decrypt(credential.secret_enc) == "my-secret"
    # native uid reported by the venue is backfilled onto the account
    assert main.native_uid == "uid-1"


async def test_add_credential_rejected(db, connector):
    main = await make_main(db)
    with pytest.raises(svc.CredentialInvalid):
        await svc.add_credential(db, connector, main, "HMAC", "my-api-key", "wrong")
    rows = (await db.execute(select(ApiCredential))).scalars().all()
    assert rows == []


async def test_sync_subaccounts_idempotent(db, connector):
    main = await make_main(db)
    await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)
    await connector.create_subaccount({}, "alpha")
    await connector.create_subaccount({}, "beta")

    assert await svc.sync_subaccounts(db, connector, main) == 2
    assert await svc.sync_subaccounts(db, connector, main) == 0

    subs = (
        (await db.execute(select(Account).where(Account.parent_id == main.id)))
        .scalars().all()
    )
    assert {s.label for s in subs} == {"alpha", "beta"}
    assert all(s.account_type == "sub" for s in subs)


async def test_create_subaccount_registers_locally(db, connector):
    main = await make_main(db)
    await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)

    sub = await svc.create_subaccount(db, connector, main, "gamma")
    assert sub.parent_id == main.id
    assert sub.native_uid in connector.subaccounts


async def test_create_sub_api_key_stores_encrypted(db, connector):
    main = await make_main(db)
    await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)
    sub = await svc.create_subaccount(db, connector, main, "delta")

    credential, secret = await svc.create_sub_api_key(
        db, connector, main, sub, ["read", "trade"]
    )
    assert credential.account_id == sub.id
    assert credential.read_only is False
    assert SecretBox().decrypt(credential.secret_enc) == secret


async def test_revoke_sub_key_hits_venue(db, connector):
    main = await make_main(db)
    await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)
    sub = await svc.create_subaccount(db, connector, main, "epsilon")
    credential, _ = await svc.create_sub_api_key(db, connector, main, sub, ["read"])

    await svc.revoke_credential(db, connector, credential, main_account=main)
    assert credential.status == "revoked"
    assert credential.api_key in connector.revoked_keys


async def test_revoke_main_key_local_only(db, connector):
    main = await make_main(db)
    credential, _ = await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)

    await svc.revoke_credential(db, connector, credential)
    assert credential.status == "revoked"
    assert connector.revoked_keys == []


async def test_account_tree(db, connector):
    main = await make_main(db)
    await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)
    await svc.create_subaccount(db, connector, main, "zeta")

    tree = await svc.load_account_tree(db)
    assert len(tree) == 1
    assert tree[0].label == "main"
    assert len(tree[0].credentials) == 1
    assert [c.label for c in tree[0].children] == ["zeta"]
