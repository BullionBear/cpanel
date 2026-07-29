"""Account and API credential management.

Registration and the overview tree are exchange-agnostic. Venue-side
operations (subaccount sync/create, key create/revoke) are capability-gated:
GET /accounts/exchanges tells the frontend what each venue supports, so the
per-exchange pages render only the operations that actually exist there.
"""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.connectors.base import Capability, ExchangeConnector
from app.connectors.registry import connectors
from app.core.audit import Actor, audit, current_actor
from app.core.db import get_db
from app.models.account import Account, ApiCredential
from app.services import accounts as svc

router = APIRouter(
    prefix="/accounts", tags=["accounts"], dependencies=[Depends(current_actor)]
)


def _ips_from_permissions_blob(permissions: Any) -> list[str]:
    if not isinstance(permissions, dict):
        return []
    raw = permissions.get("ips")
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, str)]


# -- schemas ----------------------------------------------------------------

class CredentialOut(BaseModel):
    id: int
    scheme: str
    api_key: str
    label: str | None
    permissions: dict | None
    read_only: bool | None
    can_withdraw: bool | None
    tags: list[str] = []
    # IP allowlist mirrored from the venue (also nested under permissions.ips).
    ips: list[str] = []
    status: str
    verified_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_default(cls, v):
        return v or []

    @model_validator(mode="wrap")
    @classmethod
    def _populate_ips(cls, values: Any, handler):
        if hasattr(values, "permissions") and not isinstance(values, dict):
            ips = _ips_from_permissions_blob(values.permissions)
            obj = handler(values)
            return obj if obj.ips else obj.model_copy(update={"ips": ips})
        if isinstance(values, dict) and not values.get("ips"):
            values = {
                **values,
                "ips": _ips_from_permissions_blob(values.get("permissions")),
            }
        return handler(values)


class AccountOut(BaseModel):
    id: int
    exchange: str
    label: str  # the system-only nickname
    account_type: str
    parent_id: int | None
    native_uid: str | None
    status: str
    created_at: datetime
    credentials: list[CredentialOut] = []

    model_config = {"from_attributes": True}


class AccountNode(AccountOut):
    children: list[AccountOut] = []


class ExchangeCapabilities(BaseModel):
    exchange: str
    subaccount_list: bool
    subaccount_create: bool
    subaccount_freeze: bool
    api_key_create: bool
    api_key_modify: bool
    api_key_revoke: bool
    transfers: bool
    withdrawals: bool
    deposits: bool
    # True when the venue exposes no provisioning at all (registration-only,
    # e.g. Binance): the operator brings keys/subaccounts made on the venue.
    registration_only: bool
    # The venue's wallet structure, in display order (e.g. Binance
    # SPOT/FUNDING/FUTURES, Bybit UNIFIED/FUNDING).
    wallets: list[str] = []


class AccountCreate(BaseModel):
    exchange: str
    label: str = Field(min_length=1, max_length=128)
    account_type: str = Field(pattern="^(main|sub)$")
    parent_id: int | None = None
    native_uid: str | None = None


class CredentialCreate(BaseModel):
    scheme: str = Field(default="HMAC", pattern="^(HMAC|ED25519|RSA)$")
    api_key: str = Field(min_length=8)
    secret: str = Field(min_length=8)
    label: str | None = None
    tags: list[str] = []
    passphrase: str | None = None  # required for Bitget


class RegisterAccount(BaseModel):
    """Combined account + credential registration (the universal flow)."""
    exchange: str
    label: str = Field(min_length=1, max_length=128)  # nickname
    account_type: str = Field(default="main", pattern="^(main|sub)$")
    parent_id: int | None = None
    scheme: str = Field(default="HMAC", pattern="^(HMAC|ED25519|RSA)$")
    api_key: str = Field(min_length=8)
    secret: str = Field(min_length=8)
    tags: list[str] = []
    cred_label: str | None = None
    passphrase: str | None = None  # required for Bitget


class AccountRename(BaseModel):
    label: str = Field(min_length=1, max_length=128)


class SubaccountCreate(BaseModel):
    label: str = Field(min_length=1, max_length=64)


class SubKeyCreate(BaseModel):
    sub_label: str
    # Panel-level permission scope tokens (e.g. spot, derivatives, wallet).
    permissions: list[str] = ["derivatives"]
    read_only: bool = False
    ips: list[str] | None = None
    note: str | None = Field(default=None, max_length=200)
    tags: list[str] = []


class SubKeyCreated(BaseModel):
    credential: CredentialOut
    # Plaintext secret. Also retrievable later via the reveal endpoint.
    secret: str
    passphrase: str | None = None


class TagsUpdate(BaseModel):
    tags: list[str]


class KeyModify(BaseModel):
    permissions: list[str] | None = None
    read_only: bool | None = None


class FreezeUpdate(BaseModel):
    frozen: bool


class SecretOut(BaseModel):
    api_key: str
    secret: str
    passphrase: str | None = None


# -- helpers ----------------------------------------------------------------

async def _account_by_ref(db: AsyncSession, exchange: str, label: str) -> Account:
    """Resolve an account by its natural key (exchange + nickname)."""
    result = await db.execute(
        select(Account)
        .where(Account.exchange == exchange.upper(), Account.label == label)
        .options(selectinload(Account.credentials))
    )
    account = result.scalars().first()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


async def _subaccount_by_ref(db: AsyncSession, parent: Account, sub_label: str) -> Account:
    result = await db.execute(
        select(Account)
        .where(Account.parent_id == parent.id, Account.label == sub_label)
        .options(selectinload(Account.credentials))
    )
    sub = result.scalars().first()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subaccount not found")
    return sub


def _credential_by_key(account: Account, api_key: str) -> ApiCredential:
    for credential in account.credentials:
        if credential.api_key == api_key:
            return credential
    raise HTTPException(status_code=404, detail="Credential not found")


async def _load_account(db: AsyncSession, account_id: int) -> Account:
    """Reload an account with fresh credentials for building a response."""
    account = await db.get(
        Account,
        account_id,
        options=[selectinload(Account.credentials)],
        populate_existing=True,
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def _get_connector(exchange: str) -> ExchangeConnector:
    try:
        return connectors.get(exchange)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown exchange {exchange!r}")


def _require(connector: ExchangeConnector, capability: Capability) -> None:
    if not connector.supports(capability):
        raise HTTPException(
            status_code=409,
            detail=f"{connector.exchange} does not support {capability.value} via API",
        )


# -- overview and capabilities ------------------------------------------------

@router.get("/tree", response_model=list[AccountNode])
async def account_tree(db: AsyncSession = Depends(get_db)):
    # Fill permissions.ips for keys registered before allowlists were stored.
    await svc.backfill_missing_ip_allowlists(db, connectors.get)
    return [AccountNode.model_validate(a) for a in await svc.load_account_tree(db)]


@router.get("/exchanges", response_model=list[ExchangeCapabilities])
async def exchange_capabilities():
    result = []
    for c in connectors.all():
        if not c.supports(Capability.CREDENTIAL_VERIFY):
            continue
        # "Registration only" means the panel cannot provision keys/subaccounts
        # on the venue — the operator brings keys made on the venue. Listing
        # (sync) is a read and does not count as provisioning.
        provisioning = c.supports(Capability.SUBACCOUNT_CREATE) or c.supports(
            Capability.API_KEY_CREATE
        )
        result.append(
            ExchangeCapabilities(
                exchange=c.exchange,
                subaccount_list=c.supports(Capability.SUBACCOUNT_LIST),
                subaccount_create=c.supports(Capability.SUBACCOUNT_CREATE),
                subaccount_freeze=c.supports(Capability.SUBACCOUNT_FREEZE),
                api_key_create=c.supports(Capability.API_KEY_CREATE),
                api_key_modify=c.supports(Capability.API_KEY_MODIFY),
                api_key_revoke=c.supports(Capability.API_KEY_REVOKE),
                transfers=c.supports(Capability.TRANSFERS),
                withdrawals=c.supports(Capability.WITHDRAWALS),
                deposits=c.supports(Capability.DEPOSITS),
                registration_only=not provisioning,
                wallets=[w.value for w in c.wallets],
            )
        )
    return result


# -- registration -------------------------------------------------------------

@router.post("", response_model=AccountOut, status_code=201)
async def create_account(
    body: AccountCreate,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    _get_connector(body.exchange)  # reject unknown exchanges early
    async with audit(db, actor, "accounts.create", target=f"{body.exchange}:{body.label}"):
        try:
            account = await svc.register_account(
                db, body.exchange, body.label, body.account_type,
                parent_id=body.parent_id, native_uid=body.native_uid,
            )
        except svc.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return AccountOut.model_validate(await _load_account(db, account.id))


@router.patch("/{exchange}/{label}", response_model=AccountOut)
async def rename_account(
    exchange: str,
    label: str,
    body: AccountRename,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Change an account's nickname."""
    account = await _account_by_ref(db, exchange, label)
    async with audit(
        db, actor, "accounts.rename",
        target=f"{account.exchange}:{account.label}",
        params={"label": body.label},
    ):
        try:
            await svc.rename_account(db, account, body.label)
        except svc.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return AccountOut.model_validate(await _load_account(db, account.id))


@router.post("/register", response_model=AccountOut, status_code=201)
async def register_account(
    body: RegisterAccount,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Register an account together with its API key in one step."""
    connector = _get_connector(body.exchange)
    _require(connector, Capability.CREDENTIAL_VERIFY)
    if body.parent_id is not None:
        parent = await _load_account(db, body.parent_id)
        if parent.exchange.upper() != body.exchange.upper():
            raise HTTPException(status_code=400, detail="Parent belongs to a different exchange")
    async with audit(
        db, actor, "accounts.register",
        target=f"{body.exchange}:{body.label}",
        params={"scheme": body.scheme, "api_key": body.api_key[:8] + "...", "tags": body.tags},
    ):
        try:
            account, _, _ = await svc.register_account_with_credential(
                db, connector, body.exchange, body.label, body.account_type,
                body.scheme, body.api_key, body.secret,
                tags=body.tags, parent_id=body.parent_id, cred_label=body.cred_label,
                passphrase=body.passphrase,
            )
        except svc.CredentialInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except svc.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return AccountOut.model_validate(await _load_account(db, account.id))


@router.post("/{exchange}/{label}/credentials", response_model=CredentialOut, status_code=201)
async def add_credential(
    exchange: str,
    label: str,
    body: CredentialCreate,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _account_by_ref(db, exchange, label)
    connector = _get_connector(account.exchange)
    _require(connector, Capability.CREDENTIAL_VERIFY)
    async with audit(
        db, actor, "accounts.add_credential",
        target=f"{account.exchange}:{account.label}",
        params={"scheme": body.scheme, "api_key": body.api_key[:8] + "..."},
    ):
        try:
            credential, _ = await svc.add_credential(
                db, connector, account, body.scheme, body.api_key, body.secret,
                label=body.label, tags=body.tags, passphrase=body.passphrase,
            )
        except svc.CredentialInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return CredentialOut.model_validate(credential)


@router.post(
    "/{exchange}/{label}/credentials/{api_key}/reveal", response_model=SecretOut
)
async def reveal_credential_secret(
    exchange: str,
    label: str,
    api_key: str,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Decrypt and return a stored secret. Every reveal is audited."""
    account = await _account_by_ref(db, exchange, label)
    credential = _credential_by_key(account, api_key)
    async with audit(
        db, actor, "accounts.reveal_secret",
        target=f"{account.exchange}:{account.label}",
        params={"api_key": credential.api_key[:8] + "..."},
    ):
        secret = svc.reveal_secret(credential)
        passphrase = svc.reveal_passphrase(credential)
    return SecretOut(
        api_key=credential.api_key, secret=secret, passphrase=passphrase
    )


@router.put(
    "/{exchange}/{label}/credentials/{api_key}/tags", response_model=CredentialOut
)
async def update_credential_tags(
    exchange: str,
    label: str,
    api_key: str,
    body: TagsUpdate,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _account_by_ref(db, exchange, label)
    credential = _credential_by_key(account, api_key)
    async with audit(
        db, actor, "accounts.set_tags",
        target=f"{account.exchange}:{account.label}",
        params={"tags": body.tags},
    ):
        credential = await svc.set_credential_tags(db, credential, body.tags)
    return CredentialOut.model_validate(credential)


@router.post(
    "/{exchange}/{label}/credentials/{api_key}/modify", response_model=CredentialOut
)
async def modify_credential(
    exchange: str,
    label: str,
    api_key: str,
    body: KeyModify,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Change a subaccount key's permissions / read-only flag on the venue."""
    account = await _account_by_ref(db, exchange, label)  # the subaccount
    credential = _credential_by_key(account, api_key)
    connector = _get_connector(account.exchange)
    _require(connector, Capability.API_KEY_MODIFY)
    if account.parent_id is None:
        raise HTTPException(status_code=400, detail="Only subaccount keys can be modified")
    main_account = await _load_account(db, account.parent_id)
    async with audit(
        db, actor, "accounts.modify_api_key",
        target=f"{account.exchange}:{account.label}",
        params={"permissions": body.permissions, "read_only": body.read_only},
    ):
        try:
            credential = await svc.modify_sub_api_key(
                db, connector, main_account, credential,
                permissions=body.permissions, read_only=body.read_only,
            )
        except svc.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return CredentialOut.model_validate(credential)


@router.post("/{exchange}/{label}/credentials/{api_key}/revoke", status_code=204)
async def revoke_credential(
    exchange: str,
    label: str,
    api_key: str,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _account_by_ref(db, exchange, label)
    credential = _credential_by_key(account, api_key)
    connector = _get_connector(account.exchange)
    main_account = (
        await db.get(Account, account.parent_id) if account.parent_id else None
    )
    async with audit(
        db, actor, "accounts.revoke_credential",
        target=f"{account.exchange}:{account.label}",
        params={"api_key": credential.api_key[:8] + "..."},
    ):
        await svc.revoke_credential(db, connector, credential, main_account=main_account)


@router.delete("/{exchange}/{label}/credentials/{api_key}", status_code=204)
async def delete_credential(
    exchange: str,
    label: str,
    api_key: str,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Remove a stored credential from the panel (no venue call)."""
    account = await _account_by_ref(db, exchange, label)
    credential = _credential_by_key(account, api_key)
    async with audit(
        db, actor, "accounts.delete_credential",
        target=f"{account.exchange}:{account.label}",
        params={"api_key": credential.api_key[:8] + "..."},
    ):
        await svc.delete_credential(db, credential)


# -- venue-side operations (capability-gated) ---------------------------------

@router.post("/{exchange}/{label}/subaccounts/sync", response_model=list[AccountOut])
async def sync_subaccounts(
    exchange: str,
    label: str,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _account_by_ref(db, exchange, label)
    connector = _get_connector(account.exchange)
    _require(connector, Capability.SUBACCOUNT_LIST)
    async with audit(
        db, actor, "accounts.sync_subaccounts",
        target=f"{account.exchange}:{account.label}",
    ) as entry:
        try:
            created, removed = await svc.sync_subaccounts(db, connector, account)
        except svc.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        entry.params = {"created": created, "removed": removed}
    result = await db.execute(
        select(Account)
        .where(Account.parent_id == account.id, Account.status != "removed")
        .options(selectinload(Account.credentials))
        .order_by(Account.label)
    )
    return [AccountOut.model_validate(a) for a in result.scalars()]


@router.post(
    "/{exchange}/{label}/subaccounts/{sub_label}/freeze", response_model=AccountOut
)
async def freeze_subaccount(
    exchange: str,
    label: str,
    sub_label: str,
    body: FreezeUpdate,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _account_by_ref(db, exchange, label)
    connector = _get_connector(account.exchange)
    _require(connector, Capability.SUBACCOUNT_FREEZE)
    sub = await _subaccount_by_ref(db, account, sub_label)
    async with audit(
        db, actor, "accounts.freeze_subaccount",
        target=f"{account.exchange}:{sub.label}",
        params={"frozen": body.frozen},
    ):
        try:
            sub = await svc.set_subaccount_frozen(db, connector, account, sub, body.frozen)
        except svc.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return AccountOut.model_validate(await _load_account(db, sub.id))


@router.post("/{exchange}/{label}/subaccounts", response_model=AccountOut, status_code=201)
async def create_subaccount(
    exchange: str,
    label: str,
    body: SubaccountCreate,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _account_by_ref(db, exchange, label)
    connector = _get_connector(account.exchange)
    _require(connector, Capability.SUBACCOUNT_CREATE)
    async with audit(
        db, actor, "accounts.create_subaccount",
        target=f"{account.exchange}:{account.label}",
        params={"label": body.label},
    ):
        try:
            sub = await svc.create_subaccount(db, connector, account, body.label)
        except svc.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return AccountOut.model_validate(await _load_account(db, sub.id))


@router.post("/{exchange}/{label}/api-keys", response_model=SubKeyCreated, status_code=201)
async def create_sub_api_key(
    exchange: str,
    label: str,
    body: SubKeyCreate,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _account_by_ref(db, exchange, label)
    connector = _get_connector(account.exchange)
    _require(connector, Capability.API_KEY_CREATE)
    sub = await _subaccount_by_ref(db, account, body.sub_label)
    async with audit(
        db, actor, "accounts.create_api_key",
        target=f"{account.exchange}:{sub.label}",
        params={
            "permissions": body.permissions,
            "read_only": body.read_only,
            "ips": body.ips,
            "tags": body.tags,
        },
    ):
        try:
            credential, secret = await svc.create_sub_api_key(
                db, connector, account, sub, body.permissions,
                tags=body.tags, read_only=body.read_only, ips=body.ips, note=body.note,
            )
        except svc.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return SubKeyCreated(
        credential=CredentialOut.model_validate(credential),
        secret=secret,
        passphrase=svc.reveal_passphrase(credential),
    )
