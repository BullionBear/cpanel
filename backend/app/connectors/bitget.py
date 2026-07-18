"""Bitget connector.

Bitget serves everything from one host (api.bitget.com, APIv2) with HMAC-SHA256
+ passphrase auth. Markets split across path prefixes and a futures
``productType`` selector:

    SPOT     /api/v2/spot/...
    PERP     /api/v2/mix/...  productType=USDT-FUTURES
    CMARGIN  /api/v2/mix/...  productType=COIN-FUTURES

USDC-margined perps and delivery contracts stay out of scope. There is no
official PyPI SDK — this module signs raw HTTP (httpx) itself.

Design decisions and venue quirks:

- **Wallets**: ``SPOT``, ``FUNDING`` (transfer leg ``p2p``), ``FUTURES``
  (``usdt_futures`` / ``coin_futures``). Withdrawals draw from the wallet
  that holds the funds (typically funding or spot).
- **Credentials** always include a passphrase (``ACCESS-PASSPHRASE``).
- **History** is account-wide per surface: streams ``spot``,
  ``mix:USDT-FUTURES``, ``mix:COIN-FUTURES``. Pagination uses ``idLessThan``
  inside sliding 24h time windows; the cursor is JSON
  ``{"id": "...", "start": epoch_ms}``.
- **Provisioning** uses virtual subaccounts. Master keys are registration-
  only (UI-pasted). Sub API keys are minted via
  ``/api/v2/user/create-virtual-subaccount-apikey`` with a generated
  passphrase. Freeze / modify / revoke are not declared — Bitget's virtual
  sub APIs do not expose a clean panel equivalent for those.
- **Spot market orders** size in base coin (``size``); limit uses ``force``
  for TIF (``gtc`` / ``ioc`` / ``fok`` / ``post_only``). Mix orders assume
  one-way position mode.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
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

_PRODUCT_TYPES: list[tuple[str, MarketType]] = [
    ("USDT-FUTURES", MarketType.PERP),
    ("COIN-FUTURES", MarketType.CMARGIN),
]
_PRODUCT_OF = {mt: pt for pt, mt in _PRODUCT_TYPES}
_MARKET_OF = {pt: mt for pt, mt in _PRODUCT_TYPES}

_TIF = {
    TimeInForce.GTC: "gtc",
    TimeInForce.IOC: "ioc",
    TimeInForce.FOK: "fok",
    TimeInForce.POST_ONLY: "post_only",
}

_WALLET_ACCOUNT = {
    WalletType.SPOT: "spot",
    WalletType.FUNDING: "p2p",
    WalletType.FUTURES: "usdt_futures",
}

_PERMISSION_SCOPES = {
    "spot": "spot_trade",
    "derivatives": "contract_trade",
    "wallet": "transfer",
    "options": "margin_trade",
}

_SPOT_ORDER_STATUS = {
    "live": OrderStatus.OPEN,
    "new": OrderStatus.OPEN,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
    "canceled": OrderStatus.CANCELLED,
}

_MIX_ORDER_STATUS = {
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

# Spot account authorities that imply write/trade/withdraw.
_TRADE_AUTHS = {"coow", "cpow", "stow", "spot_trade", "contract_trade"}
_WITHDRAW_AUTHS = {"wwow", "withdraw"}
_READ_AUTHS = {"coor", "cpor", "stor", "read"}


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
    if raw == "online":
        return "active"
    if raw == "offline":
        return "delisted"
    return "halted"


def _mix_status(raw: str) -> str:
    if raw in ("normal", "listed"):
        return "active"
    if raw in ("off", "delisted"):
        return "delisted"
    return "halted"


def normalize_spot_instrument(info: dict) -> Instrument | None:
    symbol = info.get("symbol") or ""
    base = info.get("baseCoin") or ""
    quote = info.get("quoteCoin") or ""
    if not symbol or not base or not quote:
        return None
    price_tick = _step_from_precision(info.get("pricePrecision", 2))
    qty_step = _step_from_precision(info.get("quantityPrecision", 4))
    min_qty = _opt_dec(info.get("minTradeAmount")) or Decimal(0)
    max_qty = _opt_dec(info.get("maxTradeAmount"))
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
    symbol_type = str(info.get("symbolType") or info.get("symbolType", "") or "").lower()
    # Skip delivery / dated contracts.
    if symbol_type and symbol_type not in ("perpetual", "perp", ""):
        return None
    if info.get("offTime") not in (None, "", "0", 0) and symbol_type == "delivery":
        return None
    symbol = info.get("symbol") or ""
    base = info.get("baseCoin") or ""
    quote = info.get("quoteCoin") or ""
    if not symbol or not base or not quote:
        return None
    # pricePlace = decimal places; priceEndStep = step in those places.
    price_place = int(info.get("pricePlace") or info.get("pricePrecision") or 2)
    price_end = _dec(info.get("priceEndStep") or 1)
    price_tick = price_end * _step_from_precision(price_place)
    vol_place = info.get("volumePlace")
    if vol_place is not None:
        qty_step = _step_from_precision(vol_place)
    else:
        qty_step = _opt_dec(info.get("sizeMultiplier")) or Decimal(1)
    min_qty = _opt_dec(info.get("minTradeNum")) or qty_step
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


def normalize_spot_balances(rows: list[dict]) -> list[Balance]:
    balances: list[Balance] = []
    for row in rows:
        free = _dec(row.get("available") or 0)
        locked = _dec(row.get("frozen") or 0) + _dec(row.get("locked") or 0)
        if free + locked <= 0:
            continue
        asset = (row.get("coin") or row.get("coinName") or "").upper()
        if not asset:
            continue
        balances.append(Balance(wallet=WalletType.SPOT, asset=asset, free=free, locked=locked))
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


def normalize_futures_balances(rows: list[dict], product_type: str = "") -> list[Balance]:
    """Mix account rows → FUTURES wallet balances (zeros dropped)."""
    balances: list[Balance] = []
    for row in rows:
        free = _dec(row.get("available") or row.get("crossedMaxAvailable") or 0)
        locked = _dec(row.get("locked") or row.get("frozen") or 0)
        # Equity may be the only non-zero field on empty open positions.
        if free + locked <= 0:
            equity = _dec(row.get("accountEquity") or row.get("usdtEquity") or 0)
            if equity <= 0:
                continue
            free = equity
        asset = (row.get("marginCoin") or row.get("coin") or "").upper()
        if not asset:
            # USDT-FUTURES defaults to USDT margin.
            asset = "USDT" if product_type == "USDT-FUTURES" else ""
        if not asset:
            continue
        balances.append(Balance(wallet=WalletType.FUTURES, asset=asset, free=free, locked=locked))
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
        hold = str(row.get("holdSide") or "long").lower()
        side = "short" if hold == "short" else "long"
        out.append(
            Position(
                universal_symbol=universal,
                side=side,
                size=abs(size),
                entry_price=_dec(row.get("openPriceAvg") or row.get("averageOpenPrice") or 0),
                mark_price=_opt_dec(row.get("markPrice")),
                unrealized_pnl=_opt_dec(row.get("unrealizedPL") or row.get("unrealizedPnl")),
                margin=_opt_dec(row.get("marginSize") or row.get("margin")),
            )
        )
    return out


def _order_side(raw: str) -> OrderSide:
    return OrderSide.BUY if str(raw).lower() in ("buy", "open_long", "close_short") else OrderSide.SELL


def _order_type(raw: str) -> OrderType:
    return OrderType.MARKET if str(raw).lower() == "market" else OrderType.LIMIT


def normalize_spot_order(raw: dict, universal_symbol: str) -> Order:
    status = _SPOT_ORDER_STATUS.get(str(raw.get("status", "")).lower(), OrderStatus.OPEN)
    price = _opt_dec(raw.get("priceAvg") or raw.get("price"))
    return Order(
        universal_symbol=universal_symbol,
        native_order_id=str(raw.get("orderId") or ""),
        client_order_id=raw.get("clientOid") or None,
        side=_order_side(raw.get("side", "")),
        type=_order_type(raw.get("orderType", "")),
        status=status,
        qty=_dec(raw.get("size") or raw.get("quantity") or 0),
        filled_qty=_dec(raw.get("baseVolume") or raw.get("filledQty") or raw.get("accBaseVolume") or 0),
        price=price,
        created_at=_ms_to_dt(raw.get("cTime")),
        updated_at=_ms_to_dt(raw.get("uTime") or raw.get("cTime")),
    )


def normalize_mix_order(raw: dict, universal_symbol: str) -> Order:
    status = _MIX_ORDER_STATUS.get(str(raw.get("status", "")).lower(), OrderStatus.OPEN)
    return Order(
        universal_symbol=universal_symbol,
        native_order_id=str(raw.get("orderId") or ""),
        client_order_id=raw.get("clientOid") or None,
        side=_order_side(raw.get("side", "")),
        type=_order_type(raw.get("orderType", "")),
        status=status,
        qty=_dec(raw.get("size") or 0),
        filled_qty=_dec(raw.get("baseVolume") or raw.get("filledQty") or raw.get("accBaseVolume") or 0),
        price=_opt_dec(raw.get("priceAvg") or raw.get("price")),
        created_at=_ms_to_dt(raw.get("cTime")),
        updated_at=_ms_to_dt(raw.get("uTime") or raw.get("cTime")),
    )


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
    return Trade(
        universal_symbol=universal_symbol,
        native_trade_id=str(raw.get("tradeId") or raw.get("fillId") or ""),
        native_order_id=str(raw.get("orderId") or ""),
        side=_order_side(raw.get("side", "")),
        price=_dec(raw.get("priceAvg") or raw.get("price") or 0),
        qty=_dec(raw.get("size") or raw.get("baseVolume") or 0),
        fee=fee,
        fee_asset=fee_asset,
        traded_at=_ms_to_dt(raw.get("cTime")),
    )


def normalize_mix_trade(raw: dict, universal_symbol: str) -> Trade:
    fee, fee_asset = _fee_parts(raw)
    return Trade(
        universal_symbol=universal_symbol,
        native_trade_id=str(raw.get("tradeId") or raw.get("fillId") or ""),
        native_order_id=str(raw.get("orderId") or ""),
        side=_order_side(raw.get("side", "")),
        price=_dec(raw.get("price") or raw.get("priceAvg") or 0),
        qty=_dec(raw.get("baseVolume") or raw.get("size") or 0),
        fee=fee,
        fee_asset=fee_asset,
        traded_at=_ms_to_dt(raw.get("cTime")),
    )


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
        tx_id=raw.get("tradeId") or raw.get("txId") or raw.get("txid") or None,
        fee=_opt_dec(raw.get("fee")),
        created_at=_ms_to_dt(raw.get("cTime") or raw.get("createtime")),
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
    created = _ms_to_dt(raw.get("cTime") or raw.get("createtime"))
    return Deposit(
        native_id=str(raw.get("orderId") or raw.get("depositId") or raw.get("id") or ""),
        asset=(raw.get("coin") or "").upper(),
        amount=_dec(raw.get("size") or raw.get("amount") or 0),
        status=status,
        network=(raw.get("chain") or raw.get("dest") or None),
        tx_id=raw.get("tradeId") or raw.get("txId") or raw.get("txid") or None,
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
        settled_at=_ms_to_dt(raw.get("cTime") or raw.get("settleTime")),
    )


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
    wallets = (WalletType.SPOT, WalletType.FUNDING, WalletType.FUTURES)

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
        data = await self._api("GET", "/api/v2/spot/public/symbols")
        return data or []

    async def _fetch_mix_contracts(self, product_type: str) -> list[dict]:
        data = await self._api(
            "GET", "/api/v2/mix/market/contracts", params={"productType": product_type}
        )
        return data or []

    async def _fetch_spot_tickers(self) -> list[dict]:
        data = await self._api("GET", "/api/v2/spot/market/tickers")
        return data or []

    async def _fetch_mix_tickers(self, product_type: str) -> list[dict]:
        data = await self._api(
            "GET", "/api/v2/mix/market/tickers", params={"productType": product_type}
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

        for product_type, market_type in _PRODUCT_TYPES:
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
            last = raw.get("lastPr") or raw.get("close") or raw.get("last")
            if universal and last not in (None, ""):
                prices[universal] = _dec(last)
        for product_type, market_type in _PRODUCT_TYPES:
            for raw in await self._fetch_mix_tickers(product_type):
                native = (raw.get("symbol") or "").upper()
                universal = self._native_map.get((market_type, native))
                last = raw.get("lastPr") or raw.get("last") or raw.get("markPrice")
                if universal and last not in (None, ""):
                    prices[universal] = _dec(last)
        return prices

    # -- credentials ------------------------------------------------------------

    async def verify_credential(self, credential: Credential) -> CredentialInfo:
        try:
            info = await self._api(
                "GET", "/api/v2/spot/account/info", credential=credential
            )
        except Exception as exc:
            reason = credential_error_reason(self.exchange, exc)
            logger.warning("Bitget credential verification failed: %s", reason, exc_info=True)
            return CredentialInfo(valid=False, reason=reason)
        authorities = {
            str(a).lower()
            for a in (info.get("authorities") or info.get("permissions") or [])
        }
        can_trade = bool(authorities & _TRADE_AUTHS) or any(
            "trade" in a for a in authorities
        )
        can_withdraw = bool(authorities & _WITHDRAW_AUTHS) or any(
            "withdraw" in a for a in authorities
        )
        read_only = bool(authorities) and not can_trade and not can_withdraw
        parent = info.get("parentId")
        is_master = parent in (None, "", 0, "0")
        return CredentialInfo(
            valid=True,
            native_uid=str(info.get("userId") or "") or None,
            is_master=is_master,
            read_only=read_only if authorities else None,
            can_trade=can_trade if authorities else None,
            can_withdraw=can_withdraw if authorities else None,
            permissions={"authorities": sorted(authorities)} if authorities else info,
        )

    # -- balances / positions ---------------------------------------------------

    async def _fetch_spot_assets(self, credential: Credential) -> list[dict]:
        data = await self._api(
            "GET",
            "/api/v2/spot/account/assets",
            credential=credential,
            params={"assetType": "hold_only"},
        )
        return data or []

    async def _fetch_funding_assets(self, credential: Credential) -> list[dict]:
        data = await self._api(
            "GET", "/api/v2/account/funding-assets", credential=credential
        )
        return data or []

    async def _fetch_mix_accounts(
        self, credential: Credential, product_type: str
    ) -> list[dict]:
        data = await self._api(
            "GET",
            "/api/v2/mix/account/accounts",
            credential=credential,
            params={"productType": product_type},
        )
        return data or []

    async def _fetch_mix_positions(
        self, credential: Credential, product_type: str
    ) -> list[dict]:
        data = await self._api(
            "GET",
            "/api/v2/mix/position/all-position",
            credential=credential,
            params={"productType": product_type},
        )
        return data or []

    async def fetch_balances(self, credential: Credential) -> list[Balance]:
        balances = normalize_spot_balances(await self._fetch_spot_assets(credential))
        try:
            balances.extend(
                normalize_funding_balances(await self._fetch_funding_assets(credential))
            )
        except BitgetAPIError as exc:
            logger.debug("Bitget funding assets unavailable: %s", exc)
        for product_type, _ in _PRODUCT_TYPES:
            try:
                rows = await self._fetch_mix_accounts(credential, product_type)
                balances.extend(normalize_futures_balances(rows, product_type))
            except BitgetAPIError as exc:
                logger.debug("Bitget %s accounts unavailable: %s", product_type, exc)
        return balances

    async def fetch_positions(self, credential: Credential) -> list[Position]:
        if not self._native_map:
            await self.fetch_instruments()
        positions: list[Position] = []
        for product_type, _ in _PRODUCT_TYPES:
            try:
                rows = await self._fetch_mix_positions(credential, product_type)
            except BitgetAPIError as exc:
                logger.debug("Bitget %s positions unavailable: %s", product_type, exc)
                continue
            positions.extend(
                normalize_positions(rows, product_type, self._native_map)
            )
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

    async def place_order(self, credential: Credential, request: OrderRequest) -> Order:
        await self._ensure_maps()
        market_type, native = self._resolve(request.universal_symbol)
        force = _TIF.get(request.time_in_force or TimeInForce.GTC, "gtc")
        if request.type == OrderType.MARKET:
            force = "ioc"
        if market_type == MarketType.SPOT:
            body: dict[str, Any] = {
                "symbol": native,
                "side": "buy" if request.side == OrderSide.BUY else "sell",
                "orderType": "market" if request.type == OrderType.MARKET else "limit",
                "force": force,
                "size": str(request.qty),
            }
            if request.type == OrderType.LIMIT:
                if request.price is None:
                    raise ValueError("Limit orders require a price")
                body["price"] = str(request.price)
            if request.client_order_id:
                body["clientOid"] = request.client_order_id
            raw = await self._api(
                "POST",
                "/api/v2/spot/trade/place-order",
                credential=credential,
                body=body,
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

        product_type = _PRODUCT_OF[market_type]
        margin_coin = "USDT" if market_type == MarketType.PERP else native.replace("USD", "")
        # Inverse settle is typically the base coin (BTC for BTCUSD).
        if market_type == MarketType.CMARGIN:
            try:
                parsed = parse_universal_symbol(request.universal_symbol)
                # pair is BASEQUOTE without separator; for CMARGIN quote is usually USD.
                for quote in ("USD", "USDT", "USDC"):
                    if parsed.pair.endswith(quote):
                        margin_coin = parsed.pair[: -len(quote)]
                        break
            except InvalidUniversalSymbol:
                pass
        body = {
            "symbol": native,
            "productType": product_type,
            "marginMode": "crossed",
            "marginCoin": margin_coin,
            "size": str(request.qty),
            "side": "buy" if request.side == OrderSide.BUY else "sell",
            "orderType": "market" if request.type == OrderType.MARKET else "limit",
            "force": force,
        }
        if request.type == OrderType.LIMIT:
            if request.price is None:
                raise ValueError("Limit orders require a price")
            body["price"] = str(request.price)
        if request.reduce_only:
            body["reduceOnly"] = "YES"
        if request.client_order_id:
            body["clientOid"] = request.client_order_id
        raw = await self._api(
            "POST", "/api/v2/mix/order/place-order", credential=credential, body=body
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
        market_type, native = self._resolve(universal_symbol)
        if market_type == MarketType.SPOT:
            await self._api(
                "POST",
                "/api/v2/spot/trade/cancel-order",
                credential=credential,
                body={"symbol": native, "orderId": native_order_id},
            )
        else:
            await self._api(
                "POST",
                "/api/v2/mix/order/cancel-order",
                credential=credential,
                body={
                    "symbol": native,
                    "productType": _PRODUCT_OF[market_type],
                    "orderId": native_order_id,
                },
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
        orders: list[Order] = []
        if universal_symbol is None or self._resolve(universal_symbol)[0] == MarketType.SPOT:
            params: dict[str, str] = {}
            if universal_symbol:
                params["symbol"] = self._resolve(universal_symbol)[1]
            rows = await self._api(
                "GET",
                "/api/v2/spot/trade/unfilled-orders",
                credential=credential,
                params=params or None,
            )
            for raw in rows or []:
                native = (raw.get("symbol") or "").upper()
                universal = self._native_map.get((MarketType.SPOT, native))
                if universal is None:
                    continue
                if universal_symbol and universal != universal_symbol:
                    continue
                orders.append(normalize_spot_order(raw, universal))
            if universal_symbol:
                return orders

        for product_type, market_type in _PRODUCT_TYPES:
            if universal_symbol:
                mt, native = self._resolve(universal_symbol)
                if mt != market_type:
                    continue
                params = {"productType": product_type, "symbol": native}
            else:
                params = {"productType": product_type}
            try:
                rows = await self._api(
                    "GET",
                    "/api/v2/mix/order/orders-pending",
                    credential=credential,
                    params=params,
                )
            except BitgetAPIError:
                continue
            for raw in rows or []:
                native = (raw.get("symbol") or "").upper()
                universal = self._native_map.get((market_type, native))
                if universal is None:
                    continue
                if universal_symbol and universal != universal_symbol:
                    continue
                orders.append(normalize_mix_order(raw, universal))
        return orders

    async def fetch_order(
        self,
        credential: Credential,
        universal_symbol: str,
        native_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> Order | None:
        await self._ensure_maps()
        market_type, native = self._resolve(universal_symbol)
        params: dict[str, str] = {"symbol": native}
        if native_order_id:
            params["orderId"] = native_order_id
        if client_order_id:
            params["clientOid"] = client_order_id
        if market_type == MarketType.SPOT:
            for path in (
                "/api/v2/spot/trade/orderInfo",
                "/api/v2/spot/trade/unfilled-orders",
                "/api/v2/spot/trade/history-orders",
            ):
                try:
                    data = await self._api(
                        "GET", path, credential=credential, params=params
                    )
                except BitgetAPIError:
                    continue
                rows = data if isinstance(data, list) else ([data] if data else [])
                for raw in rows:
                    if native_order_id and str(raw.get("orderId")) != str(native_order_id):
                        continue
                    if client_order_id and str(raw.get("clientOid") or "") != client_order_id:
                        continue
                    return normalize_spot_order(raw, universal_symbol)
            return None

        params["productType"] = _PRODUCT_OF[market_type]
        for path in (
            "/api/v2/mix/order/detail",
            "/api/v2/mix/order/orders-pending",
            "/api/v2/mix/order/orders-history",
        ):
            try:
                data = await self._api(
                    "GET", path, credential=credential, params=params
                )
            except BitgetAPIError:
                continue
            rows = data if isinstance(data, list) else ([data] if data else [])
            # Some mix detail endpoints wrap under {"entrustedList": [...]}
            if isinstance(data, dict) and "entrustedList" in data:
                rows = data.get("entrustedList") or []
            for raw in rows:
                if native_order_id and str(raw.get("orderId")) != str(native_order_id):
                    continue
                if client_order_id and str(raw.get("clientOid") or "") != client_order_id:
                    continue
                return normalize_mix_order(raw, universal_symbol)
        return None

    # -- history ----------------------------------------------------------------

    def history_stream_keys(self, active_symbols: list[str]) -> list[str]:
        return ["spot"] + [f"mix:{pt}" for pt, _ in _PRODUCT_TYPES]

    def initial_history_cursor(self, start: datetime) -> str | None:
        return json.dumps({"start": int(start.timestamp() * 1000), "id": ""})

    def _history_window(self, cursor: str | None) -> tuple[int, int, int, str]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        last_id = ""
        if cursor:
            decoded = json.loads(cursor)
            start_ms = int(decoded["start"])
            last_id = str(decoded.get("id") or "")
        else:
            start_ms = now_ms - int(DEFAULT_HISTORY_BACKFILL.total_seconds() * 1000)
        return start_ms, min(start_ms + _HISTORY_WINDOW_MS, now_ms), now_ms, last_id

    @staticmethod
    def _next_history_cursor(
        start_ms: int, end_ms: int, now_ms: int, rows: list[dict], id_field: str, limit: int
    ) -> str | None:
        if len(rows) >= limit:
            last = str(rows[-1].get(id_field) or "")
            return json.dumps({"start": start_ms, "id": last})
        if end_ms < now_ms:
            return json.dumps({"start": end_ms, "id": ""})
        return None

    async def _history_page(
        self, credential: Credential, stream_key: str, cursor: str | None, kind: str
    ) -> Page:
        await self._ensure_maps()
        limit = _TRADE_PAGE_LIMIT if kind == "trades" else _ORDER_PAGE_LIMIT
        start_ms, end_ms, now_ms, last_id = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)

        params: dict[str, str] = {
            "startTime": str(start_ms),
            "endTime": str(end_ms),
            "limit": str(limit),
        }
        if last_id:
            params["idLessThan"] = last_id

        items: list = []
        rows: list[dict] = []
        id_field = "tradeId" if kind == "trades" else "orderId"

        if stream_key == "spot":
            path = (
                "/api/v2/spot/trade/fills"
                if kind == "trades"
                else "/api/v2/spot/trade/history-orders"
            )
            data = await self._api("GET", path, credential=credential, params=params)
            rows = data if isinstance(data, list) else []
            for raw in rows:
                universal = self._native_map.get(
                    (MarketType.SPOT, (raw.get("symbol") or "").upper())
                )
                if universal is None:
                    continue
                items.append(
                    normalize_spot_trade(raw, universal)
                    if kind == "trades"
                    else normalize_spot_order(raw, universal)
                )
        elif stream_key.startswith("mix:"):
            product_type = stream_key.split(":", 1)[1]
            market_type = _MARKET_OF[product_type]
            params["productType"] = product_type
            path = (
                "/api/v2/mix/order/fills"
                if kind == "trades"
                else "/api/v2/mix/order/orders-history"
            )
            try:
                data = await self._api(
                    "GET", path, credential=credential, params=params
                )
            except BitgetAPIError:
                data = []
            if isinstance(data, dict):
                rows = data.get("entrustedList") or data.get("fillList") or data.get("list") or []
                # Mix uses endId for the next page on some endpoints.
                end_id = data.get("endId")
                if end_id and not rows:
                    rows = []
                elif end_id and len(rows) >= limit:
                    # Prefer endId as the idLessThan token.
                    for r in rows:
                        r.setdefault(id_field, end_id)
            else:
                rows = data or []
            for raw in rows:
                universal = self._native_map.get(
                    (market_type, (raw.get("symbol") or "").upper())
                )
                if universal is None:
                    continue
                items.append(
                    normalize_mix_trade(raw, universal)
                    if kind == "trades"
                    else normalize_mix_order(raw, universal)
                )
        else:
            raise ValueError(f"Unknown Bitget history stream {stream_key!r}")

        return Page(
            items=items,
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, rows, id_field, limit
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
        passphrase = secrets.token_urlsafe(12)
        perm_list = ["read"]
        if not read_only:
            for scope in permissions:
                token = _PERMISSION_SCOPES.get(scope)
                if token and token not in perm_list:
                    perm_list.append(token)
        body: dict[str, Any] = {
            "subAccountUid": subaccount,
            "passphrase": passphrase,
            "label": (note or "cpanel")[:32],
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
        return ApiKeyResult(
            api_key=str(data.get("apiKey") or data.get("accessKey") or ""),
            secret=str(data.get("secret") or data.get("secretKey") or ""),
            scheme=KeyScheme.HMAC,
            permissions=perm_list,
            passphrase=str(data.get("passphrase") or passphrase),
        )

    # -- transfers --------------------------------------------------------------

    def _wallet_leg(self, wallet: WalletType | None, asset: str = "USDT") -> str:
        wallet = wallet or WalletType.SPOT
        if wallet == WalletType.FUTURES:
            return "coin_futures" if asset.upper() not in ("USDT", "USDC") else "usdt_futures"
        leg = _WALLET_ACCOUNT.get(wallet)
        if leg is None:
            raise ValueError("Bitget wallets are SPOT, FUNDING, and FUTURES")
        return leg

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
        if from_subaccount or to_subaccount:
            body = {
                "fromType": self._wallet_leg(from_wallet, asset),
                "toType": self._wallet_leg(to_wallet, asset),
                "amount": str(amount),
                "coin": asset,
                "fromUserId": from_subaccount or "",
                "toUserId": to_subaccount or "",
            }
            # Omit empty sides so Bitget treats them as the credential account.
            if not body["fromUserId"]:
                del body["fromUserId"]
            if not body["toUserId"]:
                del body["toUserId"]
            data = await self._api(
                "POST",
                "/api/v2/spot/wallet/subaccount-transfer",
                credential=credential,
                body=body,
            )
        else:
            src = self._wallet_leg(from_wallet, asset)
            dst = self._wallet_leg(to_wallet, asset)
            if src == dst:
                raise ValueError("Bitget transfer requires distinct wallet legs")
            data = await self._api(
                "POST",
                "/api/v2/spot/wallet/transfer",
                credential=credential,
                body={
                    "fromType": src,
                    "toType": dst,
                    "amount": str(amount),
                    "coin": asset,
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
            params["subAccountUid"] = subaccount
            path = "/api/v2/spot/wallet/subaccount-deposit-address"
        else:
            path = "/api/v2/spot/wallet/deposit-address"
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
            "/api/v2/spot/wallet/withdrawal",
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
            "/api/v2/spot/wallet/withdrawal-records",
            credential=credential,
            params={"limit": "100"},
        )
        for raw in data or []:
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
            "/api/v2/spot/wallet/deposit-records",
            credential=credential,
            params=params,
        )
        return [normalize_deposit_record(raw) for raw in (data or [])]

    def fund_history_stream_keys(self) -> list[str]:
        return ["account"]

    async def fetch_deposits_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        start_ms, end_ms, now_ms, last_id = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        params: dict[str, str] = {
            "startTime": str(start_ms),
            "endTime": str(end_ms),
            "limit": "100",
        }
        if last_id:
            params["idLessThan"] = last_id
        data = await self._api(
            "GET",
            "/api/v2/spot/wallet/deposit-records",
            credential=credential,
            params=params,
        )
        rows = data if isinstance(data, list) else []
        items = [normalize_deposit_record(raw) for raw in rows]
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, rows, "orderId", 100
            ),
        )

    async def fetch_withdrawals_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        start_ms, end_ms, now_ms, last_id = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        params: dict[str, str] = {
            "startTime": str(start_ms),
            "endTime": str(end_ms),
            "limit": "100",
        }
        if last_id:
            params["idLessThan"] = last_id
        data = await self._api(
            "GET",
            "/api/v2/spot/wallet/withdrawal-records",
            credential=credential,
            params=params,
        )
        rows = data if isinstance(data, list) else []
        items = [normalize_withdrawal_record(raw) for raw in rows]
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, rows, "orderId", 100
            ),
        )

    # -- funding fees -----------------------------------------------------------

    def funding_history_stream_keys(self) -> list[str]:
        return [f"mix:{pt}" for pt, _ in _PRODUCT_TYPES]

    async def fetch_funding_fees_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        await self._ensure_maps()
        if not stream_key.startswith("mix:"):
            raise ValueError(f"Unknown Bitget funding stream {stream_key!r}")
        product_type = stream_key.split(":", 1)[1]
        market_type = _MARKET_OF[product_type]
        start_ms, end_ms, now_ms, last_id = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        params: dict[str, str] = {
            "productType": product_type,
            "businessType": "contract_settle_fee",
            "startTime": str(start_ms),
            "endTime": str(end_ms),
            "limit": "100",
        }
        if last_id:
            params["idLessThan"] = last_id
        try:
            data = await self._api(
                "GET",
                "/api/v2/mix/account/bill",
                credential=credential,
                params=params,
            )
        except BitgetAPIError:
            return Page(
                items=[],
                next_cursor=(
                    json.dumps({"start": end_ms, "id": ""}) if end_ms < now_ms else None
                ),
            )
        rows = data if isinstance(data, list) else (data or {}).get("bills") or []
        items: list[FundingFee] = []
        for raw in rows:
            universal = self._native_map.get(
                (market_type, (raw.get("symbol") or "").upper())
            )
            if universal is None:
                continue
            items.append(normalize_funding_fee(raw, universal))
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, rows, "billId", 100
            ),
        )
