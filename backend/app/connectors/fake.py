"""In-memory fake exchange used by tests and local development.

Implements the full connector surface with deterministic behavior: orders
rest until ``fill_order`` is called, history is whatever was traded, and
provisioning fabricates keys. No network, no persistence.
"""
import itertools
from datetime import datetime, timezone
from decimal import Decimal

from app.connectors.base import Capability, Credential, ExchangeConnector
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
    Page,
    Position,
    SubaccountInfo,
    Trade,
    TransferResult,
    WalletType,
    Withdrawal,
    WithdrawalStatus,
)

_DEFAULT_INSTRUMENTS = [
    Instrument(
        universal_symbol="FAKE_SPOT_BTCUSDT",
        exchange="FAKE",
        market_type=MarketType.SPOT,
        native_symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        filters=InstrumentFilters(
            price_tick=Decimal("0.01"),
            qty_step=Decimal("0.00001"),
            min_qty=Decimal("0.00001"),
            min_notional=Decimal("5"),
        ),
    ),
    Instrument(
        universal_symbol="FAKE_PERP_ETHUSDT",
        exchange="FAKE",
        market_type=MarketType.PERP,
        native_symbol="ETHUSDT",
        base_asset="ETH",
        quote_asset="USDT",
        filters=InstrumentFilters(
            price_tick=Decimal("0.01"),
            qty_step=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("5"),
        ),
        settle_asset="USDT",
    ),
]


class FakeConnector(ExchangeConnector):
    exchange = "FAKE"
    capabilities = frozenset(Capability)
    wallets = (WalletType.SPOT, WalletType.FUNDING, WalletType.FUTURES)

    def __init__(self):
        self._ids = itertools.count(1)
        self.instruments: list[Instrument] = list(_DEFAULT_INSTRUMENTS)
        self.prices: dict[str, Decimal] = {
            "FAKE_SPOT_BTCUSDT": Decimal("60000"),
            "FAKE_PERP_ETHUSDT": Decimal("3000"),
        }
        self.balances: list[Balance] = [
            Balance(wallet=WalletType.SPOT, asset="USDT", free=Decimal("10000"), locked=Decimal("0")),
        ]
        self.positions: list[Position] = []
        self.orders: dict[str, Order] = {}
        self.trades: list[Trade] = []
        self.subaccounts: list[str] = []
        self.revoked_keys: list[str] = []
        self.created_keys: list[dict] = []
        self.modified_keys: list[dict] = []
        self.frozen: dict[str, bool] = {}
        self.transfers: list[dict] = []
        # Cross-exchange plumbing. Withdrawals rest as PENDING until the
        # complete_withdrawal test hook runs; deposits appear only via the
        # credit_deposit hook.
        self.networks: dict[str, list[AssetNetwork]] = {}
        self.withdrawals: dict[str, Withdrawal] = {}
        self.deposits: list[Deposit] = []
        self.transfer_failure: Exception | None = None  # raised by next transfer()

    # -- public market data --------------------------------------------------

    async def fetch_instruments(self) -> list[Instrument]:
        return list(self.instruments)

    async def fetch_reference_prices(self) -> dict[str, Decimal]:
        return dict(self.prices)

    # -- account state --------------------------------------------------------

    async def fetch_balances(self, credential: Credential) -> list[Balance]:
        return list(self.balances)

    async def fetch_positions(self, credential: Credential) -> list[Position]:
        return list(self.positions)

    # -- orders ----------------------------------------------------------

    async def place_order(self, credential: Credential, request: OrderRequest) -> Order:
        order = Order(
            universal_symbol=request.universal_symbol,
            native_order_id=str(next(self._ids)),
            client_order_id=request.client_order_id,
            side=request.side,
            type=request.type,
            status=OrderStatus.OPEN,
            qty=request.qty,
            filled_qty=Decimal("0"),
            price=request.price,
            created_at=datetime.now(timezone.utc),
        )
        self.orders[order.native_order_id] = order
        return order

    async def cancel_order(
        self, credential: Credential, universal_symbol: str, native_order_id: str
    ) -> Order:
        order = self.orders[native_order_id]
        cancelled = Order(
            **{**order.__dict__, "status": OrderStatus.CANCELLED,
               "updated_at": datetime.now(timezone.utc)}
        )
        self.orders[native_order_id] = cancelled
        return cancelled

    async def fetch_open_orders(
        self, credential: Credential, universal_symbol: str | None = None
    ) -> list[Order]:
        open_states = {OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED}
        return [
            o for o in self.orders.values()
            if o.status in open_states
            and (universal_symbol is None or o.universal_symbol == universal_symbol)
        ]

    async def fetch_order(
        self,
        credential: Credential,
        universal_symbol: str,
        native_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> Order | None:
        if native_order_id is not None:
            return self.orders.get(native_order_id)
        for order in self.orders.values():
            if order.client_order_id == client_order_id:
                return order
        return None

    def fill_order(self, native_order_id: str) -> Trade:
        """Test hook: fully fill a resting order and record its trade."""
        order = self.orders[native_order_id]
        filled = Order(
            **{**order.__dict__, "status": OrderStatus.FILLED, "filled_qty": order.qty,
               "updated_at": datetime.now(timezone.utc)}
        )
        self.orders[native_order_id] = filled
        trade = Trade(
            universal_symbol=order.universal_symbol,
            native_trade_id=str(next(self._ids)),
            native_order_id=native_order_id,
            side=order.side,
            price=order.price or self.prices[order.universal_symbol],
            qty=order.qty,
            fee=(order.price or Decimal("0")) * order.qty * Decimal("0.001"),
            fee_asset="USDT",
            traded_at=datetime.now(timezone.utc),
        )
        self.trades.append(trade)
        return trade

    # -- history ----------------------------------------------------------

    def initial_history_cursor(self, start) -> str | None:
        # Fake cursors are list indexes, not timestamps; a new stream simply
        # starts at the beginning of the in-memory history.
        return None

    async def fetch_trades(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        start = int(cursor) if cursor else 0
        items = self.trades[start:start + 100]
        end = start + len(items)
        return Page(items=items, next_cursor=str(end) if end < len(self.trades) else None)

    async def fetch_orders_history(
        self, credential: Credential, stream_key: str, cursor: str | None
    ) -> Page:
        orders = sorted(self.orders.values(), key=lambda o: int(o.native_order_id))
        start = int(cursor) if cursor else 0
        items = orders[start:start + 100]
        end = start + len(items)
        return Page(items=items, next_cursor=str(end) if end < len(orders) else None)

    # -- accounts and provisioning -----------------------------------------

    async def verify_credential(self, credential: Credential) -> CredentialInfo:
        valid = not str(credential.get("secret", "")).startswith("wrong")
        return CredentialInfo(
            valid=valid,
            native_uid="uid-1" if valid else None,
            is_master=True,
            read_only=False,
            can_trade=True,
            can_withdraw=True,
            permissions={"note": "fake"} if valid else None,
        )

    async def list_subaccounts(self, credential: Credential) -> list[SubaccountInfo]:
        return [
            SubaccountInfo(native_uid=uid, label=uid.split("-", 2)[-1], status="normal")
            for uid in self.subaccounts
        ]

    async def create_subaccount(self, credential: Credential, label: str) -> SubaccountInfo:
        native_id = f"sub-{next(self._ids)}-{label}"
        self.subaccounts.append(native_id)
        return SubaccountInfo(native_uid=native_id, label=label, status="normal")

    async def create_api_key(
        self, credential: Credential, subaccount: str | None, scheme: KeyScheme,
        permissions: list[str],
        read_only: bool = False,
        ips: list[str] | None = None,
        note: str | None = None,
    ) -> ApiKeyResult:
        n = next(self._ids)
        self.created_keys.append(
            {
                "subaccount": subaccount,
                "permissions": permissions,
                "read_only": read_only,
                "ips": ips,
                "note": note,
            }
        )
        return ApiKeyResult(
            api_key=f"fake-key-{n}",
            secret=f"fake-secret-{n}",
            scheme=scheme,
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
        self.modified_keys.append(
            {"api_key": api_key, "read_only": read_only, "permissions": permissions}
        )

    async def revoke_api_key(
        self, credential: Credential, subaccount: str | None, api_key: str
    ) -> None:
        self.revoked_keys.append(api_key)

    async def set_subaccount_frozen(
        self, credential: Credential, subaccount: str, frozen: bool
    ) -> None:
        self.frozen[subaccount] = frozen

    # -- transfers ------------------------------------------------------

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
        if self.transfer_failure is not None:
            failure, self.transfer_failure = self.transfer_failure, None
            raise failure
        record = {
            "asset": asset, "amount": amount,
            "from_wallet": from_wallet, "to_wallet": to_wallet,
            "from_subaccount": from_subaccount, "to_subaccount": to_subaccount,
        }
        self.transfers.append(record)
        return TransferResult(native_transfer_id=str(next(self._ids)), status="confirmed")

    # -- withdrawals / deposits -------------------------------------------

    async def fetch_asset_networks(
        self, credential: Credential, asset: str
    ) -> list[AssetNetwork]:
        return self.networks.get(
            asset,
            [
                AssetNetwork(
                    network="TESTNET",
                    deposit_enabled=True,
                    withdraw_enabled=True,
                    withdraw_min=Decimal("1"),
                    withdraw_fee=Decimal("0.5"),
                )
            ],
        )

    async def fetch_deposit_address(
        self,
        credential: Credential,
        asset: str,
        network: str,
        subaccount: str | None = None,
    ) -> DepositAddress:
        suffix = subaccount or "self"
        return DepositAddress(
            asset=asset, network=network, address=f"fake-{asset}-{network}-{suffix}"
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
        withdrawal = Withdrawal(
            native_id=f"wd-{next(self._ids)}",
            asset=asset,
            amount=Decimal(str(amount)),
            status=WithdrawalStatus.PENDING,
            address=address,
            network=network,
        )
        self.withdrawals[withdrawal.native_id] = withdrawal
        return withdrawal

    async def fetch_withdrawal(
        self, credential: Credential, native_id: str
    ) -> Withdrawal | None:
        return self.withdrawals.get(native_id)

    async def fetch_deposits(
        self,
        credential: Credential,
        asset: str | None = None,
        since: datetime | None = None,
        subaccount: str | None = None,
    ) -> list[Deposit]:
        return [d for d in self.deposits if asset is None or d.asset == asset]

    def complete_withdrawal(
        self, native_id: str, tx_id: str = "0xfaketx", fee: Decimal = Decimal("0.5")
    ) -> Withdrawal:
        """Test hook: the venue broadcast the withdrawal."""
        old = self.withdrawals[native_id]
        done = Withdrawal(
            **{**old.__dict__, "status": WithdrawalStatus.COMPLETED,
               "tx_id": tx_id, "fee": fee}
        )
        self.withdrawals[native_id] = done
        return done

    def credit_deposit(
        self,
        asset: str,
        amount: Decimal,
        network: str = "TESTNET",
        tx_id: str | None = None,
    ) -> Deposit:
        """Test hook: a deposit arrived and credited on the venue."""
        deposit = Deposit(
            native_id=f"dep-{next(self._ids)}",
            asset=asset,
            amount=amount,
            status=DepositStatus.CREDITED,
            network=network,
            tx_id=tx_id,
            credited_at=datetime.now(timezone.utc),
        )
        self.deposits.append(deposit)
        return deposit
