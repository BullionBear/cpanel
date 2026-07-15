"""Binance connector (Phase 1: public market data).

Binance splits its markets across three separate API surfaces, each with its
own base URL and official SDK package:

    SPOT     api.binance.com    binance-sdk-spot
    PERP     fapi.binance.com   binance-sdk-derivatives-trading-usds-futures
    CMARGIN  dapi.binance.com   binance-sdk-derivatives-trading-coin-futures

So one instrument refresh means three exchange-info calls. The futures
surfaces also list delivery contracts; only perpetuals are kept for the
first release (DELIVERY/OPTION come later).

The official SDKs are synchronous, so calls run in a worker thread. All
normalization works on plain API-shaped dicts (see sdk_util) to keep it
testable with fixtures from the API docs.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal

from binance_common.configuration import ConfigurationRestAPI
from binance_sdk_derivatives_trading_coin_futures.derivatives_trading_coin_futures import (
    DerivativesTradingCoinFutures,
)
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    DerivativesTradingUsdsFutures,
)
from binance_sdk_spot.spot import Spot
from binance_sdk_sub_account.sub_account import SubAccount
from binance_sdk_wallet.wallet import Wallet

from app.connectors.base import Capability, Credential, ExchangeConnector
from app.connectors.errors import credential_error_reason
from app.connectors.sdk_util import dump_sdk_model
from app.connectors.symbols import build_universal_symbol
from app.connectors.types import (
    Balance,
    CredentialInfo,
    Instrument,
    InstrumentFilters,
    MarketType,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    Position,
    SubaccountInfo,
    TimeInForce,
    WalletType,
)
from app.core.ratelimit import Priority, TokenBucket

logger = logging.getLogger(__name__)

_TIMEOUT_MS = 30_000

_PEM_RE = re.compile(r"-----BEGIN ([A-Z0-9 ]+?)-----(.*?)-----END \1-----", re.DOTALL)


def _normalize_pem(secret: str) -> str:
    """Restore a PEM whose newlines were flattened to spaces.

    Copy/paste and some form inputs collapse a PEM private key onto a single
    line (``-----BEGIN PRIVATE KEY----- <base64> -----END PRIVATE KEY-----``).
    The Binance SDK signs Ed25519 requests with PyCryptodome's
    ``ECC.import_key``, which requires a real multi-line PEM and otherwise
    raises ``A PEM file must have at least 3 lines``. Rebuild the canonical
    header/64-col-body/footer layout; already-valid PEMs pass through
    unchanged apart from surrounding whitespace.
    """
    text = secret.strip()
    match = _PEM_RE.search(text)
    if not match:
        return text
    label = match.group(1).strip()
    body = "".join(match.group(2).split())
    wrapped = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN {label}-----\n{wrapped}\n-----END {label}-----\n"


def _auth_config(credential: Credential) -> ConfigurationRestAPI:
    """Build SDK auth config from a decrypted credential mapping.

    HMAC keys pass the secret as api_secret; Ed25519 keys pass the PEM
    private key instead (the SDK signs requests with it).
    """
    if credential.get("scheme") == "ED25519":
        return ConfigurationRestAPI(
            api_key=credential["api_key"],
            private_key=_normalize_pem(credential["secret"]).encode(),
            timeout=_TIMEOUT_MS,
        )
    return ConfigurationRestAPI(
        api_key=credential["api_key"],
        api_secret=credential["secret"],
        timeout=_TIMEOUT_MS,
    )


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _filters_by_type(symbol_info: dict) -> dict[str, dict]:
    return {f["filterType"]: f for f in symbol_info.get("filters", [])}


def _spot_status(status: str) -> str:
    return "active" if status == "TRADING" else "halted"


def normalize_spot_instrument(info: dict) -> Instrument | None:
    filters = _filters_by_type(info)
    price_filter = filters.get("PRICE_FILTER")
    lot = filters.get("LOT_SIZE")
    if not price_filter or not lot:
        return None
    # Newer symbols use NOTIONAL, older ones MIN_NOTIONAL
    notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
    min_notional = notional.get("minNotional")
    base, quote = info["baseAsset"], info["quoteAsset"]
    return Instrument(
        universal_symbol=build_universal_symbol("BINANCE", MarketType.SPOT, base, quote),
        exchange="BINANCE",
        market_type=MarketType.SPOT,
        native_symbol=info["symbol"],
        base_asset=base,
        quote_asset=quote,
        filters=InstrumentFilters(
            price_tick=_dec(price_filter["tickSize"]),
            qty_step=_dec(lot["stepSize"]),
            min_qty=_dec(lot["minQty"]),
            max_qty=_dec(lot["maxQty"]),
            min_notional=_dec(min_notional) if min_notional is not None else None,
        ),
        status=_spot_status(info.get("status", "")),
    )


def normalize_um_instrument(info: dict) -> Instrument | None:
    """USD-margined futures (fapi) -> PERP. Delivery contracts are skipped."""
    if info.get("contractType") != "PERPETUAL":
        return None
    filters = _filters_by_type(info)
    price_filter = filters.get("PRICE_FILTER")
    lot = filters.get("LOT_SIZE")
    if not price_filter or not lot:
        return None
    # fapi's MIN_NOTIONAL filter uses the key "notional"
    notional_filter = filters.get("MIN_NOTIONAL") or {}
    min_notional = notional_filter.get("notional") or notional_filter.get("minNotional")
    base, quote = info["baseAsset"], info["quoteAsset"]
    return Instrument(
        universal_symbol=build_universal_symbol("BINANCE", MarketType.PERP, base, quote),
        exchange="BINANCE",
        market_type=MarketType.PERP,
        native_symbol=info["symbol"],
        base_asset=base,
        quote_asset=quote,
        filters=InstrumentFilters(
            price_tick=_dec(price_filter["tickSize"]),
            qty_step=_dec(lot["stepSize"]),
            min_qty=_dec(lot["minQty"]),
            max_qty=_dec(lot["maxQty"]),
            min_notional=_dec(min_notional) if min_notional is not None else None,
        ),
        status=_spot_status(info.get("status", "")),
        settle_asset=info.get("marginAsset"),
    )


def normalize_cm_instrument(info: dict) -> Instrument | None:
    """COIN-margined futures (dapi) -> CMARGIN. Delivery contracts are skipped."""
    if info.get("contractType") != "PERPETUAL":
        return None
    filters = _filters_by_type(info)
    price_filter = filters.get("PRICE_FILTER")
    lot = filters.get("LOT_SIZE")
    if not price_filter or not lot:
        return None
    base, quote = info["baseAsset"], info["quoteAsset"]
    status = info.get("contractStatus") or info.get("status", "")
    contract_size = info.get("contractSize")
    return Instrument(
        universal_symbol=build_universal_symbol("BINANCE", MarketType.CMARGIN, base, quote),
        exchange="BINANCE",
        market_type=MarketType.CMARGIN,
        native_symbol=info["symbol"],  # e.g. BTCUSD_PERP
        base_asset=base,
        quote_asset=quote,
        filters=InstrumentFilters(
            price_tick=_dec(price_filter["tickSize"]),
            qty_step=_dec(lot["stepSize"]),
            min_qty=_dec(lot["minQty"]),
            max_qty=_dec(lot["maxQty"]),
            min_notional=None,  # qty is in contracts; no notional filter on dapi
        ),
        status=_spot_status(status),
        contract_size=_dec(contract_size) if contract_size is not None else None,
        settle_asset=info.get("marginAsset"),
    )


def normalize_spot_balances(rows: list[dict]) -> list[Balance]:
    """Spot get_account balances → normalized SPOT balances (zeros dropped)."""
    balances: list[Balance] = []
    for row in rows:
        free = _dec(row.get("free", 0))
        locked = _dec(row.get("locked", 0))
        if free == 0 and locked == 0:
            continue
        balances.append(
            Balance(wallet=WalletType.SPOT, asset=row["asset"], free=free, locked=locked)
        )
    return balances


def normalize_futures_balances(rows: list[dict]) -> list[Balance]:
    """USD-M/COIN-M account-balance rows → normalized DERIVATIVES balances.

    ``balance`` is the wallet total; ``availableBalance`` is what is free of
    margin, so the margin currently posted is the difference.
    """
    balances: list[Balance] = []
    for row in rows:
        total = _dec(row.get("balance", 0))
        available = _dec(row.get("availableBalance", row.get("balance", 0)))
        if total == 0 and available == 0:
            continue
        locked = total - available
        if locked < 0:
            locked = Decimal("0")
        balances.append(
            Balance(
                wallet=WalletType.DERIVATIVES,
                asset=row["asset"],
                free=total - locked,
                locked=locked,
            )
        )
    return balances


def _opt_dec(value) -> Decimal | None:
    return _dec(value) if value not in (None, "") else None


def normalize_positions(
    rows: list[dict],
    market_type: MarketType,
    native_map: dict[tuple[MarketType, str], str],
) -> list[Position]:
    """Futures position rows → normalized non-zero positions.

    Symbols not present in the instrument map (e.g. dated-futures positions,
    which the panel does not track yet) are skipped rather than guessed.
    """
    positions: list[Position] = []
    for row in rows:
        amount = _dec(row.get("positionAmt", 0))
        if amount == 0:
            continue
        universal = native_map.get((market_type, row.get("symbol", "")))
        if universal is None:
            continue
        positions.append(
            Position(
                universal_symbol=universal,
                side="long" if amount > 0 else "short",
                size=abs(amount),
                entry_price=_dec(row.get("entryPrice", 0)),
                mark_price=_opt_dec(row.get("markPrice")),
                unrealized_pnl=_opt_dec(row.get("unRealizedProfit")),
                margin=_opt_dec(row.get("isolatedMargin")),
            )
        )
    return positions


# Binance native order status -> normalized lifecycle.
_ORDER_STATUS = {
    "NEW": OrderStatus.OPEN,
    "PENDING_NEW": OrderStatus.OPEN,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELLED,
    "PENDING_CANCEL": OrderStatus.OPEN,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.CANCELLED,
    "EXPIRED_IN_MATCH": OrderStatus.CANCELLED,
}


def _order_status(raw: str | None) -> OrderStatus:
    return _ORDER_STATUS.get((raw or "").upper(), OrderStatus.OPEN)


def _order_type(raw: str | None) -> OrderType:
    return OrderType.MARKET if (raw or "").upper() == "MARKET" else OrderType.LIMIT


def normalize_order(raw: dict, universal_symbol: str) -> Order:
    """A Binance order dict (spot or futures) → normalized Order."""
    order_id = raw.get("orderId")
    return Order(
        universal_symbol=universal_symbol,
        native_order_id=str(order_id) if order_id is not None else "",
        client_order_id=raw.get("clientOrderId") or None,
        side=OrderSide(str(raw.get("side", "BUY")).upper()),
        type=_order_type(raw.get("type")),
        status=_order_status(raw.get("status")),
        qty=_dec(raw.get("origQty", 0) or 0),
        filled_qty=_dec(raw.get("executedQty", 0) or 0),
        price=_opt_dec(raw.get("price")) if _opt_dec(raw.get("price")) else None,
        created_at=_epoch_ms(raw.get("time") or raw.get("transactTime")),
        updated_at=_epoch_ms(raw.get("updateTime")),
    )


def _epoch_ms(value) -> datetime | None:
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


class BinanceConnector(ExchangeConnector):
    exchange = "BINANCE"
    # Registration-only for keys. Binance's API cannot mint/rotate/revoke
    # subaccount API keys for a normal (non-broker) master account, so the
    # operator creates keys on Binance and registers each here. Subaccounts,
    # however, can be *listed* from the venue, so we keep read-only sync (no
    # create). (See docs/ACCOUNT_REDESIGN.md.)
    capabilities = frozenset(
        {
            Capability.INSTRUMENTS,
            Capability.REFERENCE_PRICES,
            Capability.CREDENTIAL_VERIFY,
            Capability.BALANCES,
            Capability.POSITIONS,
            Capability.ORDERS,
            Capability.SUBACCOUNT_LIST,
        }
    )

    def __init__(self, bucket: TokenBucket | None = None):
        self._bucket = bucket
        config = lambda: ConfigurationRestAPI(timeout=_TIMEOUT_MS)  # noqa: E731
        self._spot = Spot(config_rest_api=config())
        self._um = DerivativesTradingUsdsFutures(config_rest_api=config())
        self._cm = DerivativesTradingCoinFutures(config_rest_api=config())
        # (market_type, native_symbol) -> universal_symbol, warmed by fetch_instruments
        self._native_map: dict[tuple[MarketType, str], str] = {}
        # universal_symbol -> (market_type, native_symbol), the reverse lookup
        self._universal_map: dict[str, tuple[MarketType, str]] = {}

    async def _call(self, func, *args, **kwargs):
        if self._bucket is not None:
            await self._bucket.acquire(priority=Priority.SYNC)
        return await asyncio.to_thread(func, *args, **kwargs)

    # -- raw fetches (overridden by tests with canned payloads) --------------

    async def _fetch_spot_exchange_info(self) -> list[dict]:
        resp = await self._call(self._spot.rest_api.exchange_info)
        return dump_sdk_model(resp.data()).get("symbols", [])

    async def _fetch_um_exchange_info(self) -> list[dict]:
        resp = await self._call(self._um.rest_api.exchange_information)
        return dump_sdk_model(resp.data()).get("symbols", [])

    async def _fetch_cm_exchange_info(self) -> list[dict]:
        resp = await self._call(self._cm.rest_api.exchange_information)
        return dump_sdk_model(resp.data()).get("symbols", [])

    async def _fetch_spot_prices(self) -> list[dict]:
        resp = await self._call(self._spot.rest_api.ticker_price)
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data]

    async def _fetch_um_prices(self) -> list[dict]:
        resp = await self._call(self._um.rest_api.symbol_price_ticker)
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data]

    async def _fetch_cm_prices(self) -> list[dict]:
        resp = await self._call(self._cm.rest_api.symbol_price_ticker)
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data]

    # -- connector interface --------------------------------------------------

    async def fetch_instruments(self) -> list[Instrument]:
        spot_raw, um_raw, cm_raw = (
            await self._fetch_spot_exchange_info(),
            await self._fetch_um_exchange_info(),
            await self._fetch_cm_exchange_info(),
        )
        instruments: list[Instrument] = []
        for raw, normalize in (
            (spot_raw, normalize_spot_instrument),
            (um_raw, normalize_um_instrument),
            (cm_raw, normalize_cm_instrument),
        ):
            for info in raw:
                instrument = normalize(info)
                if instrument is not None:
                    instruments.append(instrument)
        self._native_map = {
            (i.market_type, i.native_symbol): i.universal_symbol for i in instruments
        }
        self._universal_map = {
            i.universal_symbol: (i.market_type, i.native_symbol) for i in instruments
        }
        return instruments

    # -- accounts -----------------------------------------------------------

    async def _fetch_api_restrictions(self, credential: Credential) -> dict:
        wallet = Wallet(config_rest_api=_auth_config(credential))
        resp = await self._call(wallet.rest_api.get_api_key_permission)
        return dump_sdk_model(resp.data())

    async def _fetch_account_uid(self, credential: Credential) -> str | None:
        try:
            spot = Spot(config_rest_api=_auth_config(credential))
            resp = await self._call(spot.rest_api.get_account, omit_zero_balances=True)
            return str(dump_sdk_model(resp.data()).get("uid", "")) or None
        except Exception:
            return None  # e.g. futures-only key without spot permission

    async def verify_credential(self, credential: Credential) -> CredentialInfo:
        try:
            restrictions = await self._fetch_api_restrictions(credential)
        except Exception as exc:
            reason = credential_error_reason(self.exchange, exc)
            logger.warning("Binance credential verification failed: %s", reason, exc_info=True)
            return CredentialInfo(valid=False, reason=reason)
        can_trade = bool(
            restrictions.get("enableSpotAndMarginTrading") or restrictions.get("enableFutures")
        )
        can_withdraw = bool(restrictions.get("enableWithdrawals"))
        return CredentialInfo(
            valid=True,
            native_uid=await self._fetch_account_uid(credential),
            read_only=not can_trade and not can_withdraw,
            can_trade=can_trade,
            can_withdraw=can_withdraw,
            permissions=restrictions,
        )

    # -- account state ------------------------------------------------------

    async def _fetch_spot_balances(self, credential: Credential) -> list[dict]:
        spot = Spot(config_rest_api=_auth_config(credential))
        resp = await self._call(spot.rest_api.get_account, omit_zero_balances=True)
        return dump_sdk_model(resp.data()).get("balances", [])

    async def _fetch_um_balances(self, credential: Credential) -> list[dict]:
        um = DerivativesTradingUsdsFutures(config_rest_api=_auth_config(credential))
        resp = await self._call(um.rest_api.futures_account_balance_v3)
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data]

    async def _fetch_cm_balances(self, credential: Credential) -> list[dict]:
        cm = DerivativesTradingCoinFutures(config_rest_api=_auth_config(credential))
        resp = await self._call(cm.rest_api.futures_account_balance)
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data]

    async def _fetch_um_positions(self, credential: Credential) -> list[dict]:
        um = DerivativesTradingUsdsFutures(config_rest_api=_auth_config(credential))
        resp = await self._call(um.rest_api.position_information_v3)
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data]

    async def _fetch_cm_positions(self, credential: Credential) -> list[dict]:
        cm = DerivativesTradingCoinFutures(config_rest_api=_auth_config(credential))
        resp = await self._call(cm.rest_api.position_information)
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data]

    async def fetch_balances(self, credential: Credential) -> list[Balance]:
        return [
            *normalize_spot_balances(await self._fetch_spot_balances(credential)),
            *normalize_futures_balances(await self._fetch_um_balances(credential)),
            *normalize_futures_balances(await self._fetch_cm_balances(credential)),
        ]

    async def fetch_positions(self, credential: Credential) -> list[Position]:
        if not self._native_map:
            await self.fetch_instruments()
        return [
            *normalize_positions(
                await self._fetch_um_positions(credential), MarketType.PERP, self._native_map
            ),
            *normalize_positions(
                await self._fetch_cm_positions(credential), MarketType.CMARGIN, self._native_map
            ),
        ]

    # -- orders -------------------------------------------------------------

    async def _resolve_symbol(self, universal_symbol: str) -> tuple[MarketType, str]:
        if not self._universal_map:
            await self.fetch_instruments()
        pair = self._universal_map.get(universal_symbol)
        if pair is None:
            raise ValueError(f"Unknown instrument {universal_symbol!r} for Binance")
        return pair

    def _rest_for(self, market_type: MarketType, credential: Credential):
        cfg = _auth_config(credential)
        if market_type == MarketType.SPOT:
            return Spot(config_rest_api=cfg).rest_api
        if market_type == MarketType.PERP:
            return DerivativesTradingUsdsFutures(config_rest_api=cfg).rest_api
        if market_type == MarketType.CMARGIN:
            return DerivativesTradingCoinFutures(config_rest_api=cfg).rest_api
        raise ValueError(f"Orders are not supported on {market_type} for Binance")

    async def place_order(self, credential: Credential, request: OrderRequest) -> Order:
        market_type, native_symbol = await self._resolve_symbol(request.universal_symbol)
        rest = self._rest_for(market_type, credential)
        is_spot = market_type == MarketType.SPOT
        kwargs: dict = {
            "symbol": native_symbol,
            "side": request.side.value,
            "type": request.type.value,
            "quantity": float(request.qty),
        }
        if request.type == OrderType.LIMIT:
            if request.price is not None:
                kwargs["price"] = float(request.price)
            post_only = request.time_in_force == TimeInForce.POST_ONLY
            if is_spot:
                # Spot post-only is a distinct order type, not a TIF.
                if post_only:
                    kwargs["type"] = "LIMIT_MAKER"
                    kwargs.pop("time_in_force", None)
                else:
                    kwargs["time_in_force"] = (request.time_in_force or TimeInForce.GTC).value
            else:
                # Futures express post-only through the GTX time-in-force.
                kwargs["time_in_force"] = (
                    "GTX" if post_only else (request.time_in_force or TimeInForce.GTC).value
                )
        if not is_spot:
            # Perp/coin-margined extras; ignored on spot.
            if request.reduce_only:
                kwargs["reduce_only"] = "true"
            if request.position_side is not None:
                kwargs["position_side"] = request.position_side.value
        if request.client_order_id:
            kwargs["new_client_order_id"] = request.client_order_id
        resp = await self._call(rest.new_order, **kwargs)
        return normalize_order(dump_sdk_model(resp.data()), request.universal_symbol)

    async def cancel_order(
        self, credential: Credential, universal_symbol: str, native_order_id: str
    ) -> Order:
        market_type, native_symbol = await self._resolve_symbol(universal_symbol)
        rest = self._rest_for(market_type, credential)
        method = rest.delete_order if market_type == MarketType.SPOT else rest.cancel_order
        resp = await self._call(method, symbol=native_symbol, order_id=int(native_order_id))
        return normalize_order(dump_sdk_model(resp.data()), universal_symbol)

    async def fetch_open_orders(
        self, credential: Credential, universal_symbol: str | None = None
    ) -> list[Order]:
        if not self._native_map:
            await self.fetch_instruments()
        orders: list[Order] = []
        surfaces = [
            (MarketType.SPOT, "get_open_orders"),
            (MarketType.PERP, "current_all_open_orders"),
            (MarketType.CMARGIN, "current_all_open_orders"),
        ]
        for market_type, method_name in surfaces:
            rest = self._rest_for(market_type, credential)
            resp = await self._call(getattr(rest, method_name))
            rows = dump_sdk_model(resp.data())
            for raw in rows if isinstance(rows, list) else [rows]:
                universal = self._native_map.get((market_type, raw.get("symbol", "")))
                if universal is None:
                    continue
                if universal_symbol and universal != universal_symbol:
                    continue
                orders.append(normalize_order(raw, universal))
        return orders

    async def fetch_order(
        self,
        credential: Credential,
        universal_symbol: str,
        native_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> Order | None:
        market_type, native_symbol = await self._resolve_symbol(universal_symbol)
        rest = self._rest_for(market_type, credential)
        method = rest.get_order if market_type == MarketType.SPOT else rest.query_order
        kwargs: dict = {"symbol": native_symbol}
        if native_order_id is not None:
            kwargs["order_id"] = int(native_order_id)
        elif client_order_id is not None:
            kwargs["orig_client_order_id"] = client_order_id
        else:
            return None
        try:
            resp = await self._call(method, **kwargs)
        except Exception:  # noqa: BLE001 - unknown id → treated as "not found"
            return None
        raw = dump_sdk_model(resp.data())
        return normalize_order(raw, universal_symbol) if raw else None

    async def list_subaccounts(self, credential: Credential) -> list[SubaccountInfo]:
        sub = SubAccount(config_rest_api=_auth_config(credential))
        resp = await self._call(sub.rest_api.query_sub_account_list, limit=200)
        data = dump_sdk_model(resp.data())
        return [
            SubaccountInfo(
                native_uid=row["email"],
                label=row["email"],
                status="frozen" if row.get("isFreeze") else "normal",
                raw=row,
            )
            for row in data.get("sub_accounts") or data.get("subAccounts") or []
        ]

    async def create_subaccount(self, credential: Credential, label: str) -> SubaccountInfo:
        sub = SubAccount(config_rest_api=_auth_config(credential))
        resp = await self._call(
            sub.rest_api.create_a_virtual_sub_account, sub_account_string=label
        )
        email = dump_sdk_model(resp.data()).get("email", "")
        return SubaccountInfo(native_uid=email, label=label, status="normal")

    # -- reference prices -----------------------------------------------------

    async def fetch_reference_prices(self) -> dict[str, Decimal]:
        if not self._native_map:
            await self.fetch_instruments()
        prices: dict[str, Decimal] = {}
        for market_type, rows in (
            (MarketType.SPOT, await self._fetch_spot_prices()),
            (MarketType.PERP, await self._fetch_um_prices()),
            (MarketType.CMARGIN, await self._fetch_cm_prices()),
        ):
            for row in rows:
                universal = self._native_map.get((market_type, row.get("symbol", "")))
                if universal is not None and row.get("price") is not None:
                    prices[universal] = _dec(row["price"])
        return prices
