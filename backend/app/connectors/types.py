"""Normalized types spoken above the connector boundary.

Everything here is exchange-agnostic: universal symbols, normalized enums,
and Decimal quantities. Native shapes and names must never escape a
connector.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class MarketType(StrEnum):
    SPOT = "SPOT"
    PERP = "PERP"          # USDT/USDC-margined perpetual (Bybit "linear")
    CMARGIN = "CMARGIN"    # COIN-margined perpetual (inverse)
    DELIVERY = "DELIVERY"
    OPTION = "OPTION"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class TimeInForce(StrEnum):
    """Normalized time-in-force. POST_ONLY is a maker-only instruction that each
    connector maps to its venue's mechanism (Binance ``LIMIT_MAKER``/``GTX``,
    Bybit ``PostOnly``)."""
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    POST_ONLY = "POST_ONLY"


class PositionSide(StrEnum):
    """Derivatives position the order applies to. BOTH is one-way mode; LONG /
    SHORT are hedge-mode sides (Binance ``positionSide``, Bybit ``positionIdx``)."""
    BOTH = "BOTH"
    LONG = "LONG"
    SHORT = "SHORT"


class OrderStatus(StrEnum):
    SUBMITTING = "SUBMITTING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class WalletType(StrEnum):
    SPOT = "SPOT"
    MARGIN = "MARGIN"
    DERIVATIVES = "DERIVATIVES"
    FUNDING = "FUNDING"


class KeyScheme(StrEnum):
    HMAC = "HMAC"
    ED25519 = "ED25519"


@dataclass(frozen=True)
class InstrumentFilters:
    price_tick: Decimal
    qty_step: Decimal
    min_qty: Decimal
    max_qty: Decimal | None = None
    min_notional: Decimal | None = None


@dataclass(frozen=True)
class Instrument:
    universal_symbol: str          # e.g. BINANCE_SPOT_BTCUSDT
    exchange: str
    market_type: MarketType
    native_symbol: str
    base_asset: str
    quote_asset: str
    filters: InstrumentFilters
    status: str = "active"         # active | halted | delisted
    contract_size: Decimal | None = None
    settle_asset: str | None = None
    expiry: datetime | None = None


@dataclass(frozen=True)
class Balance:
    wallet: WalletType
    asset: str
    free: Decimal
    locked: Decimal

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


@dataclass(frozen=True)
class Position:
    universal_symbol: str
    side: str                      # long | short
    size: Decimal
    entry_price: Decimal
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    margin: Decimal | None = None


@dataclass(frozen=True)
class OrderRequest:
    universal_symbol: str
    side: OrderSide
    type: OrderType
    qty: Decimal
    price: Decimal | None = None   # required for LIMIT
    client_order_id: str | None = None
    # Exchange/category-specific options; connectors apply what their venue and
    # market support and ignore the rest (spot ignores reduce_only/position_side).
    time_in_force: TimeInForce | None = None
    reduce_only: bool = False
    position_side: PositionSide | None = None


@dataclass(frozen=True)
class Order:
    universal_symbol: str
    native_order_id: str
    client_order_id: str | None
    side: OrderSide
    type: OrderType
    status: OrderStatus
    qty: Decimal
    filled_qty: Decimal
    price: Decimal | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class Trade:
    universal_symbol: str
    native_trade_id: str
    native_order_id: str
    side: OrderSide
    price: Decimal
    qty: Decimal
    fee: Decimal
    fee_asset: str
    traded_at: datetime | None = None


@dataclass(frozen=True)
class Page:
    """One page of an incremental history fetch.

    ``next_cursor`` is opaque to callers; the connector defines its meaning
    (trade id, timestamp, or a native pagination token). ``None`` means the
    stream is caught up.
    """
    items: list = field(default_factory=list)
    next_cursor: str | None = None


@dataclass(frozen=True)
class CredentialInfo:
    """Normalized result of verifying an API credential against its venue."""
    valid: bool
    native_uid: str | None = None      # venue account id (Bybit uid, …)
    is_master: bool | None = None
    read_only: bool | None = None
    can_trade: bool | None = None
    can_withdraw: bool | None = None
    permissions: dict | None = None    # venue-specific detail, for display
    reason: str | None = None          # why verification failed (when not valid)


@dataclass(frozen=True)
class SubaccountInfo:
    """One subaccount as reported by the venue."""
    native_uid: str                    # Bybit uid / Binance email
    label: str
    status: str | None = None
    raw: dict | None = None


@dataclass(frozen=True)
class ApiKeyResult:
    api_key: str
    secret: str                    # plaintext only at creation time
    scheme: KeyScheme
    permissions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TransferResult:
    native_transfer_id: str
    status: str                    # submitted | confirmed | failed
