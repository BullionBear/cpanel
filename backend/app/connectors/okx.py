"""OKX connector.

OKX serves everything from one host (www.okx.com, REST v5) with
HMAC-SHA256 + passphrase auth. Markets use an ``instType`` selector:

    SPOT     instType=SPOT                       instId=BTC-USDT
    PERP     instType=SWAP  ctType=linear        instId=BTC-USDT-SWAP
    CMARGIN  instType=SWAP  ctType=inverse       instId=BTC-USD-SWAP

Delivery FUTURES and OPTION stay out of scope. There is no official PyPI
SDK in our deps — this module signs raw HTTP (httpx) itself.

Design decisions and venue quirks:

- **Wallets** (Bybit-shaped): ``UNIFIED`` (Trading Account, leg ``18``) and
  ``FUNDING`` (leg ``6``). Spot + derivatives collateral live together in
  the trading account.
- **Credentials** always include a passphrase (``OK-ACCESS-PASSPHRASE``).
- **History** is account-wide per surface: streams ``spot``,
  ``swap:linear``, ``swap:inverse``. Pagination uses ``after`` bill/ord
  ids inside sliding 24h time windows; the cursor is JSON
  ``{"after": "...", "start": epoch_ms}``.
- **Provisioning** is Bybit-like minus freeze: list/create subaccounts and
  create/modify/revoke sub API keys. Master keys are registration-only.
  Create-sub and key mutations require an IP-bound master key; some
  accounts reject create-sub with code ``59518`` (use the web UI).
  Sub keys minted via API get ``read_only`` / ``trade`` only (no withdraw).
- **Spot** orders use ``tdMode=cash``; swap orders default to ``cross``.
  One-way uses ``posSide=net``; hedge mode passes ``long``/``short``.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import string
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
    KeyScheme,
    MarketType,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    Page,
    Position,
    PositionSide,
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

BASE_URL = "https://www.okx.com"
_TIMEOUT = 15.0

# Wallet → OKX transfer accountType codes.
_WALLET_ACCOUNT = {
    WalletType.UNIFIED: "18",
    WalletType.FUNDING: "6",
}

_ORDER_STATUS = {
    "live": OrderStatus.OPEN,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "cancelled": OrderStatus.CANCELLED,
    "mmp_canceled": OrderStatus.CANCELLED,
}

# OKX withdrawal state codes (string).
_WITHDRAWAL_STATUS = {
    "-3": WithdrawalStatus.FAILED,
    "-2": WithdrawalStatus.FAILED,
    "-1": WithdrawalStatus.FAILED,
    "0": WithdrawalStatus.PENDING,
    "1": WithdrawalStatus.PENDING,
    "2": WithdrawalStatus.SENT,
    "4": WithdrawalStatus.PENDING,
    "5": WithdrawalStatus.PENDING,
    "6": WithdrawalStatus.PENDING,
    "7": WithdrawalStatus.FAILED,
    "8": WithdrawalStatus.PENDING,
    "9": WithdrawalStatus.FAILED,
    "10": WithdrawalStatus.COMPLETED,
    "12": WithdrawalStatus.PENDING,
}

_DEPOSIT_STATUS = {
    "0": DepositStatus.PENDING,
    "1": DepositStatus.CREDITED,
    "2": DepositStatus.CREDITED,
    "8": DepositStatus.PENDING,
    "11": DepositStatus.FAILED,
    "12": DepositStatus.FAILED,
}

_HISTORY_WINDOW_MS = 24 * 60 * 60 * 1000
_HISTORY_STREAMS = ("spot", "swap:linear", "swap:inverse")

_SUBACCT_RE = re.compile(r"^[A-Za-z0-9]{6,20}$")


class OkxAPIError(Exception):
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


def _okx_passphrase(length: int = 16) -> str:
    """Generate an OKX-compliant passphrase.

    8–32 chars with at least one upper, lower, digit, and special character.
    """
    length = max(8, min(length, 32))
    specials = "!@#$%^&*"
    # Ensure required classes, then fill.
    chars = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(specials),
    ]
    alphabet = string.ascii_letters + string.digits + specials
    chars.extend(secrets.choice(alphabet) for _ in range(length - 4))
    # Shuffle without relying on random.shuffle (use secrets).
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


def _sign(secret: str, timestamp: str, method: str, path: str, query: str, body: str) -> str:
    request_path = path + (f"?{query}" if query else "")
    prehash = f"{timestamp}{method.upper()}{request_path}{body}"
    digest = hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _inst_status(raw: str) -> str:
    if raw == "live":
        return "active"
    if raw in ("suspend", "settle", "settling"):
        return "halted"
    return "delisted"


def _pair_from_inst(info: dict) -> tuple[str, str] | None:
    """Return (base, quote) for universal-symbol construction."""
    base = (info.get("baseCcy") or "").upper()
    quote = (info.get("quoteCcy") or "").upper()
    if base and quote:
        return base, quote
    # SWAP often leaves baseCcy/quoteCcy empty; parse uly (e.g. BTC-USDT) or instId.
    uly = info.get("uly") or ""
    if "-" in uly:
        parts = uly.split("-")
        if len(parts) >= 2:
            return parts[0].upper(), parts[1].upper()
    inst_id = info.get("instId") or ""
    parts = inst_id.split("-")
    if len(parts) >= 2:
        return parts[0].upper(), parts[1].upper()
    return None


def normalize_spot_instrument(info: dict) -> Instrument | None:
    inst_id = info.get("instId") or ""
    pair = _pair_from_inst(info)
    if not inst_id or pair is None:
        return None
    base, quote = pair
    tick = _opt_dec(info.get("tickSz")) or Decimal("0.01")
    lot = _opt_dec(info.get("lotSz")) or Decimal("0.0001")
    min_sz = _opt_dec(info.get("minSz")) or lot
    return Instrument(
        universal_symbol=build_universal_symbol("OKX", MarketType.SPOT, base, quote),
        exchange="OKX",
        market_type=MarketType.SPOT,
        native_symbol=inst_id,
        base_asset=base,
        quote_asset=quote,
        filters=InstrumentFilters(
            price_tick=tick,
            qty_step=lot,
            min_qty=min_sz,
            min_notional=_opt_dec(info.get("minSz")),
        ),
        status=_inst_status(str(info.get("state") or "")),
    )


def normalize_swap_instrument(info: dict) -> Instrument | None:
    """Map a SWAP row to PERP (linear) or CMARGIN (inverse); skip others."""
    ct_type = str(info.get("ctType") or "").lower()
    if ct_type == "linear":
        market_type = MarketType.PERP
    elif ct_type == "inverse":
        market_type = MarketType.CMARGIN
    else:
        return None
    inst_id = info.get("instId") or ""
    pair = _pair_from_inst(info)
    if not inst_id or pair is None:
        return None
    base, quote = pair
    tick = _opt_dec(info.get("tickSz")) or Decimal("0.01")
    lot = _opt_dec(info.get("lotSz")) or Decimal(1)
    min_sz = _opt_dec(info.get("minSz")) or lot
    settle = (info.get("settleCcy") or "").upper() or None
    if market_type == MarketType.PERP and not settle:
        settle = quote
    if market_type == MarketType.CMARGIN and not settle:
        settle = base
    return Instrument(
        universal_symbol=build_universal_symbol("OKX", market_type, base, quote),
        exchange="OKX",
        market_type=market_type,
        native_symbol=inst_id,
        base_asset=base,
        quote_asset=quote,
        filters=InstrumentFilters(
            price_tick=tick,
            qty_step=lot,
            min_qty=min_sz,
        ),
        status=_inst_status(str(info.get("state") or "")),
        contract_size=_opt_dec(info.get("ctVal")),
        settle_asset=settle,
    )


def normalize_unified_balances(details: list[dict]) -> list[Balance]:
    balances: list[Balance] = []
    for row in details:
        free = _dec(row.get("availBal") or 0)
        locked = _dec(row.get("frozenBal") or 0)
        if free + locked <= 0:
            # Fall back to cashBal / eq when avail/frozen are empty strings.
            eq = _dec(row.get("cashBal") or row.get("eq") or 0)
            if eq <= 0:
                continue
            free = eq
        asset = (row.get("ccy") or "").upper()
        if not asset:
            continue
        balances.append(Balance(wallet=WalletType.UNIFIED, asset=asset, free=free, locked=locked))
    return balances


def normalize_funding_balances(rows: list[dict]) -> list[Balance]:
    balances: list[Balance] = []
    for row in rows:
        free = _dec(row.get("availBal") or 0)
        locked = _dec(row.get("frozenBal") or 0)
        if free + locked <= 0:
            continue
        asset = (row.get("ccy") or "").upper()
        if not asset:
            continue
        balances.append(Balance(wallet=WalletType.FUNDING, asset=asset, free=free, locked=locked))
    return balances


def normalize_positions(
    rows: list[dict],
    universal_of: dict[tuple[MarketType, str], str],
) -> list[Position]:
    out: list[Position] = []
    for row in rows:
        size = _dec(row.get("pos") or 0)
        if size == 0:
            continue
        native = row.get("instId") or ""
        ct_type = str(row.get("ctType") or "").lower()
        # Prefer ctType; fall back to instId suffix / settle.
        if ct_type == "inverse" or (not ct_type and native.endswith("-USD-SWAP")):
            market_type = MarketType.CMARGIN
        else:
            market_type = MarketType.PERP
        universal = universal_of.get((market_type, native))
        if universal is None:
            # Try the other market type if ctType was missing.
            universal = universal_of.get((MarketType.PERP, native)) or universal_of.get(
                (MarketType.CMARGIN, native)
            )
        if universal is None:
            continue
        pos_side = str(row.get("posSide") or "net").lower()
        if pos_side == "short" or (pos_side == "net" and size < 0):
            side = "short"
        else:
            side = "long"
        out.append(
            Position(
                universal_symbol=universal,
                side=side,
                size=abs(size),
                entry_price=_dec(row.get("avgPx") or 0),
                mark_price=_opt_dec(row.get("markPx")),
                unrealized_pnl=_opt_dec(row.get("upl")),
                margin=_opt_dec(row.get("margin") or row.get("imr")),
            )
        )
    return out


def _order_side(raw: str) -> OrderSide:
    return OrderSide.BUY if str(raw).lower() == "buy" else OrderSide.SELL


def _order_type(raw: str) -> OrderType:
    return OrderType.MARKET if str(raw).lower() == "market" else OrderType.LIMIT


def normalize_order(raw: dict, universal_symbol: str) -> Order:
    status = _ORDER_STATUS.get(str(raw.get("state") or "").lower(), OrderStatus.OPEN)
    return Order(
        universal_symbol=universal_symbol,
        native_order_id=str(raw.get("ordId") or ""),
        client_order_id=raw.get("clOrdId") or None,
        side=_order_side(raw.get("side") or "buy"),
        type=_order_type(raw.get("ordType") or "limit"),
        status=status,
        qty=_dec(raw.get("sz") or 0),
        filled_qty=_dec(raw.get("accFillSz") or 0),
        price=_opt_dec(raw.get("px")),
        created_at=_ms_to_dt(raw.get("cTime")),
        updated_at=_ms_to_dt(raw.get("uTime")),
    )


def normalize_trade(raw: dict, universal_symbol: str) -> Trade:
    return Trade(
        universal_symbol=universal_symbol,
        native_trade_id=str(raw.get("tradeId") or raw.get("billId") or ""),
        native_order_id=str(raw.get("ordId") or ""),
        side=_order_side(raw.get("side") or "buy"),
        price=_dec(raw.get("fillPx") or raw.get("px") or 0),
        qty=_dec(raw.get("fillSz") or raw.get("sz") or 0),
        fee=abs(_dec(raw.get("fee") or 0)),
        fee_asset=(raw.get("feeCcy") or "").upper(),
        traded_at=_ms_to_dt(raw.get("ts") or raw.get("fillTime")),
    )


def normalize_asset_networks(rows: list[dict], asset: str) -> list[AssetNetwork]:
    asset_u = asset.upper()
    networks: list[AssetNetwork] = []
    for row in rows:
        if (row.get("ccy") or "").upper() != asset_u:
            continue
        chain = row.get("chain") or ""
        # chain looks like "USDT-ERC20" — keep venue chain code as-is.
        network = chain.split("-", 1)[-1] if "-" in chain else chain
        networks.append(
            AssetNetwork(
                network=network.upper() if network else chain,
                deposit_enabled=str(row.get("canDep") or "").lower() in ("true", "1"),
                withdraw_enabled=str(row.get("canWd") or "").lower() in ("true", "1"),
                withdraw_min=_dec(row.get("minWd") or 0),
                withdraw_fee=_dec(row.get("fee") or row.get("minFee") or 0),
            )
        )
    return networks


def normalize_withdrawal_record(raw: dict) -> Withdrawal:
    state = str(raw.get("state") if raw.get("state") is not None else "")
    status = _WITHDRAWAL_STATUS.get(state, WithdrawalStatus.PENDING)
    chain = raw.get("chain") or ""
    network = chain.split("-", 1)[-1] if "-" in chain else chain
    return Withdrawal(
        native_id=str(raw.get("wdId") or raw.get("clientId") or ""),
        asset=(raw.get("ccy") or "").upper(),
        amount=_dec(raw.get("amt") or 0),
        status=status,
        address=raw.get("to") or None,
        network=network.upper() if network else None,
        tx_id=raw.get("txId") or None,
        fee=_opt_dec(raw.get("fee")),
        created_at=_ms_to_dt(raw.get("ts") or raw.get("cTime")),
    )


def normalize_deposit_record(raw: dict) -> Deposit:
    state = str(raw.get("state") if raw.get("state") is not None else "")
    status = _DEPOSIT_STATUS.get(state, DepositStatus.PENDING)
    chain = raw.get("chain") or ""
    network = chain.split("-", 1)[-1] if "-" in chain else chain
    return Deposit(
        native_id=str(raw.get("depId") or raw.get("txId") or ""),
        asset=(raw.get("ccy") or "").upper(),
        amount=_dec(raw.get("amt") or 0),
        status=status,
        network=network.upper() if network else None,
        tx_id=raw.get("txId") or None,
        credited_at=_ms_to_dt(raw.get("ts")),
        created_at=_ms_to_dt(raw.get("ts") or raw.get("cTime")),
    )


def normalize_funding_fee(raw: dict, universal_symbol: str) -> FundingFee:
    # OKX bills: type 8 = funding fee; balChg is signed.
    amount = _dec(raw.get("balChg") or raw.get("pnl") or 0)
    return FundingFee(
        universal_symbol=universal_symbol,
        native_id=str(raw.get("billId") or ""),
        asset=(raw.get("ccy") or "").upper(),
        amount=amount,
        funding_rate=_opt_dec(raw.get("fillPx")),  # not always present
        settled_at=_ms_to_dt(raw.get("ts")),
    )


def _subacct_name(label: str) -> str:
    """Derive a venue-legal subAcct (6–20 alphanumeric) from a panel label."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", label)
    if len(cleaned) < 6:
        cleaned = (cleaned + "okxsub")[:6]
        if len(cleaned) < 6:
            cleaned = cleaned.ljust(6, "0")
    cleaned = cleaned[:20]
    if not _SUBACCT_RE.match(cleaned):
        cleaned = f"okx{secrets.token_hex(4)}"[:20]
    return cleaned


def _scope_perm(permissions: list[str], read_only: bool) -> str:
    if read_only:
        return "read_only"
    # Any trading-ish scope grants trade; wallet alone stays read_only+trade
    # so transfers work under the Trade permission on OKX.
    if any(s in permissions for s in ("spot", "derivatives", "wallet", "options", "trade")):
        return "read_only,trade"
    return "read_only"


class OkxConnector(ExchangeConnector):
    exchange = "OKX"
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
        }
    )
    wallets = (WalletType.UNIFIED, WalletType.FUNDING)

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
        path: str,
        *,
        credential: Credential | None = None,
        params: dict | None = None,
        body: dict | None = None,
    ) -> Any:
        # Drop None values so they don't appear in the query string.
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        query = urlencode(clean_params, doseq=True)
        body_str = json.dumps(body) if body is not None else ""
        headers = {"Content-Type": "application/json"}
        if credential is not None:
            passphrase = credential.get("passphrase")
            if not passphrase:
                raise OkxAPIError("OKX credentials require a passphrase")
            # ISO8601 with milliseconds + Z.
            now = datetime.now(timezone.utc)
            timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"
            headers.update(
                {
                    "OK-ACCESS-KEY": credential["api_key"],
                    "OK-ACCESS-SIGN": _sign(
                        credential["secret"], timestamp, method, path, query, body_str
                    ),
                    "OK-ACCESS-TIMESTAMP": timestamp,
                    "OK-ACCESS-PASSPHRASE": passphrase,
                }
            )
        url = BASE_URL + path
        if query:
            url += "?" + query
        with httpx.Client(timeout=_TIMEOUT) as client:
            if method.upper() == "GET":
                resp = client.get(url, headers=headers)
            else:
                resp = client.request(method.upper(), url, headers=headers, content=body_str)
        try:
            payload = resp.json()
        except Exception as exc:
            raise OkxAPIError(
                f"OKX non-JSON response (HTTP {resp.status_code})",
                status_code=resp.status_code,
            ) from exc
        code = str(payload.get("code", ""))
        if not resp.is_success or code != "0":
            raise OkxAPIError(
                payload.get("msg") or f"OKX error code={code}",
                code=code or None,
                status_code=resp.status_code,
            )
        return payload.get("data")

    async def _api(
        self,
        method: str,
        path: str,
        *,
        credential: Credential | None = None,
        params: dict | None = None,
        body: dict | None = None,
    ) -> Any:
        return await self._call(
            self._request, method, path, credential=credential, params=params, body=body
        )

    # -- market data ------------------------------------------------------------

    async def fetch_instruments(self) -> list[Instrument]:
        instruments: list[Instrument] = []
        native_map: dict[tuple[MarketType, str], str] = {}
        universal_map: dict[str, tuple[MarketType, str]] = {}

        spot_rows = await self._api(
            "GET", "/api/v5/public/instruments", params={"instType": "SPOT"}
        )
        for raw in spot_rows or []:
            inst = normalize_spot_instrument(raw)
            if inst is None:
                continue
            instruments.append(inst)
            native_map[(MarketType.SPOT, inst.native_symbol)] = inst.universal_symbol
            universal_map[inst.universal_symbol] = (MarketType.SPOT, inst.native_symbol)

        swap_rows = await self._api(
            "GET", "/api/v5/public/instruments", params={"instType": "SWAP"}
        )
        for raw in swap_rows or []:
            inst = normalize_swap_instrument(raw)
            if inst is None:
                continue
            instruments.append(inst)
            native_map[(inst.market_type, inst.native_symbol)] = inst.universal_symbol
            universal_map[inst.universal_symbol] = (inst.market_type, inst.native_symbol)

        self._native_map = native_map
        self._universal_map = universal_map
        return instruments

    async def fetch_reference_prices(self) -> dict[str, Decimal]:
        if not self._native_map:
            await self.fetch_instruments()
        prices: dict[str, Decimal] = {}
        for inst_type in ("SPOT", "SWAP"):
            rows = await self._api(
                "GET", "/api/v5/public/tickers", params={"instType": inst_type}
            )
            for raw in rows or []:
                native = raw.get("instId") or ""
                last = raw.get("last")
                if last in (None, ""):
                    continue
                for mt in (MarketType.SPOT, MarketType.PERP, MarketType.CMARGIN):
                    universal = self._native_map.get((mt, native))
                    if universal:
                        prices[universal] = _dec(last)
                        break
        return prices

    # -- credentials ------------------------------------------------------------

    async def verify_credential(self, credential: Credential) -> CredentialInfo:
        if not credential.get("passphrase"):
            return CredentialInfo(valid=False, reason="OKX credentials require a passphrase")
        try:
            rows = await self._api(
                "GET", "/api/v5/account/config", credential=credential
            )
        except Exception as exc:
            reason = credential_error_reason(self.exchange, exc)
            logger.warning("OKX credential verification failed: %s", reason, exc_info=True)
            return CredentialInfo(valid=False, reason=reason)
        info = (rows or [{}])[0]
        perm_raw = str(info.get("perm") or "")
        perms = {p.strip().lower() for p in perm_raw.split(",") if p.strip()}
        can_trade = "trade" in perms
        can_withdraw = "withdraw" in perms
        read_only = bool(perms) and not can_trade and not can_withdraw
        uid = str(info.get("uid") or "") or None
        main_uid = str(info.get("mainUid") or "") or None
        is_master = bool(uid and main_uid and uid == main_uid) if uid and main_uid else None
        return CredentialInfo(
            valid=True,
            native_uid=uid,
            is_master=is_master,
            read_only=read_only if perms else None,
            can_trade=can_trade if perms else None,
            can_withdraw=can_withdraw if perms else None,
            permissions={"perm": sorted(perms)} if perms else info,
        )

    # -- balances / positions ---------------------------------------------------

    async def fetch_balances(self, credential: Credential) -> list[Balance]:
        balances: list[Balance] = []
        try:
            rows = await self._api(
                "GET", "/api/v5/account/balance", credential=credential
            )
            details = []
            for acct in rows or []:
                details.extend(acct.get("details") or [])
            balances.extend(normalize_unified_balances(details))
        except OkxAPIError as exc:
            logger.debug("OKX trading balance unavailable: %s", exc)
        try:
            fund_rows = await self._api(
                "GET", "/api/v5/asset/balances", credential=credential
            )
            balances.extend(normalize_funding_balances(fund_rows or []))
        except OkxAPIError as exc:
            logger.debug("OKX funding balance unavailable: %s", exc)
        return balances

    async def fetch_positions(self, credential: Credential) -> list[Position]:
        if not self._native_map:
            await self.fetch_instruments()
        try:
            rows = await self._api(
                "GET",
                "/api/v5/account/positions",
                credential=credential,
                params={"instType": "SWAP"},
            )
        except OkxAPIError as exc:
            logger.debug("OKX positions unavailable: %s", exc)
            return []
        return normalize_positions(rows or [], self._native_map)

    # -- orders -----------------------------------------------------------------

    def _resolve(self, universal_symbol: str) -> tuple[MarketType, str]:
        if universal_symbol in self._universal_map:
            return self._universal_map[universal_symbol]
        parsed = parse_universal_symbol(universal_symbol)
        # Best-effort native reconstruction when maps are cold.
        pair = parsed.pair
        if parsed.market_type == MarketType.SPOT:
            # Insert dash before common quote suffixes.
            for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
                if pair.endswith(quote) and len(pair) > len(quote):
                    return parsed.market_type, f"{pair[:-len(quote)]}-{quote}"
        if parsed.market_type == MarketType.PERP:
            for quote in ("USDT", "USDC"):
                if pair.endswith(quote) and len(pair) > len(quote):
                    return parsed.market_type, f"{pair[:-len(quote)]}-{quote}-SWAP"
        if parsed.market_type == MarketType.CMARGIN:
            for quote in ("USD",):
                if pair.endswith(quote) and len(pair) > len(quote):
                    return parsed.market_type, f"{pair[:-len(quote)]}-{quote}-SWAP"
        raise InvalidUniversalSymbol(f"Cannot resolve OKX native symbol for {universal_symbol}")

    async def _ensure_maps(self) -> None:
        if not self._universal_map:
            await self.fetch_instruments()

    def _ord_type(self, request: OrderRequest) -> str:
        if request.type == OrderType.MARKET:
            return "market"
        tif = request.time_in_force or TimeInForce.GTC
        if tif == TimeInForce.POST_ONLY:
            return "post_only"
        if tif == TimeInForce.IOC:
            return "ioc"
        if tif == TimeInForce.FOK:
            return "fok"
        return "limit"

    def _pos_side(self, request: OrderRequest, market_type: MarketType) -> str | None:
        if market_type == MarketType.SPOT:
            return None
        side = request.position_side or PositionSide.BOTH
        if side == PositionSide.LONG:
            return "long"
        if side == PositionSide.SHORT:
            return "short"
        return "net"

    async def place_order(self, credential: Credential, request: OrderRequest) -> Order:
        await self._ensure_maps()
        market_type, native = self._resolve(request.universal_symbol)
        body: dict[str, Any] = {
            "instId": native,
            "tdMode": "cash" if market_type == MarketType.SPOT else "cross",
            "side": "buy" if request.side == OrderSide.BUY else "sell",
            "ordType": self._ord_type(request),
            "sz": str(request.qty),
        }
        if request.type == OrderType.LIMIT:
            if request.price is None:
                raise ValueError("Limit orders require a price")
            body["px"] = str(request.price)
        if request.client_order_id:
            body["clOrdId"] = request.client_order_id
        pos_side = self._pos_side(request, market_type)
        if pos_side:
            body["posSide"] = pos_side
        if request.reduce_only and market_type != MarketType.SPOT:
            body["reduceOnly"] = True
        raw = await self._api(
            "POST", "/api/v5/trade/order", credential=credential, body=body
        )
        row = (raw or [{}])[0]
        order_id = str(row.get("ordId") or "")
        fetched = await self.fetch_order(
            credential,
            request.universal_symbol,
            native_order_id=order_id or None,
            client_order_id=request.client_order_id,
        )
        if fetched:
            return fetched
        return Order(
            universal_symbol=request.universal_symbol,
            native_order_id=order_id,
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
        await self._ensure_maps()
        _, native = self._resolve(universal_symbol)
        await self._api(
            "POST",
            "/api/v5/trade/cancel-order",
            credential=credential,
            body={"instId": native, "ordId": native_order_id},
        )
        fetched = await self.fetch_order(
            credential, universal_symbol, native_order_id=native_order_id
        )
        if fetched:
            return fetched
        return Order(
            universal_symbol=universal_symbol,
            native_order_id=native_order_id,
            client_order_id=None,
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            status=OrderStatus.CANCELLED,
            qty=Decimal(0),
            filled_qty=Decimal(0),
            price=None,
        )

    async def fetch_open_orders(
        self, credential: Credential, universal_symbol: str | None = None
    ) -> list[Order]:
        await self._ensure_maps()
        params: dict[str, Any] = {}
        if universal_symbol:
            market_type, native = self._resolve(universal_symbol)
            params["instId"] = native
            params["instType"] = "SPOT" if market_type == MarketType.SPOT else "SWAP"
        else:
            # Pull both surfaces.
            orders: list[Order] = []
            for inst_type in ("SPOT", "SWAP"):
                rows = await self._api(
                    "GET",
                    "/api/v5/trade/orders-pending",
                    credential=credential,
                    params={"instType": inst_type},
                )
                for raw in rows or []:
                    universal = self._universal_for_inst(raw.get("instId") or "")
                    if universal:
                        orders.append(normalize_order(raw, universal))
            return orders
        rows = await self._api(
            "GET",
            "/api/v5/trade/orders-pending",
            credential=credential,
            params=params,
        )
        out: list[Order] = []
        for raw in rows or []:
            universal = universal_symbol or self._universal_for_inst(raw.get("instId") or "")
            if universal:
                out.append(normalize_order(raw, universal))
        return out

    async def fetch_order(
        self,
        credential: Credential,
        universal_symbol: str,
        native_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> Order | None:
        await self._ensure_maps()
        _, native = self._resolve(universal_symbol)
        params: dict[str, Any] = {"instId": native}
        if native_order_id:
            params["ordId"] = native_order_id
        elif client_order_id:
            params["clOrdId"] = client_order_id
        else:
            raise ValueError("fetch_order requires native_order_id or client_order_id")
        try:
            rows = await self._api(
                "GET", "/api/v5/trade/order", credential=credential, params=params
            )
        except OkxAPIError:
            return None
        if not rows:
            return None
        return normalize_order(rows[0], universal_symbol)

    def _universal_for_inst(self, inst_id: str) -> str | None:
        for mt in (MarketType.SPOT, MarketType.PERP, MarketType.CMARGIN):
            universal = self._native_map.get((mt, inst_id))
            if universal:
                return universal
        return None

    # -- history ----------------------------------------------------------------

    def history_stream_keys(self, active_symbols: list[str]) -> list[str]:
        return list(_HISTORY_STREAMS)

    def initial_history_cursor(self, start: datetime) -> str | None:
        return json.dumps({"start": int(start.timestamp() * 1000)})

    def funding_history_stream_keys(self) -> list[str]:
        return ["swap:linear", "swap:inverse"]

    def _stream_inst_type(self, stream_key: str) -> tuple[str, str | None]:
        """Return (instType, ctType filter or None)."""
        if stream_key == "spot":
            return "SPOT", None
        if stream_key == "swap:linear":
            return "SWAP", "linear"
        if stream_key == "swap:inverse":
            return "SWAP", "inverse"
        raise ValueError(f"Unknown OKX history stream {stream_key!r}")

    def _history_window(self, cursor: str | None) -> tuple[int, int, int, str | None]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        after: str | None = None
        if cursor:
            decoded = json.loads(cursor)
            start_ms = int(decoded["start"])
            after = decoded.get("after") or None
        else:
            start_ms = now_ms - int(DEFAULT_HISTORY_BACKFILL.total_seconds() * 1000)
        return start_ms, min(start_ms + _HISTORY_WINDOW_MS, now_ms), now_ms, after

    @staticmethod
    def _next_history_cursor(
        start_ms: int, end_ms: int, now_ms: int, after: str | None, page_full: bool
    ) -> str | None:
        if after and page_full:
            return json.dumps({"start": start_ms, "after": after})
        if end_ms < now_ms:
            return json.dumps({"start": end_ms})
        return None

    def _row_matches_ct(self, raw: dict, ct_filter: str | None) -> bool:
        if ct_filter is None:
            return True
        inst_id = raw.get("instId") or ""
        if ct_filter == "linear":
            return "-USDT-SWAP" in inst_id or "-USDC-SWAP" in inst_id
        if ct_filter == "inverse":
            return inst_id.endswith("-USD-SWAP") and "-USDT-" not in inst_id
        return True

    async def _history_page(
        self,
        credential: Credential,
        path: str,
        stream_key: str,
        cursor: str | None,
        limit: int,
        normalize,
        id_field: str,
    ) -> Page:
        if not self._native_map:
            await self.fetch_instruments()
        start_ms, end_ms, now_ms, after = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        inst_type, ct_filter = self._stream_inst_type(stream_key)
        params: dict[str, Any] = {
            "instType": inst_type,
            "begin": str(start_ms),
            "end": str(end_ms),
            "limit": str(limit),
        }
        if after:
            params["after"] = after
        rows = await self._api("GET", path, credential=credential, params=params) or []
        items = []
        last_id: str | None = None
        for raw in rows:
            if not self._row_matches_ct(raw, ct_filter):
                continue
            universal = self._universal_for_inst(raw.get("instId") or "")
            if universal is None:
                continue
            items.append(normalize(raw, universal))
            last_id = str(raw.get(id_field) or "") or last_id
        page_full = len(rows) >= limit
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, last_id if page_full else None, page_full
            ),
        )

    async def fetch_trades(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        return await self._history_page(
            credential,
            "/api/v5/trade/fills-history",
            stream_key,
            cursor,
            100,
            normalize_trade,
            "billId",
        )

    async def fetch_orders_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        return await self._history_page(
            credential,
            "/api/v5/trade/orders-history",
            stream_key,
            cursor,
            100,
            normalize_order,
            "ordId",
        )

    async def fetch_funding_fees_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        # Funding settlements appear as account bills type=8.
        if not self._native_map:
            await self.fetch_instruments()
        start_ms, end_ms, now_ms, after = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        _, ct_filter = self._stream_inst_type(stream_key)
        params: dict[str, Any] = {
            "instType": "SWAP",
            "type": "8",
            "begin": str(start_ms),
            "end": str(end_ms),
            "limit": "100",
        }
        if after:
            params["after"] = after
        rows = await self._api(
            "GET", "/api/v5/account/bills-archive", credential=credential, params=params
        ) or []
        items = []
        last_id: str | None = None
        for raw in rows:
            if not self._row_matches_ct(raw, ct_filter):
                continue
            universal = self._universal_for_inst(raw.get("instId") or "")
            if universal is None:
                continue
            items.append(normalize_funding_fee(raw, universal))
            last_id = str(raw.get("billId") or "") or last_id
        page_full = len(rows) >= 100
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, last_id if page_full else None, page_full
            ),
        )

    # -- provisioning -----------------------------------------------------------

    async def list_subaccounts(self, credential: Credential) -> list[SubaccountInfo]:
        rows = await self._api(
            "GET", "/api/v5/users/subaccount/list", credential=credential
        )
        out: list[SubaccountInfo] = []
        for m in rows or []:
            enable = m.get("enable")
            status = "normal" if enable in (True, "true", "True") else "frozen"
            out.append(
                SubaccountInfo(
                    native_uid=str(m.get("uid") or m.get("subAcct") or ""),
                    label=m.get("label") or m.get("subAcct") or "",
                    status=status,
                    raw=m,
                )
            )
        return out

    async def create_subaccount(self, credential: Credential, label: str) -> SubaccountInfo:
        sub_acct = _subacct_name(label)
        try:
            rows = await self._api(
                "POST",
                "/api/v5/users/subaccount/create-subaccount",
                credential=credential,
                body={"subAcct": sub_acct, "type": "1", "label": (label or sub_acct)[:32]},
            )
        except OkxAPIError as exc:
            if exc.code == "59518":
                raise OkxAPIError(
                    "OKX rejected sub-account creation via API for this master "
                    "(code 59518) — create it on the OKX website/app instead",
                    code=exc.code,
                    status_code=exc.status_code,
                ) from exc
            raise
        row = (rows or [{}])[0]
        sub_name = str(row.get("subAcct") or sub_acct)
        return SubaccountInfo(
            native_uid=str(row.get("uid") or sub_name),
            label=row.get("label") or label,
            status="normal",
            raw={"subAcct": sub_name, **row},
        )

    async def _sub_acct_for(self, credential: Credential, subaccount: str) -> str:
        """OKX key APIs want subAcct name; the panel may store the numeric uid."""
        resolved = await self._resolve_sub_acct(credential, subaccount)
        if not resolved:
            raise ValueError(f"Could not resolve OKX subAcct for {subaccount!r}")
        return resolved

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
            raise ValueError("OKX issues only HMAC keys via API")
        if not subaccount:
            raise ValueError("OKX API key creation targets a subaccount")
        passphrase = _okx_passphrase()
        body: dict[str, Any] = {
            "subAcct": await self._sub_acct_for(credential, subaccount),
            "label": (note or "cpanel")[:50],
            "passphrase": passphrase,
            "perm": _scope_perm(permissions, read_only),
        }
        if ips:
            body["ip"] = ",".join(ips)
        rows = await self._api(
            "POST",
            "/api/v5/users/subaccount/apikey",
            credential=credential,
            body=body,
        )
        row = (rows or [{}])[0]
        return ApiKeyResult(
            api_key=row["apiKey"],
            secret=row["secretKey"],
            scheme=KeyScheme.HMAC,
            permissions=permissions,
            passphrase=row.get("passphrase") or passphrase,
            ips=ips or [],
        )

    async def modify_api_key(
        self,
        credential: Credential,
        subaccount: str | None,
        api_key: str,
        read_only: bool | None = None,
        permissions: list[str] | None = None,
    ) -> None:
        if not subaccount:
            raise ValueError("OKX API key modify targets a subaccount")
        body: dict[str, Any] = {
            "subAcct": await self._sub_acct_for(credential, subaccount),
            "apiKey": api_key,
        }
        if read_only is not None or permissions is not None:
            body["perm"] = _scope_perm(permissions or [], bool(read_only))
        await self._api(
            "POST",
            "/api/v5/users/subaccount/modify-apikey",
            credential=credential,
            body=body,
        )

    async def revoke_api_key(
        self, credential: Credential, subaccount: str | None, api_key: str
    ) -> None:
        if not subaccount:
            raise ValueError("OKX API key revoke targets a subaccount")
        await self._api(
            "POST",
            "/api/v5/users/subaccount/delete-apikey",
            credential=credential,
            body={
                "subAcct": await self._sub_acct_for(credential, subaccount),
                "apiKey": api_key,
            },
        )

    async def list_api_keys(
        self, credential: Credential, subaccount: str | None = None
    ) -> list[ApiKeyInfo]:
        if not subaccount:
            return []
        rows = await self._api(
            "GET",
            "/api/v5/users/subaccount/apikey",
            credential=credential,
            params={"subAcct": await self._sub_acct_for(credential, subaccount)},
        )
        out: list[ApiKeyInfo] = []
        for row in rows or []:
            ip_raw = row.get("ip") or ""
            ips = [p.strip() for p in ip_raw.split(",") if p.strip()]
            out.append(
                ApiKeyInfo(
                    api_key=str(row.get("apiKey") or ""),
                    ips=ips,
                    label=row.get("label"),
                )
            )
        return out

    # -- transfers / withdrawals / deposits -----------------------------------

    async def _account_config(self, credential: Credential) -> dict:
        rows = await self._api("GET", "/api/v5/account/config", credential=credential)
        return (rows or [{}])[0]

    async def _resolve_sub_acct(
        self, credential: Credential, native_uid: str | None
    ) -> str | None:
        """Map a panel-stored uid to OKX subAcct name for transfer APIs."""
        if not native_uid:
            return None
        if not native_uid.isdigit():
            return native_uid
        for sub in await self.list_subaccounts(credential):
            if sub.native_uid == native_uid:
                raw = sub.raw or {}
                return str(raw.get("subAcct") or sub.label)
        return native_uid

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
        src = _WALLET_ACCOUNT.get(from_wallet or WalletType.UNIFIED)
        dst = _WALLET_ACCOUNT.get(to_wallet or WalletType.UNIFIED)
        if src is None or dst is None:
            raise ValueError("OKX wallets are UNIFIED and FUNDING")
        from_sub = await self._resolve_sub_acct(credential, from_subaccount)
        to_sub = await self._resolve_sub_acct(credential, to_subaccount)

        body: dict[str, Any] = {
            "ccy": asset.upper(),
            "amt": str(amount),
            "from": src,
            "to": dst,
        }
        if from_sub or to_sub:
            # type: 1 master→sub, 2 sub→master, 3/4 sub↔sub (use subaccount transfer).
            if from_sub and to_sub:
                rows = await self._api(
                    "POST",
                    "/api/v5/asset/subaccount/transfer",
                    credential=credential,
                    body={
                        "ccy": asset.upper(),
                        "amt": str(amount),
                        "from": src,
                        "to": dst,
                        "fromSubAccount": from_sub,
                        "toSubAccount": to_sub,
                    },
                )
            elif to_sub and not from_sub:
                body.update({"type": "1", "subAcct": to_sub})
                rows = await self._api(
                    "POST",
                    "/api/v5/asset/transfer",
                    credential=credential,
                    body=body,
                )
            else:
                body.update({"type": "2", "subAcct": from_sub})
                rows = await self._api(
                    "POST",
                    "/api/v5/asset/transfer",
                    credential=credential,
                    body=body,
                )
        else:
            if src == dst:
                raise ValueError(
                    "OKX internal transfers move between FUNDING and UNIFIED wallets"
                )
            body["type"] = "0"
            rows = await self._api(
                "POST", "/api/v5/asset/transfer", credential=credential, body=body
            )
        row = (rows or [{}])[0]
        return TransferResult(
            native_transfer_id=str(row.get("transId") or ""),
            status="confirmed",
        )

    async def fetch_asset_networks(
        self, credential: Credential, asset: str
    ) -> list[AssetNetwork]:
        rows = await self._api(
            "GET",
            "/api/v5/asset/currencies",
            credential=credential,
            params={"ccy": asset.upper()},
        )
        return normalize_asset_networks(rows or [], asset)

    async def fetch_deposit_address(
        self,
        credential: Credential,
        asset: str,
        network: str,
        subaccount: str | None = None,
    ) -> DepositAddress:
        if subaccount:
            raise ValueError(
                "OKX has no master-for-sub deposit address endpoint — "
                "register the subaccount's own API key to receive deposits"
            )
        rows = await self._api(
            "GET",
            "/api/v5/asset/deposit-address",
            credential=credential,
            params={"ccy": asset.upper()},
        )
        network_u = network.upper()
        chosen = None
        for row in rows or []:
            chain = (row.get("chain") or "").upper()
            # Match "USDT-ERC20" against network ERC20 / ETH.
            if chain.endswith(f"-{network_u}") or chain == network_u or network_u in chain:
                chosen = row
                break
        if chosen is None and rows:
            chosen = rows[0]
        if not chosen or not chosen.get("addr"):
            raise ValueError(f"OKX returned no {asset}/{network} deposit address")
        return DepositAddress(
            asset=asset.upper(),
            network=network_u,
            address=chosen["addr"],
            tag=chosen.get("tag") or chosen.get("memo") or None,
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
        # Resolve full chain name (e.g. USDT-ERC20) from currencies catalog.
        networks = await self._api(
            "GET",
            "/api/v5/asset/currencies",
            credential=credential,
            params={"ccy": asset.upper()},
        )
        chain = None
        network_u = network.upper()
        for row in networks or []:
            c = row.get("chain") or ""
            if c.upper().endswith(f"-{network_u}") or c.upper() == network_u:
                chain = c
                break
        if not chain:
            chain = f"{asset.upper()}-{network_u}"
        body: dict[str, Any] = {
            "ccy": asset.upper(),
            "amt": str(amount),
            "dest": "4",  # on-chain
            "toAddr": address,
            "chain": chain,
        }
        if tag:
            body["toAddr"] = f"{address}:{tag}" if ":" not in address else address
        rows = await self._api(
            "POST", "/api/v5/asset/withdrawal", credential=credential, body=body
        )
        row = (rows or [{}])[0]
        return Withdrawal(
            native_id=str(row.get("wdId") or ""),
            asset=asset.upper(),
            amount=_dec(amount),
            status=WithdrawalStatus.PENDING,
            address=address,
            network=network_u,
        )

    async def fetch_withdrawal(
        self, credential: Credential, native_id: str
    ) -> Withdrawal | None:
        rows = await self._api(
            "GET",
            "/api/v5/asset/withdrawal-history",
            credential=credential,
            params={"wdId": native_id},
        )
        if not rows:
            return None
        return normalize_withdrawal_record(rows[0])

    async def fetch_deposits(
        self,
        credential: Credential,
        asset: str | None = None,
        since: datetime | None = None,
        subaccount: str | None = None,
    ) -> list[Deposit]:
        if subaccount:
            raise ValueError(
                "OKX has no master-for-sub deposit history endpoint — "
                "use the subaccount's own credential"
            )
        params: dict[str, Any] = {}
        if asset:
            params["ccy"] = asset.upper()
        if since:
            params["after"] = str(int(since.timestamp() * 1000))
        rows = await self._api(
            "GET",
            "/api/v5/asset/deposit-history",
            credential=credential,
            params=params,
        )
        return [normalize_deposit_record(r) for r in (rows or [])]

    async def fetch_deposits_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        start_ms, end_ms, now_ms, after = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        params: dict[str, Any] = {
            "begin": str(start_ms),
            "end": str(end_ms),
            "limit": "100",
        }
        if after:
            params["after"] = after
        rows = await self._api(
            "GET",
            "/api/v5/asset/deposit-history",
            credential=credential,
            params=params,
        ) or []
        items = [normalize_deposit_record(r) for r in rows]
        last_id = str(rows[-1].get("ts") or rows[-1].get("depId") or "") if rows else None
        page_full = len(rows) >= 100
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, last_id if page_full else None, page_full
            ),
        )

    async def fetch_withdrawals_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        start_ms, end_ms, now_ms, after = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        params: dict[str, Any] = {
            "begin": str(start_ms),
            "end": str(end_ms),
            "limit": "100",
        }
        if after:
            params["after"] = after
        rows = await self._api(
            "GET",
            "/api/v5/asset/withdrawal-history",
            credential=credential,
            params=params,
        ) or []
        items = [normalize_withdrawal_record(r) for r in rows]
        last_id = str(rows[-1].get("ts") or rows[-1].get("wdId") or "") if rows else None
        page_full = len(rows) >= 100
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, last_id if page_full else None, page_full
            ),
        )
