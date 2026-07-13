from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .common import API_TYPES, ORDER_SIDES, ORDER_TYPES


@dataclass(frozen=True)
class APIContext:
    api_key: str
    api_secret: str
    passphrase: Optional[str] = None
    api_type: API_TYPES = "HMAC"


class ExchangeClient(ABC):
    @abstractmethod
    def verify_api(self) -> None:
        pass

    @abstractmethod
    def get_account_uid(self) -> int:
        pass

    @abstractmethod
    def get_balance(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_symbol_info(self, symbol: str, product: str = "SPOT") -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_order_status(
        self, symbol: str, order_id: Optional[str] = None, client_order_id: Optional[str] = None,
        product: str = "SPOT",
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def cancel_order(
        self, symbol: str, order_id: Optional[str] = None, client_order_id: Optional[str] = None,
        product: str = "SPOT",
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    def create_order(
        self, symbol: str, side: ORDER_SIDES, order_type: ORDER_TYPES, quantity: str,
        price: Optional[str] = None, product: str = "SPOT", reduce_only: bool = False,
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_mytrades(
        self, category: str, start_ms: int, end_ms: int, symbol: Optional[str] = None, limit: int = 200
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        pass
