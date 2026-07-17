"""Transfer orchestration: persisted state machines for fund movement.

Every transfer is a ``transfer`` row plus ordered ``transfer_step`` rows
(ARCHITECTURE.md "Transfer Orchestration"):

    intra:       submit → confirm
    cross:       resolve_address → validate → submit_withdrawal
                 → await_withdrawal → await_deposit
    withdrawal:  validate → submit_withdrawal → await_withdrawal
                 (external address — no destination account, no deposit match)

:func:`advance_transfer` is the engine. It walks the steps in order, marking
each ``running`` (committed *before* any venue call, so intent is durable)
and then ``succeeded`` / ``failed``. Waiting steps — the withdrawal and
deposit polls — stay ``running`` between polls and are re-driven by the
transfer-progress job until they resolve.

Failure model, by step type:

- **Money-moving steps** (``submit``, ``submit_withdrawal``): an exception
  after the call started is ambiguous — the venue may have accepted it. The
  transfer is flagged *stuck* for the operator (who verifies on the venue
  and retries or fails it) instead of auto-resubmitting. The same applies to
  a money-moving step found ``running`` on restart: it was interrupted
  mid-call and is never blindly re-executed.
- **Waiting steps**: exceptions are transient poll failures; recorded on the
  step and retried next tick.
- **Preparation steps** (resolve/validate): definitive validation problems
  raise :class:`TransferError` and fail the transfer; unexpected errors
  (venue briefly down) are recorded and retried.

A transfer with no state change for TRANSFER_STUCK_MINUTES is flagged stuck
by :func:`flag_stuck_transfers` — surfaced, but still polled.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.connectors.base import Capability, Credential, ExchangeConnector
from app.connectors.types import (
    Deposit,
    DepositStatus,
    WalletType,
    WithdrawalStatus,
)
from app.core.config import settings
from app.models.account import Account
from app.models.transfer import Transfer, TransferStep
from app.services.accounts import (
    AccountError,
    decrypt_credential,
    get_active_credential,
)
from app.services.snapshots import _price_map, _value_in_quote

logger = logging.getLogger(__name__)

__all__ = [
    "TransferError",
    "TransferValidationError",
    "TransferGuardrailError",
    "INTRA_STEPS",
    "CROSS_STEPS",
    "WITHDRAWAL_STEPS",
    "create_intra_transfer",
    "create_cross_transfer",
    "create_withdrawal",
    "preview_cross_transfer",
    "preview_withdrawal",
    "list_available_networks",
    "get_deposit_address",
    "list_recent_deposits",
    "advance_transfer",
    "retry_transfer",
    "fail_transfer",
    "flag_stuck_transfers",
    "get_transfer",
    "list_transfers",
]

INTRA_STEPS = ["submit", "confirm"]
CROSS_STEPS = [
    "resolve_address",
    "validate",
    "submit_withdrawal",
    "await_withdrawal",
    "await_deposit",
]
WITHDRAWAL_STEPS = ["validate", "submit_withdrawal", "await_withdrawal"]

# Steps whose venue call moves money: never auto-re-executed after an
# ambiguous failure or a crash mid-call.
_MONEY_MOVING_STEPS = {"submit", "submit_withdrawal"}
# Steps that poll for an asynchronous outcome and may legitimately wait.
_WAITING_STEPS = {"await_withdrawal", "await_deposit"}

# Sentinel a handler returns while its outcome is still pending.
_WAIT = object()


class TransferError(Exception):
    """Definitive transfer failure surfaced to the caller."""


class TransferValidationError(TransferError):
    """The request is invalid (accounts, wallets, asset/network pair, amount)."""


class TransferGuardrailError(TransferError):
    """The request is rejected by a configured guardrail."""


# -- helpers ------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; treat them as UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _wallet(value: str | None) -> WalletType | None:
    return WalletType(value) if value else None


async def _main_of(db: AsyncSession, account: Account) -> Account:
    if account.parent_id is None:
        return account
    parent = await db.get(Account, account.parent_id)
    if parent is None:
        raise TransferError(f"Account {account.label!r} has a missing parent account")
    return parent


async def _own_credential(db: AsyncSession, account: Account) -> Credential:
    return decrypt_credential(await get_active_credential(db, account))


async def _deposit_call_params(
    db: AsyncSession, account: Account
) -> tuple[Credential, str | None]:
    """Credential + subaccount arg for deposit-side calls.

    Prefer the account's own key; a keyless subaccount falls back to its
    master's key plus the sub's native uid (both venues expose sub deposit
    endpoints to the master).
    """
    try:
        return await _own_credential(db, account), None
    except AccountError:
        if account.account_type != "sub":
            raise
        if not account.native_uid:
            raise AccountError(
                f"Subaccount {account.label!r} has no native uid; sync subaccounts first"
            )
        main = await _main_of(db, account)
        return await _own_credential(db, main), account.native_uid


async def _intra_call_params(
    db: AsyncSession, from_account: Account, to_account: Account
) -> tuple[Credential, str | None, str | None]:
    """(credential, from_subaccount, to_subaccount) for an intra move.

    A same-account wallet move signs with the account's own key. A move
    between accounts must stay inside one hierarchy and signs with the
    master's key; a ``None`` subaccount side means the master itself.
    """
    if from_account.id == to_account.id:
        return await _own_credential(db, from_account), None, None

    main_from = await _main_of(db, from_account)
    main_to = await _main_of(db, to_account)
    if main_from.id != main_to.id:
        raise TransferValidationError(
            "Intra-exchange transfers must stay within one account hierarchy"
        )

    def sub_uid(account: Account, main: Account) -> str | None:
        if account.id == main.id:
            return None
        if not account.native_uid:
            raise TransferValidationError(
                f"Subaccount {account.label!r} has no native uid; sync subaccounts first"
            )
        return account.native_uid

    return (
        await _own_credential(db, main_from),
        sub_uid(from_account, main_from),
        sub_uid(to_account, main_to),
    )


async def check_transfer_guardrails(
    db: AsyncSession, exchange: str, asset: str, amount: Decimal
) -> None:
    """Enforce the max-transfer-size guardrail (valued in the quote asset)."""
    max_value = Decimal(str(settings.TRANSFER_MAX_VALUE or 0))
    if max_value <= 0:
        return
    prices = await _price_map(db, exchange.upper())
    value = _value_in_quote(asset, amount, prices)
    if value is None:
        raise TransferGuardrailError(
            f"Cannot enforce the max-transfer guardrail: no reference price for {asset}"
        )
    if value > max_value:
        raise TransferGuardrailError(
            f"Transfer value {value} exceeds the configured maximum ({max_value})"
        )


def _init_steps(transfer: Transfer, names: list[str]) -> None:
    for seq, name in enumerate(names):
        transfer.steps.append(TransferStep(seq=seq, name=name, status="pending"))


def _source_network_check(
    exchange: str, networks: list, network: str, asset: str, amount: Decimal
) -> tuple[object | None, list[str]]:
    """Withdrawal-side validation of one (asset, network) pair.

    Returns the matching network (or None) and the problems found.
    """
    src_net = next((n for n in networks if n.network == network), None)
    problems: list[str] = []
    if src_net is None:
        problems.append(f"{exchange} does not list {asset} on {network}")
    elif not src_net.withdraw_enabled:
        problems.append(f"{exchange} has {asset}/{network} withdrawals disabled")
    elif amount < src_net.withdraw_min:
        problems.append(
            f"Amount is below the {exchange} withdrawal minimum "
            f"({src_net.withdraw_min} {asset})"
        )
    return src_net, problems


def _serialize_network(
    network, *, deposit_enabled: bool | None = None
) -> dict:
    return {
        "network": network.network,
        "withdraw_enabled": network.withdraw_enabled,
        "deposit_enabled": (
            network.deposit_enabled if deposit_enabled is None else deposit_enabled
        ),
        "withdraw_min": str(network.withdraw_min),
        "withdraw_fee": str(network.withdraw_fee),
    }


def _suggest_network(networks: list[dict]) -> str | None:
    """Sole option, otherwise the cheapest withdraw fee."""
    if not networks:
        return None
    if len(networks) == 1:
        return networks[0]["network"]
    return min(networks, key=lambda n: Decimal(n["withdraw_fee"]))["network"]


async def list_available_networks(
    db: AsyncSession,
    asset: str,
    *,
    from_account: Account | None = None,
    source_connector: ExchangeConnector | None = None,
    to_account: Account | None = None,
    dest_connector: ExchangeConnector | None = None,
) -> dict:
    """Derive selectable networks for withdrawal and/or deposit.

    - Source only: withdraw-enabled chains on ``from_account``.
    - Destination only: deposit-enabled chains on ``to_account``.
    - Both: intersection (withdraw on source ∧ deposit on dest), matching
      venue network codes exactly.

    ``suggested_network`` is the only option, or the lowest-fee option among
    several — never auto-submitted; the operator still confirms.
    """
    asset = asset.upper()
    if from_account is None and to_account is None:
        raise TransferValidationError("from_account_id or to_account_id is required")

    source_nets: list = []
    if from_account is not None:
        if source_connector is None:
            raise TransferValidationError("source connector is required")
        source_connector.require(Capability.WITHDRAWALS)
        src_cred = await _own_credential(db, from_account)
        source_nets = await source_connector.fetch_asset_networks(src_cred, asset)

    dest_nets: list = []
    if to_account is not None:
        if dest_connector is None:
            raise TransferValidationError("destination connector is required")
        dest_connector.require(Capability.DEPOSITS)
        dest_cred, _ = await _deposit_call_params(db, to_account)
        dest_nets = await dest_connector.fetch_asset_networks(dest_cred, asset)

    if from_account is not None and to_account is not None:
        dest_by_net = {n.network.upper(): n for n in dest_nets}
        networks = []
        for src in source_nets:
            if not src.withdraw_enabled:
                continue
            dest = dest_by_net.get(src.network.upper())
            if dest is None or not dest.deposit_enabled:
                continue
            networks.append(_serialize_network(src, deposit_enabled=True))
    elif from_account is not None:
        networks = [
            _serialize_network(n)
            for n in source_nets
            if n.withdraw_enabled
        ]
    else:
        networks = [
            _serialize_network(n)
            for n in dest_nets
            if n.deposit_enabled
        ]

    networks.sort(key=lambda n: (Decimal(n["withdraw_fee"]), n["network"]))
    return {
        "asset": asset,
        "networks": networks,
        "suggested_network": _suggest_network(networks),
    }


# -- creation -----------------------------------------------------------------

async def create_intra_transfer(
    db: AsyncSession,
    connector: ExchangeConnector,
    from_account: Account,
    to_account: Account,
    asset: str,
    amount: Decimal,
    from_wallet: WalletType | None = None,
    to_wallet: WalletType | None = None,
) -> Transfer:
    """Create and start a main↔sub / wallet↔wallet transfer. Returns the row."""
    connector.require(Capability.TRANSFERS)
    if amount <= 0:
        raise TransferValidationError("Transfer amount must be positive")
    if from_account.exchange != to_account.exchange:
        raise TransferValidationError(
            "Accounts are on different exchanges; use a cross-exchange transfer"
        )
    # Wallet structure differs per venue (Binance: SPOT/FUNDING/FUTURES,
    # Bybit: UNIFIED/FUNDING) — reject wallets the exchange does not have.
    for wallet in (from_wallet, to_wallet):
        if wallet is not None and wallet not in connector.wallets:
            supported = ", ".join(w.value for w in connector.wallets)
            raise TransferValidationError(
                f"{from_account.exchange} has no {wallet.value} wallet"
                f" (available: {supported})"
            )
    if from_account.id == to_account.id and (
        from_wallet is None or to_wallet is None or from_wallet == to_wallet
    ):
        raise TransferValidationError(
            "A same-account transfer needs two different wallets"
        )
    # Resolve now so hierarchy/credential problems fail fast, before any row.
    await _intra_call_params(db, from_account, to_account)
    await check_transfer_guardrails(db, from_account.exchange, asset, amount)

    transfer = Transfer(
        kind="intra",
        status="in_progress",
        asset=asset.upper(),
        amount=amount,
        from_account_id=from_account.id,
        to_account_id=to_account.id,
        from_exchange=from_account.exchange,
        to_exchange=to_account.exchange,
        from_wallet=from_wallet.value if from_wallet else None,
        to_wallet=to_wallet.value if to_wallet else None,
        updated_at=_utcnow(),
    )
    _init_steps(transfer, INTRA_STEPS)
    db.add(transfer)
    await db.commit()

    transfer = await get_transfer(db, transfer.id)
    return await advance_transfer(db, transfer, lambda _: connector)


async def create_cross_transfer(
    db: AsyncSession,
    source_connector: ExchangeConnector,
    dest_connector: ExchangeConnector,
    from_account: Account,
    to_account: Account,
    asset: str,
    amount: Decimal,
    network: str,
) -> Transfer:
    """Create and start a cross-exchange transfer (withdrawal + deposit)."""
    source_connector.require(Capability.WITHDRAWALS)
    dest_connector.require(Capability.DEPOSITS)
    if amount <= 0:
        raise TransferValidationError("Transfer amount must be positive")
    if from_account.exchange == to_account.exchange:
        raise TransferValidationError(
            "Accounts are on the same exchange; use an intra-exchange transfer"
        )
    if not network:
        raise TransferValidationError("A cross-exchange transfer needs a network")
    # Fail fast on missing credentials before persisting anything.
    await _own_credential(db, from_account)
    await _deposit_call_params(db, to_account)
    await check_transfer_guardrails(db, from_account.exchange, asset, amount)

    transfer = Transfer(
        kind="cross",
        status="in_progress",
        asset=asset.upper(),
        amount=amount,
        from_account_id=from_account.id,
        to_account_id=to_account.id,
        from_exchange=from_account.exchange,
        to_exchange=to_account.exchange,
        network=network.upper(),
        updated_at=_utcnow(),
    )
    _init_steps(transfer, CROSS_STEPS)
    db.add(transfer)
    await db.commit()

    def get_connector(exchange: str) -> ExchangeConnector:
        return source_connector if exchange == from_account.exchange else dest_connector

    transfer = await get_transfer(db, transfer.id)
    return await advance_transfer(db, transfer, get_connector)


async def preview_cross_transfer(
    db: AsyncSession,
    source_connector: ExchangeConnector,
    dest_connector: ExchangeConnector,
    from_account: Account,
    to_account: Account,
    asset: str,
    amount: Decimal,
    network: str,
) -> dict:
    """Validate a cross-exchange transfer without moving anything.

    Returns the per-side network facts plus every problem found, so the UI
    can show a confirmation preview before the operator commits.
    """
    source_connector.require(Capability.WITHDRAWALS)
    dest_connector.require(Capability.DEPOSITS)
    asset = asset.upper()
    network = network.upper()

    problems: list[str] = []
    try:
        await check_transfer_guardrails(db, from_account.exchange, asset, amount)
    except TransferGuardrailError as exc:
        problems.append(str(exc))

    src_cred = await _own_credential(db, from_account)
    dest_cred, dest_sub = await _deposit_call_params(db, to_account)

    src_net, src_problems = _source_network_check(
        from_account.exchange,
        await source_connector.fetch_asset_networks(src_cred, asset),
        network, asset, amount,
    )
    problems.extend(src_problems)

    dest_net = next(
        (
            n for n in await dest_connector.fetch_asset_networks(dest_cred, asset)
            if n.network == network
        ),
        None,
    )
    if dest_net is None:
        problems.append(f"{to_account.exchange} does not list {asset} on {network}")
    elif not dest_net.deposit_enabled:
        problems.append(f"{to_account.exchange} has {asset}/{network} deposits disabled")

    fee = src_net.withdraw_fee if src_net else None
    return {
        "ok": not problems,
        "problems": problems,
        "withdraw_min": str(src_net.withdraw_min) if src_net else None,
        "expected_fee": str(fee) if fee is not None else None,
        "deposit_enabled": dest_net.deposit_enabled if dest_net else None,
        "deposit_via_master": dest_sub is not None,
    }


async def create_withdrawal(
    db: AsyncSession,
    connector: ExchangeConnector,
    from_account: Account,
    asset: str,
    amount: Decimal,
    address: str,
    network: str,
    tag: str | None = None,
) -> Transfer:
    """Create and start a withdrawal to an external address.

    Tracked like any transfer (validate → submit → poll until the venue
    reports it sent), but with no destination account: what happens after the
    funds leave the venue is outside the panel's sight.
    """
    connector.require(Capability.WITHDRAWALS)
    if amount <= 0:
        raise TransferValidationError("Withdrawal amount must be positive")
    if not address or not address.strip():
        raise TransferValidationError("A withdrawal needs a destination address")
    if not network:
        raise TransferValidationError("A withdrawal needs a network")
    await _own_credential(db, from_account)  # fail fast on missing credential
    await check_transfer_guardrails(db, from_account.exchange, asset, amount)

    transfer = Transfer(
        kind="withdrawal",
        status="in_progress",
        asset=asset.upper(),
        amount=amount,
        from_account_id=from_account.id,
        to_account_id=None,
        from_exchange=from_account.exchange,
        to_exchange="EXTERNAL",
        network=network.upper(),
        address=address.strip(),
        address_tag=tag or None,
        updated_at=_utcnow(),
    )
    _init_steps(transfer, WITHDRAWAL_STEPS)
    db.add(transfer)
    await db.commit()

    transfer = await get_transfer(db, transfer.id)
    return await advance_transfer(db, transfer, lambda _: connector)


async def preview_withdrawal(
    db: AsyncSession,
    connector: ExchangeConnector,
    from_account: Account,
    asset: str,
    amount: Decimal,
    network: str,
) -> dict:
    """Source-side validation of an external withdrawal, without moving anything."""
    connector.require(Capability.WITHDRAWALS)
    asset = asset.upper()
    network = network.upper()

    problems: list[str] = []
    try:
        await check_transfer_guardrails(db, from_account.exchange, asset, amount)
    except TransferGuardrailError as exc:
        problems.append(str(exc))

    credential = await _own_credential(db, from_account)
    src_net, src_problems = _source_network_check(
        from_account.exchange,
        await connector.fetch_asset_networks(credential, asset),
        network, asset, amount,
    )
    problems.extend(src_problems)
    return {
        "ok": not problems,
        "problems": problems,
        "withdraw_min": str(src_net.withdraw_min) if src_net else None,
        "expected_fee": str(src_net.withdraw_fee) if src_net else None,
    }


# -- deposit side (receiving funds from outside the panel) ---------------------

async def get_deposit_address(
    db: AsyncSession,
    connector: ExchangeConnector,
    account: Account,
    asset: str,
    network: str,
):
    """Resolve the account's deposit address for (asset, network) — fetched
    fresh from the venue, never cached."""
    connector.require(Capability.DEPOSITS)
    credential, sub = await _deposit_call_params(db, account)
    return await connector.fetch_deposit_address(
        credential, asset.upper(), network.upper(), subaccount=sub
    )


async def list_recent_deposits(
    db: AsyncSession,
    connector: ExchangeConnector,
    account: Account,
    asset: str | None = None,
) -> list:
    """The account's recent deposits, straight from the venue."""
    connector.require(Capability.DEPOSITS)
    credential, sub = await _deposit_call_params(db, account)
    return await connector.fetch_deposits(
        credential, asset=asset.upper() if asset else None, subaccount=sub
    )


# -- step handlers --------------------------------------------------------------

async def _accounts(db: AsyncSession, transfer: Transfer) -> tuple[Account, Account | None]:
    """(source account, destination account). The destination is None for
    plain withdrawals; steps that need one only run for cross transfers."""
    from_account = await db.get(Account, transfer.from_account_id)
    if from_account is None:
        raise TransferError("The transfer's source account no longer exists")
    to_account = None
    if transfer.to_account_id is not None:
        to_account = await db.get(Account, transfer.to_account_id)
        if to_account is None:
            raise TransferError("The transfer's destination account no longer exists")
    return from_account, to_account


async def _step_submit(db, transfer, step, get_connector):
    connector = get_connector(transfer.from_exchange)
    connector.require(Capability.TRANSFERS)
    from_account, to_account = await _accounts(db, transfer)
    credential, from_sub, to_sub = await _intra_call_params(db, from_account, to_account)
    result = await connector.transfer(
        credential,
        transfer.asset,
        transfer.amount,
        from_wallet=_wallet(transfer.from_wallet),
        to_wallet=_wallet(transfer.to_wallet),
        from_subaccount=from_sub,
        to_subaccount=to_sub,
    )
    if result.status == "failed":
        raise TransferError("The venue reported the transfer failed")
    transfer.native_transfer_id = result.native_transfer_id
    step.detail = {"native_transfer_id": result.native_transfer_id, "status": result.status}


async def _step_confirm(db, transfer, step, get_connector):
    # Both venues' transfer endpoints answer synchronously — a returned id is
    # a completed move — so confirmation just finalizes the submit result.
    # Kept as its own step for the requested → submitted → confirmed trail.
    submit = next(s for s in transfer.steps if s.name == "submit")
    step.detail = {"confirmed_from": (submit.detail or {}).get("status", "submitted")}


async def _step_resolve_address(db, transfer, step, get_connector):
    connector = get_connector(transfer.to_exchange)
    connector.require(Capability.DEPOSITS)
    _, to_account = await _accounts(db, transfer)
    if to_account is None:
        raise TransferError("Address resolution needs a destination account")
    credential, sub = await _deposit_call_params(db, to_account)
    resolved = await connector.fetch_deposit_address(
        credential, transfer.asset, transfer.network, subaccount=sub
    )
    transfer.address = resolved.address
    transfer.address_tag = resolved.tag
    step.detail = {"address": resolved.address, "tag": resolved.tag}


async def _step_validate(db, transfer, step, get_connector):
    source = get_connector(transfer.from_exchange)
    from_account, to_account = await _accounts(db, transfer)
    src_cred = await _own_credential(db, from_account)

    src_net, problems = _source_network_check(
        transfer.from_exchange,
        await source.fetch_asset_networks(src_cred, transfer.asset),
        transfer.network, transfer.asset, transfer.amount,
    )
    if problems:
        raise TransferValidationError("; ".join(problems))

    # Plain withdrawals have no destination account to validate.
    if to_account is not None:
        dest = get_connector(transfer.to_exchange)
        dest_cred, _ = await _deposit_call_params(db, to_account)
        dest_net = next(
            (
                n for n in await dest.fetch_asset_networks(dest_cred, transfer.asset)
                if n.network == transfer.network
            ),
            None,
        )
        if dest_net is None or not dest_net.deposit_enabled:
            raise TransferValidationError(
                f"{transfer.to_exchange} cannot receive {transfer.asset} over {transfer.network}"
            )
    step.detail = {
        "withdraw_min": str(src_net.withdraw_min),
        "expected_fee": str(src_net.withdraw_fee),
    }


async def _step_submit_withdrawal(db, transfer, step, get_connector):
    connector = get_connector(transfer.from_exchange)
    connector.require(Capability.WITHDRAWALS)
    if not transfer.address:
        raise TransferError("No resolved deposit address to withdraw to")
    from_account, _ = await _accounts(db, transfer)
    credential = await _own_credential(db, from_account)
    withdrawal = await connector.withdraw(
        credential,
        transfer.asset,
        transfer.amount,
        address=transfer.address,
        network=transfer.network,
        tag=transfer.address_tag,
    )
    transfer.native_withdrawal_id = withdrawal.native_id
    step.detail = {"native_withdrawal_id": withdrawal.native_id}


async def _step_await_withdrawal(db, transfer, step, get_connector):
    connector = get_connector(transfer.from_exchange)
    from_account, _ = await _accounts(db, transfer)
    credential = await _own_credential(db, from_account)
    withdrawal = await connector.fetch_withdrawal(
        credential, transfer.native_withdrawal_id
    )
    if withdrawal is None:
        return _WAIT
    if withdrawal.tx_id:
        transfer.tx_id = withdrawal.tx_id
    if withdrawal.fee is not None:
        transfer.fee = withdrawal.fee
        transfer.fee_asset = transfer.asset
    if withdrawal.status == WithdrawalStatus.FAILED:
        raise TransferError("The venue cancelled or rejected the withdrawal")
    if withdrawal.status in (WithdrawalStatus.SENT, WithdrawalStatus.COMPLETED):
        step.detail = {"status": withdrawal.status.value, "tx_id": withdrawal.tx_id}
        return None
    return _WAIT


def _match_deposit(transfer: Transfer, deposits: list[Deposit]) -> Deposit | None:
    """Find the deposit that is this transfer's arrival.

    A tx-id match is authoritative when both sides know the hash; otherwise
    fall back to asset + network + an amount in [amount − fee, amount]
    (venues differ on whether the fee comes out of the sent amount).
    """
    fee = transfer.fee or Decimal("0")
    for deposit in deposits:
        if deposit.status != DepositStatus.CREDITED or deposit.asset != transfer.asset:
            continue
        if transfer.tx_id and deposit.tx_id:
            if deposit.tx_id.lower() == transfer.tx_id.lower():
                return deposit
            continue
        if transfer.network and deposit.network and deposit.network != transfer.network:
            continue
        if transfer.amount - fee <= deposit.amount <= transfer.amount:
            return deposit
    return None


async def _step_await_deposit(db, transfer, step, get_connector):
    connector = get_connector(transfer.to_exchange)
    _, to_account = await _accounts(db, transfer)
    if to_account is None:
        raise TransferError("Deposit matching needs a destination account")
    credential, sub = await _deposit_call_params(db, to_account)
    deposits = await connector.fetch_deposits(
        credential,
        asset=transfer.asset,
        since=_as_utc(transfer.created_at),
        subaccount=sub,
    )
    matched = _match_deposit(transfer, deposits)
    if matched is None:
        return _WAIT
    transfer.deposit_native_id = matched.native_id
    transfer.received_amount = matched.amount
    if transfer.fee is None and matched.amount < transfer.amount:
        transfer.fee = transfer.amount - matched.amount
        transfer.fee_asset = transfer.asset
    step.detail = {
        "deposit_native_id": matched.native_id,
        "received_amount": str(matched.amount),
    }


_STEP_HANDLERS = {
    "submit": _step_submit,
    "confirm": _step_confirm,
    "resolve_address": _step_resolve_address,
    "validate": _step_validate,
    "submit_withdrawal": _step_submit_withdrawal,
    "await_withdrawal": _step_await_withdrawal,
    "await_deposit": _step_await_deposit,
}


# -- the engine -----------------------------------------------------------------

async def advance_transfer(
    db: AsyncSession, transfer: Transfer, get_connector
) -> Transfer:
    """Drive a transfer as far as it can go right now.

    Executes pending steps in order until one waits, fails, or all succeed.
    Safe to call repeatedly — that is exactly what the progress job does.
    ``get_connector`` maps an exchange name to its connector.
    """
    if transfer.status != "in_progress":
        return transfer

    for step in sorted(transfer.steps, key=lambda s: s.seq):
        if step.status == "succeeded":
            continue
        if step.status == "failed":
            return transfer

        if step.status == "running" and step.name in _MONEY_MOVING_STEPS:
            # Found running on entry ⇒ interrupted mid-call (crash/restart or
            # a prior ambiguous failure): the venue may or may not have
            # executed it. Surface for the operator; never re-execute.
            transfer.stuck = True
            transfer.error = (
                f"Step {step.name!r} was interrupted mid-call; verify the outcome "
                "on the venue, then retry or fail this transfer"
            )
            transfer.updated_at = _utcnow()
            await db.commit()
            return transfer

        if step.status == "pending":
            step.status = "running"
            step.started_at = _utcnow()
            transfer.updated_at = _utcnow()
            await db.commit()  # durable intent before any side effect

        try:
            outcome = await _STEP_HANDLERS[step.name](db, transfer, step, get_connector)
        except (TransferError, AccountError) as exc:
            return await _fail(db, transfer, step, str(exc))
        except Exception as exc:  # noqa: BLE001 - classified by step type below
            if step.name in _MONEY_MOVING_STEPS:
                # Ambiguous: the call may have reached the venue.
                transfer.stuck = True
                transfer.error = f"{step.name} failed ambiguously: {exc}"[:2000]
                step.error = str(exc)[:2000]
                transfer.updated_at = _utcnow()
                await db.commit()
                return transfer
            # Waiting/preparation steps are safe to retry next tick.
            step.error = str(exc)[:2000]
            await db.commit()
            logger.warning(
                "Transfer %s step %s errored (will retry): %s",
                transfer.id, step.name, exc,
            )
            return transfer

        if outcome is _WAIT:
            await db.commit()
            return transfer

        step.status = "succeeded"
        step.error = None
        step.finished_at = _utcnow()
        transfer.updated_at = _utcnow()
        await db.commit()

    transfer.status = "completed"
    transfer.stuck = False
    transfer.completed_at = _utcnow()
    transfer.updated_at = _utcnow()
    await db.commit()
    return transfer


async def _fail(
    db: AsyncSession, transfer: Transfer, step: TransferStep, reason: str
) -> Transfer:
    step.status = "failed"
    step.error = reason[:2000]
    step.finished_at = _utcnow()
    transfer.status = "failed"
    transfer.error = reason[:2000]
    transfer.updated_at = _utcnow()
    await db.commit()
    return transfer


# -- operator actions -----------------------------------------------------------

async def retry_transfer(
    db: AsyncSession, transfer: Transfer, get_connector
) -> Transfer:
    """Operator retry of a stuck transfer.

    Resets the interrupted step to pending (the operator has verified the
    venue outcome) and advances again.
    """
    if transfer.status != "in_progress":
        raise TransferError(f"Transfer is {transfer.status}; nothing to retry")
    transfer.stuck = False
    transfer.error = None
    for step in transfer.steps:
        if step.status == "running":
            step.status = "pending"
            step.error = None
    transfer.updated_at = _utcnow()
    await db.commit()
    return await advance_transfer(db, transfer, get_connector)


async def fail_transfer(db: AsyncSession, transfer: Transfer, reason: str) -> Transfer:
    """Operator marks a transfer failed in the panel (venue side untouched)."""
    if transfer.status == "completed":
        raise TransferError("Transfer already completed")
    running = next((s for s in transfer.steps if s.status == "running"), None)
    if running is not None:
        return await _fail(db, transfer, running, reason)
    transfer.status = "failed"
    transfer.error = reason[:2000]
    transfer.updated_at = _utcnow()
    await db.commit()
    return transfer


async def flag_stuck_transfers(db: AsyncSession) -> int:
    """Flag unfinished transfers with no progress for TRANSFER_STUCK_MINUTES."""
    threshold = _utcnow() - timedelta(minutes=settings.TRANSFER_STUCK_MINUTES)
    result = await db.execute(
        select(Transfer).where(Transfer.status == "in_progress", Transfer.stuck.is_(False))
    )
    flagged = 0
    for transfer in result.scalars():
        updated = _as_utc(transfer.updated_at)
        if updated is not None and updated < threshold:
            transfer.stuck = True
            flagged += 1
    if flagged:
        await db.commit()
    return flagged


# -- reads ------------------------------------------------------------------------

async def get_transfer(db: AsyncSession, transfer_id: int) -> Transfer:
    result = await db.execute(
        select(Transfer)
        .where(Transfer.id == transfer_id)
        .options(selectinload(Transfer.steps))
    )
    transfer = result.scalars().first()
    if transfer is None:
        raise TransferError(f"Transfer {transfer_id} not found")
    return transfer


async def list_transfers(
    db: AsyncSession,
    *,
    exchange: str | None = None,
    account_id: int | None = None,
    status: str | None = None,
    stuck_only: bool = False,
    limit: int = 100,
) -> list[Transfer]:
    query = (
        select(Transfer)
        .options(selectinload(Transfer.steps))
        .order_by(Transfer.created_at.desc(), Transfer.id.desc())
        .limit(limit)
    )
    if exchange:
        exchange = exchange.upper()
        query = query.where(
            (Transfer.from_exchange == exchange) | (Transfer.to_exchange == exchange)
        )
    if account_id is not None:
        query = query.where(
            (Transfer.from_account_id == account_id)
            | (Transfer.to_account_id == account_id)
        )
    if status:
        query = query.where(Transfer.status == status)
    if stuck_only:
        query = query.where(Transfer.stuck.is_(True))
    return list((await db.execute(query)).scalars())
