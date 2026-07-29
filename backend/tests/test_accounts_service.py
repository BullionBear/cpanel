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


async def test_bitget_credential_requires_and_stores_passphrase(db, connector):
    main = await make_main(db, exchange="BITGET", label="bg-main")
    with pytest.raises(svc.CredentialInvalid, match="passphrase"):
        await svc.add_credential(db, connector, main, "HMAC", "my-api-key", "my-secret")

    credential, _ = await svc.add_credential(
        db,
        connector,
        main,
        "HMAC",
        "my-api-key",
        "my-secret",
        passphrase="venue-pass",
    )
    assert credential.passphrase_enc is not None
    assert "venue-pass" not in credential.passphrase_enc
    decrypted = svc.decrypt_credential(credential)
    assert decrypted["passphrase"] == "venue-pass"
    assert svc.reveal_passphrase(credential) == "venue-pass"


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

    assert await svc.sync_subaccounts(db, connector, main) == (2, 0)
    assert await svc.sync_subaccounts(db, connector, main) == (0, 0)

    subs = (
        (await db.execute(select(Account).where(Account.parent_id == main.id)))
        .scalars().all()
    )
    assert {s.label for s in subs} == {"alpha", "beta"}
    assert all(s.account_type == "sub" and s.status == "active" for s in subs)


async def test_sync_marks_venue_deleted_subaccounts_removed(db, connector):
    main = await make_main(db)
    await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)
    sub = await svc.create_subaccount(db, connector, main, "doomed")
    credential, _ = await svc.create_sub_api_key(db, connector, main, sub, ["read"])

    # Deleted on the venue side (e.g. via the exchange web UI)
    connector.subaccounts.remove(sub.native_uid)

    assert await svc.sync_subaccounts(db, connector, main) == (0, 1)
    await db.refresh(sub)
    await db.refresh(credential)
    assert sub.status == "removed"
    assert credential.status == "revoked"

    # Removed subaccounts drop out of the tree...
    tree = await svc.load_account_tree(db)
    assert tree[0].children == []

    # ...and syncing again does not double-count them
    assert await svc.sync_subaccounts(db, connector, main) == (0, 0)

    # If the venue reports the uid again, the local row is revived
    connector.subaccounts.append(sub.native_uid)
    assert await svc.sync_subaccounts(db, connector, main) == (0, 0)
    await db.refresh(sub)
    assert sub.status == "active"


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


async def test_create_sub_api_key_passes_options(db, connector):
    main = await make_main(db)
    await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)
    sub = await svc.create_subaccount(db, connector, main, "theta")

    credential, _ = await svc.create_sub_api_key(
        db, connector, main, sub, ["spot", "wallet"],
        read_only=True, ips=["1.2.3.4"], note="ro key",
    )
    assert credential.read_only is True
    assert credential.label == "ro key"
    assert credential.permissions == {
        "granted": ["spot", "wallet"],
        "ips": ["1.2.3.4"],
    }

    created = connector.created_keys[-1]
    assert created["read_only"] is True
    assert created["ips"] == ["1.2.3.4"]
    assert created["note"] == "ro key"


async def test_modify_sub_api_key_preserves_ips(db, connector):
    main = await make_main(db)
    await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)
    sub = await svc.create_subaccount(db, connector, main, "iota")
    credential, _ = await svc.create_sub_api_key(
        db, connector, main, sub, ["spot"], ips=["1.2.3.4", "5.6.7.8"],
    )

    updated = await svc.modify_sub_api_key(
        db, connector, main, credential, permissions=["wallet"], read_only=True,
    )
    assert updated.permissions == {
        "granted": ["wallet"],
        "ips": ["1.2.3.4", "5.6.7.8"],
    }
    assert updated.read_only is True


async def test_sync_refreshes_sub_key_ip_allowlist(db, connector):
    main = await make_main(db)
    await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)
    sub = await svc.create_subaccount(db, connector, main, "kappa")
    credential, _ = await svc.create_sub_api_key(
        db, connector, main, sub, ["spot"], ips=["1.1.1.1"],
    )
    # Simulate a venue-side allowlist change.
    connector.created_keys[-1]["ips"] = ["9.9.9.9"]
    # Drop the stored field so refresh must repopulate it.
    credential.permissions = {"granted": ["spot"]}
    await db.commit()

    await svc.sync_subaccounts(db, connector, main)
    await db.refresh(credential)
    assert credential.permissions["ips"] == ["9.9.9.9"]


async def test_backfill_missing_ip_allowlists(db, connector):
    main = await make_main(db)
    await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)
    sub = await svc.create_subaccount(db, connector, main, "lambda")
    credential, _ = await svc.create_sub_api_key(
        db, connector, main, sub, ["spot"], ips=["8.8.8.8"],
    )
    credential.permissions = {"granted": ["spot"]}
    await db.commit()

    updated = await svc.backfill_missing_ip_allowlists(db, lambda _ex: connector)
    assert updated >= 1
    await db.refresh(credential)
    assert credential.permissions["ips"] == ["8.8.8.8"]


async def test_revoke_sub_key_hits_venue(db, connector):
    main = await make_main(db)
    await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)
    sub = await svc.create_subaccount(db, connector, main, "epsilon")
    credential, _ = await svc.create_sub_api_key(db, connector, main, sub, ["read"])
    cred_id, api_key = credential.id, credential.api_key

    await svc.revoke_credential(db, connector, credential, main_account=main)
    assert api_key in connector.revoked_keys
    # Revoking removes the credential from the panel entirely.
    assert await db.get(ApiCredential, cred_id) is None


async def test_revoke_main_key_local_only(db, connector):
    main = await make_main(db)
    credential, _ = await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)
    cred_id = credential.id

    await svc.revoke_credential(db, connector, credential)
    assert connector.revoked_keys == []
    assert await db.get(ApiCredential, cred_id) is None


async def test_delete_credential_local_only(db, connector):
    main = await make_main(db)
    credential, _ = await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)
    cred_id = credential.id

    await svc.delete_credential(db, credential)
    assert connector.revoked_keys == []
    assert await db.get(ApiCredential, cred_id) is None


async def test_account_tree(db, connector):
    main = await make_main(db)
    await svc.add_credential(db, connector, main, "HMAC", "k", "s" * 8)
    await svc.create_subaccount(db, connector, main, "zeta")

    tree = await svc.load_account_tree(db)
    assert len(tree) == 1
    assert tree[0].label == "main"
    assert len(tree[0].credentials) == 1
    assert [c.label for c in tree[0].children] == ["zeta"]
