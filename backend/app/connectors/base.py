"""Abstract exchange connector: the full method surface from ARCHITECTURE.md.

Concrete connectors (Binance, Bybit, …) live one module per exchange and are
the only place exchange SDKs are imported. Each connector declares its
capabilities and overrides only the methods it supports — every method
defaults to raising ``NotSupported``, so capability declarations and actual
behavior stay consistent. Callers check ``supports()`` (or catch
``NotSupported``) before offering an operation for a given venue.

Credentials are passed per call as an opaque ``credential`` mapping decrypted
by the caller at the last moment — connectors never store secrets.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.connectors.types import (
    ApiKeyResult,
    AssetNetwork,
    Balance,
    CredentialInfo,
    Deposit,
    DepositAddress,
    Instrument,
    KeyScheme,
    Order,
    OrderRequest,
    Page,
    Position,
    SubaccountInfo,
    TransferResult,
    WalletType,
    Withdrawal,
)

Credential = dict[str, Any]

# How far back a brand-new history stream starts. An account whose stream has
# no sync_cursor yet backfills this window first; from then on the stored
# cursor drives incremental sync.
DEFAULT_HISTORY_BACKFILL = timedelta(days=3)


class Capability(StrEnum):
    INSTRUMENTS = "instruments"
    REFERENCE_PRICES = "reference_prices"
    CREDENTIAL_VERIFY = "credential_verify"
    BALANCES = "balances"
    POSITIONS = "positions"
    ORDERS = "orders"
    TRADE_HISTORY = "trade_history"
    ORDER_HISTORY = "order_history"
    SUBACCOUNT_LIST = "subaccount_list"
    SUBACCOUNT_CREATE = "subaccount_create"
    SUBACCOUNT_FREEZE = "subaccount_freeze"
    API_KEY_CREATE = "api_key_create"
    API_KEY_MODIFY = "api_key_modify"
    API_KEY_REVOKE = "api_key_revoke"
    TRANSFERS = "transfers"
    WITHDRAWALS = "withdrawals"
    DEPOSITS = "deposits"


class NotSupported(Exception):
    def __init__(self, exchange: str, capability: Capability):
        self.exchange = exchange
        self.capability = capability
        super().__init__(f"{exchange} connector does not support {capability.value}")


class ExchangeConnector:
    """Base class for one exchange integration."""

    exchange: str = ""
    capabilities: frozenset[Capability] = frozenset()
    # The venue's wallet structure — which WalletType values exist on this
    # exchange (order = display order). Empty means "no wallet distinction".
    wallets: tuple[WalletType, ...] = ()

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def require(self, capability: Capability) -> None:
        if not self.supports(capability):
            raise NotSupported(self.exchange, capability)

    # -- public market data -------------------------------------------------

    async def fetch_instruments(self) -> list[Instrument]:
        """All tradable instruments, normalized to universal symbols."""
        raise NotSupported(self.exchange, Capability.INSTRUMENTS)

    async def fetch_reference_prices(self) -> dict[str, Decimal]:
        """Current reference price per universal symbol."""
        raise NotSupported(self.exchange, Capability.REFERENCE_PRICES)

    # -- account state ------------------------------------------------------

    async def fetch_balances(self, credential: Credential) -> list[Balance]:
        raise NotSupported(self.exchange, Capability.BALANCES)

    async def fetch_positions(self, credential: Credential) -> list[Position]:
        raise NotSupported(self.exchange, Capability.POSITIONS)

    # -- orders ---------------------------------------------------------

    async def place_order(self, credential: Credential, request: OrderRequest) -> Order:
        raise NotSupported(self.exchange, Capability.ORDERS)

    async def cancel_order(
        self, credential: Credential, universal_symbol: str, native_order_id: str
    ) -> Order:
        raise NotSupported(self.exchange, Capability.ORDERS)

    async def fetch_open_orders(
        self, credential: Credential, universal_symbol: str | None = None
    ) -> list[Order]:
        raise NotSupported(self.exchange, Capability.ORDERS)

    async def fetch_order(
        self,
        credential: Credential,
        universal_symbol: str,
        native_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> Order | None:
        """Look up one order — client_order_id lookup enables idempotent recovery."""
        raise NotSupported(self.exchange, Capability.ORDERS)

    # -- history (cursor-based, strategy owned by the connector) -------------

    async def fetch_trades(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        """Next page of trades for one stream (per-symbol or account-wide)."""
        raise NotSupported(self.exchange, Capability.TRADE_HISTORY)

    async def fetch_orders_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        raise NotSupported(self.exchange, Capability.ORDER_HISTORY)

    def history_stream_keys(self, active_symbols: list[str]) -> list[str]:
        """Streams to sync for an account.

        Account-wide venues (Bybit-style) return a single stream; per-symbol
        venues (Binance-style) override this to return one per active symbol.
        """
        return ["account"]

    def initial_history_cursor(self, start: datetime) -> str | None:
        """Cursor that starts a brand-new stream at ``start``.

        Cursors are opaque to callers, so the sync worker cannot build one
        itself when it seeds a new stream (a fresh account backfilling
        ``DEFAULT_HISTORY_BACKFILL`` into the past). Default is an epoch-ms
        timestamp, which both time-windowed venues understand; connectors
        with token cursors override this.
        """
        return str(int(start.timestamp() * 1000))

    # -- accounts and provisioning ----------------------------------------

    async def verify_credential(self, credential: Credential) -> CredentialInfo:
        """Check a credential against the venue and report its permissions."""
        raise NotSupported(self.exchange, Capability.CREDENTIAL_VERIFY)

    async def list_subaccounts(self, credential: Credential) -> list[SubaccountInfo]:
        """Subaccounts as the venue reports them (master credential required)."""
        raise NotSupported(self.exchange, Capability.SUBACCOUNT_LIST)

    async def create_subaccount(self, credential: Credential, label: str) -> SubaccountInfo:
        raise NotSupported(self.exchange, Capability.SUBACCOUNT_CREATE)

    async def set_subaccount_frozen(
        self, credential: Credential, subaccount: str, frozen: bool
    ) -> None:
        """Freeze or unfreeze a subaccount on the venue."""
        raise NotSupported(self.exchange, Capability.SUBACCOUNT_FREEZE)

    async def create_api_key(
        self, credential: Credential, subaccount: str | None, scheme: KeyScheme,
        permissions: list[str],
        read_only: bool = False,
        ips: list[str] | None = None,
        note: str | None = None,
    ) -> ApiKeyResult:
        """Mint an API key on the venue.

        ``permissions`` is a list of panel-level scope tokens (e.g. ``spot``,
        ``derivatives``, ``wallet``); each connector maps them to its own model.
        ``read_only`` marks the key as non-trading, ``ips`` restricts it to an
        allowlist (empty = unrestricted), and ``note`` is a free-form label.
        """
        raise NotSupported(self.exchange, Capability.API_KEY_CREATE)

    async def modify_api_key(
        self,
        credential: Credential,
        subaccount: str | None,
        api_key: str,
        read_only: bool | None = None,
        permissions: list[str] | None = None,
    ) -> None:
        """Change permissions / read-only flag of an existing key on the venue."""
        raise NotSupported(self.exchange, Capability.API_KEY_MODIFY)

    async def revoke_api_key(
        self, credential: Credential, subaccount: str | None, api_key: str
    ) -> None:
        raise NotSupported(self.exchange, Capability.API_KEY_REVOKE)

    # -- transfers ------------------------------------------------------

    async def transfer(
        self,
        credential: Credential,
        asset: str,
        amount: Any,
        from_wallet: WalletType | None = None,
        to_wallet: WalletType | None = None,
        from_subaccount: str | None = None,
        to_subaccount: str | None = None,
    ) -> TransferResult:
        """Intra-exchange move: wallet ↔ wallet and/or account ↔ account.

        ``from_subaccount`` / ``to_subaccount`` are venue-native uids; ``None``
        means "the account the credential belongs to" (typically the master
        when both sides are given, or a self wallet move when both are None).
        """
        raise NotSupported(self.exchange, Capability.TRANSFERS)

    # -- withdrawals / deposits (cross-exchange transfers) ----------------

    async def fetch_asset_networks(
        self, credential: Credential, asset: str
    ) -> list[AssetNetwork]:
        """Chains the asset can move over, with per-chain withdraw/deposit
        status, minimums, and fees — used to validate a cross-exchange
        transfer on both sides before any money moves."""
        raise NotSupported(self.exchange, Capability.WITHDRAWALS)

    async def fetch_deposit_address(
        self,
        credential: Credential,
        asset: str,
        network: str,
        subaccount: str | None = None,
    ) -> DepositAddress:
        """Deposit address for (asset, network). With ``subaccount`` set the
        credential must be the master's and the address is the sub's."""
        raise NotSupported(self.exchange, Capability.DEPOSITS)

    async def withdraw(
        self,
        credential: Credential,
        asset: str,
        amount: Any,
        address: str,
        network: str,
        tag: str | None = None,
    ) -> Withdrawal:
        """Submit an on-chain withdrawal. Returns at least the native id;
        status/tx are advanced by polling ``fetch_withdrawal``."""
        raise NotSupported(self.exchange, Capability.WITHDRAWALS)

    async def fetch_withdrawal(
        self, credential: Credential, native_id: str
    ) -> Withdrawal | None:
        """Current state of one withdrawal (None if the venue does not know it)."""
        raise NotSupported(self.exchange, Capability.WITHDRAWALS)

    async def fetch_deposits(
        self,
        credential: Credential,
        asset: str | None = None,
        since: datetime | None = None,
        subaccount: str | None = None,
    ) -> list[Deposit]:
        """Recent deposits, newest first — polled to match an incoming
        cross-exchange transfer. Same ``subaccount`` convention as
        ``fetch_deposit_address``."""
        raise NotSupported(self.exchange, Capability.DEPOSITS)
