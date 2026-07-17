"""Bybit connector (Phase 1: public market data).

Unlike Binance, Bybit serves every market from one base URL (v5 API): the
market type is a ``category`` query parameter, and results are paginated
with a cursor:

    SPOT     /v5/market/instruments-info?category=spot
    PERP     /v5/market/instruments-info?category=linear   (perpetuals only)
    CMARGIN  /v5/market/instruments-info?category=inverse  (perpetuals only)

The linear and inverse categories also contain dated futures
(LinearFutures / InverseFutures contract types); those are skipped until
DELIVERY support lands.

Uses the official ``pybit`` SDK (synchronous, run in a worker thread).
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from pybit.unified_trading import HTTP

from app.connectors.base import (
    DEFAULT_HISTORY_BACKFILL,
    Capability,
    Credential,
    ExchangeConnector,
)
from app.connectors.errors import credential_error_reason
from app.connectors.symbols import build_universal_symbol
from app.connectors.types import (
    ApiKeyResult,
    AssetNetwork,
    Balance,
    CredentialInfo,
    Deposit,
    DepositAddress,
    DepositStatus,
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
    PositionSide,
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

# Permission sets granted to panel-created sub API keys
_TRADE_PERMISSIONS = {
    "ContractTrade": ["Order", "Position"],
    "Spot": ["SpotTrade"],
    "Wallet": ["AccountTransfer", "SubMemberTransferList"],
}
_READ_PERMISSIONS = {
    "ContractTrade": ["Position"],
    "Wallet": ["AccountTransfer"],
}

# Panel-level permission scope tokens → Bybit's per-module permission entries.
# The dialog lets the operator pick any combination of these when creating a key.
_PERMISSION_SCOPES: dict[str, dict[str, list[str]]] = {
    "spot": {"Spot": ["SpotTrade"]},
    "derivatives": {"ContractTrade": ["Order", "Position"]},
    "wallet": {"Wallet": ["AccountTransfer", "SubMemberTransferList"]},
    "options": {"Options": ["OptionsTrade"]},
    "copytrading": {"CopyTrading": ["CopyTrading"]},
    "exchange": {"Exchange": ["ExchangeHistory"]},
}


def _build_permissions(scopes: list[str]) -> dict[str, list[str]]:
    """Merge the selected scope tokens into Bybit's permission object.

    Falls back to a minimal read-only position permission when nothing usable is
    selected (Bybit rejects an empty permission set)."""
    merged: dict[str, list[str]] = {}
    for scope in scopes:
        for category, entries in _PERMISSION_SCOPES.get(scope, {}).items():
            bucket = merged.setdefault(category, [])
            for entry in entries:
                if entry not in bucket:
                    bucket.append(entry)
    return merged or {"ContractTrade": ["Position"]}

_CATEGORIES: list[tuple[str, MarketType]] = [
    ("spot", MarketType.SPOT),
    ("linear", MarketType.PERP),
    ("inverse", MarketType.CMARGIN),
]

# Normalized market type -> Bybit v5 category.
_CATEGORY_OF = {
    MarketType.SPOT: "spot",
    MarketType.PERP: "linear",
    MarketType.CMARGIN: "inverse",
}

# Normalized time-in-force -> Bybit value.
_TIF = {
    TimeInForce.GTC: "GTC",
    TimeInForce.IOC: "IOC",
    TimeInForce.FOK: "FOK",
    TimeInForce.POST_ONLY: "PostOnly",
}

# Normalized position side -> Bybit positionIdx (0 one-way, 1 long, 2 short).
_POSITION_IDX = {PositionSide.BOTH: 0, PositionSide.LONG: 1, PositionSide.SHORT: 2}

# Bybit native order status -> normalized lifecycle.
_ORDER_STATUS = {
    "New": OrderStatus.OPEN,
    "Created": OrderStatus.OPEN,
    "Untriggered": OrderStatus.OPEN,
    "Triggered": OrderStatus.OPEN,
    "PartiallyFilled": OrderStatus.PARTIALLY_FILLED,
    "Filled": OrderStatus.FILLED,
    "Cancelled": OrderStatus.CANCELLED,
    "PartiallyFilledCanceled": OrderStatus.CANCELLED,
    "Deactivated": OrderStatus.CANCELLED,
    "Rejected": OrderStatus.REJECTED,
}

_PERP_CONTRACT_TYPES = {"LinearPerpetual", "InversePerpetual"}

# Normalized wallet -> Bybit v5 accountType. A Unified Trading Account keeps
# spot, margin, and derivatives collateral in one UNIFIED wallet; FUND is the
# separate funding wallet (also where on-chain withdrawals draw from).
_WALLET_ACCOUNT_TYPE = {
    WalletType.SPOT: "UNIFIED",
    WalletType.MARGIN: "UNIFIED",
    WalletType.DERIVATIVES: "UNIFIED",
    WalletType.FUNDING: "FUND",
}

# Withdrawal record ``status`` string -> normalized lifecycle.
# BlockchainConfirmed = broadcast (tx known) but not yet final.
_WITHDRAWAL_STATUS = {
    "SecurityCheck": WithdrawalStatus.PENDING,
    "Pending": WithdrawalStatus.PENDING,
    "success": WithdrawalStatus.COMPLETED,
    "CancelByUser": WithdrawalStatus.FAILED,
    "Reject": WithdrawalStatus.FAILED,
    "Fail": WithdrawalStatus.FAILED,
    "BlockchainConfirmed": WithdrawalStatus.SENT,
    "MoreInformationRequired": WithdrawalStatus.PENDING,
}

# Deposit record ``status`` int: 3 = success, 10012 = credited to funding
# pool; 4 = failed; the rest are still confirming.
_DEPOSIT_STATUS = {
    1: DepositStatus.PENDING,
    2: DepositStatus.PENDING,
    3: DepositStatus.CREDITED,
    4: DepositStatus.FAILED,
    10011: DepositStatus.PENDING,
    10012: DepositStatus.CREDITED,
}


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _status(info: dict) -> str:
    return "active" if info.get("status") == "Trading" else "halted"


def normalize_instrument(info: dict, market_type: MarketType) -> Instrument | None:
    if market_type in (MarketType.PERP, MarketType.CMARGIN):
        # linear/inverse categories mix perpetuals and dated futures
        if info.get("contractType") not in _PERP_CONTRACT_TYPES:
            return None
    price_filter = info.get("priceFilter") or {}
    lot = info.get("lotSizeFilter") or {}
    tick = price_filter.get("tickSize")
    if tick is None or not lot:
        return None

    if market_type == MarketType.SPOT:
        # Spot lot filter: basePrecision is the qty step, minOrderAmt the notional floor
        qty_step = lot.get("basePrecision")
        min_notional = lot.get("minOrderAmt")
        settle = None
    else:
        qty_step = lot.get("qtyStep")
        min_notional = lot.get("minNotionalValue")
        settle = info.get("settleCoin")
    if qty_step is None:
        return None

    base, quote = info["baseCoin"], info["quoteCoin"]
    max_qty = lot.get("maxOrderQty")
    return Instrument(
        universal_symbol=build_universal_symbol("BYBIT", market_type, base, quote),
        exchange="BYBIT",
        market_type=market_type,
        native_symbol=info["symbol"],
        base_asset=base,
        quote_asset=quote,
        filters=InstrumentFilters(
            price_tick=_dec(tick),
            qty_step=_dec(qty_step),
            min_qty=_dec(lot.get("minOrderQty", "0")),
            max_qty=_dec(max_qty) if max_qty is not None else None,
            min_notional=_dec(min_notional) if min_notional is not None else None,
        ),
        status=_status(info),
        settle_asset=settle,
    )


def _opt_dec(value) -> Decimal | None:
    return _dec(value) if value not in (None, "") else None


def normalize_wallet_balances(coins: list[dict]) -> list[Balance]:
    """Unified-account coin rows → normalized balances (zeros dropped).

    Bybit's Unified Trading Account holds spot and derivatives collateral
    together; there is no separate spot/derivatives split over the API, so
    every coin is reported under the DERIVATIVES wallet. ``walletBalance`` is
    the coin total and ``locked`` the amount reserved by open orders/margin.
    """
    balances: list[Balance] = []
    for coin in coins:
        total = _dec(coin.get("walletBalance", 0) or 0)
        locked = _dec(coin.get("locked", 0) or 0)
        if total == 0 and locked == 0:
            continue
        if locked > total:
            locked = total
        balances.append(
            Balance(
                wallet=WalletType.DERIVATIVES,
                asset=coin["coin"],
                free=total - locked,
                locked=locked,
            )
        )
    return balances


def normalize_positions(
    rows: list[dict], native_map: dict[tuple[MarketType, str], str]
) -> list[Position]:
    """Position rows → normalized non-zero positions.

    A native symbol resolves to exactly one universal symbol across the
    perpetual categories (linear→PERP, inverse→CMARGIN); symbols the panel
    does not track are skipped.
    """
    positions: list[Position] = []
    for row in rows:
        size = _dec(row.get("size", 0) or 0)
        if size == 0:
            continue
        symbol = row.get("symbol", "")
        universal = native_map.get((MarketType.PERP, symbol)) or native_map.get(
            (MarketType.CMARGIN, symbol)
        )
        if universal is None:
            continue
        positions.append(
            Position(
                universal_symbol=universal,
                side="long" if row.get("side") == "Buy" else "short",
                size=size,
                entry_price=_dec(row.get("avgPrice", 0) or 0),
                mark_price=_opt_dec(row.get("markPrice")),
                unrealized_pnl=_opt_dec(row.get("unrealisedPnl")),
                margin=_opt_dec(row.get("positionIM")),
            )
        )
    return positions


def _order_status(raw: str | None) -> OrderStatus:
    return _ORDER_STATUS.get(raw or "", OrderStatus.OPEN)


def _epoch_ms(value) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def normalize_trade(raw: dict, universal_symbol: str) -> Trade:
    """A Bybit v5 execution dict → normalized Trade."""
    return Trade(
        universal_symbol=universal_symbol,
        native_trade_id=str(raw.get("execId", "")),
        native_order_id=str(raw.get("orderId", "")),
        side=OrderSide.BUY if raw.get("side") == "Buy" else OrderSide.SELL,
        price=_dec(raw.get("execPrice", 0) or 0),
        qty=_dec(raw.get("execQty", 0) or 0),
        fee=_dec(raw.get("execFee", 0) or 0),
        fee_asset=raw.get("feeCurrency") or "",
        traded_at=_epoch_ms(raw.get("execTime")),
    )


def normalize_order(raw: dict, universal_symbol: str) -> Order:
    """A Bybit order dict → normalized Order."""
    price = _opt_dec(raw.get("price"))
    return Order(
        universal_symbol=universal_symbol,
        native_order_id=str(raw.get("orderId", "")),
        client_order_id=raw.get("orderLinkId") or None,
        side=OrderSide.BUY if raw.get("side") == "Buy" else OrderSide.SELL,
        type=OrderType.MARKET if raw.get("orderType") == "Market" else OrderType.LIMIT,
        status=_order_status(raw.get("orderStatus")),
        qty=_dec(raw.get("qty", 0) or 0),
        filled_qty=_dec(raw.get("cumExecQty", 0) or 0),
        price=price if price and price > 0 else None,
        created_at=_epoch_ms(raw.get("createdTime")),
        updated_at=_epoch_ms(raw.get("updatedTime")),
    )


def normalize_asset_networks(coin_row: dict) -> list[AssetNetwork]:
    """One get_coin_info row → the asset's chains."""
    networks: list[AssetNetwork] = []
    for chain in coin_row.get("chains", []):
        networks.append(
            AssetNetwork(
                network=str(chain.get("chain", "")).upper(),
                deposit_enabled=chain.get("chainDeposit") == "1",
                withdraw_enabled=chain.get("chainWithdraw") == "1",
                withdraw_min=_dec(chain.get("withdrawMin", 0) or 0),
                withdraw_fee=_dec(chain.get("withdrawFee", 0) or 0),
            )
        )
    return networks


def normalize_withdrawal_record(raw: dict) -> Withdrawal:
    """A get_withdrawal_records row → normalized Withdrawal."""
    return Withdrawal(
        native_id=str(raw.get("withdrawId", "")),
        asset=raw.get("coin") or "",
        amount=_dec(raw.get("amount", 0) or 0),
        status=_WITHDRAWAL_STATUS.get(raw.get("status") or "", WithdrawalStatus.PENDING),
        address=raw.get("toAddress") or None,
        network=(raw.get("chain") or None) and str(raw["chain"]).upper(),
        tx_id=raw.get("txID") or None,
        fee=_opt_dec(raw.get("withdrawFee")),
    )


def normalize_deposit_record(raw: dict) -> Deposit:
    """A get_deposit_records row → normalized Deposit."""
    try:
        status = _DEPOSIT_STATUS.get(int(raw.get("status", 0)), DepositStatus.PENDING)
    except (TypeError, ValueError):
        status = DepositStatus.PENDING
    return Deposit(
        native_id=str(raw.get("txID") or raw.get("id") or ""),
        asset=raw.get("coin") or "",
        amount=_dec(raw.get("amount", 0) or 0),
        status=status,
        network=(raw.get("chain") or None) and str(raw["chain"]).upper(),
        tx_id=raw.get("txID") or None,
        credited_at=_epoch_ms(raw.get("successAt")),
    )


class BybitConnector(ExchangeConnector):
    exchange = "BYBIT"
    # Bybit exposes full self-service account management over API, so the
    # whole provisioning surface is available (HMAC keys only — Bybit does
    # not issue Ed25519 keys via API).
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
            Capability.SUBACCOUNT_FREEZE,
            Capability.API_KEY_CREATE,
            Capability.API_KEY_MODIFY,
            Capability.API_KEY_REVOKE,
            Capability.TRANSFERS,
            Capability.WITHDRAWALS,
            Capability.DEPOSITS,
        }
    )

    # settle coins queried for linear (USDT/USDC) positions; inverse settles
    # in the base coin, queried with no settle filter.
    _LINEAR_SETTLE = ("USDT", "USDC")

    def __init__(self, bucket: TokenBucket | None = None):
        self._bucket = bucket
        self._session = HTTP(timeout=30)
        self._native_map: dict[tuple[MarketType, str], str] = {}
        self._universal_map: dict[str, tuple[MarketType, str]] = {}

    async def _call(self, func, **kwargs):
        if self._bucket is not None:
            await self._bucket.acquire(priority=Priority.SYNC)
        return await asyncio.to_thread(func, **kwargs)

    # -- raw fetches (overridden by tests with canned payloads) --------------

    async def _fetch_category_instruments(self, category: str) -> list[dict]:
        """Walk the cursor pagination of one category to exhaustion."""
        items: list[dict] = []
        cursor: str | None = None
        while True:
            kwargs = {"category": category, "limit": 1000}
            if cursor:
                kwargs["cursor"] = cursor
            response = await self._call(self._session.get_instruments_info, **kwargs)
            result = response.get("result", {})
            items.extend(result.get("list", []))
            cursor = result.get("nextPageCursor") or None
            if not cursor:
                return items

    async def _fetch_category_tickers(self, category: str) -> list[dict]:
        response = await self._call(self._session.get_tickers, category=category)
        return response.get("result", {}).get("list", [])

    # -- accounts -----------------------------------------------------------

    @staticmethod
    def _auth_session(credential: Credential) -> HTTP:
        return HTTP(
            api_key=credential["api_key"],
            api_secret=credential["secret"],
            timeout=30,
        )

    # -- account state ------------------------------------------------------

    async def _fetch_wallet_coins(self, credential: Credential) -> list[dict]:
        session = self._auth_session(credential)
        response = await self._call(session.get_wallet_balance, accountType="UNIFIED")
        accounts = response.get("result", {}).get("list", [])
        coins: list[dict] = []
        for account in accounts:
            coins.extend(account.get("coin", []))
        return coins

    async def _fetch_positions(
        self, credential: Credential, category: str, settle_coin: str | None = None
    ) -> list[dict]:
        session = self._auth_session(credential)
        kwargs: dict = {"category": category, "limit": 200}
        if settle_coin:
            kwargs["settleCoin"] = settle_coin
        response = await self._call(session.get_positions, **kwargs)
        return response.get("result", {}).get("list", [])

    async def fetch_balances(self, credential: Credential) -> list[Balance]:
        return normalize_wallet_balances(await self._fetch_wallet_coins(credential))

    async def fetch_positions(self, credential: Credential) -> list[Position]:
        if not self._native_map:
            await self.fetch_instruments()
        rows: list[dict] = []
        for settle in self._LINEAR_SETTLE:
            rows.extend(await self._fetch_positions(credential, "linear", settle))
        rows.extend(await self._fetch_positions(credential, "inverse"))
        return normalize_positions(rows, self._native_map)

    # -- orders -------------------------------------------------------------

    async def _resolve_symbol(self, universal_symbol: str) -> tuple[str, str]:
        """universal symbol -> (bybit category, native symbol)."""
        if not self._universal_map:
            await self.fetch_instruments()
        pair = self._universal_map.get(universal_symbol)
        if pair is None:
            raise ValueError(f"Unknown instrument {universal_symbol!r} for Bybit")
        market_type, native_symbol = pair
        return _CATEGORY_OF[market_type], native_symbol

    async def place_order(self, credential: Credential, request: OrderRequest) -> Order:
        category, native_symbol = await self._resolve_symbol(request.universal_symbol)
        session = self._auth_session(credential)
        kwargs: dict = {
            "category": category,
            "symbol": native_symbol,
            "side": "Buy" if request.side == OrderSide.BUY else "Sell",
            "orderType": "Market" if request.type == OrderType.MARKET else "Limit",
            "qty": str(request.qty),
        }
        if request.type == OrderType.LIMIT and request.price is not None:
            kwargs["price"] = str(request.price)
            kwargs["timeInForce"] = _TIF.get(request.time_in_force or TimeInForce.GTC, "GTC")
        if category != "spot":
            # reduceOnly / positionIdx apply only to derivatives categories.
            if request.reduce_only:
                kwargs["reduceOnly"] = True
            if request.position_side is not None:
                kwargs["positionIdx"] = _POSITION_IDX[request.position_side]
        if request.client_order_id:
            kwargs["orderLinkId"] = request.client_order_id
        response = await self._call(session.place_order, **kwargs)
        result = response.get("result", {})
        # place_order returns only ids; echo the request as an accepted OPEN order.
        return Order(
            universal_symbol=request.universal_symbol,
            native_order_id=str(result.get("orderId", "")),
            client_order_id=result.get("orderLinkId") or request.client_order_id,
            side=request.side,
            type=request.type,
            status=OrderStatus.OPEN,
            qty=request.qty,
            filled_qty=Decimal("0"),
            price=request.price,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    async def cancel_order(
        self, credential: Credential, universal_symbol: str, native_order_id: str
    ) -> Order:
        category, native_symbol = await self._resolve_symbol(universal_symbol)
        session = self._auth_session(credential)
        await self._call(
            session.cancel_order,
            category=category,
            symbol=native_symbol,
            orderId=native_order_id,
        )
        fetched = await self.fetch_order(
            credential, universal_symbol, native_order_id=native_order_id
        )
        if fetched is not None:
            return fetched
        return Order(
            universal_symbol=universal_symbol,
            native_order_id=native_order_id,
            client_order_id=None,
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            status=OrderStatus.CANCELLED,
            qty=Decimal("0"),
            filled_qty=Decimal("0"),
            price=None,
            updated_at=datetime.now(timezone.utc),
        )

    async def _fetch_open_orders(
        self, session: HTTP, category: str, settle_coin: str | None = None
    ) -> list[dict]:
        kwargs: dict = {"category": category, "limit": 50}
        if settle_coin:
            kwargs["settleCoin"] = settle_coin
        response = await self._call(session.get_open_orders, **kwargs)
        return response.get("result", {}).get("list", [])

    async def fetch_open_orders(
        self, credential: Credential, universal_symbol: str | None = None
    ) -> list[Order]:
        if not self._native_map:
            await self.fetch_instruments()
        session = self._auth_session(credential)
        rows: list[tuple[MarketType, dict]] = []
        for raw in await self._fetch_open_orders(session, "spot"):
            rows.append((MarketType.SPOT, raw))
        for settle in self._LINEAR_SETTLE:
            for raw in await self._fetch_open_orders(session, "linear", settle):
                rows.append((MarketType.PERP, raw))
        for raw in await self._fetch_open_orders(session, "inverse"):
            rows.append((MarketType.CMARGIN, raw))

        orders: list[Order] = []
        for market_type, raw in rows:
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
        category, native_symbol = await self._resolve_symbol(universal_symbol)
        session = self._auth_session(credential)
        kwargs: dict = {"category": category, "symbol": native_symbol}
        if native_order_id is not None:
            kwargs["orderId"] = native_order_id
        elif client_order_id is not None:
            kwargs["orderLinkId"] = client_order_id
        else:
            return None
        try:
            response = await self._call(session.get_order_history, **kwargs)
        except Exception:  # noqa: BLE001 - unknown id → treated as "not found"
            return None
        items = response.get("result", {}).get("list", [])
        return normalize_order(items[0], universal_symbol) if items else None

    # -- history --------------------------------------------------------------
    #
    # Bybit serves account-wide history per v5 category, so each category is
    # one stream. Endpoints combine a bounded time window with native token
    # pagination, so the cursor is a JSON pair: the window start (epoch ms)
    # plus the venue's nextPageCursor within that window. Each fetch makes
    # exactly one venue call; the window slides forward once its pages are
    # exhausted, until it reaches the present.

    _HISTORY_WINDOW_MS = 24 * 60 * 60 * 1000

    def history_stream_keys(self, active_symbols: list[str]) -> list[str]:
        return [category for category, _ in _CATEGORIES]

    def initial_history_cursor(self, start: datetime) -> str | None:
        return json.dumps({"start": int(start.timestamp() * 1000)})

    def _history_window(self, cursor: str | None) -> tuple[int, int, int, str | None]:
        """(window start, window end, now, native page token) for one fetch."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        native: str | None = None
        if cursor:
            decoded = json.loads(cursor)
            start_ms = int(decoded["start"])
            native = decoded.get("cursor") or None
        else:
            start_ms = now_ms - int(DEFAULT_HISTORY_BACKFILL.total_seconds() * 1000)
        return start_ms, min(start_ms + self._HISTORY_WINDOW_MS, now_ms), now_ms, native

    @staticmethod
    def _next_history_cursor(
        start_ms: int, end_ms: int, now_ms: int, native: str | None
    ) -> str | None:
        if native:
            return json.dumps({"start": start_ms, "cursor": native})
        if end_ms < now_ms:
            return json.dumps({"start": end_ms})
        return None

    async def _fetch_history_page(
        self,
        credential: Credential,
        method_name: str,
        category: str,
        start_ms: int,
        end_ms: int,
        limit: int,
        native_cursor: str | None,
    ) -> tuple[list[dict], str | None]:
        session = self._auth_session(credential)
        kwargs: dict = {
            "category": category,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": limit,
        }
        if native_cursor:
            kwargs["cursor"] = native_cursor
        response = await self._call(getattr(session, method_name), **kwargs)
        result = response.get("result", {})
        return result.get("list", []), result.get("nextPageCursor") or None

    def _universal_for(self, category: str, native_symbol: str) -> str | None:
        market_type = dict(_CATEGORIES)[category]
        return self._native_map.get((market_type, native_symbol))

    async def _history_page(
        self,
        credential: Credential,
        method_name: str,
        stream_key: str,
        cursor: str | None,
        limit: int,
        normalize,
    ) -> Page:
        if not self._native_map:
            await self.fetch_instruments()
        start_ms, end_ms, now_ms, native = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        rows, next_native = await self._fetch_history_page(
            credential, method_name, stream_key, start_ms, end_ms, limit, native
        )
        items = []
        for raw in rows:
            universal = self._universal_for(stream_key, raw.get("symbol", ""))
            if universal is None:
                continue  # dated futures / untracked instruments
            items.append(normalize(raw, universal))
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(start_ms, end_ms, now_ms, next_native),
        )

    async def fetch_trades(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        return await self._history_page(
            credential, "get_executions", stream_key, cursor, 100, normalize_trade
        )

    async def fetch_orders_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        return await self._history_page(
            credential, "get_order_history", stream_key, cursor, 50, normalize_order
        )

    async def verify_credential(self, credential: Credential) -> CredentialInfo:
        session = self._auth_session(credential)
        try:
            response = await self._call(session.get_api_key_information)
        except Exception as exc:
            reason = credential_error_reason(self.exchange, exc)
            logger.warning("Bybit credential verification failed: %s", reason, exc_info=True)
            return CredentialInfo(valid=False, reason=reason)
        info = response.get("result", {})
        permissions = info.get("permissions") or {}
        can_trade = bool(permissions.get("ContractTrade") or permissions.get("Spot"))
        can_withdraw = "Withdraw" in (permissions.get("Wallet") or [])
        return CredentialInfo(
            valid=True,
            native_uid=str(info.get("userID", "")) or None,
            is_master=bool(info.get("isMaster")),
            read_only=info.get("readOnly") == 1,
            can_trade=can_trade,
            can_withdraw=can_withdraw,
            permissions=permissions,
        )

    async def list_subaccounts(self, credential: Credential) -> list[SubaccountInfo]:
        session = self._auth_session(credential)
        response = await self._call(session.get_sub_uid_list)
        members = response.get("result", {}).get("subMembers", [])
        return [
            SubaccountInfo(
                native_uid=str(m["uid"]),
                label=m.get("username") or str(m["uid"]),
                status="normal" if m.get("status") == 1 else "frozen",
                raw=m,
            )
            for m in members
        ]

    async def create_subaccount(self, credential: Credential, label: str) -> SubaccountInfo:
        session = self._auth_session(credential)
        response = await self._call(
            session.create_sub_uid, username=label, memberType=1, note="created by cpanel"
        )
        result = response.get("result", {})
        return SubaccountInfo(
            native_uid=str(result.get("uid", "")),
            label=result.get("username") or label,
            status="normal",
        )

    async def create_api_key(
        self, credential: Credential, subaccount: str | None, scheme: KeyScheme,
        permissions: list[str],
        read_only: bool = False,
        ips: list[str] | None = None,
        note: str | None = None,
    ) -> ApiKeyResult:
        if scheme != KeyScheme.HMAC:
            raise ValueError("Bybit issues only HMAC keys via API")
        if not subaccount:
            raise ValueError("Bybit API key creation targets a subaccount uid")
        session = self._auth_session(credential)
        kwargs: dict = {
            "subuid": int(subaccount),
            "readOnly": 1 if read_only else 0,
            "permissions": _build_permissions(permissions),
            "note": note or "created by cpanel",
        }
        if ips:
            kwargs["ips"] = ",".join(ips)
        response = await self._call(session.create_sub_api_key, **kwargs)
        result = response.get("result", {})
        return ApiKeyResult(
            api_key=result["apiKey"],
            secret=result["secret"],
            scheme=KeyScheme.HMAC,
            permissions=permissions,
        )

    async def modify_api_key(
        self,
        credential: Credential,
        subaccount: str | None,
        api_key: str,
        read_only: bool | None = None,
        permissions: list[str] | None = None,
    ) -> None:
        """Change a sub key's read-only flag and/or permission set.

        Called with the master credential, so the target sub key is named via
        the ``apikey`` parameter (Bybit's convention for master-managed keys).
        """
        session = self._auth_session(credential)
        kwargs: dict = {"apikey": api_key}
        if read_only is not None:
            kwargs["readOnly"] = 1 if read_only else 0
        if permissions is not None:
            grant_trade = "trade" in permissions
            kwargs["permissions"] = _TRADE_PERMISSIONS if grant_trade else _READ_PERMISSIONS
        await self._call(session.modify_sub_api_key, **kwargs)

    async def revoke_api_key(
        self, credential: Credential, subaccount: str | None, api_key: str
    ) -> None:
        session = self._auth_session(credential)
        await self._call(session.delete_sub_api_key, apiKey=api_key)

    async def set_subaccount_frozen(
        self, credential: Credential, subaccount: str, frozen: bool
    ) -> None:
        session = self._auth_session(credential)
        await self._call(
            session.freeze_sub_uid, subuid=int(subaccount), frozen=1 if frozen else 0
        )

    # -- transfers / withdrawals / deposits -----------------------------------
    #
    # Same-account wallet moves use the internal transfer (FUND ↔ UNIFIED);
    # moves between uids (main ↔ sub) use the universal transfer, which needs
    # both member ids — a ``None`` side resolves to the credential's own uid.
    # Withdrawals draw from the FUND wallet (Bybit's rule for UTA accounts).

    async def _master_uid(self, credential: Credential) -> str:
        session = self._auth_session(credential)
        response = await self._call(session.get_api_key_information)
        uid = str(response.get("result", {}).get("userID", "") or "")
        if not uid:
            raise ValueError("Could not resolve the credential's Bybit uid")
        return uid

    @staticmethod
    def _transfer_result(result: dict) -> TransferResult:
        status = (result.get("status") or "").upper()
        if status in ("SUCCESS", ""):
            normalized = "confirmed"
        elif status == "FAILED":
            normalized = "failed"
        else:
            normalized = "submitted"
        return TransferResult(
            native_transfer_id=str(result.get("transferId", "")), status=normalized
        )

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
        session = self._auth_session(credential)
        src = _WALLET_ACCOUNT_TYPE[from_wallet or WalletType.DERIVATIVES]
        dst = _WALLET_ACCOUNT_TYPE[to_wallet or WalletType.DERIVATIVES]
        transfer_id = str(uuid.uuid4())
        if from_subaccount or to_subaccount:
            # Universal transfer needs both member ids; a None side is the
            # credential's own (master) account.
            master_uid = None
            if not from_subaccount or not to_subaccount:
                master_uid = await self._master_uid(credential)
            from_uid = from_subaccount or master_uid
            to_uid = to_subaccount or master_uid
            response = await self._call(
                session.create_universal_transfer,
                transferId=transfer_id,
                coin=asset,
                amount=str(amount),
                fromMemberId=int(from_uid),
                toMemberId=int(to_uid),
                fromAccountType=src,
                toAccountType=dst,
            )
        else:
            if src == dst:
                raise ValueError(
                    "Bybit internal transfers move between FUND and UNIFIED wallets"
                )
            response = await self._call(
                session.create_internal_transfer,
                transferId=transfer_id,
                coin=asset,
                amount=str(amount),
                fromAccountType=src,
                toAccountType=dst,
            )
        return self._transfer_result(response.get("result", {}))

    async def _fetch_coin_info(self, credential: Credential, asset: str) -> dict:
        session = self._auth_session(credential)
        response = await self._call(session.get_coin_info, coin=asset)
        rows = response.get("result", {}).get("rows", [])
        return rows[0] if rows else {}

    async def fetch_asset_networks(
        self, credential: Credential, asset: str
    ) -> list[AssetNetwork]:
        return normalize_asset_networks(await self._fetch_coin_info(credential, asset))

    async def _fetch_deposit_address_raw(
        self, credential: Credential, asset: str, network: str, subaccount: str | None
    ) -> dict:
        session = self._auth_session(credential)
        if subaccount:
            response = await self._call(
                session.get_sub_deposit_address,
                coin=asset, chainType=network, subMemberId=subaccount,
            )
        else:
            response = await self._call(
                session.get_master_deposit_address, coin=asset, chainType=network
            )
        return response.get("result", {})

    async def fetch_deposit_address(
        self,
        credential: Credential,
        asset: str,
        network: str,
        subaccount: str | None = None,
    ) -> DepositAddress:
        result = await self._fetch_deposit_address_raw(
            credential, asset, network, subaccount
        )
        chains = result.get("chains")
        chain = (chains[0] if isinstance(chains, list) else chains) or {}
        address = chain.get("addressDeposit") or ""
        if not address:
            raise ValueError(f"Bybit returned no {asset}/{network} deposit address")
        return DepositAddress(
            asset=asset,
            network=network.upper(),
            address=address,
            tag=chain.get("tagDeposit") or None,
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
        session = self._auth_session(credential)
        kwargs: dict = {
            "coin": asset,
            "chain": network,
            "address": address,
            "amount": str(amount),
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "accountType": "FUND",
        }
        if tag:
            kwargs["tag"] = tag
        response = await self._call(session.withdraw, **kwargs)
        return Withdrawal(
            native_id=str(response.get("result", {}).get("id", "")),
            asset=asset,
            amount=_dec(amount),
            status=WithdrawalStatus.PENDING,
            address=address,
            network=network.upper(),
        )

    async def fetch_withdrawal(
        self, credential: Credential, native_id: str
    ) -> Withdrawal | None:
        session = self._auth_session(credential)
        response = await self._call(
            session.get_withdrawal_records, withdrawID=native_id, limit=1
        )
        rows = response.get("result", {}).get("rows", [])
        return normalize_withdrawal_record(rows[0]) if rows else None

    async def _fetch_deposit_rows(
        self,
        credential: Credential,
        asset: str | None,
        start_ms: int | None,
        subaccount: str | None,
    ) -> list[dict]:
        session = self._auth_session(credential)
        kwargs: dict = {"limit": 50}
        if asset:
            kwargs["coin"] = asset
        if start_ms is not None:
            kwargs["startTime"] = start_ms
        if subaccount:
            response = await self._call(
                session.get_sub_deposit_records, subMemberId=subaccount, **kwargs
            )
        else:
            response = await self._call(session.get_deposit_records, **kwargs)
        return response.get("result", {}).get("rows", [])

    async def fetch_deposits(
        self,
        credential: Credential,
        asset: str | None = None,
        since: datetime | None = None,
        subaccount: str | None = None,
    ) -> list[Deposit]:
        start_ms = int(since.timestamp() * 1000) if since else None
        rows = await self._fetch_deposit_rows(credential, asset, start_ms, subaccount)
        return [normalize_deposit_record(raw) for raw in rows]

    # -- connector interface --------------------------------------------------

    async def fetch_instruments(self) -> list[Instrument]:
        instruments: list[Instrument] = []
        for category, market_type in _CATEGORIES:
            for info in await self._fetch_category_instruments(category):
                instrument = normalize_instrument(info, market_type)
                if instrument is not None:
                    instruments.append(instrument)
        self._native_map = {
            (i.market_type, i.native_symbol): i.universal_symbol for i in instruments
        }
        self._universal_map = {
            i.universal_symbol: (i.market_type, i.native_symbol) for i in instruments
        }
        return instruments

    async def fetch_reference_prices(self) -> dict[str, Decimal]:
        if not self._native_map:
            await self.fetch_instruments()
        prices: dict[str, Decimal] = {}
        for category, market_type in _CATEGORIES:
            for row in await self._fetch_category_tickers(category):
                universal = self._native_map.get((market_type, row.get("symbol", "")))
                last = row.get("lastPrice")
                if universal is not None and last:
                    prices[universal] = _dec(last)
        return prices
