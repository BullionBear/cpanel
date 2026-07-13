"""Account registry and credential management.

Secrets are envelope-encrypted at rest and decrypted only at the moment a
connector call needs to be signed. Credentials are verified against the
venue when registered; the venue's reported permissions are captured on the
credential row for display.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.connectors.base import Credential, ExchangeConnector
from app.connectors.types import CredentialInfo
from app.core.crypto import SecretBox
from app.models.account import Account, ApiCredential


class AccountError(Exception):
    pass


class CredentialInvalid(AccountError):
    pass


def decrypt_credential(credential: ApiCredential) -> Credential:
    """Decrypted mapping for connector calls. Use at signing time only."""
    return {
        "api_key": credential.api_key,
        "secret": SecretBox().decrypt(credential.secret_enc),
        "scheme": credential.scheme,
    }


async def get_active_credential(db: AsyncSession, account: Account) -> ApiCredential:
    result = await db.execute(
        select(ApiCredential)
        .where(ApiCredential.account_id == account.id, ApiCredential.status == "active")
        .order_by(ApiCredential.id)
    )
    credential = result.scalars().first()
    if credential is None:
        raise AccountError(f"Account {account.label!r} has no active credential")
    return credential


async def register_account(
    db: AsyncSession,
    exchange: str,
    label: str,
    account_type: str,
    parent_id: int | None = None,
    native_uid: str | None = None,
) -> Account:
    if account_type == "sub" and parent_id is None:
        raise AccountError("Subaccounts must reference a parent account")
    if account_type == "main" and parent_id is not None:
        raise AccountError("Main accounts cannot have a parent")
    account = Account(
        exchange=exchange.upper(),
        label=label,
        account_type=account_type,
        parent_id=parent_id,
        native_uid=native_uid,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


async def add_credential(
    db: AsyncSession,
    connector: ExchangeConnector,
    account: Account,
    scheme: str,
    api_key: str,
    secret: str,
    label: str | None = None,
) -> tuple[ApiCredential, CredentialInfo]:
    """Encrypt, verify against the venue, and store a credential."""
    info = await connector.verify_credential(
        {"api_key": api_key, "secret": secret, "scheme": scheme}
    )
    if not info.valid:
        raise CredentialInvalid("The venue rejected this API key")

    credential = ApiCredential(
        account_id=account.id,
        scheme=scheme,
        api_key=api_key,
        secret_enc=SecretBox().encrypt(secret),
        label=label,
        permissions=info.permissions,
        read_only=info.read_only,
        can_withdraw=info.can_withdraw,
        status="active",
        verified_at=datetime.now(timezone.utc),
    )
    db.add(credential)
    if info.native_uid and not account.native_uid:
        account.native_uid = info.native_uid
    await db.commit()
    await db.refresh(credential)
    return credential, info


async def sync_subaccounts(
    db: AsyncSession, connector: ExchangeConnector, account: Account
) -> tuple[int, int]:
    """Reconcile local subaccounts with the venue's list.

    New venue subaccounts are imported, previously removed ones that reappear
    are revived, and local subaccounts the venue no longer reports are marked
    removed (their credentials revoked locally — the venue destroyed them with
    the account). Returns (created, removed).
    """
    master = await get_active_credential(db, account)
    venue_subs = await connector.list_subaccounts(decrypt_credential(master))
    venue_uids = {s.native_uid for s in venue_subs}

    result = await db.execute(
        select(Account)
        .where(Account.parent_id == account.id)
        .options(selectinload(Account.credentials))
    )
    known = {a.native_uid: a for a in result.scalars() if a.native_uid}

    created = 0
    for sub in venue_subs:
        existing = known.get(sub.native_uid)
        if existing is None:
            db.add(
                Account(
                    exchange=account.exchange,
                    label=sub.label,
                    account_type="sub",
                    parent_id=account.id,
                    native_uid=sub.native_uid,
                )
            )
            created += 1
        elif existing.status != "active":
            existing.status = "active"

    removed = 0
    for uid, local in known.items():
        if uid in venue_uids or local.status == "removed":
            continue
        local.status = "removed"
        for credential in local.credentials:
            if credential.status == "active":
                credential.status = "revoked"
        removed += 1

    await db.commit()
    return created, removed


async def create_subaccount(
    db: AsyncSession, connector: ExchangeConnector, account: Account, label: str
) -> Account:
    """Create a subaccount on the venue and register it locally."""
    master = await get_active_credential(db, account)
    sub = await connector.create_subaccount(decrypt_credential(master), label)
    row = Account(
        exchange=account.exchange,
        label=sub.label,
        account_type="sub",
        parent_id=account.id,
        native_uid=sub.native_uid,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def create_sub_api_key(
    db: AsyncSession,
    connector: ExchangeConnector,
    main_account: Account,
    sub_account: Account,
    permissions: list[str],
    scheme: str = "HMAC",
) -> tuple[ApiCredential, str]:
    """Create an API key for a subaccount via the venue.

    Returns the stored credential and the plaintext secret — the only moment
    it exists outside the encrypted envelope. Callers show it once and drop it.
    """
    if not sub_account.native_uid:
        raise AccountError("Subaccount has no native uid; sync subaccounts first")
    master = await get_active_credential(db, main_account)
    from app.connectors.types import KeyScheme

    key = await connector.create_api_key(
        decrypt_credential(master),
        subaccount=sub_account.native_uid,
        scheme=KeyScheme(scheme),
        permissions=permissions,
    )
    credential = ApiCredential(
        account_id=sub_account.id,
        scheme=key.scheme.value,
        api_key=key.api_key,
        secret_enc=SecretBox().encrypt(key.secret),
        label="created by cpanel",
        permissions={"granted": key.permissions},
        read_only="trade" not in permissions,
        can_withdraw=False,
        status="active",
        verified_at=datetime.now(timezone.utc),
    )
    db.add(credential)
    await db.commit()
    await db.refresh(credential)
    return credential, key.secret


async def revoke_credential(
    db: AsyncSession,
    connector: ExchangeConnector,
    credential: ApiCredential,
    main_account: Account | None = None,
) -> None:
    """Revoke on the venue when supported (subaccount keys), always locally."""
    from app.connectors.base import Capability

    account = await db.get(Account, credential.account_id)
    if (
        connector.supports(Capability.API_KEY_REVOKE)
        and account is not None
        and account.account_type == "sub"
        and main_account is not None
    ):
        master = await get_active_credential(db, main_account)
        await connector.revoke_api_key(
            decrypt_credential(master),
            subaccount=account.native_uid,
            api_key=credential.api_key,
        )
    credential.status = "revoked"
    await db.commit()


async def load_account_tree(db: AsyncSession) -> list[Account]:
    """All active main accounts with active children and credential metadata."""
    active_children = Account.children.and_(Account.status == "active")
    result = await db.execute(
        select(Account)
        .where(Account.account_type == "main", Account.status == "active")
        .options(
            selectinload(Account.credentials),
            selectinload(active_children).selectinload(Account.credentials),
        )
        .order_by(Account.exchange, Account.label)
    )
    return list(result.scalars())
