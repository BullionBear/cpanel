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
    out: Credential = {
        "api_key": credential.api_key,
        "secret": SecretBox().decrypt(credential.secret_enc),
        "scheme": credential.scheme,
    }
    if credential.passphrase_enc:
        out["passphrase"] = SecretBox().decrypt(credential.passphrase_enc)
    return out


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


def _credential_payload(
    scheme: str, api_key: str, secret: str, passphrase: str | None = None
) -> Credential:
    payload: Credential = {"api_key": api_key, "secret": secret, "scheme": scheme}
    if passphrase:
        payload["passphrase"] = passphrase
    return payload


async def add_credential(
    db: AsyncSession,
    connector: ExchangeConnector,
    account: Account,
    scheme: str,
    api_key: str,
    secret: str,
    label: str | None = None,
    tags: list[str] | None = None,
    passphrase: str | None = None,
) -> tuple[ApiCredential, CredentialInfo]:
    """Encrypt, verify against the venue, and store a credential."""
    if account.exchange.upper() == "BITGET" and not passphrase:
        raise CredentialInvalid("Bitget API keys require a passphrase")
    info = await connector.verify_credential(
        _credential_payload(scheme, api_key, secret, passphrase)
    )
    if not info.valid:
        raise CredentialInvalid(info.reason or "The venue rejected this API key")

    credential = ApiCredential(
        account_id=account.id,
        scheme=scheme,
        api_key=api_key,
        secret_enc=SecretBox().encrypt(secret),
        passphrase_enc=SecretBox().encrypt(passphrase) if passphrase else None,
        label=label,
        permissions=info.permissions,
        read_only=info.read_only,
        can_withdraw=info.can_withdraw,
        tags=list(tags or []),
        status="active",
        verified_at=datetime.now(timezone.utc),
    )
    db.add(credential)
    if info.native_uid and not account.native_uid:
        account.native_uid = info.native_uid
    await db.commit()
    await db.refresh(credential)
    return credential, info


async def register_account_with_credential(
    db: AsyncSession,
    connector: ExchangeConnector,
    exchange: str,
    label: str,
    account_type: str,
    scheme: str,
    api_key: str,
    secret: str,
    tags: list[str] | None = None,
    parent_id: int | None = None,
    cred_label: str | None = None,
    passphrase: str | None = None,
) -> tuple[Account, ApiCredential, CredentialInfo]:
    """Register an account and its API key in a single step.

    The key is verified against the venue first; only if it is valid are the
    account and credential persisted together. The venue-native id is derived
    from verification and stored on the account.
    """
    if account_type == "sub" and parent_id is None:
        raise AccountError("Subaccounts must reference a parent account")
    if account_type == "main" and parent_id is not None:
        raise AccountError("Main accounts cannot have a parent")
    if exchange.upper() == "BITGET" and not passphrase:
        raise CredentialInvalid("Bitget API keys require a passphrase")

    info = await connector.verify_credential(
        _credential_payload(scheme, api_key, secret, passphrase)
    )
    if not info.valid:
        raise CredentialInvalid(info.reason or "The venue rejected this API key")

    account = Account(
        exchange=exchange.upper(),
        label=label,
        account_type=account_type,
        parent_id=parent_id,
        native_uid=info.native_uid,
    )
    db.add(account)
    await db.flush()

    credential = ApiCredential(
        account_id=account.id,
        scheme=scheme,
        api_key=api_key,
        secret_enc=SecretBox().encrypt(secret),
        passphrase_enc=SecretBox().encrypt(passphrase) if passphrase else None,
        label=cred_label,
        permissions=info.permissions,
        read_only=info.read_only,
        can_withdraw=info.can_withdraw,
        tags=list(tags or []),
        status="active",
        verified_at=datetime.now(timezone.utc),
    )
    db.add(credential)
    await db.commit()
    await db.refresh(account)
    await db.refresh(credential)
    return account, credential, info


async def rename_account(db: AsyncSession, account: Account, label: str) -> Account:
    """Change an account's nickname (system-only label)."""
    if label != account.label:
        clash = await db.execute(
            select(Account.id).where(
                Account.exchange == account.exchange,
                Account.label == label,
                Account.id != account.id,
            )
        )
        if clash.scalars().first() is not None:
            raise AccountError(
                f"Another {account.exchange} account already uses the nickname {label!r}"
            )
    account.label = label
    await db.commit()
    await db.refresh(account)
    return account


def reveal_secret(credential: ApiCredential) -> str:
    """Decrypt and return a stored secret. Callers MUST audit each reveal."""
    return SecretBox().decrypt(credential.secret_enc)


def reveal_passphrase(credential: ApiCredential) -> str | None:
    """Decrypt and return a stored passphrase, if any. Callers MUST audit."""
    if not credential.passphrase_enc:
        return None
    return SecretBox().decrypt(credential.passphrase_enc)


async def set_credential_tags(
    db: AsyncSession, credential: ApiCredential, tags: list[str]
) -> ApiCredential:
    credential.tags = list(tags)
    await db.commit()
    await db.refresh(credential)
    return credential


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
        elif existing.status == "removed":
            # Revive a previously-removed sub; leave a frozen one frozen.
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
    try:
        sub = await connector.create_subaccount(decrypt_credential(master), label)
    except AccountError:
        raise
    except Exception as exc:
        # Venue SDKs raise their own types (GateApiException, …); surface the
        # clean message as a client error instead of a 500.
        message = getattr(exc, "message", None) or str(exc)
        raise AccountError(str(message).strip() or "Venue rejected subaccount creation") from exc
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
    tags: list[str] | None = None,
    read_only: bool = False,
    ips: list[str] | None = None,
    note: str | None = None,
) -> tuple[ApiCredential, str]:
    """Create an API key for a subaccount via the venue.

    ``permissions`` are panel-level scope tokens; ``read_only`` marks the key
    non-trading, ``ips`` restricts it to an allowlist, and ``note`` is a
    free-form venue label. Returns the stored credential and the plaintext
    secret — the only moment it exists outside the encrypted envelope.
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
        read_only=read_only,
        ips=ips,
        note=note,
    )
    credential = ApiCredential(
        account_id=sub_account.id,
        scheme=key.scheme.value,
        api_key=key.api_key,
        secret_enc=SecretBox().encrypt(key.secret),
        passphrase_enc=(
            SecretBox().encrypt(key.passphrase) if key.passphrase else None
        ),
        label=note or "created by cpanel",
        permissions={"granted": key.permissions},
        read_only=read_only,
        can_withdraw=False,
        tags=list(tags or []),
        status="active",
        verified_at=datetime.now(timezone.utc),
    )
    db.add(credential)
    await db.commit()
    await db.refresh(credential)
    return credential, key.secret


async def modify_sub_api_key(
    db: AsyncSession,
    connector: ExchangeConnector,
    main_account: Account,
    credential: ApiCredential,
    permissions: list[str] | None = None,
    read_only: bool | None = None,
) -> ApiCredential:
    """Change a subaccount key's permissions / read-only flag on the venue."""
    sub_account = await db.get(Account, credential.account_id)
    if sub_account is None or sub_account.account_type != "sub":
        raise AccountError("Only subaccount keys can be modified")
    if not sub_account.native_uid:
        raise AccountError("Subaccount has no native uid")
    master = await get_active_credential(db, main_account)
    await connector.modify_api_key(
        decrypt_credential(master),
        subaccount=sub_account.native_uid,
        api_key=credential.api_key,
        read_only=read_only,
        permissions=permissions,
    )
    if read_only is not None:
        credential.read_only = read_only
    if permissions is not None:
        credential.permissions = {"granted": permissions}
    await db.commit()
    await db.refresh(credential)
    return credential


async def set_subaccount_frozen(
    db: AsyncSession,
    connector: ExchangeConnector,
    main_account: Account,
    sub_account: Account,
    frozen: bool,
) -> Account:
    """Freeze/unfreeze a subaccount on the venue and mirror status locally."""
    if not sub_account.native_uid:
        raise AccountError("Subaccount has no native uid; sync subaccounts first")
    master = await get_active_credential(db, main_account)
    await connector.set_subaccount_frozen(
        decrypt_credential(master), sub_account.native_uid, frozen
    )
    sub_account.status = "frozen" if frozen else "active"
    await db.commit()
    await db.refresh(sub_account)
    return sub_account


async def revoke_credential(
    db: AsyncSession,
    connector: ExchangeConnector,
    credential: ApiCredential,
    main_account: Account | None = None,
) -> None:
    """Revoke on the venue when supported (subaccount keys), then remove locally.

    Revoking always deletes the stored credential — a revoked key is dead and is
    not kept around in the panel. Use :func:`delete_credential` to drop a key
    from the panel without touching the venue.
    """
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
    await db.delete(credential)
    await db.commit()


async def delete_credential(db: AsyncSession, credential: ApiCredential) -> None:
    """Remove a stored credential from the panel without touching the venue."""
    await db.delete(credential)
    await db.commit()


async def load_account_tree(db: AsyncSession) -> list[Account]:
    """Main accounts with their non-removed children and credential metadata."""
    active_children = Account.children.and_(Account.status != "removed")
    result = await db.execute(
        select(Account)
        .where(Account.account_type == "main", Account.status != "removed")
        .options(
            selectinload(Account.credentials),
            selectinload(active_children).selectinload(Account.credentials),
        )
        .order_by(Account.exchange, Account.label)
    )
    return list(result.scalars())
