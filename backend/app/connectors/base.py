"""Abstract exchange connector: the full method surface from ARCHITECTURE.md.

Concrete connectors (Binance, Bybit, …) live one module per exchange and are
the only place exchange SDKs are imported. Each connector declares its
capabilities; callers must check ``supports()`` (or catch ``NotSupported``)
before offering an operation for a given venue.

Credentials are passed per call as an opaque ``credential`` mapping decrypted
by the caller at the last moment — connectors never store secrets.
"""
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from app.connectors.types import (
    ApiKeyResult,
    Balance,
    Instrument,
    KeyScheme,
    Order,
    OrderRequest,
    Page,
    Position,
    TransferResult,
    WalletType,
)

Credential = dict[str, Any]


class Capability(StrEnum):
    INSTRUMENTS = "instruments"
    REFERENCE_PRICES = "reference_prices"
    BALANCES = "balances"
    POSITIONS = "positions"
    ORDERS = "orders"
    TRADE_HISTORY = "trade_history"
    ORDER_HISTORY = "order_history"
    SUBACCOUNTS = "subaccounts"
    API_KEYS = "api_keys"
    TRANSFERS = "transfers"
    WITHDRAWALS = "withdrawals"


class NotSupported(Exception):
    def __init__(self, exchange: str, capability: Capability):
        self.exchange = exchange
        self.capability = capability
        super().__init__(f"{exchange} connector does not support {capability.value}")


class ExchangeConnector(ABC):
    """Base class for one exchange integration."""

    exchange: str = ""
    capabilities: frozenset[Capability] = frozenset()

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def require(self, capability: Capability) -> None:
        if not self.supports(capability):
            raise NotSupported(self.exchange, capability)

    # -- public market data -------------------------------------------------

    @abstractmethod
    async def fetch_instruments(self) -> list[Instrument]:
        """All tradable instruments, normalized to universal symbols."""

    @abstractmethod
    async def fetch_reference_prices(self) -> dict[str, Any]:
        """Current reference price per universal symbol."""

    # -- account state ------------------------------------------------------

    @abstractmethod
    async def fetch_balances(self, credential: Credential) -> list[Balance]: ...

    @abstractmethod
    async def fetch_positions(self, credential: Credential) -> list[Position]: ...

    # -- orders ---------------------------------------------------------

    @abstractmethod
    async def place_order(self, credential: Credential, request: OrderRequest) -> Order: ...

    @abstractmethod
    async def cancel_order(
        self, credential: Credential, universal_symbol: str, native_order_id: str
    ) -> Order: ...

    @abstractmethod
    async def fetch_open_orders(
        self, credential: Credential, universal_symbol: str | None = None
    ) -> list[Order]: ...

    @abstractmethod
    async def fetch_order(
        self,
        credential: Credential,
        universal_symbol: str,
        native_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> Order | None:
        """Look up one order — client_order_id lookup enables idempotent recovery."""

    # -- history (cursor-based, strategy owned by the connector) -------------

    @abstractmethod
    async def fetch_trades(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        """Next page of trades for one stream (per-symbol or account-wide)."""

    @abstractmethod
    async def fetch_orders_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page: ...

    def history_stream_keys(self, active_symbols: list[str]) -> list[str]:
        """Streams to sync for an account.

        Account-wide venues (Bybit-style) return a single stream; per-symbol
        venues (Binance-style) override this to return one per active symbol.
        """
        return ["account"]

    # -- provisioning ---------------------------------------------------

    @abstractmethod
    async def create_subaccount(self, credential: Credential, label: str) -> str:
        """Create a subaccount, returning its native identifier."""

    @abstractmethod
    async def create_api_key(
        self, credential: Credential, subaccount: str | None, scheme: KeyScheme,
        permissions: list[str],
    ) -> ApiKeyResult: ...

    @abstractmethod
    async def revoke_api_key(
        self, credential: Credential, subaccount: str | None, api_key: str
    ) -> None: ...

    # -- transfers ------------------------------------------------------

    @abstractmethod
    async def transfer(
        self,
        credential: Credential,
        asset: str,
        amount: Any,
        from_wallet: WalletType | None = None,
        to_wallet: WalletType | None = None,
        from_subaccount: str | None = None,
        to_subaccount: str | None = None,
    ) -> TransferResult: ...
