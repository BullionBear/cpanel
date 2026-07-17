"""Bybit normalization tests with fixtures shaped like the v5 API docs."""
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.connectors.base import DEFAULT_HISTORY_BACKFILL
from app.connectors.bybit import (
    BybitConnector,
    normalize_asset_networks,
    normalize_deposit_record,
    normalize_fund_balances,
    normalize_instrument,
    normalize_positions,
    normalize_trade,
    normalize_wallet_balances,
    normalize_withdrawal_record,
)
from app.connectors.types import (
    DepositStatus,
    MarketType,
    OrderSide,
    WalletType,
    WithdrawalStatus,
)

SPOT_BTCUSDT = {
    "symbol": "BTCUSDT",
    "baseCoin": "BTC",
    "quoteCoin": "USDT",
    "status": "Trading",
    "lotSizeFilter": {
        "basePrecision": "0.000001",
        "quotePrecision": "0.00000001",
        "minOrderQty": "0.000048",
        "maxOrderQty": "71.73956243",
        "minOrderAmt": "1",
        "maxOrderAmt": "4000000",
    },
    "priceFilter": {"tickSize": "0.01"},
}

LINEAR_ETHUSDT = {
    "symbol": "ETHUSDT",
    "contractType": "LinearPerpetual",
    "status": "Trading",
    "baseCoin": "ETH",
    "quoteCoin": "USDT",
    "settleCoin": "USDT",
    "priceFilter": {"minPrice": "0.01", "maxPrice": "19999.98", "tickSize": "0.01"},
    "lotSizeFilter": {
        "maxOrderQty": "7240",
        "minOrderQty": "0.01",
        "qtyStep": "0.01",
        "minNotionalValue": "5",
    },
}

LINEAR_ETH_FUTURES = {**LINEAR_ETHUSDT, "symbol": "ETHUSDT-26SEP26", "contractType": "LinearFutures"}

INVERSE_BTCUSD = {
    "symbol": "BTCUSD",
    "contractType": "InversePerpetual",
    "status": "Trading",
    "baseCoin": "BTC",
    "quoteCoin": "USD",
    "settleCoin": "BTC",
    "priceFilter": {"minPrice": "0.10", "maxPrice": "199999.80", "tickSize": "0.10"},
    "lotSizeFilter": {"maxOrderQty": "1000000", "minOrderQty": "1", "qtyStep": "1"},
}

INVERSE_BTC_FUTURES = {**INVERSE_BTCUSD, "symbol": "BTCUSDH26", "contractType": "InverseFutures"}


def test_normalize_spot():
    inst = normalize_instrument(SPOT_BTCUSDT, MarketType.SPOT)
    assert inst.universal_symbol == "BYBIT_SPOT_BTCUSDT"
    assert inst.filters.qty_step == Decimal("0.000001")  # basePrecision
    assert inst.filters.min_notional == Decimal("1")     # minOrderAmt
    assert inst.settle_asset is None
    assert inst.status == "active"


def test_normalize_linear_perp():
    inst = normalize_instrument(LINEAR_ETHUSDT, MarketType.PERP)
    assert inst.universal_symbol == "BYBIT_PERP_ETHUSDT"
    assert inst.native_symbol == "ETHUSDT"
    assert inst.filters.qty_step == Decimal("0.01")
    assert inst.filters.min_notional == Decimal("5")
    assert inst.settle_asset == "USDT"


def test_linear_dated_futures_skipped():
    assert normalize_instrument(LINEAR_ETH_FUTURES, MarketType.PERP) is None


def test_normalize_inverse_perp():
    inst = normalize_instrument(INVERSE_BTCUSD, MarketType.CMARGIN)
    assert inst.universal_symbol == "BYBIT_CMARGIN_BTCUSD"
    assert inst.settle_asset == "BTC"
    assert inst.filters.price_tick == Decimal("0.10")


def test_inverse_dated_futures_skipped():
    assert normalize_instrument(INVERSE_BTC_FUTURES, MarketType.CMARGIN) is None


def test_non_trading_status_is_halted():
    prelaunch = {**LINEAR_ETHUSDT, "status": "PreLaunch"}
    assert normalize_instrument(prelaunch, MarketType.PERP).status == "halted"


@pytest.fixture
def connector(monkeypatch):
    c = BybitConnector()
    pages = {
        "spot": [SPOT_BTCUSDT],
        "linear": [LINEAR_ETHUSDT, LINEAR_ETH_FUTURES],
        "inverse": [INVERSE_BTCUSD, INVERSE_BTC_FUTURES],
    }
    tickers = {
        "spot": [{"symbol": "BTCUSDT", "lastPrice": "62600.5"}],
        "linear": [{"symbol": "ETHUSDT", "lastPrice": "3301"}, {"symbol": "ETHUSDT-26SEP26", "lastPrice": "3400"}],
        "inverse": [{"symbol": "BTCUSD", "lastPrice": "62601"}],
    }

    async def fetch_instruments(category):
        return pages[category]

    async def fetch_tickers(category):
        return tickers[category]

    monkeypatch.setattr(c, "_fetch_category_instruments", fetch_instruments)
    monkeypatch.setattr(c, "_fetch_category_tickers", fetch_tickers)
    return c


async def test_fetch_instruments_across_categories(connector):
    instruments = await connector.fetch_instruments()
    assert {i.universal_symbol for i in instruments} == {
        "BYBIT_SPOT_BTCUSDT",
        "BYBIT_PERP_ETHUSDT",
        "BYBIT_CMARGIN_BTCUSD",
    }


async def test_reference_prices(connector):
    prices = await connector.fetch_reference_prices()
    assert prices == {
        "BYBIT_SPOT_BTCUSDT": Decimal("62600.5"),
        "BYBIT_PERP_ETHUSDT": Decimal("3301"),
        "BYBIT_CMARGIN_BTCUSD": Decimal("62601"),
    }


# -- balances / positions ----------------------------------------------------

WALLET_COINS = [
    {"coin": "USDT", "walletBalance": "10000", "locked": "500"},
    {"coin": "BTC", "walletBalance": "0.25", "locked": "0"},
    {"coin": "DUST", "walletBalance": "0", "locked": "0"},
]

LINEAR_POSITIONS = [
    {
        "symbol": "ETHUSDT",
        "side": "Buy",
        "size": "2",
        "avgPrice": "3000",
        "markPrice": "3100",
        "unrealisedPnl": "200",
        "positionIM": "600",
    },
    {"symbol": "ETHUSDT", "side": "", "size": "0", "avgPrice": "0"},
]
INVERSE_POSITIONS = [
    {
        "symbol": "BTCUSD",
        "side": "Sell",
        "size": "1000",
        "avgPrice": "60000",
        "markPrice": "59000",
        "unrealisedPnl": "5",
    }
]


def test_normalize_wallet_balances_drops_zero_and_splits_locked():
    balances = normalize_wallet_balances(WALLET_COINS)
    assert {b.asset for b in balances} == {"USDT", "BTC"}
    usdt = next(b for b in balances if b.asset == "USDT")
    assert usdt.wallet == WalletType.UNIFIED
    assert usdt.locked == Decimal("500")
    assert usdt.free == Decimal("9500")
    assert usdt.total == Decimal("10000")


def test_normalize_fund_balances_locks_committed_amounts():
    balances = normalize_fund_balances(
        [
            {"coin": "USDT", "walletBalance": "100", "transferBalance": "80"},
            {"coin": "BTC", "walletBalance": "0", "transferBalance": "0"},
        ]
    )
    assert len(balances) == 1
    assert balances[0].wallet == WalletType.FUNDING
    assert balances[0].free == Decimal("80")
    assert balances[0].locked == Decimal("20")


def test_normalize_positions_linear_and_inverse():
    native_map = {
        (MarketType.PERP, "ETHUSDT"): "BYBIT_PERP_ETHUSDT",
        (MarketType.CMARGIN, "BTCUSD"): "BYBIT_CMARGIN_BTCUSD",
    }
    positions = normalize_positions(LINEAR_POSITIONS + INVERSE_POSITIONS, native_map)
    assert {p.universal_symbol for p in positions} == {
        "BYBIT_PERP_ETHUSDT",
        "BYBIT_CMARGIN_BTCUSD",
    }
    eth = next(p for p in positions if p.universal_symbol == "BYBIT_PERP_ETHUSDT")
    assert eth.side == "long" and eth.size == Decimal("2")
    btc = next(p for p in positions if p.universal_symbol == "BYBIT_CMARGIN_BTCUSD")
    assert btc.side == "short"


def test_positions_skip_untracked_symbols():
    assert normalize_positions(LINEAR_POSITIONS, {}) == []


async def test_fetch_balances(connector, monkeypatch):
    async def wallet_coins(cred):
        return WALLET_COINS

    async def fund_coins(cred):
        return [{"coin": "USDT", "walletBalance": "50", "transferBalance": "50"}]

    monkeypatch.setattr(connector, "_fetch_wallet_coins", wallet_coins)
    monkeypatch.setattr(connector, "_fetch_fund_coins", fund_coins)
    balances = await connector.fetch_balances({"api_key": "k", "secret": "s"})
    assert {b.asset for b in balances} == {"USDT", "BTC"}
    assert {b.wallet for b in balances} == {WalletType.UNIFIED, WalletType.FUNDING}


async def test_fetch_positions(connector, monkeypatch):
    async def positions(cred, category, settle_coin=None):
        if category == "linear" and settle_coin == "USDT":
            return LINEAR_POSITIONS
        if category == "inverse":
            return INVERSE_POSITIONS
        return []

    monkeypatch.setattr(connector, "_fetch_positions", positions)
    result = await connector.fetch_positions({"api_key": "k", "secret": "s"})
    assert {p.universal_symbol for p in result} == {
        "BYBIT_PERP_ETHUSDT",
        "BYBIT_CMARGIN_BTCUSD",
    }


async def test_cursor_pagination_walked(monkeypatch):
    c = BybitConnector()
    calls = []

    async def fake_call(func, **kwargs):
        calls.append(kwargs)
        if kwargs.get("cursor") is None:
            return {"result": {"list": [SPOT_BTCUSDT], "nextPageCursor": "page2"}}
        return {"result": {"list": [{**SPOT_BTCUSDT, "symbol": "ETHUSDT", "baseCoin": "ETH"}], "nextPageCursor": ""}}

    monkeypatch.setattr(c, "_call", fake_call)
    items = await c._fetch_category_instruments("spot")
    assert len(items) == 2
    assert len(calls) == 2
    assert calls[1]["cursor"] == "page2"


# -- history ------------------------------------------------------------------

EXECUTION = {
    "symbol": "ETHUSDT",
    "orderId": "order-1",
    "execId": "exec-1",
    "side": "Sell",
    "execPrice": "3100.5",
    "execQty": "0.5",
    "execFee": "0.093",
    "feeCurrency": "USDT",
    "execTime": "1752345600000",
}

ORDER_ROW = {
    "symbol": "ETHUSDT",
    "orderId": "order-1",
    "orderLinkId": "cpanel-abc",
    "side": "Sell",
    "orderType": "Limit",
    "orderStatus": "Filled",
    "qty": "0.5",
    "cumExecQty": "0.5",
    "price": "3100.5",
    "createdTime": "1752345500000",
    "updatedTime": "1752345600000",
}


def test_normalize_trade():
    trade = normalize_trade(EXECUTION, "BYBIT_PERP_ETHUSDT")
    assert trade.side == OrderSide.SELL
    assert trade.native_trade_id == "exec-1"
    assert trade.native_order_id == "order-1"
    assert trade.price == Decimal("3100.5")
    assert trade.fee == Decimal("0.093")
    assert trade.fee_asset == "USDT"
    assert trade.traded_at == datetime.fromtimestamp(1752345600, tz=timezone.utc)


def test_history_stream_keys_are_categories():
    assert BybitConnector().history_stream_keys(["ignored"]) == ["spot", "linear", "inverse"]


def test_initial_history_cursor_encodes_window_start():
    start = datetime(2026, 7, 12, tzinfo=timezone.utc)
    cursor = BybitConnector().initial_history_cursor(start)
    assert json.loads(cursor) == {"start": int(start.timestamp() * 1000)}


async def test_fetch_trades_walks_native_pages_then_window(connector, monkeypatch):
    calls = []

    async def history_page(credential, method, category, start, end, limit, native):
        calls.append((method, category, start, end, limit, native))
        if native is None:
            return [EXECUTION], "tok-2"
        return [dict(EXECUTION, execId="exec-2")], None

    monkeypatch.setattr(connector, "_fetch_history_page", history_page)
    cred = {"api_key": "k", "secret": "s"}

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - 2 * 24 * 60 * 60 * 1000
    cursor = json.dumps({"start": start_ms})

    # Page 1: venue returns a native token → cursor keeps the window, adds token.
    page1 = await connector.fetch_trades(cred, "linear", cursor)
    assert page1.items[0].universal_symbol == "BYBIT_PERP_ETHUSDT"
    assert json.loads(page1.next_cursor) == {"start": start_ms, "cursor": "tok-2"}
    assert calls[0][:2] == ("get_executions", "linear")
    assert calls[0][4] == 100  # executions page cap

    # Page 2: token exhausted → the window slides forward 24h.
    page2 = await connector.fetch_trades(cred, "linear", page1.next_cursor)
    assert calls[1][5] == "tok-2"
    assert json.loads(page2.next_cursor) == {"start": start_ms + 24 * 60 * 60 * 1000}


async def test_fetch_trades_caught_up(connector, monkeypatch):
    async def history_page(credential, method, category, start, end, limit, native):
        return [], None

    monkeypatch.setattr(connector, "_fetch_history_page", history_page)
    recent = int(datetime.now(timezone.utc).timestamp() * 1000) - 60_000
    page = await connector.fetch_trades(
        {"api_key": "k", "secret": "s"}, "linear", json.dumps({"start": recent})
    )
    assert page.items == []
    assert page.next_cursor is None


async def test_fetch_trades_defaults_to_three_day_backfill(connector, monkeypatch):
    starts = []

    async def history_page(credential, method, category, start, end, limit, native):
        starts.append(start)
        return [], None

    monkeypatch.setattr(connector, "_fetch_history_page", history_page)
    before = datetime.now(timezone.utc)
    await connector.fetch_trades({"api_key": "k", "secret": "s"}, "linear", None)
    expected = int((before - DEFAULT_HISTORY_BACKFILL).timestamp() * 1000)
    assert abs(starts[0] - expected) < 60_000


async def test_untracked_symbols_skipped(connector, monkeypatch):
    async def history_page(credential, method, category, start, end, limit, native):
        return [EXECUTION, dict(EXECUTION, symbol="ETHUSDT-26SEP26", execId="x")], None

    monkeypatch.setattr(connector, "_fetch_history_page", history_page)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    page = await connector.fetch_trades(
        {"api_key": "k", "secret": "s"}, "linear", json.dumps({"start": now_ms - 60_000})
    )
    assert [t.native_trade_id for t in page.items] == ["exec-1"]


async def test_fetch_orders_history_uses_order_endpoint(connector, monkeypatch):
    calls = []

    async def history_page(credential, method, category, start, end, limit, native):
        calls.append((method, limit))
        return [ORDER_ROW], None

    monkeypatch.setattr(connector, "_fetch_history_page", history_page)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    page = await connector.fetch_orders_history(
        {"api_key": "k", "secret": "s"}, "linear", json.dumps({"start": now_ms - 60_000})
    )
    assert calls[0] == ("get_order_history", 50)
    order = page.items[0]
    assert order.universal_symbol == "BYBIT_PERP_ETHUSDT"
    assert order.client_order_id == "cpanel-abc"
    assert order.status.value == "FILLED"


# -- transfers / withdrawals / deposits ---------------------------------------

COIN_INFO_ROW = {
    "coin": "USDT",
    "chains": [
        {
            "chain": "TRX",
            "chainType": "TRC20",
            "chainDeposit": "1",
            "chainWithdraw": "1",
            "withdrawMin": "10",
            "withdrawFee": "1",
        },
        {
            "chain": "ETH",
            "chainType": "ERC20",
            "chainDeposit": "1",
            "chainWithdraw": "0",
            "withdrawMin": "20",
            "withdrawFee": "4",
        },
    ],
}


def test_normalize_asset_networks():
    networks = normalize_asset_networks(COIN_INFO_ROW)
    trx = next(n for n in networks if n.network == "TRX")
    assert trx.withdraw_enabled and trx.deposit_enabled
    assert trx.withdraw_min == Decimal("10")
    assert trx.withdraw_fee == Decimal("1")
    assert next(n for n in networks if n.network == "ETH").withdraw_enabled is False


def test_normalize_withdrawal_record_statuses():
    base = {
        "withdrawId": "10195", "coin": "USDT", "amount": "100",
        "toAddress": "TAbc", "chain": "TRX", "withdrawFee": "1",
    }
    assert normalize_withdrawal_record({**base, "status": "success"}).status \
        == WithdrawalStatus.COMPLETED
    sent = normalize_withdrawal_record(
        {**base, "status": "BlockchainConfirmed", "txID": "0xabc"}
    )
    assert sent.status == WithdrawalStatus.SENT
    assert sent.tx_id == "0xabc"
    assert sent.fee == Decimal("1")
    assert normalize_withdrawal_record({**base, "status": "Reject"}).status \
        == WithdrawalStatus.FAILED
    assert normalize_withdrawal_record({**base, "status": "SecurityCheck"}).status \
        == WithdrawalStatus.PENDING


def test_normalize_deposit_record_statuses():
    base = {"coin": "USDT", "amount": "99", "chain": "TRX", "txID": "0xabc"}
    credited = normalize_deposit_record(
        {**base, "status": 3, "successAt": "1700000000000"}
    )
    assert credited.status == DepositStatus.CREDITED
    assert credited.native_id == "0xabc"
    assert credited.credited_at is not None
    assert normalize_deposit_record({**base, "status": 2}).status == DepositStatus.PENDING
    assert normalize_deposit_record({**base, "status": 4}).status == DepositStatus.FAILED


async def test_internal_transfer_between_wallets(monkeypatch):
    c = BybitConnector()
    calls = []

    async def call(func, **kwargs):
        calls.append((func.__name__, kwargs))
        return {"result": {"transferId": "uuid-1", "status": "SUCCESS"}}

    monkeypatch.setattr(c, "_call", call)
    result = await c.transfer(
        {"api_key": "k", "secret": "s"}, "USDT", Decimal("100"),
        from_wallet=WalletType.FUNDING, to_wallet=WalletType.UNIFIED,
    )
    name, kwargs = calls[0]
    assert name == "create_internal_transfer"
    assert kwargs["fromAccountType"] == "FUND"
    assert kwargs["toAccountType"] == "UNIFIED"
    assert kwargs["amount"] == "100"
    assert result.status == "confirmed"
    assert result.native_transfer_id == "uuid-1"


async def test_internal_transfer_same_wallet_rejected():
    c = BybitConnector()
    with pytest.raises(ValueError):
        await c.transfer(
            {"api_key": "k", "secret": "s"}, "USDT", Decimal("1"),
            from_wallet=WalletType.UNIFIED, to_wallet=WalletType.UNIFIED,
        )


async def test_transfer_rejects_foreign_wallet_type():
    # Bybit's UTA has no SPOT wallet — only UNIFIED and FUNDING exist.
    c = BybitConnector()
    with pytest.raises(ValueError):
        await c.transfer(
            {"api_key": "k", "secret": "s"}, "USDT", Decimal("1"),
            from_wallet=WalletType.SPOT, to_wallet=WalletType.UNIFIED,
        )


async def test_universal_transfer_resolves_master_uid(monkeypatch):
    c = BybitConnector()
    calls = []

    async def call(func, **kwargs):
        calls.append((func.__name__, kwargs))
        if func.__name__ == "get_api_key_information":
            return {"result": {"userID": 111}}
        return {"result": {"transferId": "uuid-2", "status": "PENDING"}}

    monkeypatch.setattr(c, "_call", call)
    result = await c.transfer(
        {"api_key": "k", "secret": "s"}, "USDT", Decimal("5"),
        to_subaccount="222",
    )
    name, kwargs = calls[-1]
    assert name == "create_universal_transfer"
    assert kwargs["fromMemberId"] == 111
    assert kwargs["toMemberId"] == 222
    assert result.status == "submitted"


async def test_withdraw_draws_from_fund_wallet(monkeypatch):
    c = BybitConnector()
    calls = []

    async def call(func, **kwargs):
        calls.append((func.__name__, kwargs))
        return {"result": {"id": "10195"}}

    monkeypatch.setattr(c, "_call", call)
    withdrawal = await c.withdraw(
        {"api_key": "k", "secret": "s"}, "USDT", Decimal("100"), "TAbc", "TRX"
    )
    name, kwargs = calls[0]
    assert name == "withdraw"
    assert kwargs["accountType"] == "FUND"
    assert kwargs["chain"] == "TRX"
    assert withdrawal.native_id == "10195"
    assert withdrawal.status == WithdrawalStatus.PENDING


async def test_fetch_deposit_address_reads_chain(monkeypatch):
    c = BybitConnector()

    async def raw(credential, asset, network, subaccount):
        return {
            "coin": "USDT",
            "chains": {"chainType": "TRC20", "chain": "TRX",
                       "addressDeposit": "TAbc123", "tagDeposit": ""},
        }

    monkeypatch.setattr(c, "_fetch_deposit_address_raw", raw)
    resolved = await c.fetch_deposit_address(
        {"api_key": "k", "secret": "s"}, "USDT", "TRX"
    )
    assert resolved.address == "TAbc123"
    assert resolved.tag is None
