import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional
from urllib.parse import urlencode

import binance as bn
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from pybit.unified_trading import HTTP as BybitHTTP

from app.utils import trim_decimal_string

logger = logging.getLogger(__name__)

API_TYPES = Literal["HMAC", "ED25519", "RSA"]
EXCHANGES = Literal["BINANCE", "BYBIT", "BITGET"]
WALLETS = Literal["SPOT", "MARGIN", "FUTURES", "UNIFIED", "FUNDING"]
ORDER_TYPES = Literal["MARKET", "LIMIT"]
ORDER_SIDES = Literal["BUY", "SELL"]
ORDER_STATUS = Literal["NEW", "FILLED", "PARTIAL_FILLED", "REJECT", "CANCEL"]
ENDING_STATUSES = {"FILLED", "REJECT", "CANCEL"}

PRODUCT_TO_BYBIT_CATEGORY = {
    "SPOT": "spot",
    "LINEAR": "linear",
    "UMARGIN": "linear",
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


class BitgetClient:
    """Lightweight REST client for Bitget API v2 with HMAC/RSA signing."""

    BASE_URL = "https://api.bitget.com"
    TIMEOUT = 10

    def __init__(self, api_key: str, api_secret: str, passphrase: str, api_type: API_TYPES = "HMAC"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.api_type = api_type
        self._session = requests.Session()

    def _sign(self, timestamp: str, method: str, request_path: str, query_string: str = "", body: str = "") -> str:
        message = timestamp + method.upper() + request_path
        if query_string:
            message += "?" + query_string
        if body:
            message += body

        if self.api_type == "RSA":
            private_key = serialization.load_pem_private_key(self.api_secret.encode(), password=None)
            signature = private_key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
            return base64.b64encode(signature).decode()

        mac = hmac.new(self.api_secret.encode(), message.encode(), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode()

    def _request(self, method: str, path: str, params: Optional[Dict] = None, data: Optional[Dict] = None) -> Dict:
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        query_string = urlencode(params) if params else ""
        body = json.dumps(data) if data else ""

        sign = self._sign(timestamp, method, path, query_string, body)
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": sign,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

        url = self.BASE_URL + path
        if query_string:
            url += "?" + query_string

        try:
            if method.upper() == "GET":
                resp = self._session.get(url, headers=headers, timeout=self.TIMEOUT)
            else:
                resp = self._session.post(url, headers=headers, data=body, timeout=self.TIMEOUT)
        except requests.exceptions.Timeout as e:
            raise NetworkError(f"Bitget request timed out: {e}", exchange="BITGET", original_error=e)
        except (requests.exceptions.ConnectionError, OSError) as e:
            raise NetworkError(f"Network error connecting to Bitget: {e}", exchange="BITGET", original_error=e)

        try:
            result = resp.json()
        except Exception:
            if not resp.ok:
                raise ExchangeError(
                    f"Bitget HTTP {resp.status_code}: {resp.text[:200]}",
                    exchange="BITGET",
                    error_code=resp.status_code,
                )
            raise ExchangeError("Bitget returned non-JSON response", exchange="BITGET")

        if not resp.ok or str(result.get("code", "")) != "00000":
            code = result.get("code", resp.status_code)
            msg = result.get("msg", f"Bitget error (HTTP {resp.status_code})")
            logger.warning("Bitget API error: %s %s -> code=%s msg=%s", method, path, code, msg)
            raise ExchangeError(msg, exchange="BITGET", error_code=int(code) if code else None)

        return result

    def get(self, path: str, params: Optional[Dict] = None) -> Dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, data: Optional[Dict] = None) -> Dict:
        return self._request("POST", path, data=data)


def create_bitget_client(
    api_key: str, api_secret: str, passphrase: str = "", api_type: API_TYPES = "HMAC"
) -> BitgetClient:
    return BitgetClient(api_key=api_key, api_secret=api_secret, passphrase=passphrase, api_type=api_type)


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
    if exchange.upper() == "BITGET":
        return {
            "LIVE": "NEW",
            "NEW": "NEW",
            "PARTIALLY_FILLED": "PARTIAL_FILLED",
            "FILLED": "FILLED",
            "CANCELLED": "CANCEL",
            "CANCELED": "CANCEL",
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
