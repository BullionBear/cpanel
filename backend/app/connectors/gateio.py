"""Gate.io connector.

Gate serves everything from one host (api.gateio.ws, APIv4, HMAC-SHA512)
but splits markets across path prefixes; perpetual futures are sharded by
settle currency:

    SPOT     /spot                 SpotApi
    PERP     /futures/usdt         FuturesApi(settle="usdt")   "direct"
    CMARGIN  /futures/btc          FuturesApi(settle="btc")    "inverse"

Delivery futures and options are separate APIs and stay out of scope.
Uses the official ``gate-api`` SDK (synchronous, run in a worker thread).

Design decisions and venue quirks:

- **Wallets**: ``SPOT`` and ``FUTURES``. Gate has no separate funding
  wallet — deposits credit the spot account and withdrawals draw from it.
  Both perpetual accounts (usdt- and btc-settled) report under ``FUTURES``;
  a transfer's futures leg infers its settle currency from the asset
  (USDT → the usdt account, BTC → the btc account).
- **Client order ids** ride the ``text`` field and must be prefixed
  ``t-`` on the wire; the prefix is stripped when normalizing back.
- **Spot market buys** are quote-denominated on Gate (no base-qty option).
  The panel therefore sizes market buys in the quote asset; ``place_order``
  passes ``qty`` through as ``amount``, and ``normalize_spot_order`` keeps
  qty/filled in quote (``amount`` / ``filled_total``).
- **Futures order size** is a signed contract count (positive buy,
  negative sell); market orders are ``price="0"`` with ``tif=ioc``.
  One-way position mode is assumed — ``position_side`` is ignored.
- **History** is account-wide per surface, so the streams are ``spot`` /
  ``usdt`` / ``btc``. Endpoints are time-windowed (from/to in *seconds*)
  with page (spot) or offset (futures) pagination, so the cursor is a JSON
  pair ``{"start": epoch_ms, "skip": rows_consumed_in_window}``; the
  window slides forward 24h once its pages are exhausted.
- **Provisioning** is full self-service. Subaccount ``login_name`` values
  are globally unique on Gate and restricted to ``[A-Za-z0-9_]``, so
  ``create_subaccount`` derives a suffixed username from the panel label
  and stores the original label in ``remark``. The SDK's ``SubAccountKey``
  model drops the one-time ``secret`` returned on key creation, so
  ``create_api_key`` reads the raw HTTP response instead. Created keys
  always get the ``account`` read permission on top of the requested
  scopes so ``verify_credential`` works on them.
- **Sub-account keys cannot withdraw or run main↔sub transfers** (venue
  rule — master key only). Gate also has no master-for-sub deposit
  address/history endpoints, so deposit-side calls with ``subaccount``
  set are rejected: register the sub's own key to receive deposits.
- **Credential verification** prefers ``/account/detail`` but falls back
  to the wallet-scope trade-fee endpoint (which also returns the user
  id) when the key lacks the "account" permission — keys created in
  Gate's UI often omit that scope.
"""
import asyncio
import json
import logging
import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal

import gate_api
from gate_api.exceptions import GateApiException

from app.connectors.base import (
    DEFAULT_HISTORY_BACKFILL,
    Capability,
    Credential,
    ExchangeConnector,
)
from app.connectors.errors import credential_error_reason
from app.connectors.symbols import InvalidUniversalSymbol, build_universal_symbol
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

# Futures settle currencies: usdt-margined perps -> PERP, btc-settled
# inverse perps -> CMARGIN.
_SETTLES: list[tuple[str, MarketType]] = [
    ("usdt", MarketType.PERP),
    ("btc", MarketType.CMARGIN),
]
_SETTLE_OF = {market_type: settle for settle, market_type in _SETTLES}

# Normalized time-in-force -> Gate value (poc = "pending or cancelled",
# Gate's post-only).
_TIF = {
    TimeInForce.GTC: "gtc",
    TimeInForce.IOC: "ioc",
    TimeInForce.FOK: "fok",
    TimeInForce.POST_ONLY: "poc",
}

# Normalized wallet -> Gate transfer account name.
_WALLET_ACCOUNT = {
    WalletType.SPOT: "spot",
    WalletType.FUTURES: "futures",
}

# Panel scope tokens -> Gate API key permission names.
_PERMISSION_SCOPES = {
    "spot": "spot",
    "derivatives": "futures",
    "wallet": "wallet",
    "options": "options",
    "copytrading": "copy",
    "exchange": "account",
}

# Withdrawal record status -> normalized lifecycle. EXTPEND = broadcast
# on-chain waiting for confirmations.
_WITHDRAWAL_STATUS = {
    "DONE": WithdrawalStatus.COMPLETED,
    "EXTPEND": WithdrawalStatus.SENT,
    "CANCEL": WithdrawalStatus.FAILED,
    "CANCELPEND": WithdrawalStatus.FAILED,
    "REJECT": WithdrawalStatus.FAILED,
    "INVALID": WithdrawalStatus.FAILED,
    "FAIL": WithdrawalStatus.FAILED,
    # BCODE / REQUEST / MANUAL / VERIFY / PROCES / PEND / DMOVE / SPLITPEND /
    # REVIEW / LOCKED / FVERIFY and anything new -> PENDING (the default).
}

# Deposit record status -> normalized lifecycle. DEP_CREDITED is credited
# with withdrawals still locked — credited for our purposes.
_DEPOSIT_STATUS = {
    "DONE": DepositStatus.CREDITED,
    "DEP_CREDITED": DepositStatus.CREDITED,
    "INVALID": DepositStatus.FAILED,
    "BLOCKED": DepositStatus.FAILED,
    "CANCEL": DepositStatus.FAILED,
    "FAIL": DepositStatus.FAILED,
    # MANUAL / PEND / REVIEW / TRACK / ... -> PENDING (the default).
}

# GateApiException labels that mean "this account/surface does not exist
# yet" (e.g. futures never opened) rather than a real failure.
_EMPTY_ACCOUNT_LABELS = {"USER_NOT_FOUND", "CONTRACT_NOT_FOUND", "POSITION_NOT_FOUND"}

# Gate login_name: letters, digits, underscore only; globally unique across
# the venue, so panel labels like "prom" collide unless we suffix them.
_LOGIN_NAME_RE = re.compile(r"[^A-Za-z0-9_]+")
_LOGIN_NAME_MAX = 28  # leave room for "_" + 6 hex chars


def _gate_login_name(label: str) -> str:
    base = _LOGIN_NAME_RE.sub("_", label).strip("_") or "sub"
    base = base[:_LOGIN_NAME_MAX]
    return f"{base}_{secrets.token_hex(3)}"


def _configuration(key: str | None = None, secret: str | None = None) -> gate_api.Configuration:
    config = gate_api.Configuration(key=key, secret=secret)
    # The generated validator rejects omitted "required" params that the venue
    # itself treats as optional (e.g. currency_pair on finished-order queries).
    config.client_side_validation = False
    return config


def dump_gate_model(model):
    """Recursively dump a gate-api model to a plain dict in API JSON shape.

    ``to_dict()`` keeps python attribute names (``_from``); normalization
    and tests work on the documented REST shape instead, which
    ``attribute_map`` provides.
    """
    if isinstance(model, list):
        return [dump_gate_model(m) for m in model]
    if hasattr(model, "attribute_map"):
        return {
            api_name: dump_gate_model(getattr(model, attr))
            for attr, api_name in model.attribute_map.items()
            if getattr(model, attr) is not None
        }
    if isinstance(model, dict):
        return {k: dump_gate_model(v) for k, v in model.items()}
    return model


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _opt_dec(value) -> Decimal | None:
    return _dec(value) if value not in (None, "") else None


def _epoch_ms(value) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        # Gate ms timestamps can carry a fractional part ("1548000000123.456").
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def _epoch_s(value) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def _to_text(client_order_id: str) -> str:
    """Panel client order id -> Gate ``text`` (must be ``t-`` prefixed)."""
    return f"t-{client_order_id}"


def _from_text(text) -> str | None:
    """Gate ``text`` -> panel client order id (None for venue-made values)."""
    if isinstance(text, str) and text.startswith("t-"):
        return text[2:] or None
    return None


def _pair_assets(name: str) -> tuple[str, str]:
    """A futures contract name like ``BTC_USDT`` -> (base, quote)."""
    base, _, quote = name.partition("_")
    return base, quote


# -- instruments ----------------------------------------------------------


def normalize_spot_instrument(info: dict) -> Instrument | None:
    """A /spot/currency_pairs row → Instrument (premarket pairs skipped)."""
    if info.get("type") == "premarket":
        return None
    base, quote = info.get("base", ""), info.get("quote", "")
    price_places = info.get("precision")
    qty_places = info.get("amount_precision")
    if not base or not quote or price_places is None or qty_places is None:
        return None
    try:
        universal = build_universal_symbol("GATEIO", MarketType.SPOT, base, quote)
    except InvalidUniversalSymbol:
        return None
    min_qty = info.get("min_base_amount")
    max_qty = info.get("max_base_amount")
    min_notional = info.get("min_quote_amount")
    return Instrument(
        universal_symbol=universal,
        exchange="GATEIO",
        market_type=MarketType.SPOT,
        native_symbol=info["id"],
        base_asset=base,
        quote_asset=quote,
        filters=InstrumentFilters(
            price_tick=Decimal(1).scaleb(-int(price_places)),
            qty_step=Decimal(1).scaleb(-int(qty_places)),
            min_qty=_dec(min_qty) if min_qty else Decimal("0"),
            max_qty=_dec(max_qty) if max_qty else None,
            min_notional=_dec(min_notional) if min_notional else None,
        ),
        status="active" if info.get("trade_status") == "tradable" else "halted",
    )


def _contract_status(info: dict) -> str:
    status = info.get("status") or ("trading" if not info.get("in_delisting") else "delisting")
    if status == "trading" and not info.get("in_delisting"):
        return "active"
    if status in ("delisted",):
        return "delisted"
    return "halted"


def normalize_contract(info: dict, market_type: MarketType, settle: str) -> Instrument | None:
    """A /futures/{settle}/contracts row → Instrument.

    Futures sizes are integer contract counts; ``quanto_multiplier`` is the
    contract size (base asset for direct contracts, quote for inverse).
    """
    name = info.get("name", "")
    tick = info.get("order_price_round")
    if not name or "_" not in name or tick is None:
        return None
    base, quote = _pair_assets(name)
    try:
        universal = build_universal_symbol("GATEIO", market_type, base, quote)
    except InvalidUniversalSymbol:
        return None
    multiplier = _opt_dec(info.get("quanto_multiplier"))
    max_size = info.get("order_size_max")
    return Instrument(
        universal_symbol=universal,
        exchange="GATEIO",
        market_type=market_type,
        native_symbol=name,
        base_asset=base,
        quote_asset=quote,
        filters=InstrumentFilters(
            price_tick=_dec(tick),
            qty_step=Decimal("1"),
            min_qty=_dec(info.get("order_size_min", 1) or 1),
            max_qty=_dec(max_size) if max_size else None,
            min_notional=None,
        ),
        status=_contract_status(info),
        contract_size=multiplier if multiplier and multiplier > 0 else None,
        settle_asset=settle.upper(),
    )


# -- balances / positions --------------------------------------------------


def normalize_spot_balances(rows: list[dict]) -> list[Balance]:
    """/spot/accounts rows → normalized SPOT balances (zeros dropped)."""
    balances: list[Balance] = []
    for row in rows:
        free = _dec(row.get("available", 0) or 0)
        locked = _dec(row.get("locked", 0) or 0)
        if free == 0 and locked == 0:
            continue
        balances.append(
            Balance(wallet=WalletType.SPOT, asset=row["currency"], free=free, locked=locked)
        )
    return balances


def normalize_futures_balance(account: dict) -> list[Balance]:
    """One /futures/{settle}/accounts object → a FUTURES balance.

    ``total`` is the account balance; ``available`` is what is free of
    position/order margin, so the difference counts as locked.
    """
    if not account:
        return []
    total = _dec(account.get("total", 0) or 0)
    available = _dec(account.get("available", 0) or 0)
    if total == 0 and available == 0:
        return []
    locked = total - available
    if locked < 0:
        locked = Decimal("0")
    return [
        Balance(
            wallet=WalletType.FUTURES,
            asset=account.get("currency") or "",
            free=total - locked,
            locked=locked,
        )
    ]


def normalize_positions(
    rows: list[dict],
    market_type: MarketType,
    native_map: dict[tuple[MarketType, str], str],
) -> list[Position]:
    """Position rows → normalized non-zero positions.

    ``size`` is a signed contract count (negative = short). Contracts the
    panel does not track are skipped.
    """
    positions: list[Position] = []
    for row in rows:
        size = _dec(row.get("size", 0) or 0)
        if size == 0:
            continue
        universal = native_map.get((market_type, row.get("contract", "")))
        if universal is None:
            continue
        positions.append(
            Position(
                universal_symbol=universal,
                side="long" if size > 0 else "short",
                size=abs(size),
                entry_price=_dec(row.get("entry_price", 0) or 0),
                mark_price=_opt_dec(row.get("mark_price")),
                unrealized_pnl=_opt_dec(row.get("unrealised_pnl")),
                margin=_opt_dec(row.get("margin")),
            )
        )
    return positions


# -- orders / trades -------------------------------------------------------


def normalize_spot_order(raw: dict, universal_symbol: str) -> Order:
    """A Gate spot order dict → normalized Order.

    Market buys are quote-denominated on Gate, so qty/filled stay in quote
    (``amount`` / ``filled_total``); everything else is base-denominated.
    """
    is_market = raw.get("type") == "market"
    is_buy = raw.get("side") == "buy"
    qty = _dec(raw.get("amount", 0) or 0)
    if is_market and is_buy:
        filled = _dec(raw.get("filled_total", 0) or 0)
        left = _opt_dec(raw.get("left"))
        if filled == 0 and left is not None and qty > 0:
            filled = qty - left
    else:
        filled = _dec(raw.get("filled_amount", raw.get("filled_total", 0)) or 0)
        left = _opt_dec(raw.get("left"))
        if left is not None and qty > 0:
            filled = qty - left

    status_raw = raw.get("status")
    if status_raw == "open":
        status = OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.OPEN
    elif status_raw == "closed":
        status = OrderStatus.FILLED
    else:
        status = OrderStatus.CANCELLED

    price = _opt_dec(raw.get("price"))
    return Order(
        universal_symbol=universal_symbol,
        native_order_id=str(raw.get("id", "")),
        client_order_id=_from_text(raw.get("text")),
        side=OrderSide.BUY if is_buy else OrderSide.SELL,
        type=OrderType.MARKET if is_market else OrderType.LIMIT,
        status=status,
        qty=qty,
        filled_qty=filled,
        price=price if price and price > 0 else None,
        created_at=_epoch_ms(raw.get("create_time_ms")) or _epoch_s(raw.get("create_time")),
        updated_at=_epoch_ms(raw.get("update_time_ms")) or _epoch_s(raw.get("update_time")),
    )


def normalize_futures_order(raw: dict, universal_symbol: str) -> Order:
    """A Gate futures order dict → normalized Order.

    ``size`` is signed (negative = sell) in contracts; ``left`` is the
    unfilled remainder with the same sign. ``status`` is only open/finished;
    ``finish_as`` says how a finished order ended.
    """
    size = _dec(raw.get("size", 0) or 0)
    left = _dec(raw.get("left", 0) or 0)
    qty = abs(size)
    filled = qty - abs(left)
    if filled < 0:
        filled = Decimal("0")

    if raw.get("status") == "open":
        status = OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.OPEN
    elif raw.get("finish_as") == "filled":
        status = OrderStatus.FILLED
    else:
        status = OrderStatus.CANCELLED

    price = _opt_dec(raw.get("price"))
    return Order(
        universal_symbol=universal_symbol,
        native_order_id=str(raw.get("id", "")),
        client_order_id=_from_text(raw.get("text")),
        side=OrderSide.BUY if size > 0 else OrderSide.SELL,
        type=OrderType.MARKET if price is not None and price == 0 else OrderType.LIMIT,
        status=status,
        qty=qty,
        filled_qty=filled,
        price=price if price and price > 0 else None,
        created_at=_epoch_s(raw.get("create_time")),
        updated_at=_epoch_s(raw.get("finish_time") or raw.get("update_time")),
    )


def normalize_spot_trade(raw: dict, universal_symbol: str) -> Trade:
    """A /spot/my_trades row → normalized Trade."""
    return Trade(
        universal_symbol=universal_symbol,
        native_trade_id=str(raw.get("id", "")),
        native_order_id=str(raw.get("order_id", "")),
        side=OrderSide.BUY if raw.get("side") == "buy" else OrderSide.SELL,
        price=_dec(raw.get("price", 0) or 0),
        qty=_dec(raw.get("amount", 0) or 0),
        fee=_dec(raw.get("fee", 0) or 0),
        fee_asset=raw.get("fee_currency") or "",
        traded_at=_epoch_ms(raw.get("create_time_ms")) or _epoch_s(raw.get("create_time")),
    )


def normalize_futures_trade(raw: dict, universal_symbol: str, settle: str) -> Trade:
    """A futures my-trades row → normalized Trade (fee settles in ``settle``)."""
    size = _dec(raw.get("size", 0) or 0)
    return Trade(
        universal_symbol=universal_symbol,
        native_trade_id=str(raw.get("trade_id") or raw.get("id") or ""),
        native_order_id=str(raw.get("order_id", "")),
        side=OrderSide.BUY if size > 0 else OrderSide.SELL,
        price=_dec(raw.get("price", 0) or 0),
        qty=abs(size),
        fee=_dec(raw.get("fee", 0) or 0),
        fee_asset=settle.upper(),
        traded_at=_epoch_s(raw.get("create_time")),
    )


# -- transfers / withdrawals / deposits -------------------------------------


def normalize_asset_networks(chains: list[dict], withdraw_status: dict) -> list[AssetNetwork]:
    """/wallet/currency_chains rows + /wallet/withdraw_status row → chains.

    The chains endpoint carries enablement; minimum and per-chain fixed fee
    come from the withdraw-status endpoint.
    """
    min_amount = _dec(withdraw_status.get("withdraw_amount_mini", 0) or 0)
    fee_on_chains = withdraw_status.get("withdraw_fix_on_chains") or {}
    default_fee = _dec(withdraw_status.get("withdraw_fix", 0) or 0)
    networks: list[AssetNetwork] = []
    for chain in chains:
        name = str(chain.get("chain", ""))
        disabled = bool(int(chain.get("is_disabled", 0) or 0))
        deposit_disabled = bool(int(chain.get("is_deposit_disabled", 0) or 0))
        withdraw_disabled = bool(int(chain.get("is_withdraw_disabled", 0) or 0))
        fee = fee_on_chains.get(name)
        networks.append(
            AssetNetwork(
                network=name.upper(),
                deposit_enabled=not disabled and not deposit_disabled,
                withdraw_enabled=not disabled and not withdraw_disabled,
                withdraw_min=min_amount,
                withdraw_fee=_dec(fee) if fee not in (None, "") else default_fee,
            )
        )
    return networks


def normalize_withdrawal_record(raw: dict) -> Withdrawal:
    """A /wallet/withdrawals row → normalized Withdrawal."""
    status = _WITHDRAWAL_STATUS.get(str(raw.get("status") or "").upper(), WithdrawalStatus.PENDING)
    tx_id = raw.get("txid") or None
    if status == WithdrawalStatus.PENDING and tx_id:
        status = WithdrawalStatus.SENT
    created_at = _epoch_s(raw.get("timestamp"))
    return Withdrawal(
        native_id=str(raw.get("id", "")),
        asset=raw.get("currency") or "",
        amount=_dec(raw.get("amount", 0) or 0),
        status=status,
        address=raw.get("address") or None,
        network=(raw.get("chain") or None) and str(raw["chain"]).upper(),
        tx_id=tx_id,
        fee=_opt_dec(raw.get("fee")),
        created_at=created_at,
    )


def normalize_funding_fee(
    raw: dict, universal_symbol: str, *, settle: str
) -> FundingFee:
    """A futures account_book row (type=fund) → FundingFee.

    Gate's ``change`` is the signed settlement amount in the settle currency.
    ``id`` is unique per settle account, so prefix with the settle stream.
    """
    return FundingFee(
        universal_symbol=universal_symbol,
        native_id=f"{settle}:{raw.get('id') or ''}",
        asset=settle.upper(),
        amount=_dec(raw.get("change", 0) or 0),
        funding_rate=None,
        settled_at=_epoch_s(raw.get("time")),
    )


def normalize_deposit_record(raw: dict) -> Deposit:
    """A /wallet/deposits row → normalized Deposit."""
    status = _DEPOSIT_STATUS.get(str(raw.get("status") or "").upper(), DepositStatus.PENDING)
    created_at = _epoch_s(raw.get("timestamp"))
    return Deposit(
        native_id=str(raw.get("id") or raw.get("txid") or ""),
        asset=raw.get("currency") or "",
        amount=_dec(raw.get("amount", 0) or 0),
        status=status,
        network=(raw.get("chain") or None) and str(raw["chain"]).upper(),
        tx_id=raw.get("txid") or None,
        credited_at=created_at if status == DepositStatus.CREDITED else None,
        created_at=created_at,
    )


# History endpoints window by time (from/to in seconds) and paginate within
# the window: spot by page number, futures by row offset. 24h windows keep
# every surface inside its 30-day range limit.
_HISTORY_WINDOW_MS = 24 * 60 * 60 * 1000
_TRADE_PAGE_LIMIT = 200
_ORDER_PAGE_LIMIT = 100


class GateIOConnector(ExchangeConnector):
    exchange = "GATEIO"
    # Gate exposes full self-service account management over API (HMAC-SHA512
    # keys only). Sub keys are minted/updated/deleted via the master key.
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
            Capability.FUNDING_HISTORY,
        }
    )
    # Gate's classic account: the spot account doubles as the funding wallet
    # (deposits/withdrawals), FUTURES covers the perpetual accounts.
    wallets = (WalletType.SPOT, WalletType.FUTURES)

    def __init__(self, bucket: TokenBucket | None = None):
        self._bucket = bucket
        self._public_client = gate_api.ApiClient(_configuration())
        self._native_map: dict[tuple[MarketType, str], str] = {}
        self._universal_map: dict[str, tuple[MarketType, str]] = {}

    async def _call(self, func, *args, **kwargs):
        if self._bucket is not None:
            await self._bucket.acquire(priority=Priority.SYNC)
        return await asyncio.to_thread(func, *args, **kwargs)

    @staticmethod
    def _auth_client(credential: Credential) -> gate_api.ApiClient:
        return gate_api.ApiClient(
            _configuration(key=credential["api_key"], secret=credential["secret"])
        )

    def _spot(self, credential: Credential | None = None) -> gate_api.SpotApi:
        return gate_api.SpotApi(
            self._auth_client(credential) if credential else self._public_client
        )

    def _futures(self, credential: Credential | None = None) -> gate_api.FuturesApi:
        return gate_api.FuturesApi(
            self._auth_client(credential) if credential else self._public_client
        )

    def _wallet(self, credential: Credential) -> gate_api.WalletApi:
        return gate_api.WalletApi(self._auth_client(credential))

    # -- raw fetches (overridden by tests with canned payloads) --------------

    async def _fetch_spot_pairs(self) -> list[dict]:
        return dump_gate_model(await self._call(self._spot().list_currency_pairs))

    async def _fetch_contracts(self, settle: str) -> list[dict]:
        return dump_gate_model(
            await self._call(self._futures().list_futures_contracts, settle)
        )

    async def _fetch_spot_tickers(self) -> list[dict]:
        return dump_gate_model(await self._call(self._spot().list_tickers))

    async def _fetch_futures_tickers(self, settle: str) -> list[dict]:
        return dump_gate_model(
            await self._call(self._futures().list_futures_tickers, settle)
        )

    # -- market data ----------------------------------------------------------

    async def fetch_instruments(self) -> list[Instrument]:
        instruments: list[Instrument] = []
        for info in await self._fetch_spot_pairs():
            instrument = normalize_spot_instrument(info)
            if instrument is not None:
                instruments.append(instrument)
        for settle, market_type in _SETTLES:
            for info in await self._fetch_contracts(settle):
                instrument = normalize_contract(info, market_type, settle)
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
        for row in await self._fetch_spot_tickers():
            universal = self._native_map.get((MarketType.SPOT, row.get("currency_pair", "")))
            if universal is not None and row.get("last"):
                prices[universal] = _dec(row["last"])
        for settle, market_type in _SETTLES:
            for row in await self._fetch_futures_tickers(settle):
                universal = self._native_map.get((market_type, row.get("contract", "")))
                if universal is not None and row.get("last"):
                    prices[universal] = _dec(row["last"])
        return prices

    # -- credentials ----------------------------------------------------------

    async def _fetch_account_detail(self, credential: Credential) -> dict:
        api = gate_api.AccountApi(self._auth_client(credential))
        return dump_gate_model(await self._call(api.get_account_detail))

    async def _fetch_own_key_info(self, credential: Credential) -> dict | None:
        """Current key's permission info — main-account keys only.

        The venue returns a *list* of the main account's keys, but the SDK
        deserializes into a single ``AccountKeyInfo`` and silently yields an
        all-``None`` model, so read the raw JSON and pick our own entry."""
        api = gate_api.AccountApi(self._auth_client(credential))
        try:
            response = await self._call(api.get_account_main_keys, _preload_content=False)
        except Exception:  # noqa: BLE001 - sub keys lack this endpoint
            return None
        rows = json.loads(response.data)
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            if row.get("key") == credential["api_key"]:
                return row
        return None

    async def _fetch_trade_fee(self, credential: Credential) -> dict:
        """Wallet-scope call that also returns the user id.

        Fallback identity check for keys created without the "account"
        permission scope (which /account/detail requires)."""
        return dump_gate_model(await self._call(self._wallet(credential).get_trade_fee))

    async def verify_credential(self, credential: Credential) -> CredentialInfo:
        try:
            detail = await self._fetch_account_detail(credential)
        except GateApiException as exc:
            if exc.label != "FORBIDDEN":
                reason = credential_error_reason(self.exchange, exc)
                logger.warning("Gate.io credential verification failed: %s", reason)
                return CredentialInfo(valid=False, reason=reason)
            # The key may simply lack the "account" scope; a wallet-scope call
            # still proves the signature and yields the user id.
            try:
                detail = await self._fetch_trade_fee(credential)
            except Exception as fallback_exc:  # noqa: BLE001
                reason = credential_error_reason(self.exchange, fallback_exc)
                logger.warning("Gate.io credential verification failed: %s", reason)
                return CredentialInfo(valid=False, reason=reason)
        except Exception as exc:
            reason = credential_error_reason(self.exchange, exc)
            logger.warning("Gate.io credential verification failed: %s", reason, exc_info=True)
            return CredentialInfo(valid=False, reason=reason)

        read_only = can_trade = can_withdraw = is_master = None
        permissions = None
        ips = None
        key_info = await self._fetch_own_key_info(credential)
        if key_info is not None:
            is_master = True  # /account/main_keys answers only for main keys
            perms = key_info.get("perms") or []
            writable = {p["name"] for p in perms if not p.get("read_only")}
            permissions = {
                p["name"]: "read" if p.get("read_only") else "read-write" for p in perms
            }
            read_only = not writable
            can_trade = bool(writable & {"spot", "futures", "delivery", "options", "unified"})
            # "withdraw" is its own scope on Gate; "wallet" only covers transfers.
            can_withdraw = "withdraw" in writable
            # Always surface the field (empty = unrestricted) so the panel can
            # tell "unknown" apart from "no allowlist".
            ips = [str(ip) for ip in (key_info.get("ip_whitelist") or [])]
        return CredentialInfo(
            valid=True,
            native_uid=str(detail.get("user_id", "")) or None,
            is_master=is_master,
            read_only=read_only,
            can_trade=can_trade,
            can_withdraw=can_withdraw,
            permissions=permissions,
            ips=ips,
        )

    # -- account state ----------------------------------------------------------

    async def _fetch_spot_balances(self, credential: Credential) -> list[dict]:
        return dump_gate_model(await self._call(self._spot(credential).list_spot_accounts))

    async def _fetch_futures_account(self, credential: Credential, settle: str) -> dict:
        try:
            return dump_gate_model(
                await self._call(self._futures(credential).list_futures_accounts, settle)
            )
        except GateApiException as exc:
            if exc.label in _EMPTY_ACCOUNT_LABELS:
                return {}
            raise

    async def _fetch_positions(self, credential: Credential, settle: str) -> list[dict]:
        try:
            return dump_gate_model(
                await self._call(self._futures(credential).list_positions, settle)
            )
        except GateApiException as exc:
            if exc.label in _EMPTY_ACCOUNT_LABELS:
                return []
            raise

    async def fetch_balances(self, credential: Credential) -> list[Balance]:
        balances = normalize_spot_balances(await self._fetch_spot_balances(credential))
        for settle, _ in _SETTLES:
            balances.extend(
                normalize_futures_balance(
                    await self._fetch_futures_account(credential, settle)
                )
            )
        return balances

    async def fetch_positions(self, credential: Credential) -> list[Position]:
        if not self._native_map:
            await self.fetch_instruments()
        positions: list[Position] = []
        for settle, market_type in _SETTLES:
            positions.extend(
                normalize_positions(
                    await self._fetch_positions(credential, settle),
                    market_type,
                    self._native_map,
                )
            )
        return positions

    # -- orders ---------------------------------------------------------------

    async def _resolve_symbol(self, universal_symbol: str) -> tuple[MarketType, str]:
        if not self._universal_map:
            await self.fetch_instruments()
        pair = self._universal_map.get(universal_symbol)
        if pair is None:
            raise ValueError(f"Unknown instrument {universal_symbol!r} for Gate.io")
        return pair

    async def place_order(self, credential: Credential, request: OrderRequest) -> Order:
        market_type, native_symbol = await self._resolve_symbol(request.universal_symbol)
        if market_type == MarketType.SPOT:
            return await self._place_spot_order(credential, request, native_symbol)
        return await self._place_futures_order(
            credential, request, _SETTLE_OF[market_type], native_symbol
        )

    async def _place_spot_order(
        self, credential: Credential, request: OrderRequest, native_symbol: str
    ) -> Order:
        is_market = request.type == OrderType.MARKET
        # Market buys are quote-denominated on Gate; the panel sends quote qty.
        amount = str(request.qty)
        if is_market:
            tif = _TIF.get(request.time_in_force or TimeInForce.IOC, "ioc")
            if tif not in ("ioc", "fok"):
                tif = "ioc"  # market orders accept only ioc/fok
        else:
            tif = _TIF.get(request.time_in_force or TimeInForce.GTC, "gtc")
        order = gate_api.Order(
            currency_pair=native_symbol,
            side="buy" if request.side == OrderSide.BUY else "sell",
            type="market" if is_market else "limit",
            account="spot",
            amount=amount,
            time_in_force=tif,
        )
        if not is_market and request.price is not None:
            order.price = str(request.price)
        if request.client_order_id:
            order.text = _to_text(request.client_order_id)
        raw = dump_gate_model(
            await self._call(self._spot(credential).create_order, order)
        )
        return normalize_spot_order(raw, request.universal_symbol)

    async def _place_futures_order(
        self, credential: Credential, request: OrderRequest, settle: str, native_symbol: str
    ) -> Order:
        size = int(request.qty)
        if size <= 0 or Decimal(size) != request.qty:
            raise ValueError("Gate.io futures quantity is a whole number of contracts")
        if request.side == OrderSide.SELL:
            size = -size
        is_market = request.type == OrderType.MARKET
        if is_market:
            tif = "ioc" if (request.time_in_force or TimeInForce.IOC) != TimeInForce.FOK else "fok"
        else:
            tif = _TIF.get(request.time_in_force or TimeInForce.GTC, "gtc")
        order = gate_api.FuturesOrder(
            contract=native_symbol,
            size=size,
            price="0" if is_market else str(request.price),
            tif=tif,
            reduce_only=request.reduce_only,
        )
        if request.client_order_id:
            order.text = _to_text(request.client_order_id)
        raw = dump_gate_model(
            await self._call(self._futures(credential).create_futures_order, settle, order)
        )
        return normalize_futures_order(raw, request.universal_symbol)

    async def cancel_order(
        self, credential: Credential, universal_symbol: str, native_order_id: str
    ) -> Order:
        market_type, native_symbol = await self._resolve_symbol(universal_symbol)
        if market_type == MarketType.SPOT:
            raw = dump_gate_model(
                await self._call(
                    self._spot(credential).cancel_order, native_order_id, native_symbol
                )
            )
            return normalize_spot_order(raw, universal_symbol)
        raw = dump_gate_model(
            await self._call(
                self._futures(credential).cancel_futures_order,
                _SETTLE_OF[market_type],
                native_order_id,
            )
        )
        return normalize_futures_order(raw, universal_symbol)

    async def _fetch_spot_open_orders(self, credential: Credential) -> list[dict]:
        groups = dump_gate_model(
            await self._call(self._spot(credential).list_all_open_orders)
        )
        return [order for group in groups for order in group.get("orders", [])]

    async def _fetch_futures_open_orders(
        self, credential: Credential, settle: str
    ) -> list[dict]:
        try:
            return dump_gate_model(
                await self._call(
                    self._futures(credential).list_futures_orders, settle, "open"
                )
            )
        except GateApiException as exc:
            if exc.label in _EMPTY_ACCOUNT_LABELS:
                return []
            raise

    async def fetch_open_orders(
        self, credential: Credential, universal_symbol: str | None = None
    ) -> list[Order]:
        if not self._native_map:
            await self.fetch_instruments()
        orders: list[Order] = []
        for raw in await self._fetch_spot_open_orders(credential):
            universal = self._native_map.get((MarketType.SPOT, raw.get("currency_pair", "")))
            if universal is None or (universal_symbol and universal != universal_symbol):
                continue
            orders.append(normalize_spot_order(raw, universal))
        for settle, market_type in _SETTLES:
            for raw in await self._fetch_futures_open_orders(credential, settle):
                universal = self._native_map.get((market_type, raw.get("contract", "")))
                if universal is None or (universal_symbol and universal != universal_symbol):
                    continue
                orders.append(normalize_futures_order(raw, universal))
        return orders

    async def fetch_order(
        self,
        credential: Credential,
        universal_symbol: str,
        native_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> Order | None:
        market_type, native_symbol = await self._resolve_symbol(universal_symbol)
        if native_order_id is not None:
            order_id = native_order_id
        elif client_order_id is not None:
            # Gate resolves recent orders by their custom text directly.
            order_id = _to_text(client_order_id)
        else:
            return None
        try:
            if market_type == MarketType.SPOT:
                raw = dump_gate_model(
                    await self._call(
                        self._spot(credential).get_order, order_id, native_symbol
                    )
                )
                return normalize_spot_order(raw, universal_symbol) if raw else None
            raw = dump_gate_model(
                await self._call(
                    self._futures(credential).get_futures_order,
                    _SETTLE_OF[market_type],
                    order_id,
                )
            )
            return normalize_futures_order(raw, universal_symbol) if raw else None
        except Exception:  # noqa: BLE001 - unknown id → treated as "not found"
            return None

    # -- history ----------------------------------------------------------------
    #
    # Gate serves account-wide history per surface, so the streams are the
    # settle prefixes: "spot", "usdt", "btc". Endpoints combine a time window
    # (seconds) with page (spot) / offset (futures) pagination; the cursor is
    # a JSON pair of the window start (epoch ms) and the rows already consumed
    # in that window. One venue call per fetch; the window slides forward 24h
    # once its pages are exhausted, until it reaches the present.

    def history_stream_keys(self, active_symbols: list[str]) -> list[str]:
        return ["spot"] + [settle for settle, _ in _SETTLES]

    def initial_history_cursor(self, start: datetime) -> str | None:
        return json.dumps({"start": int(start.timestamp() * 1000)})

    def _history_window(self, cursor: str | None) -> tuple[int, int, int, int]:
        """(window start ms, window end ms, now ms, rows already consumed)."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        skip = 0
        if cursor:
            decoded = json.loads(cursor)
            start_ms = int(decoded["start"])
            skip = int(decoded.get("skip", 0))
        else:
            start_ms = now_ms - int(DEFAULT_HISTORY_BACKFILL.total_seconds() * 1000)
        return start_ms, min(start_ms + _HISTORY_WINDOW_MS, now_ms), now_ms, skip

    @staticmethod
    def _next_history_cursor(
        start_ms: int, end_ms: int, now_ms: int, skip: int, rows: int, limit: int
    ) -> str | None:
        if rows >= limit:
            return json.dumps({"start": start_ms, "skip": skip + rows})
        if end_ms < now_ms:
            return json.dumps({"start": end_ms})
        return None

    async def _fetch_spot_trades_page(
        self, credential: Credential, start_s: int, end_s: int, limit: int, page: int
    ) -> list[dict]:
        return dump_gate_model(
            await self._call(
                self._spot(credential).list_my_trades,
                _from=start_s, to=end_s, limit=limit, page=page,
            )
        )

    async def _fetch_spot_orders_page(
        self, credential: Credential, start_s: int, end_s: int, limit: int, page: int
    ) -> list[dict]:
        # currency_pair omitted -> account-wide finished orders.
        return dump_gate_model(
            await self._call(
                self._spot(credential).list_orders,
                None, "finished",
                _from=start_s, to=end_s, limit=limit, page=page,
            )
        )

    async def _fetch_futures_trades_page(
        self, credential: Credential, settle: str,
        start_s: int, end_s: int, limit: int, offset: int,
    ) -> list[dict]:
        try:
            return dump_gate_model(
                await self._call(
                    self._futures(credential).get_my_trades_with_time_range,
                    settle, _from=start_s, to=end_s, limit=limit, offset=offset,
                )
            )
        except GateApiException as exc:
            if exc.label in _EMPTY_ACCOUNT_LABELS:
                return []
            raise

    async def _fetch_futures_orders_page(
        self, credential: Credential, settle: str,
        start_s: int, end_s: int, limit: int, offset: int,
    ) -> list[dict]:
        try:
            return dump_gate_model(
                await self._call(
                    self._futures(credential).get_orders_with_time_range,
                    settle, _from=start_s, to=end_s, limit=limit, offset=offset,
                )
            )
        except GateApiException as exc:
            if exc.label in _EMPTY_ACCOUNT_LABELS:
                return []
            raise

    async def _history_page(
        self, credential: Credential, stream_key: str, cursor: str | None, kind: str
    ) -> Page:
        if not self._native_map:
            await self.fetch_instruments()
        limit = _TRADE_PAGE_LIMIT if kind == "trades" else _ORDER_PAGE_LIMIT
        start_ms, end_ms, now_ms, skip = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        start_s, end_s = start_ms // 1000, end_ms // 1000

        items: list = []
        if stream_key == "spot":
            page_number = skip // limit + 1
            if kind == "trades":
                rows = await self._fetch_spot_trades_page(
                    credential, start_s, end_s, limit, page_number
                )
            else:
                rows = await self._fetch_spot_orders_page(
                    credential, start_s, end_s, limit, page_number
                )
            for raw in rows:
                universal = self._native_map.get(
                    (MarketType.SPOT, raw.get("currency_pair", ""))
                )
                if universal is None:
                    continue
                items.append(
                    normalize_spot_trade(raw, universal)
                    if kind == "trades"
                    else normalize_spot_order(raw, universal)
                )
        else:
            market_type = dict(_SETTLES)[stream_key]
            if kind == "trades":
                rows = await self._fetch_futures_trades_page(
                    credential, stream_key, start_s, end_s, limit, skip
                )
            else:
                rows = await self._fetch_futures_orders_page(
                    credential, stream_key, start_s, end_s, limit, skip
                )
            for raw in rows:
                universal = self._native_map.get((market_type, raw.get("contract", "")))
                if universal is None:
                    continue
                items.append(
                    normalize_futures_trade(raw, universal, stream_key)
                    if kind == "trades"
                    else normalize_futures_order(raw, universal)
                )
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, skip, len(rows), limit
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

    # -- subaccounts and provisioning -------------------------------------------

    def _sub_api(self, credential: Credential) -> gate_api.SubAccountApi:
        return gate_api.SubAccountApi(self._auth_client(credential))

    async def list_subaccounts(self, credential: Credential) -> list[SubaccountInfo]:
        rows = dump_gate_model(await self._call(self._sub_api(credential).list_sub_accounts))
        return [
            SubaccountInfo(
                native_uid=str(row["user_id"]),
                label=row.get("remark") or row.get("login_name") or str(row["user_id"]),
                status="normal" if row.get("state", 1) == 1 else "frozen",
                raw=row,
            )
            for row in rows
        ]

    async def create_subaccount(self, credential: Credential, label: str) -> SubaccountInfo:
        # Gate's login_name is a globally unique username (letters/digits/_ only).
        # Keep the panel label in remark so listing/display still uses it.
        body = gate_api.SubAccount(login_name=_gate_login_name(label), remark=label)
        try:
            row = dump_gate_model(
                await self._call(self._sub_api(credential).create_sub_accounts, body)
            )
        except GateApiException as exc:
            raise ValueError(exc.message or "Gate.io rejected subaccount creation") from exc
        return SubaccountInfo(
            native_uid=str(row.get("user_id", "")),
            label=label,
            status="normal",
            raw=row,
        )

    async def set_subaccount_frozen(
        self, credential: Credential, subaccount: str, frozen: bool
    ) -> None:
        api = self._sub_api(credential)
        method = api.lock_sub_account if frozen else api.unlock_sub_account
        await self._call(method, int(subaccount))

    @staticmethod
    def _build_perms(permissions: list[str], read_only: bool) -> list[gate_api.SubAccountKeyPerms]:
        names: list[str] = []
        for scope in permissions:
            name = _PERMISSION_SCOPES.get(scope)
            if name and name not in names:
                names.append(name)
        perms = [
            gate_api.SubAccountKeyPerms(name=name, read_only=read_only) for name in names
        ]
        # Account info read access so the panel can verify the key.
        if "account" not in names:
            perms.append(gate_api.SubAccountKeyPerms(name="account", read_only=True))
        return perms

    async def _create_sub_key_raw(
        self, credential: Credential, user_id: int, body: gate_api.SubAccountKey
    ) -> dict:
        """POST the key and return the raw JSON — the SDK model drops the
        one-time ``secret`` field."""
        api = self._sub_api(credential)
        response = await self._call(
            api.create_sub_account_keys, user_id, body, _preload_content=False
        )
        return json.loads(response.data)

    async def create_api_key(
        self, credential: Credential, subaccount: str | None, scheme: KeyScheme,
        permissions: list[str],
        read_only: bool = False,
        ips: list[str] | None = None,
        note: str | None = None,
    ) -> ApiKeyResult:
        if scheme != KeyScheme.HMAC:
            raise ValueError("Gate.io issues only HMAC keys via API")
        if not subaccount:
            raise ValueError("Gate.io API key creation targets a subaccount uid")
        body = gate_api.SubAccountKey(
            name=note or "created by cpanel",
            perms=self._build_perms(permissions, read_only),
        )
        if ips:
            body.ip_whitelist = ips
        raw = await self._create_sub_key_raw(credential, int(subaccount), body)
        raw_ips = raw.get("ip_whitelist")
        return ApiKeyResult(
            api_key=raw["key"],
            secret=raw["secret"],
            scheme=KeyScheme.HMAC,
            permissions=permissions,
            ips=[str(ip) for ip in raw_ips] if raw_ips is not None else list(ips or []),
        )

    async def list_api_keys(
        self, credential: Credential, subaccount: str | None = None
    ) -> list[ApiKeyInfo]:
        """Main keys via /account/main_keys; sub keys via list_sub_account_keys."""
        if subaccount:
            rows = dump_gate_model(
                await self._call(
                    self._sub_api(credential).list_sub_account_keys, int(subaccount)
                )
            )
            if isinstance(rows, dict):
                rows = [rows]
            return [
                ApiKeyInfo(
                    api_key=str(row.get("key") or ""),
                    ips=[str(ip) for ip in (row.get("ip_whitelist") or [])],
                    label=row.get("name"),
                )
                for row in rows or []
                if row.get("key")
            ]

        api = gate_api.AccountApi(self._auth_client(credential))
        response = await self._call(api.get_account_main_keys, _preload_content=False)
        rows = json.loads(response.data)
        if isinstance(rows, dict):
            rows = [rows]
        return [
            ApiKeyInfo(
                api_key=str(row.get("key") or ""),
                ips=[str(ip) for ip in (row.get("ip_whitelist") or [])],
                label=row.get("name"),
            )
            for row in rows or []
            if row.get("key")
        ]

    async def modify_api_key(
        self,
        credential: Credential,
        subaccount: str | None,
        api_key: str,
        read_only: bool | None = None,
        permissions: list[str] | None = None,
    ) -> None:
        """Replace a sub key's permission set on the venue.

        Gate's update is a full replace, so when only the read-only flag
        changes the current permission names are re-sent with flipped flags.
        """
        if not subaccount:
            raise ValueError("Gate.io API key modification targets a subaccount uid")
        api = self._sub_api(credential)
        user_id = int(subaccount)
        if permissions is not None and any(p in _PERMISSION_SCOPES for p in permissions):
            perms = self._build_perms(permissions, read_only or False)
        else:
            current = dump_gate_model(
                await self._call(api.get_sub_account_key, user_id, api_key)
            )
            perms = [
                gate_api.SubAccountKeyPerms(
                    name=p["name"],
                    read_only=read_only if read_only is not None else p.get("read_only"),
                )
                for p in current.get("perms") or []
            ]
        await self._call(
            api.update_sub_account_keys, user_id, api_key,
            gate_api.SubAccountKey(perms=perms),
        )

    async def revoke_api_key(
        self, credential: Credential, subaccount: str | None, api_key: str
    ) -> None:
        if not subaccount:
            raise ValueError("Gate.io API key revocation targets a subaccount uid")
        await self._call(
            self._sub_api(credential).delete_sub_account_keys, int(subaccount), api_key
        )

    # -- transfers ----------------------------------------------------------------
    #
    # Same-account wallet moves use /wallet/transfers (spot ↔ futures, with
    # the futures account picked by the settle currency = the asset). Main↔sub
    # moves use /wallet/sub_account_transfers, where the main side is always
    # the spot account (venue rule) and both endpoints answer synchronously
    # with a tx id. Sub↔sub uses the dedicated endpoint. All of these need the
    # master key — Gate refuses them for sub-account keys.

    @staticmethod
    def _wallet_leg(wallet: WalletType | None) -> str:
        leg = _WALLET_ACCOUNT.get(wallet or WalletType.SPOT)
        if leg is None:
            raise ValueError("Gate.io wallets are SPOT and FUTURES")
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
        wallet = self._wallet(credential)
        if from_subaccount and to_subaccount:
            body = gate_api.SubAccountToSubAccount(
                currency=asset,
                sub_account_from=from_subaccount,
                sub_account_from_type=self._wallet_leg(from_wallet),
                sub_account_to=to_subaccount,
                sub_account_to_type=self._wallet_leg(to_wallet),
                amount=str(amount),
            )
            result = dump_gate_model(
                await self._call(wallet.sub_account_to_sub_account, body)
            )
        elif from_subaccount or to_subaccount:
            # The main side of a main↔sub transfer is always the spot account.
            main_wallet = to_wallet if from_subaccount else from_wallet
            if self._wallet_leg(main_wallet) != "spot":
                raise ValueError(
                    "Gate.io main↔sub transfers use the master's spot account"
                )
            sub_wallet = from_wallet if from_subaccount else to_wallet
            body = gate_api.SubAccountTransfer(
                sub_account=from_subaccount or to_subaccount,
                sub_account_type=self._wallet_leg(sub_wallet),
                currency=asset,
                amount=str(amount),
                direction="from" if from_subaccount else "to",
            )
            result = dump_gate_model(
                await self._call(wallet.transfer_with_sub_account, body)
            )
        else:
            src = self._wallet_leg(from_wallet)
            dst = self._wallet_leg(to_wallet)
            if src == dst:
                raise ValueError(
                    "Gate.io internal transfers move between SPOT and FUTURES wallets"
                )
            body = gate_api.Transfer(
                currency=asset, _from=src, to=dst, amount=str(amount)
            )
            if "futures" in (src, dst):
                # The settle currency names which futures account is touched.
                body.settle = asset.lower()
            result = dump_gate_model(await self._call(wallet.transfer, body))
        return TransferResult(
            native_transfer_id=str(result.get("tx_id", "")), status="confirmed"
        )

    # -- withdrawals / deposits -----------------------------------------------

    async def _fetch_currency_chains(self, credential: Credential, asset: str) -> list[dict]:
        return dump_gate_model(
            await self._call(self._wallet(credential).list_currency_chains, asset.upper())
        )

    async def _fetch_withdraw_status(self, credential: Credential, asset: str) -> dict:
        rows = dump_gate_model(
            await self._call(
                self._wallet(credential).list_withdraw_status, currency=asset.upper()
            )
        )
        return rows[0] if rows else {}

    async def fetch_asset_networks(
        self, credential: Credential, asset: str
    ) -> list[AssetNetwork]:
        chains = await self._fetch_currency_chains(credential, asset)
        status = await self._fetch_withdraw_status(credential, asset)
        return normalize_asset_networks(chains, status)

    async def _fetch_deposit_address_raw(self, credential: Credential, asset: str) -> dict:
        return dump_gate_model(
            await self._call(self._wallet(credential).get_deposit_address, asset.upper())
        )

    async def fetch_deposit_address(
        self,
        credential: Credential,
        asset: str,
        network: str,
        subaccount: str | None = None,
    ) -> DepositAddress:
        if subaccount:
            # No master-for-sub endpoint on Gate — the sub needs its own key.
            raise ValueError(
                "Gate.io cannot resolve a subaccount's deposit address with the "
                "master key; register the subaccount's own API key"
            )
        data = await self._fetch_deposit_address_raw(credential, asset)
        wanted = network.upper()
        for chain in data.get("multichain_addresses") or []:
            if str(chain.get("chain", "")).upper() != wanted:
                continue
            address = chain.get("address") or ""
            if chain.get("obtain_failed") or not address:
                break
            return DepositAddress(
                asset=asset,
                network=wanted,
                address=address,
                tag=chain.get("payment_id") or None,
            )
        raise ValueError(f"Gate.io returned no {asset}/{network} deposit address")

    async def withdraw(
        self,
        credential: Credential,
        asset: str,
        amount,
        address: str,
        network: str,
        tag: str | None = None,
    ) -> Withdrawal:
        body = gate_api.LedgerRecord(
            currency=asset.upper(),
            amount=str(amount),
            address=address,
            chain=network.upper(),
        )
        if tag:
            body.memo = tag
        api = gate_api.WithdrawalApi(self._auth_client(credential))
        raw = dump_gate_model(await self._call(api.withdraw, body))
        return Withdrawal(
            native_id=str(raw.get("id", "")),
            asset=asset,
            amount=_dec(amount),
            status=WithdrawalStatus.PENDING,
            address=address,
            network=network.upper(),
        )

    async def _fetch_withdrawal_rows(
        self,
        credential: Credential,
        *,
        native_id: str | None = None,
        start_s: int | None = None,
        end_s: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        kwargs: dict = {"limit": limit}
        if native_id is not None:
            kwargs["withdraw_id"] = native_id
        if start_s is not None:
            kwargs["_from"] = start_s
        if end_s is not None:
            kwargs["to"] = end_s
        if offset:
            kwargs["offset"] = offset
        return dump_gate_model(
            await self._call(self._wallet(credential).list_withdrawals, **kwargs)
        )

    async def fetch_withdrawal(
        self, credential: Credential, native_id: str
    ) -> Withdrawal | None:
        rows = await self._fetch_withdrawal_rows(credential, native_id=native_id)
        return normalize_withdrawal_record(rows[0]) if rows else None

    async def _fetch_deposit_rows(
        self,
        credential: Credential,
        asset: str | None,
        start_s: int | None,
        end_s: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        kwargs: dict = {"limit": limit}
        if asset:
            kwargs["currency"] = asset.upper()
        if start_s is not None:
            kwargs["_from"] = start_s
        if end_s is not None:
            kwargs["to"] = end_s
        if offset:
            kwargs["offset"] = offset
        return dump_gate_model(
            await self._call(self._wallet(credential).list_deposits, **kwargs)
        )

    async def fetch_deposits(
        self,
        credential: Credential,
        asset: str | None = None,
        since: datetime | None = None,
        subaccount: str | None = None,
    ) -> list[Deposit]:
        if subaccount:
            raise ValueError(
                "Gate.io cannot list a subaccount's deposits with the master "
                "key; register the subaccount's own API key"
            )
        start_s = int(since.timestamp()) if since else None
        rows = await self._fetch_deposit_rows(credential, asset, start_s)
        return [normalize_deposit_record(raw) for raw in rows]

    _FUND_HISTORY_LIMIT = 100

    async def _fund_history_page(
        self, credential: Credential, cursor: str | None, *, kind: str
    ) -> Page:
        start_ms, end_ms, now_ms, skip = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        limit = self._FUND_HISTORY_LIMIT
        start_s, end_s = start_ms // 1000, end_ms // 1000
        if kind == "deposits":
            rows = await self._fetch_deposit_rows(
                credential, None, start_s, end_s=end_s, limit=limit, offset=skip
            )
            items = [normalize_deposit_record(raw) for raw in rows]
        else:
            rows = await self._fetch_withdrawal_rows(
                credential, start_s=start_s, end_s=end_s, limit=limit, offset=skip
            )
            items = [normalize_withdrawal_record(raw) for raw in rows]
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, skip, len(rows), limit
            ),
        )

    async def fetch_deposits_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        return await self._fund_history_page(credential, cursor, kind="deposits")

    async def fetch_withdrawals_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        return await self._fund_history_page(credential, cursor, kind="withdrawals")

    # -- funding fee history --------------------------------------------------

    def funding_history_stream_keys(self) -> list[str]:
        return [settle for settle, _ in _SETTLES]

    async def _fetch_funding_rows(
        self,
        credential: Credential,
        settle: str,
        start_s: int,
        end_s: int,
        limit: int,
        offset: int,
    ) -> list[dict]:
        try:
            return dump_gate_model(
                await self._call(
                    self._futures(credential).list_futures_account_book,
                    settle,
                    type="fund",
                    _from=start_s,
                    to=end_s,
                    limit=limit,
                    offset=offset,
                )
            )
        except GateApiException as exc:
            if exc.label in _EMPTY_ACCOUNT_LABELS:
                return []
            raise

    async def fetch_funding_fees_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        market_type = dict(_SETTLES).get(stream_key)
        if market_type is None:
            raise ValueError(f"Unknown Gate.io funding stream {stream_key!r}")
        if not self._native_map:
            await self.fetch_instruments()
        start_ms, end_ms, now_ms, skip = self._history_window(cursor)
        if start_ms >= now_ms:
            return Page(items=[], next_cursor=None)
        limit = self._FUND_HISTORY_LIMIT
        start_s, end_s = start_ms // 1000, end_ms // 1000
        rows = await self._fetch_funding_rows(
            credential, stream_key, start_s, end_s, limit, skip
        )
        items: list[FundingFee] = []
        for raw in rows:
            universal = self._native_map.get((market_type, raw.get("contract", "")))
            if universal is None:
                continue
            items.append(
                normalize_funding_fee(raw, universal, settle=stream_key)
            )
        return Page(
            items=items,
            next_cursor=self._next_history_cursor(
                start_ms, end_ms, now_ms, skip, len(rows), limit
            ),
        )
