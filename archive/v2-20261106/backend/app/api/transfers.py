"""Transfer endpoints.

Creating a transfer starts its state machine immediately and returns the row
with whatever steps completed synchronously; the transfer-progress job keeps
driving the waiting steps. Cross-exchange transfers are irreversible on-chain
moves, so they demand an explicit ``confirm`` flag (the UI shows the
validation preview from ``/transfers/preview`` first) — one of the guardrails
from DESIGN.md, alongside the max-transfer-size cap enforced in the service.
"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import ExchangeConnector, NotSupported
from app.connectors.registry import connectors
from app.connectors.types import WalletType
from app.core.audit import Actor, audit, current_actor
from app.core.db import get_db
from app.models.account import Account
from app.services import transfers as svc
from app.services.accounts import AccountError

router = APIRouter(prefix="/transfers", tags=["transfers"], dependencies=[Depends(current_actor)])


# -- schemas ------------------------------------------------------------------

class TransferStepOut(BaseModel):
    seq: int
    name: str
    status: str
    detail: dict | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class TransferOut(BaseModel):
    id: int
    kind: str
    status: str
    stuck: bool
    asset: str
    amount: Decimal
    from_account_id: int
    to_account_id: int | None
    from_exchange: str
    to_exchange: str
    from_wallet: str | None
    to_wallet: str | None
    network: str | None
    address: str | None
    address_tag: str | None
    native_transfer_id: str | None
    native_withdrawal_id: str | None
    tx_id: str | None
    received_amount: Decimal | None
    fee: Decimal | None
    fee_asset: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    steps: list[TransferStepOut]

    model_config = {"from_attributes": True}


class TransferCreate(BaseModel):
    from_account_id: int
    to_account_id: int
    asset: str
    amount: Decimal
    # Intra-exchange wallet legs
    from_wallet: WalletType | None = None
    to_wallet: WalletType | None = None
    # Cross-exchange
    network: str | None = None
    # Required for cross-exchange transfers (irreversible on-chain move)
    confirm: bool = False


class TransferPreviewRequest(BaseModel):
    from_account_id: int
    to_account_id: int
    asset: str
    amount: Decimal
    network: str


class TransferPreviewOut(BaseModel):
    ok: bool
    problems: list[str]
    withdraw_min: str | None
    expected_fee: str | None
    deposit_enabled: bool | None
    deposit_via_master: bool


class FailRequest(BaseModel):
    reason: str


class WithdrawRequest(BaseModel):
    from_account_id: int
    asset: str
    amount: Decimal
    address: str
    tag: str | None = None
    network: str
    # Required: withdrawing to an external address is irreversible.
    confirm: bool = False


class WithdrawPreviewRequest(BaseModel):
    from_account_id: int
    asset: str
    amount: Decimal
    network: str


class WithdrawPreviewOut(BaseModel):
    ok: bool
    problems: list[str]
    withdraw_min: str | None
    expected_fee: str | None


class DepositAddressOut(BaseModel):
    asset: str
    network: str
    address: str
    tag: str | None


class DepositOut(BaseModel):
    native_id: str
    asset: str
    amount: Decimal
    status: str
    network: str | None
    tx_id: str | None
    credited_at: datetime | None

    model_config = {"from_attributes": True}


class AssetNetworkOut(BaseModel):
    network: str
    withdraw_enabled: bool
    deposit_enabled: bool
    withdraw_min: str
    withdraw_fee: str


class AvailableNetworksOut(BaseModel):
    asset: str
    networks: list[AssetNetworkOut]
    suggested_network: str | None


# -- helpers ------------------------------------------------------------------

async def _account(db: AsyncSession, account_id: int) -> Account:
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found")
    return account


def _connector(exchange: str) -> ExchangeConnector:
    try:
        return connectors.get(exchange)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown exchange {exchange!r}")


async def _transfer(db: AsyncSession, transfer_id: int):
    try:
        return await svc.get_transfer(db, transfer_id)
    except svc.TransferError:
        raise HTTPException(status_code=404, detail=f"Transfer {transfer_id} not found")


# -- endpoints ----------------------------------------------------------------

@router.get("", response_model=list[TransferOut])
async def list_transfers(
    exchange: str | None = None,
    account_id: int | None = None,
    status: str | None = None,
    stuck_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    rows = await svc.list_transfers(
        db,
        exchange=exchange,
        account_id=account_id,
        status=status,
        stuck_only=stuck_only,
        limit=limit,
    )
    return [TransferOut.model_validate(r) for r in rows]


# NOTE: fixed paths must be registered before /{transfer_id}.

@router.get("/networks", response_model=AvailableNetworksOut)
async def available_networks(
    asset: str,
    from_account_id: int | None = None,
    to_account_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List selectable networks derived from the venue(s).

    - ``from_account_id`` only: withdraw-enabled chains on the source.
    - ``to_account_id`` only: deposit-enabled chains on the destination.
    - Both: intersection (withdraw on source ∧ deposit on dest).

    ``suggested_network`` is the sole option or the cheapest fee — the
    operator still confirms before anything moves.
    """
    if from_account_id is None and to_account_id is None:
        raise HTTPException(
            status_code=422,
            detail="from_account_id or to_account_id is required",
        )
    from_account = await _account(db, from_account_id) if from_account_id else None
    to_account = await _account(db, to_account_id) if to_account_id else None
    try:
        result = await svc.list_available_networks(
            db,
            asset,
            from_account=from_account,
            source_connector=(
                _connector(from_account.exchange) if from_account else None
            ),
            to_account=to_account,
            dest_connector=_connector(to_account.exchange) if to_account else None,
        )
    except (NotSupported, svc.TransferError, AccountError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return AvailableNetworksOut(**result)


@router.get("/deposit-address", response_model=DepositAddressOut)
async def deposit_address(
    account_id: int,
    asset: str,
    network: str,
    db: AsyncSession = Depends(get_db),
):
    """Resolve an account's deposit address, fetched fresh from the venue."""
    account = await _account(db, account_id)
    try:
        resolved = await svc.get_deposit_address(
            db, _connector(account.exchange), account, asset, network
        )
    except (NotSupported, AccountError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return DepositAddressOut(
        asset=resolved.asset, network=resolved.network,
        address=resolved.address, tag=resolved.tag,
    )


@router.get("/deposits", response_model=list[DepositOut])
async def recent_deposits(
    account_id: int,
    asset: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """An account's recent deposits, straight from the venue."""
    account = await _account(db, account_id)
    try:
        deposits = await svc.list_recent_deposits(
            db, _connector(account.exchange), account, asset=asset
        )
    except (NotSupported, AccountError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [
        DepositOut(
            native_id=d.native_id, asset=d.asset, amount=d.amount,
            status=d.status.value, network=d.network, tx_id=d.tx_id,
            credited_at=d.credited_at,
        )
        for d in deposits
    ]


@router.get("/{transfer_id}", response_model=TransferOut)
async def get_transfer(transfer_id: int, db: AsyncSession = Depends(get_db)):
    return TransferOut.model_validate(await _transfer(db, transfer_id))


@router.post("/preview", response_model=TransferPreviewOut)
async def preview_transfer(
    body: TransferPreviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """Validate a cross-exchange transfer without moving anything."""
    from_account = await _account(db, body.from_account_id)
    to_account = await _account(db, body.to_account_id)
    if from_account.exchange == to_account.exchange:
        raise HTTPException(
            status_code=422,
            detail="Preview applies to cross-exchange transfers; intra transfers validate on submit",
        )
    try:
        preview = await svc.preview_cross_transfer(
            db,
            _connector(from_account.exchange),
            _connector(to_account.exchange),
            from_account,
            to_account,
            asset=body.asset,
            amount=body.amount,
            network=body.network,
        )
    except (NotSupported, svc.TransferError, AccountError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return TransferPreviewOut(**preview)


@router.post("", response_model=TransferOut)
async def create_transfer(
    body: TransferCreate,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    from_account = await _account(db, body.from_account_id)
    to_account = await _account(db, body.to_account_id)
    cross = from_account.exchange != to_account.exchange

    if cross and not body.confirm:
        raise HTTPException(
            status_code=422,
            detail="Cross-exchange transfers are irreversible; set confirm=true after reviewing the preview",
        )
    if cross and not body.network:
        raise HTTPException(status_code=422, detail="A cross-exchange transfer needs a network")

    async with audit(
        db, actor, "transfer.create",
        target=f"{from_account.exchange}:{from_account.label} -> "
               f"{to_account.exchange}:{to_account.label}",
        params={
            "asset": body.asset,
            "amount": str(body.amount),
            "kind": "cross" if cross else "intra",
            "network": body.network,
            "from_wallet": body.from_wallet.value if body.from_wallet else None,
            "to_wallet": body.to_wallet.value if body.to_wallet else None,
        },
    ) as entry:
        try:
            if cross:
                transfer = await svc.create_cross_transfer(
                    db,
                    _connector(from_account.exchange),
                    _connector(to_account.exchange),
                    from_account,
                    to_account,
                    asset=body.asset,
                    amount=body.amount,
                    network=body.network,
                )
            else:
                transfer = await svc.create_intra_transfer(
                    db,
                    _connector(from_account.exchange),
                    from_account,
                    to_account,
                    asset=body.asset,
                    amount=body.amount,
                    from_wallet=body.from_wallet,
                    to_wallet=body.to_wallet,
                )
        except (NotSupported, svc.TransferError, AccountError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        entry.params = {**(entry.params or {}), "transfer_id": transfer.id,
                        "status": transfer.status}
    return TransferOut.model_validate(await _transfer(db, transfer.id))


@router.post("/withdraw/preview", response_model=WithdrawPreviewOut)
async def preview_withdrawal(
    body: WithdrawPreviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """Source-side validation of an external withdrawal (nothing moves)."""
    account = await _account(db, body.from_account_id)
    try:
        preview = await svc.preview_withdrawal(
            db, _connector(account.exchange), account,
            asset=body.asset, amount=body.amount, network=body.network,
        )
    except (NotSupported, svc.TransferError, AccountError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return WithdrawPreviewOut(**preview)


@router.post("/withdraw", response_model=TransferOut)
async def create_withdrawal(
    body: WithdrawRequest,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Withdraw to an external address, tracked like any transfer."""
    account = await _account(db, body.from_account_id)
    if not body.confirm:
        raise HTTPException(
            status_code=422,
            detail="Withdrawals are irreversible; set confirm=true after reviewing the preview",
        )
    async with audit(
        db, actor, "transfer.withdraw",
        target=f"{account.exchange}:{account.label} -> {body.address}",
        params={
            "asset": body.asset,
            "amount": str(body.amount),
            "network": body.network,
            "address": body.address,
            "tag": body.tag,
        },
    ) as entry:
        try:
            transfer = await svc.create_withdrawal(
                db, _connector(account.exchange), account,
                asset=body.asset, amount=body.amount,
                address=body.address, network=body.network, tag=body.tag,
            )
        except (NotSupported, svc.TransferError, AccountError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        entry.params = {**(entry.params or {}), "transfer_id": transfer.id,
                        "status": transfer.status}
    return TransferOut.model_validate(await _transfer(db, transfer.id))


@router.post("/{transfer_id}/retry", response_model=TransferOut)
async def retry_transfer(
    transfer_id: int,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Retry a stuck transfer after verifying the venue-side outcome."""
    transfer = await _transfer(db, transfer_id)
    async with audit(db, actor, "transfer.retry", target=str(transfer_id)):
        try:
            transfer = await svc.retry_transfer(db, transfer, connectors.get)
        except svc.TransferError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    return TransferOut.model_validate(await _transfer(db, transfer_id))


@router.post("/{transfer_id}/fail", response_model=TransferOut)
async def fail_transfer(
    transfer_id: int,
    body: FailRequest,
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Mark a transfer failed in the panel (the venue side is not touched)."""
    transfer = await _transfer(db, transfer_id)
    async with audit(
        db, actor, "transfer.fail", target=str(transfer_id),
        params={"reason": body.reason},
    ):
        try:
            transfer = await svc.fail_transfer(db, transfer, body.reason)
        except svc.TransferError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    return TransferOut.model_validate(await _transfer(db, transfer_id))
