"""Bitget connector (Unified Trading Account only).

Bitget serves everything from one host (api.bitget.com) with HMAC-SHA256
+ passphrase auth. Account, order, and transfer calls use the UTA v3 API.
Public instrument and ticker data use the same v3 market endpoints so native
symbols match trading (coin-margined perps are ``BTCUSD_CM``, not ``BTCUSD``).

    SPOT     category=SPOT
    PERP     category=USDT-FUTURES
    CMARGIN  category=COIN-FUTURES

USDC-margined perps and delivery contracts stay out of scope. Classic
spot/mix account endpoints are not called: every Bitget account on this
panel is a unified account. There is no official PyPI SDK — this module
signs raw HTTP (httpx) itself.

Design decisions and venue quirks:

- **Wallets**: ``UNIFIED`` (transfer leg ``uta``) and ``FUNDING`` (``spot``
  on the transfer API — UTA's funding wallet). Spot margin and futures
  equity live in the unified balance; there is no separate USD-M or coin-M
  wallet to move.
- **Credentials** always include a passphrase (``ACCESS-PASSPHRASE``).
  ``GET /api/v3/account/info`` reports ``uta_mgt`` / ``uta_trade`` /
  ``withdraw``.
- **History** is account-wide per category: ``SPOT``, ``USDT-FUTURES``,
  ``COIN-FUTURES``. Each query covers at most 24h (the venue allows 30).
  Pagination is the v3 ``cursor`` token; the stored cursor is JSON
  ``{"cursor": "...", "start": epoch_ms}``.
- **Main↔sub transfers** always send both user ids. An omitted side is
  the key's own uid from ``/api/v3/account/info``.
- **Provisioning** still uses virtual subaccounts
  (``/api/v2/user/...``). Master keys are registration-only (UI-pasted).
- **Spot market buys** are quote-sized on the wire. The panel qty stays in
  the base coin and is converted with the reference price at submit time.
  Futures orders assume one-way position mode unless ``position_side`` is set.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
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
from app.connectors.symbols import build_universal_symbol, parse_universal_symbol
from app.connectors.types import (
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

BASE_URL = "https://api.bitget.com"
_TIMEOUT = 15.0

# UTA category → panel market. Order matches history-stream order.
_CATEGORIES: list[tuple[str, MarketType]] = [
    ("SPOT", MarketType.SPOT),
    ("USDT-FUTURES", MarketType.PERP),
    ("COIN-FUTURES", MarketType.CMARGIN),
]
_CATEGORY_OF = {mt: cat for cat, mt in _CATEGORIES}
_MARKET_OF = {cat: mt for cat, mt in _CATEGORIES}
_FUTURE_CATEGORIES = [item for item in _CATEGORIES if item[1] != MarketType.SPOT]

_TIF = {
    TimeInForce.GTC: "gtc",
    TimeInForce.IOC: "ioc",
    TimeInForce.FOK: "fok",
    TimeInForce.POST_ONLY: "post_only",
}

# UTA transfer account types. Funding is ``spot`` on this API.
_WALLET_ACCOUNT = {
    WalletType.UNIFIED: "uta",
    WalletType.FUNDING: "spot",
}

# Cash funding settlements on GET /api/v3/account/financial-records.
_FUNDING_FEE_TYPES = frozenset({
    "CONTRACT_MAIN_SETTLE_FEE_USER_IN",
    "CONTRACT_MAIN_SETTLE_FEE_USER_OUT",
})

_PERMISSION_SCOPES = {
    "spot": "spot_trade",
    "derivatives": "contract_trade",
    "wallet": "transfer",
    "options": "margin_trade",
}


def _bitget_passphrase(length: int = 16) -> str:
    """Generate a Bitget-compliant passphrase: 8–32 letters + digits only."""
    length = max(8, min(length, 32))
    alphabet = string.ascii_letters + string.digits
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isalpha() for c in value) and any(c.isdigit() for c in value):
            return value

_SPOT_ORDER_STATUS = {
    "live": OrderStatus.OPEN,
    "new": OrderStatus.OPEN,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
    "canceled": OrderStatus.CANCELLED,
}

# Bitget withdraw status codes (string/int mixed across docs).
_WITHDRAWAL_STATUS = {
    "pending": WithdrawalStatus.PENDING,
    "pending_review": WithdrawalStatus.PENDING,
    "pending_processing": WithdrawalStatus.PENDING,
    "processing": WithdrawalStatus.PENDING,
    "reject": WithdrawalStatus.FAILED,
    "failed": WithdrawalStatus.FAILED,
    "fail": WithdrawalStatus.FAILED,
    "success": WithdrawalStatus.COMPLETED,
    "wallet_processing": WithdrawalStatus.SENT,
    "txid": WithdrawalStatus.SENT,
}

_DEPOSIT_STATUS = {
    "pending": DepositStatus.PENDING,
    "success": DepositStatus.CREDITED,
    "fail": DepositStatus.FAILED,
    "failed": DepositStatus.FAILED,
}

_HISTORY_WINDOW_MS = 24 * 60 * 60 * 1000
_TRADE_PAGE_LIMIT = 100
_ORDER_PAGE_LIMIT = 100

# UTA account-info permissions (GET /api/v3/account/info).
_TRADE_AUTHS = {"uta_trade", "uta_mgt"}
_WITHDRAW_AUTHS = {"withdraw"}


class BitgetAPIError(Exception):
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


def _step_from_precision(precision) -> Decimal:
    try:
        n = int(precision)
    except (TypeError, ValueError):
        n = 0
    return Decimal(1) / (Decimal(10) ** n) if n > 0 else Decimal(1)


def _ms_to_dt(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _spot_status(raw: str) -> str:
    # limit_open is still tradeable (limit orders only); restrictedAPI is not.
    if raw in ("online", "limit_open"):
        return "active"
    if raw == "offline":
        return "delisted"
    return "halted"


def _mix_status(raw: str) -> str:
    if raw in ("normal", "listed", "online", "limit_open"):
        return "active"
    if raw in ("off", "offline", "delisted"):
        return "delisted"
    return "halted"


def normalize_spot_instrument(info: dict) -> Instrument | None:
    symbol = info.get("symbol") or ""
    base = info.get("baseCoin") or ""
    quote = info.get("quoteCoin") or ""
    if not symbol or not base or not quote:
        return None
    # v3 spot publishes precision as decimal places. v2 used the same names.
    price_tick = _opt_dec(info.get("priceMultiplier")) or _step_from_precision(
        info.get("pricePrecision", 2)
    )
    qty_step = _opt_dec(info.get("quantityMultiplier")) or _step_from_precision(
        info.get("quantityPrecision", 4)
    )
    min_qty = _opt_dec(info.get("minOrderQty"))
    if min_qty is None:
        min_qty = _opt_dec(info.get("minTradeAmount")) or Decimal(0)
    max_qty = _opt_dec(info.get("maxOrderQty") or info.get("maxTradeAmount"))
    min_notional = _opt_dec(info.get("minOrderAmount"))
    if min_notional is None:
        min_notional = _opt_dec(info.get("minTradeUSDT"))
    return Instrument(
        universal_symbol=build_universal_symbol("BITGET", MarketType.SPOT, base, quote),
        exchange="BITGET",
        market_type=MarketType.SPOT,
        native_symbol=symbol.upper(),
        base_asset=base.upper(),
        quote_asset=quote.upper(),
        filters=InstrumentFilters(
            price_tick=price_tick,
            qty_step=qty_step,
            min_qty=min_qty,
            max_qty=max_qty,
            min_notional=min_notional,
        ),
        status=_spot_status(str(info.get("status", ""))),
    )


def normalize_mix_instrument(info: dict, market_type: MarketType) -> Instrument | None:
    # v3 ``type`` is perpetual/delivery; v2 used ``symbolType`` for that.
    contract_type = str(info.get("type") or "").lower()
    legacy_type = str(info.get("symbolType") or "").lower()
    if contract_type and contract_type not in ("perpetual", "perp"):
        return None
    if not contract_type and legacy_type in ("delivery",):
        return None
    if info.get("deliveryPeriod") not in (None, "", "0", 0):
        return None
    symbol = info.get("symbol") or ""
    base = info.get("baseCoin") or ""
    quote = info.get("quoteCoin") or ""
    if not symbol or not base or not quote:
        return None
    # v3 quantityMultiplier is the real step (SHIB is 10000, not 10^0).
    # v2 used pricePlace/priceEndStep and volumePlace.
    price_tick = _opt_dec(info.get("priceMultiplier"))
    if price_tick is None:
        price_place = int(info.get("pricePlace") or info.get("pricePrecision") or 2)
        price_end = _dec(info.get("priceEndStep") or 1)
        price_tick = price_end * _step_from_precision(price_place)
    qty_mult = _opt_dec(info.get("quantityMultiplier"))
    if qty_mult is not None:
        qty_step = qty_mult
    else:
        vol_place = info.get("volumePlace")
        if vol_place is not None:
            qty_step = _step_from_precision(vol_place)
        else:
            qty_step = _opt_dec(info.get("sizeMultiplier")) or Decimal(1)
    min_qty = _opt_dec(info.get("minOrderQty") or info.get("minTradeNum")) or qty_step
    max_qty = _opt_dec(info.get("maxOrderQty") or info.get("maxTradeNum"))
    settle = (info.get("quoteCoin") if market_type == MarketType.PERP else info.get("baseCoin") or base)
    contract_size = _opt_dec(info.get("sizeMultiplier") or info.get("contractSize"))
    return Instrument(
        universal_symbol=build_universal_symbol("BITGET", market_type, base, quote),
        exchange="BITGET",
        market_type=market_type,
        native_symbol=symbol.upper(),
        base_asset=base.upper(),
        quote_asset=quote.upper(),
        filters=InstrumentFilters(
            price_tick=price_tick,
            qty_step=qty_step,
            min_qty=min_qty,
            max_qty=max_qty,
        ),
        status=_mix_status(str(info.get("symbolStatus") or info.get("status") or "")),
        contract_size=contract_size,
        settle_asset=settle.upper() if settle else None,
    )


def normalize_uta_balances(rows: list[dict]) -> list[Balance]:
    """Unified-account asset rows. Equity covers balance plus unrealized PnL."""
    balances: list[Balance] = []
    for row in rows:
        free = _dec(row.get("available") or 0)
        locked = _dec(row.get("frozen") or 0) + _dec(row.get("locked") or 0)
        if free + locked <= 0:
            equity = _dec(row.get("equity") or row.get("balance") or 0)
            if equity <= 0:
                continue
            free = equity
        asset = (row.get("coin") or row.get("coinName") or "").upper()
        if not asset:
            continue
        balances.append(
            Balance(wallet=WalletType.UNIFIED, asset=asset, free=free, locked=locked)
        )
    return balances


def normalize_funding_balances(rows: list[dict]) -> list[Balance]:
    balances: list[Balance] = []
    for row in rows:
        free = _dec(row.get("available") or 0)
        locked = _dec(row.get("frozen") or 0) + _dec(row.get("locked") or 0)
        if free + locked <= 0:
            continue
        asset = (row.get("coin") or row.get("coinName") or "").upper()
        if not asset:
            continue
        balances.append(Balance(wallet=WalletType.FUNDING, asset=asset, free=free, locked=locked))
    return balances


def normalize_positions(
    rows: list[dict],
    product_type: str,
    universal_of: dict[tuple[MarketType, str], str],
) -> list[Position]:
    market_type = _MARKET_OF[product_type]
    out: list[Position] = []
    for row in rows:
        size = _dec(row.get("total") or row.get("available") or 0)
        if size == 0:
            continue
        native = (row.get("symbol") or "").upper()
        universal = universal_of.get((market_type, native))
        if universal is None:
            continue
        hold = str(row.get("posSide") or row.get("holdSide") or "long").lower()
        side = "short" if hold == "short" else "long"
        out.append(
            Position(
                universal_symbol=universal,
                side=side,
                size=abs(size),
                entry_price=_dec(
                    row.get("avgPrice") or row.get("openPriceAvg") or row.get("averageOpenPrice") or 0
                ),
                mark_price=_opt_dec(row.get("markPrice")),
                unrealized_pnl=_opt_dec(
                    row.get("unrealisedPnl") or row.get("unrealizedPL") or row.get("unrealizedPnl")
                ),
                margin=_opt_dec(row.get("positionBalance") or row.get("marginSize") or row.get("margin")),
            )
        )
    return out


def _order_side(raw: str) -> OrderSide:
    return OrderSide.BUY if str(raw).lower() in ("buy", "open_long", "close_short") else OrderSide.SELL


def _order_type(raw: str) -> OrderType:
    return OrderType.MARKET if str(raw).lower() == "market" else OrderType.LIMIT


def normalize_spot_order(raw: dict, universal_symbol: str) -> Order:
    status = _SPOT_ORDER_STATUS.get(
        str(raw.get("orderStatus") or raw.get("status") or "").lower(), OrderStatus.OPEN
    )
    price = _opt_dec(raw.get("avgPrice") or raw.get("priceAvg") or raw.get("price"))
    created = raw.get("createdTime") or raw.get("cTime")
    return Order(
        universal_symbol=universal_symbol,
        native_order_id=str(raw.get("orderId") or ""),
        client_order_id=raw.get("clientOid") or None,
        side=_order_side(raw.get("side", "")),
        type=_order_type(raw.get("orderType", "")),
        status=status,
        qty=_dec(raw.get("qty") or raw.get("size") or raw.get("quantity") or 0),
        filled_qty=_dec(
            raw.get("cumExecQty") or raw.get("baseVolume") or raw.get("filledQty") or raw.get("accBaseVolume") or 0
        ),
        price=price,
        created_at=_ms_to_dt(created),
        updated_at=_ms_to_dt(raw.get("updatedTime") or raw.get("uTime") or created),
    )


def normalize_mix_order(raw: dict, universal_symbol: str) -> Order:
    return normalize_spot_order(raw, universal_symbol)


def _fee_parts(raw: dict) -> tuple[Decimal, str]:
    detail = raw.get("feeDetail")
    if isinstance(detail, str) and detail:
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            detail = {}
    if isinstance(detail, dict):
        fee = abs(_dec(detail.get("totalFee") or detail.get("fee") or 0))
        asset = str(detail.get("feeCoin") or detail.get("feeCcy") or "")
        return fee, asset
    if isinstance(detail, list) and detail:
        first = detail[0] if isinstance(detail[0], dict) else {}
        return abs(_dec(first.get("totalFee") or first.get("fee") or 0)), str(
            first.get("feeCoin") or ""
        )
    return abs(_dec(raw.get("fee") or 0)), str(raw.get("feeCoin") or "")


def normalize_spot_trade(raw: dict, universal_symbol: str) -> Trade:
    fee, fee_asset = _fee_parts(raw)
    traded = raw.get("createdTime") or raw.get("cTime")
    return Trade(
        universal_symbol=universal_symbol,
        native_trade_id=str(raw.get("execId") or raw.get("tradeId") or raw.get("fillId") or ""),
        native_order_id=str(raw.get("orderId") or ""),
        side=_order_side(raw.get("side", "")),
        price=_dec(raw.get("execPrice") or raw.get("priceAvg") or raw.get("price") or 0),
        qty=_dec(raw.get("execQty") or raw.get("size") or raw.get("baseVolume") or 0),
        fee=fee,
        fee_asset=fee_asset,
        traded_at=_ms_to_dt(traded),
    )


def normalize_mix_trade(raw: dict, universal_symbol: str) -> Trade:
    return normalize_spot_trade(raw, universal_symbol)


def normalize_withdrawal_record(raw: dict) -> Withdrawal:
    status_raw = str(raw.get("status") or raw.get("withdrawStatus") or "").lower()
    status = _WITHDRAWAL_STATUS.get(status_raw, WithdrawalStatus.PENDING)
    return Withdrawal(
        native_id=str(raw.get("orderId") or raw.get("withdrawId") or raw.get("id") or ""),
        asset=(raw.get("coin") or "").upper(),
        amount=_dec(raw.get("size") or raw.get("amount") or 0),
        status=status,
        address=raw.get("address") or raw.get("toAddress") or None,
        network=(raw.get("chain") or raw.get("dest") or None),
        tx_id=raw.get("tradeId") or raw.get("recordId") or raw.get("txId") or raw.get("txid") or None,
        fee=(abs(fee) if (fee := _opt_dec(raw.get("fee"))) is not None else None),
        created_at=_ms_to_dt(raw.get("createdTime") or raw.get("cTime") or raw.get("createtime")),
    )


def normalize_deposit_record(raw: dict) -> Deposit:
    status_raw = str(raw.get("status") or raw.get("depositStatus") or "").lower()
    # Numeric status: 1 pending, 2 success on some endpoints.
    if status_raw.isdigit():
        status = {
            "1": DepositStatus.PENDING,
            "2": DepositStatus.CREDITED,
            "3": DepositStatus.FAILED,
        }.get(status_raw, DepositStatus.PENDING)
    else:
        status = _DEPOSIT_STATUS.get(status_raw, DepositStatus.PENDING)
    created = _ms_to_dt(raw.get("createdTime") or raw.get("cTime") or raw.get("createtime"))
    return Deposit(
        native_id=str(raw.get("orderId") or raw.get("depositId") or raw.get("id") or ""),
        asset=(raw.get("coin") or "").upper(),
        amount=_dec(raw.get("size") or raw.get("amount") or 0),
        status=status,
        network=(raw.get("chain") or raw.get("dest") or None),
        tx_id=raw.get("tradeId") or raw.get("recordId") or raw.get("txId") or raw.get("txid") or None,
        credited_at=created if status == DepositStatus.CREDITED else None,
        created_at=created,
    )


def normalize_asset_networks(coin_row: dict) -> list[AssetNetwork]:
    networks: list[AssetNetwork] = []
    for chain in coin_row.get("chains") or coin_row.get("chainList") or []:
        network = str(chain.get("chain") or chain.get("chainName") or "").upper()
        if not network:
            continue
        withdrawable = str(chain.get("withdrawable") or chain.get("withdrawEnable") or "true").lower()
        rechargeable = str(chain.get("rechargeable") or chain.get("depositEnable") or "true").lower()
        networks.append(
            AssetNetwork(
                network=network,
                deposit_enabled=rechargeable in ("true", "1", "yes"),
                withdraw_enabled=withdrawable in ("true", "1", "yes"),
                withdraw_min=_dec(chain.get("minWithdrawAmount") or chain.get("withdrawMin") or 0),
                withdraw_fee=_dec(chain.get("withdrawFee") or chain.get("fee") or 0),
            )
        )
    return networks


def normalize_funding_fee(raw: dict, universal_symbol: str) -> FundingFee:
    amount = _dec(raw.get("amount") or raw.get("fundingFee") or 0)
    # Bitget sign: positive = receive on some endpoints; keep as reported.
    return FundingFee(
        universal_symbol=universal_symbol,
        native_id=str(raw.get("billId") or raw.get("id") or raw.get("fundingId") or ""),
        asset=(raw.get("coin") or raw.get("marginCoin") or "USDT").upper(),
        amount=amount,
        funding_rate=_opt_dec(raw.get("fundingRate") or raw.get("rate")),
        settled_at=_ms_to_dt(raw.get("ts") or raw.get("cTime") or raw.get("settleTime")),
    )


def _page_rows(data: Any) -> tuple[list[dict], str | None]:
    """Normalize a v3 page (``{list, cursor}``) or a bare list."""
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        rows = data.get("list") or data.get("bills") or data.get("assets") or []
        cursor = data.get("cursor") or None
        return list(rows), (str(cursor) if cursor else None)
    return [], None


def _sign(
    secret: str, timestamp: str, method: str, path: str, query: str, body: str
) -> str:
    message = timestamp + method.upper() + path
    if query:
        message += "?" + query
    if body:
        message += body
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class BitgetConnector(ExchangeConnector):
    exchange = "BITGET"
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
        query = urlencode(params or {}, doseq=True)
        body_str = json.dumps(body) if body is not None else ""
        headers = {"Content-Type": "application/json", "locale": "en-US"}
        if credential is not None:
            passphrase = credential.get("passphrase")
            if not passphrase:
                raise BitgetAPIError("Bitget credentials require a passphrase")
            timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
            headers.update(
                {
                    "ACCESS-KEY": credential["api_key"],
                    "ACCESS-SIGN": _sign(
                        credential["secret"], timestamp, method, path, query, body_str
                    ),
                    "ACCESS-TIMESTAMP": timestamp,
                    "ACCESS-PASSPHRASE": passphrase,
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
            raise BitgetAPIError(
                f"Bitget non-JSON response (HTTP {resp.status_code})",
                status_code=resp.status_code,
            ) from exc
        code = str(payload.get("code", ""))
        if not resp.is_success or code != "00000":
            raise BitgetAPIError(
                payload.get("msg") or f"Bitget error code={code}",
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

    async def _fetch_spot_symbols(self) -> list[dict]:
        data = await self._api(
            "GET", "/api/v3/market/instruments", params={"category": "SPOT"}
        )
        return data or []

    async def _fetch_mix_contracts(self, product_type: str) -> list[dict]:
        data = await self._api(
            "GET", "/api/v3/market/instruments", params={"category": product_type}
        )
        return data or []

    async def _fetch_spot_tickers(self) -> list[dict]:
        data = await self._api(
            "GET", "/api/v3/market/tickers", params={"category": "SPOT"}
        )
        return data or []

    async def _fetch_mix_tickers(self, product_type: str) -> list[dict]:
        data = await self._api(
            "GET", "/api/v3/market/tickers", params={"category": product_type}
        )
        return data or []

    async def fetch_instruments(self) -> list[Instrument]:
        instruments: list[Instrument] = []
        native_map: dict[tuple[MarketType, str], str] = {}
        universal_map: dict[str, tuple[MarketType, str]] = {}

        for raw in await self._fetch_spot_symbols():
            inst = normalize_spot_instrument(raw)
            if inst is None:
                continue
            instruments.append(inst)
            native_map[(MarketType.SPOT, inst.native_symbol)] = inst.universal_symbol
            universal_map[inst.universal_symbol] = (MarketType.SPOT, inst.native_symbol)

        for product_type, market_type in _FUTURE_CATEGORIES:
            for raw in await self._fetch_mix_contracts(product_type):
                inst = normalize_mix_instrument(raw, market_type)
                if inst is None:
                    continue
                instruments.append(inst)
                native_map[(market_type, inst.native_symbol)] = inst.universal_symbol
                universal_map[inst.universal_symbol] = (market_type, inst.native_symbol)

        self._native_map = native_map
        self._universal_map = universal_map
        return instruments

    async def fetch_reference_prices(self) -> dict[str, Decimal]:
        if not self._native_map:
            await self.fetch_instruments()
        prices: dict[str, Decimal] = {}
        for raw in await self._fetch_spot_tickers():
            native = (raw.get("symbol") or "").upper()
            universal = self._native_map.get((MarketType.SPOT, native))
            last = raw.get("lastPrice") or raw.get("lastPr") or raw.get("close") or raw.get("last")
            if universal and last not in (None, ""):
                prices[universal] = _dec(last)
        for product_type, market_type in _FUTURE_CATEGORIES:
            for raw in await self._fetch_mix_tickers(product_type):
                native = (raw.get("symbol") or "").upper()
                universal = self._native_map.get((market_type, native))
                last = raw.get("lastPrice") or raw.get("lastPr") or raw.get("last") or raw.get("markPrice")
                if universal and last not in (None, ""):
                    prices[universal] = _dec(last)
        return prices

    # -- credentials ------------------------------------------------------------

    async def verify_credential(self, credential: Credential) -> CredentialInfo:
        try:
            info = await self._api(
                "GET", "/api/v3/account/info", credential=credential
            )
        except Exception as exc:
            reason = credential_error_reason(self.exchange, exc)
            logger.warning("Bitget credential verification failed: %s", reason, exc_info=True)
            return CredentialInfo(valid=False, reason=reason)
        authorities = {
            str(a).lower()
            for a in (info.get("permissions") or info.get("authorities") or [])
        }
        can_trade = bool(authorities & _TRADE_AUTHS) or any(
            "trade" in a for a in authorities
        )
        can_withdraw = bool(authorities & _WITHDRAW_AUTHS) or any(
            "withdraw" in a for a in authorities
        )
        perm_type = str(info.get("permType") or "").lower()
        read_only = perm_type == "read-only" or (
            bool(authorities) and not can_trade and not can_withdraw
        )
        uid = str(info.get("userId") or "")
        parent = str(info.get("parentId") or "")
        # A master has an empty parent. Some responses echo the account's own uid.
        is_master = parent in ("", "0") or parent == uid
        ips = [part.strip() for part in str(info.get("ips") or "").split(",") if part.strip()]
        return CredentialInfo(
            valid=True,
            native_uid=uid or None,
            is_master=is_master,
            read_only=read_only if authorities or perm_type else None,
            can_trade=can_trade if authorities else None,
            can_withdraw=can_withdraw if authorities else None,
            permissions={"authorities": sorted(authorities)} if authorities else info,
            ips=ips or None,
        )

    # -- balances / positions ---------------------------------------------------

    async def _fetch_uta_assets(self, credential: Credential) -> list[dict]:
        data = await self._api(
            "GET", "/api/v3/account/assets", credential=credential
        )
        if isinstance(data, dict):
            return data.get("assets") or []
        return data or []

    async def _fetch_funding_assets(self, credential: Credential) -> list[dict]:
        data = await self._api(
            "GET", "/api/v3/account/funding-assets", credential=credential
        )
        return data or []

    async def _fetch_positions(
        self, credential: Credential, category: str
    ) -> list[dict]:
        data = await self._api(
            "GET",
            "/api/v3/position/current-position",
            credential=credential,
            params={"category": category},
        )
        if isinstance(data, dict):
            return data.get("list") or []
        return data or []

    async def fetch_balances(self, credential: Credential) -> list[Balance]:
        balances = normalize_uta_balances(await self._fetch_uta_assets(credential))
        try:
            balances.extend(
                normalize_funding_balances(await self._fetch_funding_assets(credential))
            )
        except BitgetAPIError as exc:
            logger.debug("Bitget funding assets unavailable: %s", exc)
        return balances

    async def fetch_positions(self, credential: Credential) -> list[Position]:
        if not self._native_map:
            await self.fetch_instruments()
        positions: list[Position] = []
        errors: list[BitgetAPIError] = []
        for category, market_type in _FUTURE_CATEGORIES:
            try:
                rows = await self._fetch_positions(credential, category)
            except BitgetAPIError as exc:
                errors.append(exc)
                logger.debug("Bitget %s positions unavailable: %s", category, exc)
                continue
            positions.extend(normalize_positions(rows, category, self._native_map))
        if errors and len(errors) == len(_FUTURE_CATEGORIES):
            raise errors[0]
        return positions

    # -- orders -----------------------------------------------------------------

    def _resolve(self, universal_symbol: str) -> tuple[MarketType, str]:
        if universal_symbol in self._universal_map:
            return self._universal_map[universal_symbol]
        parsed = parse_universal_symbol(universal_symbol)
        # Best-effort: native often equals the pair for Bitget.
        return parsed.market_type, parsed.pair

    async def _ensure_maps(self) -> None:
        if not self._universal_map:
            await self.fetch_instruments()

    async def _spot_market_buy_qty(self, request: OrderRequest) -> str:
        prices = await self.fetch_reference_prices()
        last = prices.get(request.universal_symbol)
        if last is None or last <= 0:
            raise ValueError(
                "Bitget UTA spot market buys are sized in the quote coin, "
                "and no reference price is available to convert the base quantity"
            )
        return str(request.qty * last)

    async def place_order(self, credential: Credential, request: OrderRequest) -> Order:
        await self._ensure_maps()
        market_type, native = self._resolve(request.universal_symbol)
        if market_type not in _CATEGORY_OF:
            raise ValueError(f"Bitget UTA does not trade {request.universal_symbol}")
        is_market = request.type == OrderType.MARKET
        if (
            market_type == MarketType.SPOT
            and is_market
            and request.side == OrderSide.BUY
        ):
            qty = await self._spot_market_buy_qty(request)
        else:
            qty = str(request.qty)
        body: dict[str, Any] = {
            "category": _CATEGORY_OF[market_type],
            "symbol": native,
            "qty": qty,
            "side": "buy" if request.side == OrderSide.BUY else "sell",
            "orderType": "market" if is_market else "limit",
        }
        if is_market:
            body["timeInForce"] = "ioc"
        else:
            if request.price is None:
                raise ValueError("Limit orders require a price")
            body["price"] = str(request.price)
            body["timeInForce"] = _TIF.get(request.time_in_force or TimeInForce.GTC, "gtc")
        if market_type != MarketType.SPOT:
            if request.reduce_only:
                body["reduceOnly"] = "yes"
            if request.position_side is not None and request.position_side.value != "BOTH":
                body["posSide"] = request.position_side.value.lower()
        if request.client_order_id:
            body["clientOid"] = request.client_order_id
        raw = await self._api(
            "POST", "/api/v3/trade/place-order", credential=credential, body=body
        )
        order_id = str((raw or {}).get("orderId") or "")
        fetched = await self.fetch_order(
            credential, request.universal_symbol, native_order_id=order_id or None,
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
        market_type, _native = self._resolve(universal_symbol)
        await self._api(
            "POST",
            "/api/v3/trade/cancel-order",
            credential=credential,
            body={"orderId": native_order_id, "category": _CATEGORY_OF[market_type]},
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

    def _orders_from_rows(
        self, rows: list[dict], market_type: MarketType, universal_symbol: str | None
    ) -> list[Order]:
        orders: list[Order] = []
        for raw in rows:
            native = (raw.get("symbol") or "").upper()
            universal = self._native_map.get((market_type, native))
            if universal is None:
                continue
            if universal_symbol and universal != universal_symbol:
                continue
            orders.append(normalize_spot_order(raw, universal))
        return orders

    async def fetch_open_orders(
        self, credential: Credential, universal_symbol: str | None = None
    ) -> list[Order]:
        await self._ensure_maps()
        orders: list[Order] = []
        categories = _CATEGORIES
        if universal_symbol:
            market_type, native = self._resolve(universal_symbol)
            categories = [(_CATEGORY_OF[market_type], market_type)]
        else:
            native = ""
        for category, market_type in categories:
            params: dict[str, str] = {"category": category}
            if universal_symbol:
                params["symbol"] = native
            try:
                data = await self._api(
                    "GET",
                    "/api/v3/trade/unfilled-orders",
                    credential=credential,
                    params=params,
                )
            except BitgetAPIError:
                if universal_symbol:
                    raise
                continue
            rows, _cursor = _page_rows(data)
            orders.extend(self._orders_from_rows(rows, market_type, universal_symbol))
        return orders

    async def fetch_order(
        self,
        credential: Credential,
        universal_symbol: str,
        native_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> Order | None:
        await self._ensure_maps()
        if not native_order_id and not client_order_id:
            return None
        params: dict[str, str] = {}
        if native_order_id:
            params["orderId"] = native_order_id
        if client_order_id:
            params["clientOid"] = client_order_id
        try:
            data = await self._api(
                "GET", "/api/v3/trade/order-info", credential=credential, params=params
            )
        except BitgetAPIError:
            return None
        raw = data if isinstance(data, dict) else None
        if not raw or not raw.get("orderId"):
            return None
        return normalize_spot_order(raw, universal_symbol)

    # -- history ----------------------------------------------------------------

    def history_stream_keys(self, active_symbols: list[str]) -> list[str]:
        return [category for category, _ in _CATEGORIES]

    def initial_history_cursor(self, start: datetime) -> str | None:
        return json.dumps({"start": int(start.timestamp() * 1000), "cursor": ""})

    def _history_window(self, cursor: str | None) -> tuple[int, int, int, str]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        page_cursor = ""
        if cursor:
            decoded = json.loads(cursor)
            start_ms = int(decoded["start"])
            page_cursor = str(decoded.get("cursor") or "")
        else:
            start_ms = now_ms - int(DEFAULT_HISTORY_BACKFILL.total_seconds() * 1000)
        return start_ms, min(start_ms + _HISTORY_WINDOW_MS, now_ms), now_ms, page_cursor

    @staticmethod
    def _next_history_cursor(
        start_ms: int,
        end_ms: int,
        now_ms: int,
        rows: list[dict],
        limit: int,
        page_cursor: str | None,
    ) -> str | None:
        if page_cursor and len(rows) >= limit:
            return json.dumps({"start": start_ms, "cursor": page_cursor})
        if len(rows) >= limit and not page_cursor:
            times = []
            for raw in rows:
                for key in ("createdTime", "cTime", "ts"):
                    value = raw.get(key)
                    if value in (None, ""):
                        continue
                    times.append(int(value))
                    break
            if times and start_ms < max(times) + 1 < now_ms:
                return json.dumps({"start": max(times) + 1, "cursor": ""})
        if end_ms < now_ms:
            return json.dumps({"start": end_ms, "cursor": ""})
        return None

    async def _history_page(
        self, credential: Credential, stream_key: str, cursor: str | None, kind: str
    ) -> Page:
        await self._ensure_maps()
        market_type = _MARKET_OF.get(stream_key)
        if market_type is None:
            raise ValueError(f"Unknown Bitget history stream {stream_key!r}")
        limit = _TRADE_PAGE_LIMIT if kind == "trades" else _ORDER_PAGE_LIMIT
        start_ms, end_ms, now_ms, page_cursor = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)

        params: dict[str, str] = {
            "category": stream_key,
            "startTime": str(start_ms),
            "endTime": str(end_ms),
            "limit": str(limit),
        }
        if page_cursor:
            params["cursor"] = page_cursor
        path = "/api/v3/trade/fills" if kind == "trades" else "/api/v3/trade/history-orders"
        data = await self._api("GET", path, credential=credential, params=params)
        rows, next_token = _page_rows(data)
        items: list = []
        for raw in rows:
            universal = self._native_map.get(
                (market_type, (raw.get("symbol") or "").upper())
            )
            if universal is None:
                continue
            items.append(
                normalize_spot_trade(raw, universal)
                if kind == "trades"
                else normalize_spot_order(raw, universal)
            )
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, rows, limit, next_token
            ),
        )

    async def fetch_trades(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        return await self._history_page(credential, stream_key, cursor, "trades")

    async def fetch_orders_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        return await self._history_page(credential, stream_key, cursor, "orders")

    # -- subaccounts / keys -----------------------------------------------------

    async def list_subaccounts(self, credential: Credential) -> list[SubaccountInfo]:
        data = await self._api(
            "GET",
            "/api/v2/user/virtual-subaccount-list",
            credential=credential,
            params={"limit": "100"},
        )
        rows = data if isinstance(data, list) else (data or {}).get("subAccountList") or []
        return [
            SubaccountInfo(
                native_uid=str(row.get("subAccountUid") or row.get("userId") or ""),
                label=row.get("subAccountName") or row.get("label") or str(
                    row.get("subAccountUid") or ""
                ),
                status=str(row.get("status") or "normal"),
                raw=row,
            )
            for row in rows
            if row.get("subAccountUid") or row.get("userId")
        ]

    async def create_subaccount(self, credential: Credential, label: str) -> SubaccountInfo:
        # Virtual sub names: letters/digits, often max 8 chars.
        alias = "".join(ch for ch in label if ch.isalnum())[:8] or "sub"
        alias = f"{alias}{secrets.token_hex(2)}"[:16]
        data = await self._api(
            "POST",
            "/api/v2/user/create-virtual-subaccount",
            credential=credential,
            body={"subAccountList": [alias]},
        )
        # Response may be list of created uids or a map.
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                uid = str(first.get("subAccountUid") or first.get("userId") or "")
                name = first.get("subAccountName") or alias
            else:
                uid = str(first)
                name = alias
        elif isinstance(data, dict):
            created = data.get("successList") or data.get("subAccountList") or []
            if created and isinstance(created[0], dict):
                uid = str(created[0].get("subAccountUid") or "")
                name = created[0].get("subAccountName") or alias
            else:
                uid = str(created[0]) if created else ""
                name = alias
        else:
            uid, name = "", alias
        return SubaccountInfo(native_uid=uid, label=label or name, status="normal")

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
            raise ValueError("Bitget issues only HMAC keys via API")
        if not subaccount:
            raise ValueError("Bitget API key creation targets a virtual subaccount uid")
        # Bitget requires 8–32 alphanumeric chars (letters + digits); urlsafe
        # tokens can include '-' / '_' and get rejected.
        passphrase = _bitget_passphrase()
        perm_list = ["read"]
        if not read_only:
            for scope in permissions:
                token = _PERMISSION_SCOPES.get(scope)
                if token and token not in perm_list:
                    perm_list.append(token)
        body: dict[str, Any] = {
            "subAccountUid": subaccount,
            "passphrase": passphrase,
            "label": (note or "cpanel")[:20],
            "permList": perm_list,
        }
        if ips:
            body["ipList"] = ips
        data = await self._api(
            "POST",
            "/api/v2/user/create-virtual-subaccount-apikey",
            credential=credential,
            body=body,
        )
        data = data or {}
        # Venue field names: subAccountApiKey / secretKey (not apiKey / secret).
        api_key = str(
            data.get("subAccountApiKey")
            or data.get("apiKey")
            or data.get("accessKey")
            or ""
        )
        secret = str(data.get("secretKey") or data.get("secret") or "")
        if not api_key or not secret:
            raise BitgetAPIError(
                "Bitget create-virtual-subaccount-apikey returned no api key/secret"
            )
        return ApiKeyResult(
            api_key=api_key,
            secret=secret,
            scheme=KeyScheme.HMAC,
            permissions=perm_list,
            # Response does not echo the passphrase; keep the one we sent.
            passphrase=passphrase,
        )

    # -- transfers --------------------------------------------------------------

    def _wallet_leg(self, wallet: WalletType | None) -> str:
        wallet = wallet or WalletType.UNIFIED
        leg = _WALLET_ACCOUNT.get(wallet)
        if leg is None:
            raise ValueError("Bitget UTA wallets are UNIFIED and FUNDING")
        return leg

    async def _own_uid(self, credential: Credential) -> str:
        info = await self._api("GET", "/api/v3/account/info", credential=credential)
        uid = str((info or {}).get("userId") or "")
        if not uid:
            raise BitgetAPIError("Bitget account info returned no userId")
        return uid

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
        asset = asset.upper()
        src = self._wallet_leg(from_wallet)
        dst = self._wallet_leg(to_wallet)
        client_oid = secrets.token_hex(16)
        if from_subaccount or to_subaccount:
            # Both user ids are required. An omitted side is this key's account.
            from_uid = from_subaccount or await self._own_uid(credential)
            to_uid = to_subaccount or await self._own_uid(credential)
            data = await self._api(
                "POST",
                "/api/v3/account/sub-transfer",
                credential=credential,
                body={
                    "fromType": src,
                    "toType": dst,
                    "amount": str(amount),
                    "coin": asset,
                    "fromUserId": from_uid,
                    "toUserId": to_uid,
                    "clientOid": client_oid,
                },
            )
        else:
            if src == dst:
                raise ValueError("Bitget transfer requires distinct wallet legs")
            data = await self._api(
                "POST",
                "/api/v3/account/transfer",
                credential=credential,
                body={
                    "fromType": src,
                    "toType": dst,
                    "amount": str(amount),
                    "coin": asset,
                    "clientOid": client_oid,
                },
            )
        transfer_id = ""
        if isinstance(data, dict):
            transfer_id = str(data.get("transferId") or data.get("orderId") or "")
        elif data is not None:
            transfer_id = str(data)
        return TransferResult(native_transfer_id=transfer_id, status="confirmed")

    # -- withdraw / deposit -----------------------------------------------------

    async def fetch_asset_networks(
        self, credential: Credential, asset: str
    ) -> list[AssetNetwork]:
        data = await self._api(
            "GET",
            "/api/v2/spot/public/coins",
            params={"coin": asset.upper()},
        )
        rows = data if isinstance(data, list) else ([data] if data else [])
        networks: list[AssetNetwork] = []
        for row in rows:
            networks.extend(normalize_asset_networks(row))
        return networks

    async def fetch_deposit_address(
        self,
        credential: Credential,
        asset: str,
        network: str,
        subaccount: str | None = None,
    ) -> DepositAddress:
        params = {"coin": asset.upper(), "chain": network.upper()}
        if subaccount:
            params["subUid"] = subaccount
            path = "/api/v3/account/sub-deposit-address"
        else:
            path = "/api/v3/account/deposit-address"
        data = await self._api("GET", path, credential=credential, params=params)
        data = data or {}
        address = data.get("address") or ""
        if not address:
            raise ValueError(f"Bitget returned no {asset}/{network} deposit address")
        return DepositAddress(
            asset=asset.upper(),
            network=network.upper(),
            address=address,
            tag=data.get("tag") or data.get("memo") or None,
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
        body: dict[str, Any] = {
            "coin": asset.upper(),
            "transferType": "on_chain",
            "address": address,
            "chain": network.upper(),
            "size": str(amount),
        }
        if tag:
            body["tag"] = tag
        data = await self._api(
            "POST",
            "/api/v3/account/withdrawal",
            credential=credential,
            body=body,
        )
        data = data or {}
        return Withdrawal(
            native_id=str(data.get("orderId") or data.get("withdrawalId") or ""),
            asset=asset.upper(),
            amount=_dec(amount),
            status=WithdrawalStatus.PENDING,
            address=address,
            network=network.upper(),
        )

    async def fetch_withdrawal(
        self, credential: Credential, native_id: str
    ) -> Withdrawal | None:
        data = await self._api(
            "GET",
            "/api/v3/account/withdrawal-records",
            credential=credential,
            params={"limit": "100"},
        )
        rows, _cursor = _page_rows(data)
        for raw in rows:
            wid = str(raw.get("orderId") or raw.get("withdrawId") or raw.get("id") or "")
            if wid == str(native_id):
                return normalize_withdrawal_record(raw)
        return None

    async def fetch_deposits(
        self,
        credential: Credential,
        asset: str | None = None,
        since: datetime | None = None,
        subaccount: str | None = None,
    ) -> list[Deposit]:
        params: dict[str, str] = {"limit": "100"}
        if asset:
            params["coin"] = asset.upper()
        if since:
            params["startTime"] = str(int(since.timestamp() * 1000))
        if subaccount:
            # No dedicated sub deposit-history peek; fall through to master records.
            pass
        data = await self._api(
            "GET",
            "/api/v3/account/deposit-records",
            credential=credential,
            params=params,
        )
        rows, _cursor = _page_rows(data)
        return [normalize_deposit_record(raw) for raw in rows]

    def fund_history_stream_keys(self) -> list[str]:
        return ["account"]

    async def _wallet_history(
        self, credential: Credential, path: str, cursor: str | None, normalize
    ) -> Page:
        start_ms, end_ms, now_ms, page_cursor = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        params: dict[str, str] = {
            "startTime": str(start_ms),
            "endTime": str(end_ms),
            "limit": "100",
        }
        if page_cursor:
            params["cursor"] = page_cursor
        data = await self._api("GET", path, credential=credential, params=params)
        rows, next_token = _page_rows(data)
        return Page(
            items=[normalize(raw) for raw in rows],
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, rows, 100, next_token
            ),
        )

    async def fetch_deposits_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        return await self._wallet_history(
            credential, "/api/v3/account/deposit-records", cursor, normalize_deposit_record
        )

    async def fetch_withdrawals_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        return await self._wallet_history(
            credential,
            "/api/v3/account/withdrawal-records",
            cursor,
            normalize_withdrawal_record,
        )

    # -- funding fees -----------------------------------------------------------

    def funding_history_stream_keys(self) -> list[str]:
        return [category for category, _ in _FUTURE_CATEGORIES]

    async def fetch_funding_fees_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        await self._ensure_maps()
        market_type = _MARKET_OF.get(stream_key)
        if market_type not in (MarketType.PERP, MarketType.CMARGIN):
            raise ValueError(f"Unknown Bitget funding stream {stream_key!r}")
        start_ms, end_ms, now_ms, page_cursor = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        params: dict[str, str] = {
            "category": stream_key,
            "startTime": str(start_ms),
            "endTime": str(end_ms),
            "limit": "100",
        }
        if page_cursor:
            params["cursor"] = page_cursor
        data = await self._api(
            "GET",
            "/api/v3/account/financial-records",
            credential=credential,
            params=params,
        )
        rows, next_token = _page_rows(data)
        items: list[FundingFee] = []
        for raw in rows:
            if str(raw.get("type") or "") not in _FUNDING_FEE_TYPES:
                continue
            universal = self._native_map.get(
                (market_type, (raw.get("symbol") or "").upper())
            )
            if universal is None:
                continue
            items.append(normalize_funding_fee(raw, universal))
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, rows, 100, next_token
            ),
        )
