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

from app.connectors.base import (
    DEFAULT_HISTORY_BACKFILL,
    Capability,
    Credential,
    ExchangeConnector,
)
from app.connectors.errors import credential_error_reason
from app.connectors.sdk_util import dump_sdk_model
from app.connectors.symbols import build_universal_symbol
from app.connectors.types import (
    AssetNetwork,
    Balance,
    CredentialInfo,
    Deposit,
    DepositAddress,
    DepositStatus,
    FundingFee,
    Instrument,
    InstrumentFilters,
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
    """USD-M/COIN-M account-balance rows → normalized FUTURES balances.

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
                wallet=WalletType.FUTURES,
                asset=row["asset"],
                free=total - locked,
                locked=locked,
            )
        )
    return balances


def normalize_funding_balances(rows: list[dict]) -> list[Balance]:
    """Funding-wallet asset rows → normalized FUNDING balances (zeros dropped).

    ``freeze`` and ``withdrawing`` amounts are committed elsewhere, so they
    count as locked alongside ``locked`` itself.
    """
    balances: list[Balance] = []
    for row in rows:
        free = _dec(row.get("free", 0))
        locked = (
            _dec(row.get("locked", 0))
            + _dec(row.get("freeze", 0))
            + _dec(row.get("withdrawing", 0))
        )
        if free == 0 and locked == 0:
            continue
        balances.append(
            Balance(wallet=WalletType.FUNDING, asset=row["asset"], free=free, locked=locked)
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


def normalize_trade(raw: dict, universal_symbol: str) -> Trade:
    """A Binance fill dict (spot or futures) → normalized Trade.

    Spot rows carry ``isBuyer``; futures rows carry an explicit ``side``.
    """
    if raw.get("side"):
        side = OrderSide(str(raw["side"]).upper())
    else:
        side = OrderSide.BUY if raw.get("isBuyer") else OrderSide.SELL
    return Trade(
        universal_symbol=universal_symbol,
        native_trade_id=str(raw.get("id", "")),
        native_order_id=str(raw.get("orderId", "")),
        side=side,
        price=_dec(raw.get("price", 0) or 0),
        qty=_dec(raw.get("qty", 0) or 0),
        fee=_dec(raw.get("commission", 0) or 0),
        fee_asset=raw.get("commissionAsset") or "",
        traded_at=_epoch_ms(raw.get("time")),
    )


# -- transfers / withdrawals / deposits ---------------------------------------

# Wallet type -> leg code of the user_universal_transfer ``type`` parameter
# (built as FROM_TO, e.g. MAIN_UMFUTURE for spot -> USD-M futures).
_UNIVERSAL_TRANSFER_LEG = {
    WalletType.SPOT: "MAIN",
    WalletType.FUNDING: "FUNDING",
    WalletType.FUTURES: "UMFUTURE",
}

# Wallet type -> sub-account universal-transfer accountType (no FUNDING leg
# on this endpoint).
_SUB_TRANSFER_WALLET = {
    WalletType.SPOT: "SPOT",
    WalletType.FUTURES: "USDT_FUTURE",
}

# Withdraw-history ``status`` int -> normalized lifecycle. 6 = Completed
# means the venue sent it on-chain.
_WITHDRAWAL_STATUS = {
    0: WithdrawalStatus.PENDING,   # email sent
    1: WithdrawalStatus.FAILED,    # cancelled
    2: WithdrawalStatus.PENDING,   # awaiting approval
    3: WithdrawalStatus.FAILED,    # rejected
    4: WithdrawalStatus.PENDING,   # processing
    5: WithdrawalStatus.FAILED,    # failure
    6: WithdrawalStatus.COMPLETED,
}

# Deposit-history ``status`` int: 0 pending, 1 success, 6 credited but cannot
# withdraw (still credited for our purposes), 7 wrong deposit, 8 waiting user
# confirm.
_DEPOSIT_STATUS = {
    0: DepositStatus.PENDING,
    1: DepositStatus.CREDITED,
    6: DepositStatus.CREDITED,
    7: DepositStatus.FAILED,
    8: DepositStatus.PENDING,
}


def normalize_asset_networks(coin_row: dict) -> list[AssetNetwork]:
    """One all_coins_information row → the asset's chains."""
    networks: list[AssetNetwork] = []
    for net in coin_row.get("networkList", []):
        networks.append(
            AssetNetwork(
                network=str(net.get("network", "")).upper(),
                deposit_enabled=bool(net.get("depositEnable")),
                withdraw_enabled=bool(net.get("withdrawEnable")),
                withdraw_min=_dec(net.get("withdrawMin", 0) or 0),
                withdraw_fee=_dec(net.get("withdrawFee", 0) or 0),
            )
        )
    return networks


def _binance_wall_time(value) -> datetime | None:
    """Binance deposit times are epoch-ms; withdraw ``applyTime`` is a UTC string."""
    parsed = _epoch_ms(value)
    if parsed is not None:
        return parsed
    if isinstance(value, str) and value:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def normalize_withdrawal(raw: dict) -> Withdrawal:
    """A withdraw-history row → normalized Withdrawal."""
    status = _WITHDRAWAL_STATUS.get(int(raw.get("status", 0)), WithdrawalStatus.PENDING)
    tx_id = raw.get("txId") or None
    if status == WithdrawalStatus.PENDING and tx_id:
        status = WithdrawalStatus.SENT
    created_at = _binance_wall_time(raw.get("applyTime") or raw.get("completeTime"))
    return Withdrawal(
        native_id=str(raw.get("id", "")),
        asset=raw.get("coin") or "",
        amount=_dec(raw.get("amount", 0) or 0),
        status=status,
        address=raw.get("address") or None,
        network=(raw.get("network") or None) and str(raw["network"]).upper(),
        tx_id=tx_id,
        fee=_opt_dec(raw.get("transactionFee")),
        created_at=created_at,
    )


def normalize_deposit(raw: dict) -> Deposit:
    """A deposit-history row → normalized Deposit."""
    created_at = _epoch_ms(raw.get("insertTime"))
    status = _DEPOSIT_STATUS.get(int(raw.get("status", 0)), DepositStatus.PENDING)
    return Deposit(
        native_id=str(raw.get("id") or raw.get("txId") or ""),
        asset=raw.get("coin") or "",
        amount=_dec(raw.get("amount", 0) or 0),
        status=status,
        network=(raw.get("network") or None) and str(raw["network"]).upper(),
        tx_id=raw.get("txId") or None,
        credited_at=created_at if status == DepositStatus.CREDITED else None,
        created_at=created_at,
    )


def normalize_funding_fee(
    raw: dict, universal_symbol: str, *, stream_key: str
) -> FundingFee:
    """A futures income row (incomeType=FUNDING_FEE) → FundingFee.

    ``tranId`` is unique per income type on one surface; prefix with the
    stream key so UM/CM ids cannot collide in the shared history table.
    """
    tran_id = raw.get("tranId") or raw.get("tranID") or ""
    return FundingFee(
        universal_symbol=universal_symbol,
        native_id=f"{stream_key}:{tran_id}",
        asset=raw.get("asset") or "",
        amount=_dec(raw.get("income", 0) or 0),
        funding_rate=None,
        settled_at=_epoch_ms(raw.get("time")),
    )


# History endpoints are windowed by time. 24h satisfies every surface's
# constraint (spot my_trades/all_orders require < 24h when both bounds are
# set; futures allow up to 7 days).
_HISTORY_WINDOW_MS = 24 * 60 * 60 * 1000

# Per-surface page caps (venue maxima; dapi all_orders caps at 100).
_TRADE_PAGE_LIMIT = {MarketType.SPOT: 1000, MarketType.PERP: 1000, MarketType.CMARGIN: 1000}
_ORDER_PAGE_LIMIT = {MarketType.SPOT: 1000, MarketType.PERP: 1000, MarketType.CMARGIN: 100}


def _advance_window(
    rows: list[dict], time_key: str, start_ms: int, end_ms: int, now_ms: int, limit: int
) -> str | None:
    """Next cursor for a windowed walk: stay in the window while pages are
    full, otherwise jump to the window end; ``None`` once the window reaches
    the present (caught up)."""
    if len(rows) >= limit:
        last = max(int(r.get(time_key, start_ms) or start_ms) for r in rows)
        return str(max(last, start_ms + 1))
    if end_ms < now_ms:
        return str(end_ms)
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
            Capability.TRADE_HISTORY,
            Capability.ORDER_HISTORY,
            Capability.SUBACCOUNT_LIST,
            Capability.TRANSFERS,
            Capability.WITHDRAWALS,
            Capability.DEPOSITS,
            Capability.FUNDING_HISTORY,
        }
    )
    # Binance has more wallets (Margin, Earn, Trading Bots, …) but the panel
    # supports the three that matter for moving and holding funds.
    wallets = (WalletType.SPOT, WalletType.FUNDING, WalletType.FUTURES)

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

    async def _fetch_funding_balances(self, credential: Credential) -> list[dict]:
        wallet = Wallet(config_rest_api=_auth_config(credential))
        resp = await self._call(wallet.rest_api.funding_wallet)
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data]

    async def fetch_balances(self, credential: Credential) -> list[Balance]:
        return [
            *normalize_spot_balances(await self._fetch_spot_balances(credential)),
            *normalize_funding_balances(await self._fetch_funding_balances(credential)),
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

    # -- history --------------------------------------------------------------
    #
    # Binance shards history per symbol: my_trades / all_orders require one
    # symbol per query, so each active symbol is its own stream and the
    # cursor is an epoch-ms timestamp walked forward in 24h windows. Within a
    # window, a full page advances the cursor to the last row's time (the
    # overlap re-fetch is harmless — ingestion upserts by native id).

    def history_stream_keys(self, active_symbols: list[str]) -> list[str]:
        return list(active_symbols)

    async def _fetch_my_trades(
        self,
        credential: Credential,
        market_type: MarketType,
        native_symbol: str,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[dict]:
        rest = self._rest_for(market_type, credential)
        method = rest.my_trades if market_type == MarketType.SPOT else rest.account_trade_list
        resp = await self._call(
            method, symbol=native_symbol, start_time=start_ms, end_time=end_ms, limit=limit
        )
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data]

    async def _fetch_all_orders(
        self,
        credential: Credential,
        market_type: MarketType,
        native_symbol: str,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[dict]:
        rest = self._rest_for(market_type, credential)
        resp = await self._call(
            rest.all_orders, symbol=native_symbol, start_time=start_ms, end_time=end_ms,
            limit=limit,
        )
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data]

    def _history_window(self, cursor: str | None) -> tuple[int, int, int]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if cursor:
            start_ms = int(cursor)
        else:
            start_ms = now_ms - int(DEFAULT_HISTORY_BACKFILL.total_seconds() * 1000)
        return start_ms, min(start_ms + _HISTORY_WINDOW_MS, now_ms), now_ms

    async def fetch_trades(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        market_type, native_symbol = await self._resolve_symbol(stream_key)
        start_ms, end_ms, now_ms = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        limit = _TRADE_PAGE_LIMIT[market_type]
        rows = await self._fetch_my_trades(
            credential, market_type, native_symbol, start_ms, end_ms, limit
        )
        return Page(
            items=[normalize_trade(raw, stream_key) for raw in rows],
            next_cursor=_advance_window(rows, "time", start_ms, end_ms, now_ms, limit),
        )

    async def fetch_orders_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        market_type, native_symbol = await self._resolve_symbol(stream_key)
        start_ms, end_ms, now_ms = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        limit = _ORDER_PAGE_LIMIT[market_type]
        rows = await self._fetch_all_orders(
            credential, market_type, native_symbol, start_ms, end_ms, limit
        )
        return Page(
            items=[normalize_order(raw, stream_key) for raw in rows],
            next_cursor=_advance_window(rows, "time", start_ms, end_ms, now_ms, limit),
        )

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

    # -- transfers / withdrawals / deposits -----------------------------------
    #
    # Wallet↔wallet moves use the wallet universal-transfer endpoint;
    # main↔sub moves use the sub-account universal transfer, where an omitted
    # email means "the master" (the credential's own account). Both endpoints
    # answer synchronously with a tranId, so a returned id is a confirmed move.

    async def _submit_universal_transfer(
        self, credential: Credential, transfer_type: str, asset: str, amount: float
    ) -> dict:
        wallet = Wallet(config_rest_api=_auth_config(credential))
        resp = await self._call(
            wallet.rest_api.user_universal_transfer,
            type=transfer_type, asset=asset, amount=amount,
        )
        return dump_sdk_model(resp.data())

    async def _submit_sub_transfer(
        self,
        credential: Credential,
        asset: str,
        amount: float,
        from_wallet: str,
        to_wallet: str,
        from_email: str | None,
        to_email: str | None,
    ) -> dict:
        sub = SubAccount(config_rest_api=_auth_config(credential))
        kwargs: dict = {
            "from_account_type": from_wallet,
            "to_account_type": to_wallet,
            "asset": asset,
            "amount": amount,
        }
        if from_email:
            kwargs["from_email"] = from_email
        if to_email:
            kwargs["to_email"] = to_email
        resp = await self._call(sub.rest_api.universal_transfer, **kwargs)
        return dump_sdk_model(resp.data())

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
        if from_subaccount or to_subaccount:
            src = _SUB_TRANSFER_WALLET.get(from_wallet or WalletType.SPOT)
            dst = _SUB_TRANSFER_WALLET.get(to_wallet or WalletType.SPOT)
            if src is None or dst is None:
                raise ValueError(
                    "Binance main↔sub transfers support only SPOT and FUTURES wallets"
                )
            data = await self._submit_sub_transfer(
                credential, asset, float(amount), src, dst,
                from_email=from_subaccount, to_email=to_subaccount,
            )
        else:
            src_leg = _UNIVERSAL_TRANSFER_LEG[from_wallet or WalletType.SPOT]
            dst_leg = _UNIVERSAL_TRANSFER_LEG[to_wallet or WalletType.SPOT]
            if src_leg == dst_leg:
                raise ValueError("Transfer source and destination wallets are the same")
            data = await self._submit_universal_transfer(
                credential, f"{src_leg}_{dst_leg}", asset, float(amount)
            )
        tran_id = data.get("tranId") or data.get("tran_id") or ""
        return TransferResult(native_transfer_id=str(tran_id), status="confirmed")

    async def _fetch_all_coins(self, credential: Credential) -> list[dict]:
        wallet = Wallet(config_rest_api=_auth_config(credential))
        resp = await self._call(wallet.rest_api.all_coins_information)
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data]

    async def fetch_asset_networks(
        self, credential: Credential, asset: str
    ) -> list[AssetNetwork]:
        for row in await self._fetch_all_coins(credential):
            if str(row.get("coin", "")).upper() == asset.upper():
                return normalize_asset_networks(row)
        return []

    async def _fetch_deposit_address_raw(
        self, credential: Credential, asset: str, network: str, subaccount: str | None
    ) -> dict:
        if subaccount:
            sub = SubAccount(config_rest_api=_auth_config(credential))
            resp = await self._call(
                sub.rest_api.get_sub_account_deposit_address,
                email=subaccount, coin=asset, network=network,
            )
        else:
            wallet = Wallet(config_rest_api=_auth_config(credential))
            resp = await self._call(
                wallet.rest_api.deposit_address, coin=asset, network=network
            )
        return dump_sdk_model(resp.data())

    async def fetch_deposit_address(
        self,
        credential: Credential,
        asset: str,
        network: str,
        subaccount: str | None = None,
    ) -> DepositAddress:
        data = await self._fetch_deposit_address_raw(credential, asset, network, subaccount)
        address = data.get("address") or ""
        if not address:
            raise ValueError(f"Binance returned no {asset}/{network} deposit address")
        return DepositAddress(
            asset=asset, network=network.upper(), address=address,
            tag=data.get("tag") or None,
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
        wallet = Wallet(config_rest_api=_auth_config(credential))
        kwargs: dict = {
            "coin": asset,
            "address": address,
            "amount": float(amount),
            "network": network,
        }
        if tag:
            kwargs["address_tag"] = tag
        resp = await self._call(wallet.rest_api.withdraw, **kwargs)
        data = dump_sdk_model(resp.data())
        return Withdrawal(
            native_id=str(data.get("id", "")),
            asset=asset,
            amount=_dec(amount),
            status=WithdrawalStatus.PENDING,
            address=address,
            network=network.upper(),
        )

    async def _fetch_withdraw_history(
        self,
        credential: Credential,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 100,
    ) -> list[dict]:
        kwargs: dict = {"limit": limit}
        if start_ms is not None:
            kwargs["start_time"] = start_ms
        if end_ms is not None:
            kwargs["end_time"] = end_ms
        wallet = Wallet(config_rest_api=_auth_config(credential))
        resp = await self._call(wallet.rest_api.withdraw_history, **kwargs)
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data]

    async def fetch_withdrawal(
        self, credential: Credential, native_id: str
    ) -> Withdrawal | None:
        for raw in await self._fetch_withdraw_history(credential):
            if str(raw.get("id", "")) == native_id:
                return normalize_withdrawal(raw)
        return None

    async def _fetch_deposit_history(
        self,
        credential: Credential,
        asset: str | None,
        start_ms: int | None,
        subaccount: str | None,
        end_ms: int | None = None,
        limit: int = 100,
    ) -> list[dict]:
        kwargs: dict = {"limit": limit}
        if asset:
            kwargs["coin"] = asset
        if start_ms is not None:
            kwargs["start_time"] = start_ms
        if end_ms is not None:
            kwargs["end_time"] = end_ms
        if subaccount:
            sub = SubAccount(config_rest_api=_auth_config(credential))
            resp = await self._call(
                sub.rest_api.get_sub_account_deposit_history, email=subaccount, **kwargs
            )
        else:
            wallet = Wallet(config_rest_api=_auth_config(credential))
            resp = await self._call(wallet.rest_api.deposit_history, **kwargs)
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data]

    async def fetch_deposits(
        self,
        credential: Credential,
        asset: str | None = None,
        since: datetime | None = None,
        subaccount: str | None = None,
    ) -> list[Deposit]:
        start_ms = int(since.timestamp() * 1000) if since else None
        rows = await self._fetch_deposit_history(credential, asset, start_ms, subaccount)
        return [normalize_deposit(raw) for raw in rows]

    _FUND_HISTORY_LIMIT = 100

    @staticmethod
    def _withdrawal_time_ms(raw: dict) -> int:
        """Numeric ms for window advancement (applyTime is often a wall string)."""
        created = _binance_wall_time(raw.get("applyTime") or raw.get("completeTime"))
        if created is None:
            return 0
        return int(created.timestamp() * 1000)

    async def fetch_deposits_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        start_ms, end_ms, now_ms = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        limit = self._FUND_HISTORY_LIMIT
        rows = await self._fetch_deposit_history(
            credential, None, start_ms, None, end_ms=end_ms, limit=limit
        )
        return Page(
            items=[normalize_deposit(raw) for raw in rows],
            next_cursor=_advance_window(
                rows, "insertTime", start_ms, end_ms, now_ms, limit
            ),
        )

    async def fetch_withdrawals_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        start_ms, end_ms, now_ms = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        limit = self._FUND_HISTORY_LIMIT
        rows = await self._fetch_withdraw_history(
            credential, start_ms=start_ms, end_ms=end_ms, limit=limit
        )
        # applyTime is a string; inject a numeric key so window advance works.
        stamped = [{**raw, "_t": self._withdrawal_time_ms(raw)} for raw in rows]
        return Page(
            items=[normalize_withdrawal(raw) for raw in rows],
            next_cursor=_advance_window(stamped, "_t", start_ms, end_ms, now_ms, limit),
        )

    # -- funding fee history --------------------------------------------------

    # um = USD-M (fapi), cm = COIN-M (dapi). Income is account-wide per surface.
    _FUNDING_STREAMS: dict[str, MarketType] = {
        "um": MarketType.PERP,
        "cm": MarketType.CMARGIN,
    }

    def funding_history_stream_keys(self) -> list[str]:
        return list(self._FUNDING_STREAMS)

    async def _fetch_income_history(
        self,
        credential: Credential,
        stream_key: str,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[dict]:
        if stream_key == "um":
            rest = DerivativesTradingUsdsFutures(
                config_rest_api=_auth_config(credential)
            ).rest_api
        elif stream_key == "cm":
            rest = DerivativesTradingCoinFutures(
                config_rest_api=_auth_config(credential)
            ).rest_api
        else:
            raise ValueError(f"Unknown Binance funding stream {stream_key!r}")
        # Plain string — older SDKs expose an IncomeType enum, newer ones
        # take str; both accept the wire value.
        resp = await self._call(
            rest.get_income_history,
            income_type="FUNDING_FEE",
            start_time=start_ms,
            end_time=end_ms,
            limit=limit,
        )
        data = dump_sdk_model(resp.data())
        return data if isinstance(data, list) else [data] if data else []

    async def fetch_funding_fees_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        if stream_key not in self._FUNDING_STREAMS:
            raise ValueError(f"Unknown Binance funding stream {stream_key!r}")
        if not self._native_map:
            await self.fetch_instruments()
        start_ms, end_ms, now_ms = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        limit = self._FUND_HISTORY_LIMIT
        rows = await self._fetch_income_history(
            credential, stream_key, start_ms, end_ms, limit
        )
        market_type = self._FUNDING_STREAMS[stream_key]
        items: list[FundingFee] = []
        for raw in rows:
            universal = self._native_map.get((market_type, raw.get("symbol", "")))
            if universal is None:
                continue
            items.append(normalize_funding_fee(raw, universal, stream_key=stream_key))
        return Page(
            items=items,
            next_cursor=_advance_window(rows, "time", start_ms, end_ms, now_ms, limit),
        )

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
