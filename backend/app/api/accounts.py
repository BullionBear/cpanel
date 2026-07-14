"""Account and API credential management.

Registration and the overview tree are exchange-agnostic. Venue-side
operations (subaccount sync/create, key create/revoke) are capability-gated:
GET /accounts/exchanges tells the frontend what each venue supports, so the
per-exchange pages render only the operations that actually exist there.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
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
    status: str
    verified_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_default(cls, v):
        return v or []


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
    # True when the venue exposes no provisioning at all (registration-only,
    # e.g. Binance): the operator brings keys/subaccounts made on the venue.
    registration_only: bool


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


class SubaccountCreate(BaseModel):
    label: str = Field(min_length=1, max_length=64)


class SubKeyCreate(BaseModel):
    sub_account_id: int
    permissions: list[str] = ["read"]
    tags: list[str] = []


class SubKeyCreated(BaseModel):
    credential: CredentialOut
    # Plaintext secret. Also retrievable later via the reveal endpoint.
    secret: str


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


# -- helpers ----------------------------------------------------------------

async def _get_account(db: AsyncSession, account_id: int) -> Account:
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
    return [AccountNode.model_validate(a) for a in await svc.load_account_tree(db)]


@router.get("/exchanges", response_model=list[ExchangeCapabilities])
async def exchange_capabilities():
    result = []
    for c in connectors.all():
        if not c.supports(Capability.CREDENTIAL_VERIFY):
            continue
        provisioning = any(
            c.supports(cap)
            for cap in (
                Capability.SUBACCOUNT_CREATE,
                Capability.API_KEY_CREATE,
                Capability.SUBACCOUNT_LIST,
            )
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
                registration_only=not provisioning,
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
    return AccountOut.model_validate(await _get_account(db, account.id))


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
        parent = await _get_account(db, body.parent_id)
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
            )
        except svc.CredentialInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except svc.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return AccountOut.model_validate(await _get_account(db, account.id))


@router.post("/{account_id}/credentials", response_model=CredentialOut, status_code=201)
async def add_credential(
    account_id: int,
    body: CredentialCreate,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _get_account(db, account_id)
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
                label=body.label, tags=body.tags,
            )
        except svc.CredentialInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return CredentialOut.model_validate(credential)


@router.post(
    "/{account_id}/credentials/{credential_id}/reveal", response_model=SecretOut
)
async def reveal_credential_secret(
    account_id: int,
    credential_id: int,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Decrypt and return a stored secret. Every reveal is audited."""
    account = await _get_account(db, account_id)
    credential = await db.get(ApiCredential, credential_id)
    if credential is None or credential.account_id != account.id:
        raise HTTPException(status_code=404, detail="Credential not found")
    async with audit(
        db, actor, "accounts.reveal_secret",
        target=f"{account.exchange}:{account.label}",
        params={"api_key": credential.api_key[:8] + "..."},
    ):
        secret = svc.reveal_secret(credential)
    return SecretOut(api_key=credential.api_key, secret=secret)


@router.put(
    "/{account_id}/credentials/{credential_id}/tags", response_model=CredentialOut
)
async def update_credential_tags(
    account_id: int,
    credential_id: int,
    body: TagsUpdate,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _get_account(db, account_id)
    credential = await db.get(ApiCredential, credential_id)
    if credential is None or credential.account_id != account.id:
        raise HTTPException(status_code=404, detail="Credential not found")
    async with audit(
        db, actor, "accounts.set_tags",
        target=f"{account.exchange}:{account.label}",
        params={"tags": body.tags},
    ):
        credential = await svc.set_credential_tags(db, credential, body.tags)
    return CredentialOut.model_validate(credential)


@router.post(
    "/{account_id}/credentials/{credential_id}/modify", response_model=CredentialOut
)
async def modify_credential(
    account_id: int,
    credential_id: int,
    body: KeyModify,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Change a subaccount key's permissions / read-only flag on the venue."""
    account = await _get_account(db, account_id)  # the subaccount
    credential = await db.get(ApiCredential, credential_id)
    if credential is None or credential.account_id != account.id:
        raise HTTPException(status_code=404, detail="Credential not found")
    connector = _get_connector(account.exchange)
    _require(connector, Capability.API_KEY_MODIFY)
    if account.parent_id is None:
        raise HTTPException(status_code=400, detail="Only subaccount keys can be modified")
    main_account = await _get_account(db, account.parent_id)
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


@router.post("/{account_id}/credentials/{credential_id}/revoke", status_code=204)
async def revoke_credential(
    account_id: int,
    credential_id: int,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _get_account(db, account_id)
    credential = await db.get(ApiCredential, credential_id)
    if credential is None or credential.account_id != account.id:
        raise HTTPException(status_code=404, detail="Credential not found")
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


# -- venue-side operations (capability-gated) ---------------------------------

@router.post("/{account_id}/subaccounts/sync", response_model=list[AccountOut])
async def sync_subaccounts(
    account_id: int,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _get_account(db, account_id)
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
        .where(Account.parent_id == account_id, Account.status != "removed")
        .options(selectinload(Account.credentials))
        .order_by(Account.label)
    )
    return [AccountOut.model_validate(a) for a in result.scalars()]


@router.post(
    "/{account_id}/subaccounts/{sub_id}/freeze", response_model=AccountOut
)
async def freeze_subaccount(
    account_id: int,
    sub_id: int,
    body: FreezeUpdate,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _get_account(db, account_id)
    connector = _get_connector(account.exchange)
    _require(connector, Capability.SUBACCOUNT_FREEZE)
    sub = await _get_account(db, sub_id)
    if sub.parent_id != account.id:
        raise HTTPException(status_code=400, detail="Subaccount does not belong to this account")
    async with audit(
        db, actor, "accounts.freeze_subaccount",
        target=f"{account.exchange}:{sub.label}",
        params={"frozen": body.frozen},
    ):
        try:
            sub = await svc.set_subaccount_frozen(db, connector, account, sub, body.frozen)
        except svc.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return AccountOut.model_validate(await _get_account(db, sub.id))


@router.post("/{account_id}/subaccounts", response_model=AccountOut, status_code=201)
async def create_subaccount(
    account_id: int,
    body: SubaccountCreate,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _get_account(db, account_id)
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
    return AccountOut.model_validate(await _get_account(db, sub.id))


@router.post("/{account_id}/api-keys", response_model=SubKeyCreated, status_code=201)
async def create_sub_api_key(
    account_id: int,
    body: SubKeyCreate,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    account = await _get_account(db, account_id)
    connector = _get_connector(account.exchange)
    _require(connector, Capability.API_KEY_CREATE)
    sub = await _get_account(db, body.sub_account_id)
    if sub.parent_id != account.id:
        raise HTTPException(status_code=400, detail="Subaccount does not belong to this account")
    async with audit(
        db, actor, "accounts.create_api_key",
        target=f"{account.exchange}:{sub.label}",
        params={"permissions": body.permissions, "tags": body.tags},
    ):
        try:
            credential, secret = await svc.create_sub_api_key(
                db, connector, account, sub, body.permissions, tags=body.tags
            )
        except svc.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return SubKeyCreated(credential=CredentialOut.model_validate(credential), secret=secret)
