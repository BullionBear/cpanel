from typing import Any, Dict, List, Optional

from .base import APIContext, ExchangeClient
from .common import API_TYPES, EXCHANGES, ORDER_SIDES, ORDER_TYPES
from .factory import ExchangeClientFactory


class APIService:
    def _build_context(
        self, api_key: str, api_secret: str, passphrase: Optional[str] = None, api_type: API_TYPES = "HMAC"
    ) -> APIContext:
        return APIContext(api_key=api_key, api_secret=api_secret, passphrase=passphrase, api_type=api_type)

    def _client(
        self, exchange: EXCHANGES, api_key: str, api_secret: str, passphrase: Optional[str] = None, api_type: API_TYPES = "HMAC"
    ) -> ExchangeClient:
        return ExchangeClientFactory.create(
            exchange=exchange,
            context=self._build_context(api_key=api_key, api_secret=api_secret, passphrase=passphrase, api_type=api_type),
        )

    def get_exchange_client(
        self, exchange: EXCHANGES, api_key: str, api_secret: str, passphrase: Optional[str] = None, api_type: API_TYPES = "HMAC"
    ) -> ExchangeClient:
        return self._client(exchange, api_key, api_secret, passphrase, api_type)

    def get_exchange_client_from_api_info(self, exchange: EXCHANGES, api_info: Any) -> ExchangeClient:
        return self._client(
            exchange=exchange,
            api_key=api_info.api_key,
            api_secret=api_info.api_secret,
            passphrase=getattr(api_info, "passphrase", None),
            api_type=getattr(api_info, "api_type", "HMAC"),
        )

    def verify_api(
        self, exchange: EXCHANGES, api_key: str, api_secret: str, passphrase: Optional[str] = None, api_type: API_TYPES = "HMAC"
    ) -> None:
        self._client(exchange, api_key, api_secret, passphrase, api_type).verify_api()

    def get_account_uid(
        self, exchange: EXCHANGES, api_key: str, api_secret: str, passphrase: Optional[str] = None, api_type: API_TYPES = "HMAC"
    ) -> int:
        return self._client(exchange, api_key, api_secret, passphrase, api_type).get_account_uid()

    def get_balance(
        self, exchange: EXCHANGES, api_key: str, api_secret: str, passphrase: Optional[str] = None, api_type: API_TYPES = "HMAC"
    ) -> List[Dict[str, Any]]:
        return self._client(exchange, api_key, api_secret, passphrase, api_type).get_balance()

    def get_symbol_info(
        self, exchange: EXCHANGES, symbol: str, api_key: Optional[str] = None, api_secret: Optional[str] = None
    ) -> Dict[str, Any]:
        if api_key and api_secret:
            client = self._client(exchange, api_key, api_secret)
        else:
            client = self._client(exchange, "", "")
        return client.get_symbol_info(symbol)

    def get_order_status(
        self,
        exchange: EXCHANGES,
        api_key: str,
        api_secret: str,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        passphrase: Optional[str] = None,
        api_type: API_TYPES = "HMAC",
    ) -> Dict[str, Any]:
        return self._client(exchange, api_key, api_secret, passphrase, api_type).get_order_status(
            symbol=symbol, order_id=order_id, client_order_id=client_order_id
        )

    def get_open_orders(
        self,
        exchange: EXCHANGES,
        api_key: str,
        api_secret: str,
        symbol: Optional[str] = None,
        passphrase: Optional[str] = None,
        api_type: API_TYPES = "HMAC",
    ) -> List[Dict[str, Any]]:
        return self._client(exchange, api_key, api_secret, passphrase, api_type).get_open_orders(symbol=symbol)

    def cancel_order(
        self,
        exchange: EXCHANGES,
        api_key: str,
        api_secret: str,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        passphrase: Optional[str] = None,
        api_type: API_TYPES = "HMAC",
    ) -> Dict[str, Any]:
        return self._client(exchange, api_key, api_secret, passphrase, api_type).cancel_order(
            symbol=symbol, order_id=order_id, client_order_id=client_order_id
        )

    def create_order(
        self,
        exchange: EXCHANGES,
        api_key: str,
        api_secret: str,
        symbol: str,
        side: ORDER_SIDES,
        order_type: ORDER_TYPES,
        quantity: str,
        price: Optional[str] = None,
        passphrase: Optional[str] = None,
        api_type: API_TYPES = "HMAC",
    ) -> Dict[str, Any]:
        return self._client(exchange, api_key, api_secret, passphrase, api_type).create_order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
        )

    def get_mytrades(
        self,
        exchange: EXCHANGES,
        api_key: str,
        api_secret: str,
        category: str,
        start_ms: int,
        end_ms: int,
        symbol: Optional[str] = None,
        limit: int = 200,
        passphrase: Optional[str] = None,
        api_type: API_TYPES = "HMAC",
    ) -> List[Dict[str, Any]]:
        return self._client(exchange, api_key, api_secret, passphrase, api_type).get_mytrades(
            category=category, start_ms=start_ms, end_ms=end_ms, symbol=symbol, limit=limit
        )

    # Compatibility wrappers for existing direct exchange methods.
    def verify_binance_api(
        self, api_key: str, api_secret: str, passphrase: Optional[str] = None, api_type: API_TYPES = "HMAC"
    ) -> None:
        self.verify_api("BINANCE", api_key, api_secret, passphrase, api_type)

    def verify_bybit_api(
        self, api_key: str, api_secret: str, passphrase: Optional[str] = None, api_type: API_TYPES = "HMAC"
    ) -> None:
        self.verify_api("BYBIT", api_key, api_secret, passphrase, api_type)

    def get_binance_account_uid(
        self, api_key: str, api_secret: str, passphrase: Optional[str] = None, api_type: API_TYPES = "HMAC"
    ) -> int:
        return self.get_account_uid("BINANCE", api_key, api_secret, passphrase, api_type)

    def get_bybit_account_uid(
        self, api_key: str, api_secret: str, passphrase: Optional[str] = None, api_type: API_TYPES = "HMAC"
    ) -> int:
        return self.get_account_uid("BYBIT", api_key, api_secret, passphrase, api_type)

    def get_binance_balance(
        self, api_key: str, api_secret: str, passphrase: Optional[str] = None, api_type: API_TYPES = "HMAC"
    ) -> List[Dict[str, Any]]:
        return self.get_balance("BINANCE", api_key, api_secret, passphrase, api_type)

    def get_bybit_balance(
        self, api_key: str, api_secret: str, passphrase: Optional[str] = None, api_type: API_TYPES = "HMAC"
    ) -> List[Dict[str, Any]]:
        return self.get_balance("BYBIT", api_key, api_secret, passphrase, api_type)

    def get_binance_symbol_info(self, symbol: str) -> Dict[str, Any]:
        return self.get_symbol_info("BINANCE", symbol)

    def get_bybit_symbol_info(self, symbol: str) -> Dict[str, Any]:
        return self.get_symbol_info("BYBIT", symbol)

