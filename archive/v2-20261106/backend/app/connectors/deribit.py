"""Deribit connector.

Deribit serves everything from one host (www.deribit.com, JSON-RPC v2) with
HMAC-SHA256 (``deri-hmac-sha256``) or Bearer-token auth. There is no official
PyPI SDK — this module signs raw HTTP (httpx) itself.

Surfaces we ingest:

    SPOT      kind=spot                         BTC_USDC
    PERP      kind=future  linear  perpetual    BTC_USDC-PERPETUAL
    CMARGIN   kind=future  reversed perpetual   BTC-PERPETUAL

``option``, ``*_combo``, and dated futures (``BTC-27MAR26``) are dropped.

Design decisions and venue quirks:

- **Wallets**: only ``UNIFIED``. Spot and derivatives share one currency
  equity book; there is no funding wallet and no wallet↔wallet transfer.
- **Credentials** are Client ID + Client Secret (HMAC). No passphrase.
  Trading / history / snapshots sign each request. Provisioning a *sub*
  API key uses ``public/auth`` + ``public/exchange_token`` because
  ``create_api_key`` always mints on the authenticated subject.
- **History** is account-wide per currency (``BTC``, ``ETH``, ``SOL``,
  ``USDC``, ``USDT``, ``EURR``, ``XRP``). Pagination walks 24h windows
  with ``start_timestamp``/``end_timestamp``; the cursor is JSON
  ``{"start": epoch_ms}``. Windows older than ~24h set ``historical=true``.
- **Provisioning** is self-service minus freeze: list/create subaccounts
  and create/modify/revoke keys. ``create_subaccount`` takes no name —
  we rename with ``change_subaccount_name``. Key names are ≤16
  ``[A-Za-z0-9_]``. Panel scopes map onto Deribit's ``account`` / ``trade``
  / ``wallet`` (trade is not split by market).
- **Orders**: ``private/buy`` / ``private/sell``. ``label`` is the client
  order id. One-way only — ``position_side`` is ignored. Inverse amount
  is USD; linear/spot amount is the base coin. Post-only is
  ``post_only`` + ``reject_post_only``.
- **Transfers** are main↔sub only (``submit_transfer_to_subaccount`` /
  ``submit_transfer_to_user``). Sub→main and sub↔sub switch subject via
  ``exchange_token``.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from app.connectors.base import (
    DEFAULT_HISTORY_BACKFILL,
    Capability,
    Credential,
    ExchangeConnector,
)
from app.connectors.errors import credential_error_reason
from app.connectors.symbols import InvalidUniversalSymbol, build_universal_symbol, parse_universal_symbol
from app.connectors.types import (
    ApiKeyInfo,
    ApiKeyResult,
    AssetNetwork,
    Balance,
    CredentialInfo,
    Deposit,
    DepositAddress,
    DepositStatus,
    FundingFee,
    Instrument,
    InstrumentFilters,
    InternalTransfer,
    InternalTransferDirection,
    InternalTransferScope,
    InternalTransferStatus,
    KeyScheme,
    MarketType,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    Page,
    Position,
    SubaccountInfo,
    TimeInForce,
    Trade,
    TransferResult,
    WalletType,
    Withdrawal,
    WithdrawalStatus,
)
from app.core.ratelimit import Priority, TokenBucket

logger = logging.getLogger(__name__)

BASE_URL = "https://www.deribit.com"
_TIMEOUT = 15.0
_HISTORY_WINDOW_MS = 24 * 60 * 60 * 1000
_RECENT_MS = 20 * 60 * 60 * 1000
_HISTORY_LIMIT = 100

# Settlement currencies we shard history / positions / open orders across.
_CURRENCIES = ("BTC", "ETH", "SOL", "USDC", "USDT", "EURR", "XRP")

_KEY_NAME_RE = re.compile(r"[^A-Za-z0-9_]")

_ORDER_STATUS = {
    "open": OrderStatus.OPEN,
    "filled": OrderStatus.FILLED,
    "rejected": OrderStatus.REJECTED,
    "cancelled": OrderStatus.CANCELLED,
    "canceled": OrderStatus.CANCELLED,
    "untriggered": OrderStatus.OPEN,
    "archive": OrderStatus.CANCELLED,
}

_WITHDRAWAL_STATUS = {
    "unconfirmed": WithdrawalStatus.PENDING,
    "confirmed": WithdrawalStatus.SENT,
    "completed": WithdrawalStatus.COMPLETED,
    "cancelled": WithdrawalStatus.FAILED,
    "canceled": WithdrawalStatus.FAILED,
    "interrupted": WithdrawalStatus.FAILED,
    "rejected": WithdrawalStatus.FAILED,
    "prepared": WithdrawalStatus.PENDING,
    "awaiting_second_email": WithdrawalStatus.PENDING,
}

_DEPOSIT_STATUS = {
    "pending": DepositStatus.PENDING,
    "completed": DepositStatus.CREDITED,
    "completed_early": DepositStatus.CREDITED,
    "rejected": DepositStatus.FAILED,
    "replaced": DepositStatus.FAILED,
}

_TRANSFER_STATUS = {
    "prepared": InternalTransferStatus.PENDING,
    "confirmed": InternalTransferStatus.CONFIRMED,
    "cancelled": InternalTransferStatus.FAILED,
    "waiting_for_admin": InternalTransferStatus.PENDING,
    "insufficient_funds": InternalTransferStatus.FAILED,
    "withdrawal_limit": InternalTransferStatus.FAILED,
}


class DeribitAPIError(Exception):
    """Venue or transport failure with attributes for credential_error_reason."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _opt_dec(value) -> Decimal | None:
    return _dec(value) if value not in (None, "") else None


def _ms_to_dt(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _sign(secret: str, timestamp: str, nonce: str, method: str, uri: str, body: str) -> str:
    """HTTP REST HMAC (not the WebSocket client_signature formula)."""
    request_data = f"{method.upper()}\n{uri}\n{body}\n"
    message = f"{timestamp}\n{nonce}\n{request_data}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _query_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _as_list(result: Any, *keys: str) -> list:
    if result is None:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in keys:
            rows = result.get(key)
            if isinstance(rows, list):
                return rows
    return []


def _instrument_type(raw: dict) -> str:
    return str(raw.get("instrument_type") or raw.get("future_type") or "").lower()


def _is_perpetual(raw: dict, name: str) -> bool:
    period = str(raw.get("settlement_period") or "").lower()
    return period == "perpetual" or name.endswith("-PERPETUAL") or name.endswith("_PERPETUAL")


def normalize_instrument(raw: dict) -> Instrument | None:
    """Map a get_instruments row; drop options, combos, and dated futures."""
    kind = str(raw.get("kind") or "").lower()
    if kind not in ("future", "spot"):
        return None
    name = raw.get("instrument_name") or ""
    if not name:
        return None
    base = (raw.get("base_currency") or "").upper()
    quote = (raw.get("quote_currency") or "").upper()
    settle = (raw.get("settlement_currency") or "").upper() or None
    inst_type = _instrument_type(raw)
    pair_quote = quote

    if kind == "spot":
        market_type = MarketType.SPOT
        if not settle:
            settle = quote or None
    elif _is_perpetual(raw, name):
        if inst_type == "linear":
            market_type = MarketType.PERP
            if not settle:
                settle = quote or None
        else:
            market_type = MarketType.CMARGIN
            if not quote:
                quote = "USD"
                pair_quote = "USD"
            if not settle:
                settle = base or None
    else:
        return None

    if not base or not pair_quote:
        return None
    try:
        universal = build_universal_symbol("DERIBIT", market_type, base, pair_quote)
    except InvalidUniversalSymbol:
        return None

    tick = _opt_dec(raw.get("tick_size")) or Decimal("0.01")
    step = _opt_dec(raw.get("qty_tick_size")) or _opt_dec(raw.get("min_trade_amount")) or Decimal("1")
    min_qty = _opt_dec(raw.get("min_trade_amount")) or step
    status = "active" if raw.get("is_active") else "halted"
    return Instrument(
        universal_symbol=universal,
        exchange="DERIBIT",
        market_type=market_type,
        native_symbol=name,
        base_asset=base,
        quote_asset=quote or pair_quote,
        filters=InstrumentFilters(
            price_tick=tick,
            qty_step=step,
            min_qty=min_qty,
        ),
        status=status,
        contract_size=_opt_dec(raw.get("contract_size")),
        settle_asset=settle,
    )


def normalize_balances(summaries: list[dict]) -> list[Balance]:
    balances: list[Balance] = []
    for row in summaries:
        asset = (row.get("currency") or "").upper()
        if not asset:
            continue
        free = _dec(row.get("available_funds") or 0)
        equity = _dec(row.get("equity") or row.get("balance") or 0)
        locked = equity - free
        if locked < 0:
            locked = Decimal(0)
        if free + locked <= 0:
            continue
        balances.append(Balance(wallet=WalletType.UNIFIED, asset=asset, free=free, locked=locked))
    return balances


def normalize_positions(
    rows: list[dict],
    universal_of: dict[tuple[MarketType, str], str],
) -> list[Position]:
    out: list[Position] = []
    for row in rows:
        if str(row.get("kind") or "").lower() == "option":
            continue
        size = _dec(row.get("size") or 0)
        if size == 0:
            continue
        native = row.get("instrument_name") or ""
        universal = _universal_for_native(native, universal_of)
        if universal is None:
            continue
        direction = str(row.get("direction") or "").lower()
        if direction == "sell" or size < 0:
            side = "short"
        else:
            side = "long"
        out.append(
            Position(
                universal_symbol=universal,
                side=side,
                size=abs(size),
                entry_price=_dec(row.get("average_price") or 0),
                mark_price=_opt_dec(row.get("mark_price")),
                unrealized_pnl=_opt_dec(row.get("floating_profit_loss")),
                margin=_opt_dec(row.get("initial_margin") or row.get("maintenance_margin")),
            )
        )
    return out


def _universal_for_native(
    native: str, universal_of: dict[tuple[MarketType, str], str]
) -> str | None:
    for market_type in (MarketType.SPOT, MarketType.PERP, MarketType.CMARGIN):
        found = universal_of.get((market_type, native))
        if found:
            return found
    return None


def _order_side(raw: str) -> OrderSide:
    return OrderSide.BUY if str(raw).lower() == "buy" else OrderSide.SELL


def _order_type(raw: str) -> OrderType:
    return OrderType.MARKET if "market" in str(raw).lower() else OrderType.LIMIT


def normalize_order(raw: dict, universal_symbol: str) -> Order:
    status = _ORDER_STATUS.get(str(raw.get("order_state") or "").lower(), OrderStatus.OPEN)
    filled = raw.get("filled_amount")
    if filled in (None, ""):
        filled = raw.get("filled_qty") or 0
    return Order(
        universal_symbol=universal_symbol,
        native_order_id=str(raw.get("order_id") or ""),
        client_order_id=raw.get("label") or None,
        side=_order_side(raw.get("direction") or "buy"),
        type=_order_type(raw.get("order_type") or "limit"),
        status=status,
        qty=_dec(raw.get("amount") or 0),
        filled_qty=_dec(filled or 0),
        price=_opt_dec(raw.get("price")),
        created_at=_ms_to_dt(raw.get("creation_timestamp")),
        updated_at=_ms_to_dt(raw.get("last_update_timestamp")),
    )


def normalize_trade(raw: dict, universal_symbol: str) -> Trade:
    fee = raw.get("fee")
    if fee in (None, ""):
        fee = raw.get("commission") or 0
    return Trade(
        universal_symbol=universal_symbol,
        native_trade_id=str(raw.get("trade_id") or ""),
        native_order_id=str(raw.get("order_id") or ""),
        side=_order_side(raw.get("direction") or "buy"),
        price=_dec(raw.get("price") or 0),
        qty=_dec(raw.get("amount") or 0),
        fee=abs(_dec(fee or 0)),
        fee_asset=(raw.get("fee_currency") or raw.get("fee_ccy") or "").upper(),
        traded_at=_ms_to_dt(raw.get("timestamp")),
    )


def normalize_funding_fee(raw: dict, universal_symbol: str) -> FundingFee | None:
    # settlement type=funding; skip delivery / bankruptcy.
    stype = str(raw.get("type") or raw.get("settlement_type") or "settlement").lower()
    if stype in ("delivery", "bankruptcy"):
        return None
    amount = _opt_dec(raw.get("funding") or raw.get("session_funding"))
    if amount is None:
        return None
    ts = raw.get("timestamp")
    native = raw.get("instrument_name") or ""
    return FundingFee(
        universal_symbol=universal_symbol,
        native_id=f"{native}:{ts}",
        asset=(raw.get("currency") or "").upper(),
        amount=amount,
        funding_rate=_opt_dec(raw.get("funding_rate") or raw.get("index_price")),
        settled_at=_ms_to_dt(ts),
    )


def normalize_withdrawal_record(raw: dict) -> Withdrawal:
    state = str(raw.get("state") or "").lower()
    return Withdrawal(
        native_id=str(raw.get("id") or raw.get("transaction_id") or ""),
        asset=(raw.get("currency") or "").upper(),
        amount=_dec(raw.get("amount") or 0),
        status=_WITHDRAWAL_STATUS.get(state, WithdrawalStatus.PENDING),
        address=raw.get("address") or None,
        network=(raw.get("network") or raw.get("network_display_name") or None),
        tx_id=raw.get("transaction_id") or None,
        fee=_opt_dec(raw.get("fee")),
        created_at=_ms_to_dt(raw.get("created_timestamp") or raw.get("updated_timestamp")),
    )


def normalize_deposit_record(raw: dict) -> Deposit:
    state = str(raw.get("state") or "").lower()
    status = _DEPOSIT_STATUS.get(state, DepositStatus.CREDITED if state == "completed" else DepositStatus.PENDING)
    return Deposit(
        native_id=str(raw.get("id") or raw.get("transaction_id") or ""),
        asset=(raw.get("currency") or "").upper(),
        amount=_dec(raw.get("amount") or 0),
        status=status,
        network=raw.get("network") or None,
        tx_id=raw.get("transaction_id") or None,
        credited_at=_ms_to_dt(raw.get("updated_timestamp") or raw.get("received_timestamp")),
        created_at=_ms_to_dt(raw.get("received_timestamp") or raw.get("created_timestamp")),
    )


def normalize_internal_transfer(raw: dict, account_uid: str | None = None) -> InternalTransfer:
    state = str(raw.get("state") or "").lower()
    other = raw.get("other_side")
    direction = None
    raw_dir = str(raw.get("direction") or "").lower()
    if raw_dir in ("in", "incoming", "received"):
        direction = InternalTransferDirection.IN
    elif raw_dir in ("out", "outgoing", "sent", "payment"):
        direction = InternalTransferDirection.OUT
    elif other is not None and account_uid is not None:
        direction = (
            InternalTransferDirection.OUT
            if str(other) != str(account_uid)
            else InternalTransferDirection.IN
        )
    return InternalTransfer(
        native_id=str(raw.get("id") or ""),
        asset=(raw.get("currency") or "").upper(),
        amount=_dec(raw.get("amount") or 0),
        status=_TRANSFER_STATUS.get(state, InternalTransferStatus.CONFIRMED if state == "confirmed" else InternalTransferStatus.PENDING),
        scope=InternalTransferScope.ACCOUNT,
        direction=direction,
        counterparty=str(other) if other is not None else None,
        created_at=_ms_to_dt(raw.get("created_timestamp") or raw.get("updated_timestamp")),
    )


def normalize_asset_networks(raw: dict, asset: str) -> list[AssetNetwork]:
    """``public/get_currencies`` row → chains we can move ``asset`` on."""
    currency = (raw.get("currency") or raw.get("coin_type") or "").upper()
    if currency != asset.upper():
        return []
    withdraw_fee = _dec(raw.get("withdrawal_fee") or raw.get("min_withdrawal_fee") or 0)
    withdraw_min = _dec(raw.get("min_withdrawal_fee") or raw.get("min_confirmations") or 0)
    # Coinbase-wallet currencies list named networks; legacy is one chain.
    networks: list[AssetNetwork] = []
    coinbase = raw.get("coinbase_networks") or []
    if isinstance(coinbase, list) and coinbase:
        for net in coinbase:
            if not isinstance(net, dict):
                continue
            name = (
                net.get("display_name")
                or net.get("network")
                or net.get("id")
                or ""
            )
            if not name:
                continue
            short = str(name).rsplit("/", 1)[-1].replace("-mainnet", "").upper()
            networks.append(
                AssetNetwork(
                    network=short,
                    deposit_enabled=bool(net.get("deposit_enabled", True)),
                    withdraw_enabled=bool(net.get("withdrawal_enabled", True)),
                    withdraw_min=_dec(net.get("min_withdrawal") or withdraw_min),
                    withdraw_fee=_dec(net.get("withdrawal_fee") or withdraw_fee),
                )
            )
        return networks
    # Native chain (BTC, ETH, …).
    networks.append(
        AssetNetwork(
            network=currency,
            deposit_enabled=not bool(raw.get("withdrawal_only")),
            withdraw_enabled=True,
            withdraw_min=withdraw_min,
            withdraw_fee=withdraw_fee,
        )
    )
    return networks


def _key_name(note: str | None) -> str:
    cleaned = _KEY_NAME_RE.sub("", note or "cpanel") or "cpanel"
    return cleaned[:16]


def _sub_name(label: str) -> str:
    cleaned = _KEY_NAME_RE.sub("", label or "sub") or "sub"
    return cleaned[:16]


def _scope_string(permissions: list[str], read_only: bool) -> str:
    scopes = ["account:read"]
    wants_trade = any(s in permissions for s in ("spot", "derivatives", "options", "trade"))
    wants_wallet = "wallet" in permissions
    if wants_trade:
        scopes.append("trade:read")
        if not read_only:
            scopes.append("trade:write")
    if wants_wallet:
        scopes.append("wallet:read")
        if not read_only:
            scopes.append("wallet:read_write")
    return " ".join(scopes)


def _parse_scope(max_scope: Any) -> set[str]:
    if isinstance(max_scope, list):
        return {str(s).strip().lower() for s in max_scope if s}
    return {p.strip().lower() for p in str(max_scope or "").split() if p.strip()}


def _order_payload(request: OrderRequest, native: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        "instrument_name": native,
        "amount": float(request.qty),
    }
    if request.type == OrderType.MARKET:
        params["type"] = "market"
    else:
        if request.price is None:
            raise ValueError("Limit orders require a price")
        params["type"] = "limit"
        params["price"] = float(request.price)
        tif = request.time_in_force or TimeInForce.GTC
        if tif == TimeInForce.POST_ONLY:
            params["time_in_force"] = "good_til_cancelled"
            params["post_only"] = True
            params["reject_post_only"] = True
        elif tif == TimeInForce.IOC:
            params["time_in_force"] = "immediate_or_cancel"
        elif tif == TimeInForce.FOK:
            params["time_in_force"] = "fill_or_kill"
        else:
            params["time_in_force"] = "good_til_cancelled"
    if request.client_order_id:
        params["label"] = request.client_order_id[:64]
    if request.reduce_only:
        params["reduce_only"] = True
    return params


def _unwrap_order(result: Any) -> dict:
    if isinstance(result, dict) and isinstance(result.get("order"), dict):
        return result["order"]
    if isinstance(result, dict):
        return result
    return {}


class DeribitConnector(ExchangeConnector):
    exchange = "DERIBIT"
    capabilities = frozenset(
        {
            Capability.INSTRUMENTS,
            Capability.REFERENCE_PRICES,
            Capability.CREDENTIAL_VERIFY,
            Capability.BALANCES,
            Capability.POSITIONS,
            Capability.ORDERS,
            Capability.TRADE_HISTORY,
            Capability.ORDER_HISTORY,
            Capability.SUBACCOUNT_LIST,
            Capability.SUBACCOUNT_CREATE,
            Capability.API_KEY_CREATE,
            Capability.API_KEY_MODIFY,
            Capability.API_KEY_REVOKE,
            Capability.TRANSFERS,
            Capability.WITHDRAWALS,
            Capability.DEPOSITS,
            Capability.FUNDING_HISTORY,
            Capability.INTERNAL_TRANSFER_HISTORY,
        }
    )
    wallets = (WalletType.UNIFIED,)

    def __init__(self, bucket: TokenBucket | None = None):
        self._bucket = bucket
        self._native_map: dict[tuple[MarketType, str], str] = {}
        self._universal_map: dict[str, tuple[MarketType, str]] = {}

    async def _call(self, func, *args, **kwargs):
        if self._bucket is not None:
            await self._bucket.acquire(priority=Priority.SYNC)
        return await asyncio.to_thread(func, *args, **kwargs)

    def _request(
        self,
        method: str,
        params: dict | None = None,
        credential: Credential | None = None,
        bearer: str | None = None,
    ) -> Any:
        clean = {k: _query_value(v) for k, v in (params or {}).items() if v is not None}
        path = f"/api/v2/{method}"
        query = urlencode(clean, doseq=True)
        uri = f"{path}?{query}" if query else path
        headers: dict[str, str] = {}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        elif credential is not None:
            timestamp = str(int(time.time() * 1000))
            nonce = secrets.token_hex(8)
            headers["Authorization"] = (
                f"deri-hmac-sha256 id={credential['api_key']},"
                f"ts={timestamp},sig={_sign(credential['secret'], timestamp, nonce, 'GET', uri, '')},"
                f"nonce={nonce}"
            )
        url = BASE_URL + uri
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, headers=headers)
        try:
            payload = resp.json()
        except Exception as exc:
            raise DeribitAPIError(
                f"Deribit non-JSON response (HTTP {resp.status_code})",
                status_code=resp.status_code,
            ) from exc
        error = payload.get("error") if isinstance(payload, dict) else None
        if error:
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("data") or "Deribit error")
                code = error.get("code")
            else:
                message = str(error)
                code = None
            raise DeribitAPIError(
                message,
                code=str(code) if code is not None else None,
                status_code=resp.status_code,
            )
        if not resp.is_success:
            raise DeribitAPIError(
                f"Deribit HTTP {resp.status_code}",
                status_code=resp.status_code,
            )
        return payload.get("result")

    async def _api(
        self,
        method: str,
        params: dict | None = None,
        credential: Credential | None = None,
        bearer: str | None = None,
    ) -> Any:
        return await self._call(
            self._request, method, params, credential, bearer
        )

    async def _ensure_maps(self) -> None:
        if not self._universal_map:
            await self.fetch_instruments()

    def _remember(self, inst: Instrument) -> None:
        self._native_map[(inst.market_type, inst.native_symbol)] = inst.universal_symbol
        self._universal_map[inst.universal_symbol] = (inst.market_type, inst.native_symbol)

    def _universal_for(self, native: str) -> str | None:
        return _universal_for_native(native, self._native_map)

    def _resolve(self, universal_symbol: str) -> tuple[MarketType, str]:
        if universal_symbol in self._universal_map:
            return self._universal_map[universal_symbol]
        parsed = parse_universal_symbol(universal_symbol)
        raise InvalidUniversalSymbol(
            f"Cannot resolve Deribit native symbol for {universal_symbol} "
            f"(maps cold or unknown {parsed.market_type.value} pair)"
        )

    # -- market data ------------------------------------------------------------

    async def fetch_instruments(self) -> list[Instrument]:
        instruments: list[Instrument] = []
        self._native_map = {}
        self._universal_map = {}
        for kind in ("future", "spot"):
            rows = await self._api(
                "public/get_instruments",
                {"currency": "any", "kind": kind, "expired": False},
            )
            for raw in _as_list(rows):
                inst = normalize_instrument(raw)
                if inst is None:
                    continue
                instruments.append(inst)
                self._remember(inst)
        return instruments

    async def fetch_reference_prices(self) -> dict[str, Decimal]:
        await self._ensure_maps()
        prices: dict[str, Decimal] = {}
        for kind in ("future", "spot"):
            rows = await self._api("public/get_book_summary_by_kind", {"kind": kind})
            for raw in _as_list(rows):
                native = raw.get("instrument_name") or ""
                last = raw.get("mark_price")
                if last in (None, ""):
                    last = raw.get("last") or raw.get("mid_price")
                if last in (None, ""):
                    continue
                universal = self._universal_for(native)
                if universal:
                    prices[universal] = _dec(last)
        return prices

    # -- credentials ------------------------------------------------------------

    async def verify_credential(self, credential: Credential) -> CredentialInfo:
        try:
            info = await self._api(
                "private/get_account_summaries",
                {"extended": True},
                credential=credential,
            )
        except Exception as exc:
            reason = credential_error_reason(self.exchange, exc)
            logger.warning("Deribit credential verification failed: %s", reason, exc_info=True)
            return CredentialInfo(valid=False, reason=reason)
        if not isinstance(info, dict):
            info = {}
        uid = info.get("id")
        acct_type = str(info.get("type") or "").lower()
        is_master = acct_type == "main" if acct_type else None
        can_trade = None
        can_withdraw = None
        read_only = None
        permissions = None
        ips = None
        try:
            keys = _as_list(
                await self._api("private/list_api_keys", credential=credential)
            )
            mine = next(
                (
                    row
                    for row in keys
                    if str(row.get("client_id") or "") == credential.get("api_key")
                ),
                None,
            )
            if mine:
                scopes = _parse_scope(mine.get("max_scope"))
                can_trade = any(s.startswith("trade:") and s != "trade:none" for s in scopes)
                can_withdraw = "wallet:read_write" in scopes
                read_only = bool(scopes) and not can_trade and not can_withdraw
                permissions = {"max_scope": sorted(scopes)}
                ips = [str(ip) for ip in (mine.get("ip_whitelist") or [])]
        except DeribitAPIError as exc:
            logger.debug("Deribit list_api_keys unavailable during verify: %s", exc)
        return CredentialInfo(
            valid=True,
            native_uid=str(uid) if uid is not None else None,
            is_master=is_master,
            read_only=read_only,
            can_trade=can_trade,
            can_withdraw=can_withdraw,
            permissions=permissions,
            ips=ips,
        )

    async def _account_id(self, credential: Credential) -> str | None:
        info = await self._api(
            "private/get_account_summaries",
            {"extended": True},
            credential=credential,
        )
        if isinstance(info, dict) and info.get("id") is not None:
            return str(info["id"])
        return None

    async def _bearer_for(self, credential: Credential, subaccount: str | None = None) -> str:
        auth = await self._api(
            "public/auth",
            {
                "grant_type": "client_credentials",
                "client_id": credential["api_key"],
                "client_secret": credential["secret"],
            },
        )
        if not isinstance(auth, dict) or not auth.get("access_token"):
            raise DeribitAPIError("Deribit auth did not return an access token")
        if not subaccount:
            return str(auth["access_token"])
        exchanged = await self._api(
            "public/exchange_token",
            {
                "refresh_token": auth["refresh_token"],
                "subject_id": int(subaccount),
            },
        )
        if not isinstance(exchanged, dict) or not exchanged.get("access_token"):
            raise DeribitAPIError(
                f"Could not switch Deribit session to subaccount {subaccount}"
            )
        return str(exchanged["access_token"])

    # -- balances / positions ---------------------------------------------------

    async def fetch_balances(self, credential: Credential) -> list[Balance]:
        result = await self._api(
            "private/get_account_summaries", credential=credential
        )
        summaries = result.get("summaries") if isinstance(result, dict) else result
        return normalize_balances(_as_list(summaries))

    async def fetch_positions(self, credential: Credential) -> list[Position]:
        await self._ensure_maps()
        rows: list[dict] = []
        for currency in _CURRENCIES:
            try:
                chunk = await self._api(
                    "private/get_positions",
                    {"currency": currency},
                    credential=credential,
                )
            except DeribitAPIError as exc:
                logger.debug("Deribit positions unavailable for %s: %s", currency, exc)
                continue
            rows.extend(_as_list(chunk))
        return normalize_positions(rows, self._native_map)

    # -- orders -----------------------------------------------------------------

    async def place_order(self, credential: Credential, request: OrderRequest) -> Order:
        await self._ensure_maps()
        _, native = self._resolve(request.universal_symbol)
        method = "private/buy" if request.side == OrderSide.BUY else "private/sell"
        raw = await self._api(method, _order_payload(request, native), credential=credential)
        order = normalize_order(_unwrap_order(raw), request.universal_symbol)
        if order.native_order_id:
            return order
        fetched = await self.fetch_order(
            credential,
            request.universal_symbol,
            client_order_id=request.client_order_id,
        )
        if fetched:
            return fetched
        return Order(
            universal_symbol=request.universal_symbol,
            native_order_id="",
            client_order_id=request.client_order_id,
            side=request.side,
            type=request.type,
            status=OrderStatus.OPEN,
            qty=request.qty,
            filled_qty=Decimal(0),
            price=request.price,
        )

    async def cancel_order(
        self, credential: Credential, universal_symbol: str, native_order_id: str
    ) -> Order:
        raw = await self._api(
            "private/cancel", {"order_id": native_order_id}, credential=credential
        )
        return normalize_order(_unwrap_order(raw) or {"order_id": native_order_id, "order_state": "cancelled"}, universal_symbol)

    async def fetch_open_orders(
        self, credential: Credential, universal_symbol: str | None = None
    ) -> list[Order]:
        await self._ensure_maps()
        if universal_symbol:
            _, native = self._resolve(universal_symbol)
            rows = await self._api(
                "private/get_open_orders_by_instrument",
                {"instrument_name": native},
                credential=credential,
            )
            return [normalize_order(row, universal_symbol) for row in _as_list(rows)]
        out: list[Order] = []
        for currency in _CURRENCIES:
            try:
                rows = await self._api(
                    "private/get_open_orders_by_currency",
                    {"currency": currency},
                    credential=credential,
                )
            except DeribitAPIError as exc:
                logger.debug("Deribit open orders unavailable for %s: %s", currency, exc)
                continue
            for row in _as_list(rows):
                universal = self._universal_for(row.get("instrument_name") or "")
                if universal:
                    out.append(normalize_order(row, universal))
        return out

    async def fetch_order(
        self,
        credential: Credential,
        universal_symbol: str,
        native_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> Order | None:
        await self._ensure_maps()
        try:
            if native_order_id:
                raw = await self._api(
                    "private/get_order_state",
                    {"order_id": native_order_id},
                    credential=credential,
                )
            elif client_order_id:
                raw = await self._api(
                    "private/get_order_state_by_label",
                    {"label": client_order_id},
                    credential=credential,
                )
            else:
                raise ValueError("fetch_order requires native_order_id or client_order_id")
        except DeribitAPIError:
            return None
        rows = _as_list(raw)
        row = rows[0] if rows else (_unwrap_order(raw) if raw else None)
        if not row:
            return None
        return normalize_order(row, universal_symbol)

    # -- history ----------------------------------------------------------------

    def history_stream_keys(self, active_symbols: list[str]) -> list[str]:
        return list(_CURRENCIES)

    def funding_history_stream_keys(self) -> list[str]:
        return list(_CURRENCIES)

    def fund_history_stream_keys(self) -> list[str]:
        return list(_CURRENCIES)

    def internal_transfer_stream_keys(self, account_type: str) -> list[str]:
        return list(_CURRENCIES)

    def initial_history_cursor(self, start: datetime) -> str | None:
        return json.dumps({"start": int(start.timestamp() * 1000)})

    def _history_window(self, cursor: str | None) -> tuple[int, int, int]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if cursor:
            try:
                decoded = json.loads(cursor)
                start_ms = int(decoded["start"])
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                start_ms = int(cursor)
        else:
            start_ms = now_ms - int(DEFAULT_HISTORY_BACKFILL.total_seconds() * 1000)
        end_ms = min(start_ms + _HISTORY_WINDOW_MS, now_ms)
        return start_ms, end_ms, now_ms

    @staticmethod
    def _next_history_cursor(start_ms: int, end_ms: int, now_ms: int, page_full: bool) -> str | None:
        if page_full:
            # Same window; caller should have advanced start via the last ts.
            return json.dumps({"start": start_ms})
        if end_ms < now_ms:
            return json.dumps({"start": end_ms})
        return None

    def _historical(self, end_ms: int, now_ms: int) -> bool:
        return now_ms - end_ms > _RECENT_MS

    async def fetch_trades(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        await self._ensure_maps()
        start_ms, end_ms, now_ms = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        result = await self._api(
            "private/get_user_trades_by_currency",
            {
                "currency": stream_key,
                "start_timestamp": start_ms,
                "end_timestamp": end_ms,
                "count": _HISTORY_LIMIT,
                "sorting": "asc",
                "historical": self._historical(end_ms, now_ms),
            },
            credential=credential,
        )
        rows = _as_list(result, "trades")
        items: list[Trade] = []
        last_ts = start_ms
        for raw in rows:
            if str(raw.get("instrument_kind") or raw.get("kind") or "").lower() == "option":
                continue
            universal = self._universal_for(raw.get("instrument_name") or "")
            if universal is None:
                continue
            items.append(normalize_trade(raw, universal))
            ts = raw.get("timestamp")
            if ts not in (None, ""):
                last_ts = max(last_ts, int(ts))
        page_full = len(rows) >= _HISTORY_LIMIT
        next_start = last_ts + 1 if page_full else end_ms
        if page_full:
            return Page(items=items, next_cursor=json.dumps({"start": next_start}))
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(start_ms, end_ms, now_ms, False),
        )

    async def fetch_orders_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        await self._ensure_maps()
        start_ms, end_ms, now_ms = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        offset = 0
        if cursor:
            try:
                offset = int(json.loads(cursor).get("offset") or 0)
            except (TypeError, ValueError, json.JSONDecodeError):
                offset = 0
        result = await self._api(
            "private/get_order_history_by_currency",
            {
                "currency": stream_key,
                "count": _HISTORY_LIMIT,
                "offset": offset,
                "include_unfilled": True,
                "historical": self._historical(end_ms, now_ms),
            },
            credential=credential,
        )
        rows = _as_list(result, "entries", "orders")
        items: list[Order] = []
        for raw in rows:
            ts = raw.get("creation_timestamp") or raw.get("last_update_timestamp")
            if ts not in (None, "") and not (start_ms <= int(ts) < end_ms):
                continue
            if str(raw.get("instrument_kind") or raw.get("kind") or "").lower() == "option":
                continue
            universal = self._universal_for(raw.get("instrument_name") or "")
            if universal is None:
                continue
            items.append(normalize_order(raw, universal))
        page_full = len(rows) >= _HISTORY_LIMIT
        if page_full:
            return Page(
                items=items,
                next_cursor=json.dumps({"start": start_ms, "offset": offset + _HISTORY_LIMIT}),
            )
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(start_ms, end_ms, now_ms, False),
        )

    async def fetch_funding_fees_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        await self._ensure_maps()
        start_ms, end_ms, now_ms = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        result = await self._api(
            "private/get_settlement_history_by_currency",
            {
                "currency": stream_key,
                "type": "settlement",
                "count": _HISTORY_LIMIT,
            },
            credential=credential,
        )
        rows = _as_list(result, "settlements")
        items: list[FundingFee] = []
        last_ts = start_ms
        for raw in rows:
            ts = raw.get("timestamp")
            if ts not in (None, "") and not (start_ms <= int(ts) < end_ms):
                continue
            universal = self._universal_for(raw.get("instrument_name") or "")
            if universal is None:
                continue
            fee = normalize_funding_fee(raw, universal)
            if fee is None:
                continue
            # Stamp the settlement currency from the stream when the row omits it.
            if not fee.asset:
                fee = FundingFee(
                    universal_symbol=fee.universal_symbol,
                    native_id=fee.native_id,
                    asset=stream_key,
                    amount=fee.amount,
                    funding_rate=fee.funding_rate,
                    settled_at=fee.settled_at,
                )
            items.append(fee)
            if ts not in (None, ""):
                last_ts = max(last_ts, int(ts))
        page_full = len(rows) >= _HISTORY_LIMIT
        if page_full:
            return Page(items=items, next_cursor=json.dumps({"start": last_ts + 1}))
        return Page(items=items, next_cursor=self._next_history_cursor(start_ms, end_ms, now_ms, False))

    async def fetch_deposits_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        start_ms, end_ms, now_ms = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        result = await self._api(
            "private/get_deposits",
            {"currency": stream_key, "count": _HISTORY_LIMIT},
            credential=credential,
        )
        rows = _as_list(result, "data", "deposits")
        items = []
        for raw in rows:
            ts = raw.get("received_timestamp") or raw.get("updated_timestamp")
            if ts not in (None, "") and not (start_ms <= int(ts) < end_ms):
                continue
            items.append(normalize_deposit_record(raw))
        # These endpoints are not time-windowed; always slide so the cursor
        # cannot stall on a full recent page.
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(start_ms, end_ms, now_ms, False),
        )

    async def fetch_withdrawals_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        start_ms, end_ms, now_ms = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        result = await self._api(
            "private/get_withdrawals",
            {"currency": stream_key, "count": _HISTORY_LIMIT},
            credential=credential,
        )
        rows = _as_list(result, "data", "withdrawals")
        items = []
        for raw in rows:
            ts = raw.get("created_timestamp") or raw.get("updated_timestamp")
            if ts not in (None, "") and not (start_ms <= int(ts) < end_ms):
                continue
            items.append(normalize_withdrawal_record(raw))
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(start_ms, end_ms, now_ms, False),
        )

    async def fetch_internal_transfers_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        start_ms, end_ms, now_ms = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        uid = None
        try:
            uid = await self._account_id(credential)
        except DeribitAPIError:
            uid = None
        result = await self._api(
            "private/get_transfers",
            {"currency": stream_key, "count": _HISTORY_LIMIT},
            credential=credential,
        )
        rows = _as_list(result, "data", "transfers")
        items = []
        for raw in rows:
            ts = raw.get("created_timestamp") or raw.get("updated_timestamp")
            if ts not in (None, "") and not (start_ms <= int(ts) < end_ms):
                continue
            items.append(normalize_internal_transfer(raw, uid))
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(start_ms, end_ms, now_ms, False),
        )

    # -- provisioning -----------------------------------------------------------

    async def list_subaccounts(self, credential: Credential) -> list[SubaccountInfo]:
        rows = _as_list(await self._api("private/get_subaccounts", credential=credential))
        out: list[SubaccountInfo] = []
        for row in rows:
            sid = row.get("id")
            if sid is None:
                continue
            name = row.get("username") or row.get("email") or str(sid)
            login = row.get("login_enabled")
            status = "normal" if login in (True, "true", None) else "disabled"
            out.append(
                SubaccountInfo(
                    native_uid=str(sid),
                    label=str(name),
                    status=status,
                    raw=row,
                )
            )
        return out

    async def create_subaccount(self, credential: Credential, label: str) -> SubaccountInfo:
        row = await self._api("private/create_subaccount", credential=credential)
        if not isinstance(row, dict):
            row = {}
        sid = row.get("id")
        if sid is None:
            raise DeribitAPIError("Deribit create_subaccount returned no id")
        name = _sub_name(label)
        try:
            await self._api(
                "private/change_subaccount_name",
                {"sid": int(sid), "name": name},
                credential=credential,
            )
        except DeribitAPIError as exc:
            logger.warning("Deribit rename subaccount %s failed: %s", sid, exc)
        return SubaccountInfo(
            native_uid=str(sid),
            label=label,
            status="normal",
            raw=row,
        )

    async def _key_rows(
        self, credential: Credential, subaccount: str | None
    ) -> list[dict]:
        if subaccount:
            bearer = await self._bearer_for(credential, subaccount)
            return _as_list(await self._api("private/list_api_keys", bearer=bearer))
        return _as_list(await self._api("private/list_api_keys", credential=credential))

    async def _key_id(
        self, credential: Credential, subaccount: str | None, api_key: str
    ) -> int:
        for row in await self._key_rows(credential, subaccount):
            if str(row.get("client_id") or "") == api_key:
                return int(row["id"])
        raise DeribitAPIError(f"Deribit API key {api_key!r} not found")

    async def create_api_key(
        self,
        credential: Credential,
        subaccount: str | None,
        scheme: KeyScheme,
        permissions: list[str],
        read_only: bool = False,
        ips: list[str] | None = None,
        note: str | None = None,
    ) -> ApiKeyResult:
        if scheme != KeyScheme.HMAC:
            raise ValueError("Deribit issues only HMAC keys via API")
        params = {
            "max_scope": _scope_string(permissions, read_only),
            "name": _key_name(note),
        }
        if subaccount:
            bearer = await self._bearer_for(credential, subaccount)
            row = await self._api("private/create_api_key", params, bearer=bearer)
        else:
            row = await self._api("private/create_api_key", params, credential=credential)
        if not isinstance(row, dict):
            row = {}
        return ApiKeyResult(
            api_key=str(row.get("client_id") or ""),
            secret=str(row.get("client_secret") or ""),
            scheme=KeyScheme.HMAC,
            permissions=permissions,
            ips=ips or [str(ip) for ip in (row.get("ip_whitelist") or [])],
        )

    async def modify_api_key(
        self,
        credential: Credential,
        subaccount: str | None,
        api_key: str,
        read_only: bool | None = None,
        permissions: list[str] | None = None,
    ) -> None:
        key_id = await self._key_id(credential, subaccount, api_key)
        params = {
            "id": key_id,
            "max_scope": _scope_string(permissions or [], bool(read_only)),
        }
        if subaccount:
            bearer = await self._bearer_for(credential, subaccount)
            await self._api("private/change_scope_in_api_key", params, bearer=bearer)
        else:
            await self._api("private/change_scope_in_api_key", params, credential=credential)

    async def revoke_api_key(
        self, credential: Credential, subaccount: str | None, api_key: str
    ) -> None:
        key_id = await self._key_id(credential, subaccount, api_key)
        params = {"id": key_id}
        if subaccount:
            bearer = await self._bearer_for(credential, subaccount)
            await self._api("private/remove_api_key", params, bearer=bearer)
        else:
            await self._api("private/remove_api_key", params, credential=credential)

    async def list_api_keys(
        self, credential: Credential, subaccount: str | None = None
    ) -> list[ApiKeyInfo]:
        out: list[ApiKeyInfo] = []
        for row in await self._key_rows(credential, subaccount):
            out.append(
                ApiKeyInfo(
                    api_key=str(row.get("client_id") or ""),
                    ips=[str(ip) for ip in (row.get("ip_whitelist") or [])],
                    label=row.get("name"),
                )
            )
        return out

    # -- transfers / withdrawals / deposits -----------------------------------

    async def transfer(
        self,
        credential: Credential,
        asset: str,
        amount,
        from_wallet: WalletType | None = None,
        to_wallet: WalletType | None = None,
        from_subaccount: str | None = None,
        to_subaccount: str | None = None,
    ) -> TransferResult:
        src = from_wallet or WalletType.UNIFIED
        dst = to_wallet or WalletType.UNIFIED
        if src != WalletType.UNIFIED or dst != WalletType.UNIFIED:
            raise ValueError("Deribit has a single UNIFIED wallet")
        if from_subaccount and to_subaccount and from_subaccount == to_subaccount:
            raise ValueError("Deribit transfer source and destination are the same account")
        if not from_subaccount and not to_subaccount:
            raise ValueError("Deribit transfers move between the main account and a subaccount")

        if to_subaccount and not from_subaccount:
            row = await self._api(
                "private/submit_transfer_to_subaccount",
                {
                    "currency": asset.upper(),
                    "amount": float(amount),
                    "destination": int(to_subaccount),
                },
                credential=credential,
            )
        else:
            # Sub → main or sub → sub: operate as the source subject.
            destination = to_subaccount or await self._account_id(credential)
            if destination is None:
                raise DeribitAPIError("Could not resolve Deribit destination account id")
            bearer = await self._bearer_for(credential, from_subaccount)
            row = await self._api(
                "private/submit_transfer_to_user",
                {
                    "currency": asset.upper(),
                    "amount": float(amount),
                    "destination": int(destination),
                },
                bearer=bearer,
            )
        if not isinstance(row, dict):
            row = {}
        state = str(row.get("state") or "confirmed").lower()
        status = "confirmed" if state == "confirmed" else "submitted"
        return TransferResult(
            native_transfer_id=str(row.get("id") or ""),
            status=status,
        )

    async def fetch_asset_networks(
        self, credential: Credential, asset: str
    ) -> list[AssetNetwork]:
        rows = _as_list(await self._api("public/get_currencies"))
        for row in rows:
            nets = normalize_asset_networks(row, asset)
            if nets:
                return nets
        return []

    async def fetch_deposit_address(
        self,
        credential: Credential,
        asset: str,
        network: str,
        subaccount: str | None = None,
    ) -> DepositAddress:
        params: dict[str, Any] = {"currency": asset.upper()}
        if subaccount:
            params["subaccount_id"] = int(subaccount)
        try:
            raw = await self._api(
                "private/get_current_deposit_address", params, credential=credential
            )
        except DeribitAPIError:
            if not subaccount:
                raise
            bearer = await self._bearer_for(credential, subaccount)
            raw = await self._api(
                "private/get_current_deposit_address",
                {"currency": asset.upper()},
                bearer=bearer,
            )
        if isinstance(raw, dict) and isinstance(raw.get("address"), dict):
            raw = raw["address"]
        if not isinstance(raw, dict) or not raw.get("address"):
            raise ValueError(f"Deribit returned no {asset}/{network} deposit address")
        return DepositAddress(
            asset=asset.upper(),
            network=network.upper(),
            address=str(raw["address"]),
            tag=raw.get("destination_tag") or raw.get("tag") or None,
        )

    async def withdraw(
        self,
        credential: Credential,
        asset: str,
        amount,
        address: str,
        network: str,
        tag: str | None = None,
    ) -> Withdrawal:
        params: dict[str, Any] = {
            "currency": asset.upper(),
            "amount": float(amount),
            "address": address,
        }
        if network:
            params["network"] = network
        if tag:
            params["tag"] = tag
        raw = await self._api("private/withdraw", params, credential=credential)
        if not isinstance(raw, dict):
            raw = {"id": raw, "currency": asset, "amount": amount, "state": "unconfirmed", "address": address}
        return normalize_withdrawal_record(raw)

    async def fetch_withdrawal(
        self, credential: Credential, native_id: str
    ) -> Withdrawal | None:
        for currency in _CURRENCIES:
            try:
                result = await self._api(
                    "private/get_withdrawals",
                    {"currency": currency, "count": 50},
                    credential=credential,
                )
            except DeribitAPIError:
                continue
            for raw in _as_list(result, "data", "withdrawals"):
                if str(raw.get("id") or "") == str(native_id):
                    return normalize_withdrawal_record(raw)
        return None

    async def fetch_deposits(
        self,
        credential: Credential,
        asset: str | None = None,
        since: datetime | None = None,
        subaccount: str | None = None,
    ) -> list[Deposit]:
        currencies = (asset.upper(),) if asset else _CURRENCIES
        since_ms = int(since.timestamp() * 1000) if since else None
        out: list[Deposit] = []
        for currency in currencies:
            params: dict[str, Any] = {"currency": currency, "count": 50}
            if subaccount:
                params["subaccount_id"] = int(subaccount)
            try:
                result = await self._api(
                    "private/get_deposits", params, credential=credential
                )
            except DeribitAPIError as exc:
                logger.debug("Deribit deposits unavailable for %s: %s", currency, exc)
                continue
            for raw in _as_list(result, "data", "deposits"):
                dep = normalize_deposit_record(raw)
                if since_ms and dep.created_at and int(dep.created_at.timestamp() * 1000) < since_ms:
                    continue
                out.append(dep)
        return out
