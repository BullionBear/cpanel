from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional

import binance as bn
from pybit.unified_trading import HTTP as BybitHTTP

from app.utils import trim_decimal_string

API_TYPES = Literal["HMAC", "ED25519", "RSA"]
EXCHANGES = Literal["BINANCE", "BYBIT"]
WALLETS = Literal["SPOT", "MARGIN", "FUTURES", "UNIFIED", "FUNDING"]
ORDER_TYPES = Literal["MARKET", "LIMIT"]
ORDER_SIDES = Literal["BUY", "SELL"]
ORDER_STATUS = Literal["NEW", "FILLED", "PARTIAL_FILLED", "REJECT", "CANCEL"]
ENDING_STATUSES = {"FILLED", "REJECT", "CANCEL"}

PRODUCT_TO_BYBIT_CATEGORY = {
    "SPOT": "spot",
    "LINEAR": "linear",
    "INVERSE": "inverse",
    "OPTION": "option",
}


class NetworkError(Exception):
    def __init__(self, message: str, exchange: str, original_error: Optional[Exception] = None):
        self.message = message
        self.exchange = exchange
        self.original_error = original_error
        super().__init__(self.message)


class ExchangeError(Exception):
    def __init__(
        self,
        message: str,
        exchange: str,
        error_code: Optional[int] = None,
        original_error: Optional[Exception] = None,
    ):
        self.message = message
        self.exchange = exchange
        self.error_code = error_code
        self.original_error = original_error
        super().__init__(self.message)


def create_binance_client(api_key: str, api_secret: str, api_type: API_TYPES = "HMAC") -> bn.Client:
    if api_type in ("ED25519", "RSA"):
        return bn.Client(api_key=api_key, private_key=api_secret)
    return bn.Client(api_key, api_secret)


def create_bybit_client(
    api_key: str, api_secret: str, api_type: API_TYPES = "HMAC", testnet: bool = False
) -> BybitHTTP:
    return BybitHTTP(
        api_key=api_key,
        api_secret=api_secret,
        testnet=testnet,
        rsa_authentication=(api_type == "RSA"),
    )


def normalize_order_status(exchange: str, status: str) -> str:
    status_upper = status.upper() if status else ""
    if exchange.upper() == "BINANCE":
        return {
            "NEW": "NEW",
            "PARTIALLY_FILLED": "PARTIAL_FILLED",
            "FILLED": "FILLED",
            "CANCELED": "CANCEL",
            "PENDING_CANCEL": "CANCEL",
            "REJECTED": "REJECT",
            "EXPIRED": "CANCEL",
        }.get(status_upper, "NEW")
    if exchange.upper() == "BYBIT":
        return {
            "NEW": "NEW",
            "PARTIALLYFILLED": "PARTIAL_FILLED",
            "FILLED": "FILLED",
            "CANCELED": "CANCEL",
            "REJECTED": "REJECT",
        }.get(status_upper, "NEW")
    return "NEW"


def normalize_numeric_string(value: Optional[str]) -> str:
    if value is None:
        return "-1"

    value_stripped = value.strip() if isinstance(value, str) else str(value)
    if value_stripped == "-1":
        return "-1"

    try:
        if float(value_stripped) == 0.0:
            return "-1"
    except (ValueError, TypeError):
        return value_stripped

    trimmed_value = trim_decimal_string(value_stripped)
    if trimmed_value in ["0", ""]:
        return "-1"
    return trimmed_value if trimmed_value else "-1"


def normalize_price(price: Optional[str]) -> str:
    return normalize_numeric_string(price)


def normalize_quantity(quantity: Optional[str]) -> str:
    if quantity is None:
        return "0"
    quantity_stripped = quantity.strip() if isinstance(quantity, str) else str(quantity)
    trimmed = trim_decimal_string(quantity_stripped)
    return trimmed if trimmed else "0"


class TTLCache:
    def __init__(self, ttl_seconds: int = 86400):
        self.cache: Dict[str, tuple[Any, datetime]] = {}
        self.ttl_seconds = ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        if key not in self.cache:
            return None
        value, expiry = self.cache[key]
        if datetime.now(timezone.utc) > expiry:
            del self.cache[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        expiry = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)
        self.cache[key] = (value, expiry)

    def clear(self) -> None:
        self.cache.clear()


exchange_info_cache = TTLCache(ttl_seconds=86400)
