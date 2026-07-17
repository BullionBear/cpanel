"""Gate.io normalization tests with fixtures shaped like the APIv4 docs."""
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.connectors.base import DEFAULT_HISTORY_BACKFILL
from app.connectors.gateio import (
    GateIOConnector,
    normalize_asset_networks,
    normalize_contract,
    normalize_deposit_record,
    normalize_funding_fee,
    normalize_futures_balance,
    normalize_futures_order,
    normalize_futures_trade,
    normalize_positions,
    normalize_spot_balances,
    normalize_spot_instrument,
    normalize_spot_order,
    normalize_spot_trade,
    normalize_withdrawal_record,
)
from app.connectors.types import (
    DepositStatus,
    KeyScheme,
    MarketType,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    WalletType,
    WithdrawalStatus,
)

SPOT_BTC_USDT = {
    "id": "BTC_USDT",
    "base": "BTC",
    "quote": "USDT",
    "fee": "0.2",
    "min_base_amount": "0.0001",
    "min_quote_amount": "1",
    "max_base_amount": "1000",
    "amount_precision": 4,
    "precision": 2,
    "trade_status": "tradable",
    "type": "normal",
}

SPOT_PREMARKET = {**SPOT_BTC_USDT, "id": "NEW_USDT", "base": "NEW", "type": "premarket"}

USDT_ETH_CONTRACT = {
    "name": "ETH_USDT",
    "type": "direct",
    "quanto_multiplier": "0.01",
    "order_price_round": "0.01",
    "order_size_min": 1,
    "order_size_max": 1000000,
    "in_delisting": False,
    "status": "trading",
}

BTC_INVERSE_CONTRACT = {
    "name": "BTC_USD",
    "type": "inverse",
    "quanto_multiplier": "0",
    "order_price_round": "0.1",
    "order_size_min": 1,
    "order_size_max": 1000000,
    "in_delisting": False,
    "status": "trading",
}


def test_normalize_spot_instrument():
    inst = normalize_spot_instrument(SPOT_BTC_USDT)
    assert inst.universal_symbol == "GATEIO_SPOT_BTCUSDT"
    assert inst.native_symbol == "BTC_USDT"
    assert inst.filters.price_tick == Decimal("0.01")     # precision 2
    assert inst.filters.qty_step == Decimal("0.0001")     # amount_precision 4
    assert inst.filters.min_qty == Decimal("0.0001")
    assert inst.filters.min_notional == Decimal("1")
    assert inst.status == "active"


def test_premarket_pairs_skipped():
    assert normalize_spot_instrument(SPOT_PREMARKET) is None


def test_untradable_pair_is_halted():
    halted = normalize_spot_instrument({**SPOT_BTC_USDT, "trade_status": "untradable"})
    assert halted.status == "halted"


def test_normalize_usdt_contract():
    inst = normalize_contract(USDT_ETH_CONTRACT, MarketType.PERP, "usdt")
    assert inst.universal_symbol == "GATEIO_PERP_ETHUSDT"
    assert inst.native_symbol == "ETH_USDT"
    assert inst.filters.qty_step == Decimal("1")          # contracts are integers
    assert inst.filters.price_tick == Decimal("0.01")
    assert inst.contract_size == Decimal("0.01")          # quanto_multiplier
    assert inst.settle_asset == "USDT"
    assert inst.status == "active"


def test_normalize_inverse_contract():
    inst = normalize_contract(BTC_INVERSE_CONTRACT, MarketType.CMARGIN, "btc")
    assert inst.universal_symbol == "GATEIO_CMARGIN_BTCUSD"
    assert inst.settle_asset == "BTC"
    assert inst.contract_size is None                     # multiplier "0"


def test_delisting_contract_states():
    delisting = normalize_contract(
        {**USDT_ETH_CONTRACT, "in_delisting": True}, MarketType.PERP, "usdt"
    )
    assert delisting.status == "halted"
    delisted = normalize_contract(
        {**USDT_ETH_CONTRACT, "status": "delisted"}, MarketType.PERP, "usdt"
    )
    assert delisted.status == "delisted"


@pytest.fixture
def connector(monkeypatch):
    c = GateIOConnector()

    async def spot_pairs():
        return [SPOT_BTC_USDT, SPOT_PREMARKET]

    async def contracts(settle):
        return {"usdt": [USDT_ETH_CONTRACT], "btc": [BTC_INVERSE_CONTRACT]}[settle]

    async def spot_tickers():
        return [{"currency_pair": "BTC_USDT", "last": "62600.5"}]

    async def futures_tickers(settle):
        return {
            "usdt": [{"contract": "ETH_USDT", "last": "3301"}],
            "btc": [{"contract": "BTC_USD", "last": "62601"}],
        }[settle]

    monkeypatch.setattr(c, "_fetch_spot_pairs", spot_pairs)
    monkeypatch.setattr(c, "_fetch_contracts", contracts)
    monkeypatch.setattr(c, "_fetch_spot_tickers", spot_tickers)
    monkeypatch.setattr(c, "_fetch_futures_tickers", futures_tickers)
    return c


async def test_fetch_instruments_across_surfaces(connector):
    instruments = await connector.fetch_instruments()
    assert {i.universal_symbol for i in instruments} == {
        "GATEIO_SPOT_BTCUSDT",
        "GATEIO_PERP_ETHUSDT",
        "GATEIO_CMARGIN_BTCUSD",
    }


async def test_reference_prices(connector):
    prices = await connector.fetch_reference_prices()
    assert prices == {
        "GATEIO_SPOT_BTCUSDT": Decimal("62600.5"),
        "GATEIO_PERP_ETHUSDT": Decimal("3301"),
        "GATEIO_CMARGIN_BTCUSD": Decimal("62601"),
    }


# -- balances / positions ------------------------------------------------------

SPOT_ACCOUNTS = [
    {"currency": "USDT", "available": "9500", "locked": "500"},
    {"currency": "BTC", "available": "0.25", "locked": "0"},
    {"currency": "DUST", "available": "0", "locked": "0"},
]

FUTURES_ACCOUNT = {"currency": "USDT", "total": "1000", "available": "800"}

USDT_POSITIONS = [
    {
        "contract": "ETH_USDT",
        "size": 2,
        "entry_price": "3000",
        "mark_price": "3100",
        "unrealised_pnl": "200",
        "margin": "600",
    },
    {"contract": "ETH_USDT", "size": 0, "entry_price": "0"},
]

BTC_POSITIONS = [
    {
        "contract": "BTC_USD",
        "size": -1000,
        "entry_price": "60000",
        "mark_price": "59000",
        "unrealised_pnl": "5",
    }
]


def test_normalize_spot_balances_drops_zero():
    balances = normalize_spot_balances(SPOT_ACCOUNTS)
    assert {b.asset for b in balances} == {"USDT", "BTC"}
    usdt = next(b for b in balances if b.asset == "USDT")
    assert usdt.wallet == WalletType.SPOT
    assert usdt.free == Decimal("9500")
    assert usdt.locked == Decimal("500")
    assert usdt.total == Decimal("10000")


def test_normalize_futures_balance_locks_margin():
    balances = normalize_futures_balance(FUTURES_ACCOUNT)
    assert len(balances) == 1
    assert balances[0].wallet == WalletType.FUTURES
    assert balances[0].free == Decimal("800")
    assert balances[0].locked == Decimal("200")
    assert normalize_futures_balance({}) == []
    assert normalize_futures_balance({"currency": "BTC", "total": "0", "available": "0"}) == []


def test_normalize_positions_signed_size():
    native_map = {
        (MarketType.PERP, "ETH_USDT"): "GATEIO_PERP_ETHUSDT",
        (MarketType.CMARGIN, "BTC_USD"): "GATEIO_CMARGIN_BTCUSD",
    }
    positions = normalize_positions(USDT_POSITIONS, MarketType.PERP, native_map)
    positions += normalize_positions(BTC_POSITIONS, MarketType.CMARGIN, native_map)
    assert len(positions) == 2
    eth = next(p for p in positions if p.universal_symbol == "GATEIO_PERP_ETHUSDT")
    assert eth.side == "long" and eth.size == Decimal("2")
    btc = next(p for p in positions if p.universal_symbol == "GATEIO_CMARGIN_BTCUSD")
    assert btc.side == "short" and btc.size == Decimal("1000")


def test_positions_skip_untracked_contracts():
    assert normalize_positions(USDT_POSITIONS, MarketType.PERP, {}) == []


async def test_fetch_balances(connector, monkeypatch):
    async def spot_balances(cred):
        return SPOT_ACCOUNTS

    async def futures_account(cred, settle):
        return FUTURES_ACCOUNT if settle == "usdt" else {}

    monkeypatch.setattr(connector, "_fetch_spot_balances", spot_balances)
    monkeypatch.setattr(connector, "_fetch_futures_account", futures_account)
    balances = await connector.fetch_balances({"api_key": "k", "secret": "s"})
    assert {(b.wallet, b.asset) for b in balances} == {
        (WalletType.SPOT, "USDT"),
        (WalletType.SPOT, "BTC"),
        (WalletType.FUTURES, "USDT"),
    }


async def test_fetch_positions(connector, monkeypatch):
    async def positions(cred, settle):
        return USDT_POSITIONS if settle == "usdt" else BTC_POSITIONS

    monkeypatch.setattr(connector, "_fetch_positions", positions)
    result = await connector.fetch_positions({"api_key": "k", "secret": "s"})
    assert {p.universal_symbol for p in result} == {
        "GATEIO_PERP_ETHUSDT",
        "GATEIO_CMARGIN_BTCUSD",
    }


# -- orders --------------------------------------------------------------------

SPOT_ORDER = {
    "id": "1852454420",
    "text": "t-cpanel-abc",
    "create_time": "1710488334",
    "update_time": "1710488334",
    "create_time_ms": 1710488334073,
    "update_time_ms": 1710488334074,
    "currency_pair": "BTC_USDT",
    "status": "closed",
    "type": "limit",
    "account": "spot",
    "side": "buy",
    "amount": "0.001",
    "price": "65000",
    "time_in_force": "gtc",
    "left": "0",
    "filled_amount": "0.001",
    "filled_total": "65",
    "fee": "0.0000002",
    "fee_currency": "BTC",
}

FUTURES_ORDER = {
    "id": 15675394,
    "user": 100000,
    "contract": "ETH_USDT",
    "create_time": 1546569968,
    "size": 6024,
    "iceberg": 0,
    "left": 6024,
    "price": "3765",
    "tif": "gtc",
    "is_reduce_only": False,
    "status": "open",
    "text": "t-cpanel-def",
}


def test_normalize_spot_order_filled():
    order = normalize_spot_order(SPOT_ORDER, "GATEIO_SPOT_BTCUSDT")
    assert order.native_order_id == "1852454420"
    assert order.client_order_id == "cpanel-abc"      # "t-" prefix stripped
    assert order.side == OrderSide.BUY
    assert order.status == OrderStatus.FILLED
    assert order.qty == Decimal("0.001")
    assert order.filled_qty == Decimal("0.001")
    assert order.price == Decimal("65000")
    assert order.created_at == datetime.fromtimestamp(1710488334.073, tz=timezone.utc)


def test_normalize_spot_order_statuses():
    open_order = normalize_spot_order(
        {**SPOT_ORDER, "status": "open", "left": "0.001", "filled_amount": "0"},
        "GATEIO_SPOT_BTCUSDT",
    )
    assert open_order.status == OrderStatus.OPEN
    partial = normalize_spot_order(
        {**SPOT_ORDER, "status": "open", "left": "0.0004", "filled_amount": "0.0006"},
        "GATEIO_SPOT_BTCUSDT",
    )
    assert partial.status == OrderStatus.PARTIALLY_FILLED
    assert partial.filled_qty == Decimal("0.0006")
    cancelled = normalize_spot_order(
        {**SPOT_ORDER, "status": "cancelled", "finish_as": "cancelled"},
        "GATEIO_SPOT_BTCUSDT",
    )
    assert cancelled.status == OrderStatus.CANCELLED


def test_normalize_spot_market_buy_uses_base_fill():
    # Market buys are quote-denominated: amount=65 USDT, filled 0.001 BTC.
    raw = {
        **SPOT_ORDER, "type": "market", "amount": "65", "price": "0",
        "left": "0", "filled_amount": "0.001",
    }
    order = normalize_spot_order(raw, "GATEIO_SPOT_BTCUSDT")
    assert order.type == OrderType.MARKET
    assert order.qty == Decimal("0.001")
    assert order.filled_qty == Decimal("0.001")
    assert order.price is None


def test_venue_made_text_is_not_a_client_id():
    order = normalize_spot_order({**SPOT_ORDER, "text": "apiv4"}, "GATEIO_SPOT_BTCUSDT")
    assert order.client_order_id is None


def test_normalize_futures_order():
    order = normalize_futures_order(FUTURES_ORDER, "GATEIO_PERP_ETHUSDT")
    assert order.native_order_id == "15675394"
    assert order.client_order_id == "cpanel-def"
    assert order.side == OrderSide.BUY
    assert order.status == OrderStatus.OPEN
    assert order.qty == Decimal("6024")
    assert order.filled_qty == Decimal("0")
    assert order.price == Decimal("3765")


def test_normalize_futures_order_states():
    partial = normalize_futures_order(
        {**FUTURES_ORDER, "left": 1024}, "GATEIO_PERP_ETHUSDT"
    )
    assert partial.status == OrderStatus.PARTIALLY_FILLED
    assert partial.filled_qty == Decimal("5000")
    filled = normalize_futures_order(
        {**FUTURES_ORDER, "status": "finished", "finish_as": "filled", "left": 0},
        "GATEIO_PERP_ETHUSDT",
    )
    assert filled.status == OrderStatus.FILLED
    cancelled = normalize_futures_order(
        {**FUTURES_ORDER, "status": "finished", "finish_as": "cancelled"},
        "GATEIO_PERP_ETHUSDT",
    )
    assert cancelled.status == OrderStatus.CANCELLED
    short = normalize_futures_order(
        {**FUTURES_ORDER, "size": -10, "left": -10}, "GATEIO_PERP_ETHUSDT"
    )
    assert short.side == OrderSide.SELL and short.qty == Decimal("10")
    market = normalize_futures_order(
        {**FUTURES_ORDER, "price": "0", "tif": "ioc"}, "GATEIO_PERP_ETHUSDT"
    )
    assert market.type == OrderType.MARKET and market.price is None


async def test_place_spot_market_buy_converts_to_quote(connector, monkeypatch):
    calls = []

    async def last_price(cred, native):
        return Decimal("65000")

    async def call(func, *args, **kwargs):
        calls.append((func.__name__, args))
        return {**SPOT_ORDER, "type": "market", "status": "open",
                "amount": "65", "left": "65", "filled_amount": "0"}

    monkeypatch.setattr(connector, "_spot_last_price", last_price)
    monkeypatch.setattr(connector, "_call", call)
    request = OrderRequest(
        universal_symbol="GATEIO_SPOT_BTCUSDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        qty=Decimal("0.001"),
        client_order_id="cpanel-abc",
    )
    order = await connector.place_order({"api_key": "k", "secret": "s"}, request)
    name, args = calls[0]
    assert name == "create_order"
    body = args[0]
    assert body.amount == "65.000"                     # 0.001 * 65000, quote units
    assert body.time_in_force == "ioc"
    assert body.text == "t-cpanel-abc"
    assert order.qty == Decimal("0.001")               # echoes the base request qty


async def test_place_futures_order_signed_contracts(connector, monkeypatch):
    calls = []

    async def call(func, *args, **kwargs):
        calls.append((func.__name__, args))
        return {**FUTURES_ORDER, "size": -5, "left": -5, "price": "3800"}

    monkeypatch.setattr(connector, "_call", call)
    request = OrderRequest(
        universal_symbol="GATEIO_PERP_ETHUSDT",
        side=OrderSide.SELL,
        type=OrderType.LIMIT,
        qty=Decimal("5"),
        price=Decimal("3800"),
        reduce_only=True,
        client_order_id="cpanel-def",
    )
    order = await connector.place_order({"api_key": "k", "secret": "s"}, request)
    name, args = calls[0]
    assert name == "create_futures_order"
    settle, body = args
    assert settle == "usdt"
    assert body.size == -5                             # sell = negative contracts
    assert body.reduce_only is True
    assert body.text == "t-cpanel-def"
    assert order.side == OrderSide.SELL


async def test_futures_fractional_contracts_rejected(connector):
    request = OrderRequest(
        universal_symbol="GATEIO_PERP_ETHUSDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        qty=Decimal("0.5"),
        price=Decimal("3800"),
    )
    with pytest.raises(ValueError):
        await connector.place_order({"api_key": "k", "secret": "s"}, request)


async def test_fetch_order_by_client_id_uses_text(connector, monkeypatch):
    calls = []

    async def call(func, *args, **kwargs):
        calls.append((func.__name__, args))
        return SPOT_ORDER

    monkeypatch.setattr(connector, "_call", call)
    order = await connector.fetch_order(
        {"api_key": "k", "secret": "s"},
        "GATEIO_SPOT_BTCUSDT",
        client_order_id="cpanel-abc",
    )
    name, args = calls[0]
    assert name == "get_order"
    assert args == ("t-cpanel-abc", "BTC_USDT")
    assert order.client_order_id == "cpanel-abc"


# -- history ---------------------------------------------------------------------

SPOT_TRADE = {
    "id": "1232893232",
    "create_time": "1548000000",
    "create_time_ms": "1548000000123.456",
    "currency_pair": "BTC_USDT",
    "side": "sell",
    "role": "taker",
    "amount": "0.15",
    "price": "62000",
    "order_id": "1852454420",
    "fee": "0.0000018",
    "fee_currency": "USDT",
}

FUTURES_TRADE = {
    "trade_id": "121234231",
    "create_time": 1514764800.123,
    "contract": "ETH_USDT",
    "order_id": "21893289839",
    "size": -100,
    "price": "3765",
    "role": "taker",
    "fee": "0.00028",
}


def test_normalize_spot_trade():
    trade = normalize_spot_trade(SPOT_TRADE, "GATEIO_SPOT_BTCUSDT")
    assert trade.side == OrderSide.SELL
    assert trade.native_trade_id == "1232893232"
    assert trade.native_order_id == "1852454420"
    assert trade.price == Decimal("62000")
    assert trade.qty == Decimal("0.15")
    assert trade.fee_asset == "USDT"
    # create_time_ms carries sub-ms precision ("1548000000123.456")
    assert trade.traded_at == datetime.fromtimestamp(1548000000.123456, tz=timezone.utc)


def test_normalize_futures_trade_fee_settles_in_settle_currency():
    trade = normalize_futures_trade(FUTURES_TRADE, "GATEIO_PERP_ETHUSDT", "usdt")
    assert trade.side == OrderSide.SELL
    assert trade.qty == Decimal("100")
    assert trade.fee == Decimal("0.00028")
    assert trade.fee_asset == "USDT"


def test_history_stream_keys_are_surfaces():
    assert GateIOConnector().history_stream_keys(["ignored"]) == ["spot", "usdt", "btc"]


def test_initial_history_cursor_encodes_window_start():
    start = datetime(2026, 7, 12, tzinfo=timezone.utc)
    cursor = GateIOConnector().initial_history_cursor(start)
    assert json.loads(cursor) == {"start": int(start.timestamp() * 1000)}


async def test_fetch_trades_pages_within_window_then_slides(connector, monkeypatch):
    calls = []

    async def spot_page(credential, start_s, end_s, limit, page):
        calls.append((start_s, end_s, limit, page))
        if page == 1:
            return [SPOT_TRADE] * limit          # full page → stay in window
        return [SPOT_TRADE]                      # short page → window slides

    monkeypatch.setattr(connector, "_fetch_spot_trades_page", spot_page)
    cred = {"api_key": "k", "secret": "s"}

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - 2 * 24 * 60 * 60 * 1000
    cursor = json.dumps({"start": start_ms})

    page1 = await connector.fetch_trades(cred, "spot", cursor)
    assert page1.items[0].universal_symbol == "GATEIO_SPOT_BTCUSDT"
    decoded = json.loads(page1.next_cursor)
    assert decoded["start"] == start_ms
    assert decoded["skip"] == 200
    assert calls[0][3] == 1                      # page number

    page2 = await connector.fetch_trades(cred, "spot", page1.next_cursor)
    assert calls[1][3] == 2                      # skip 200 → page 2
    assert json.loads(page2.next_cursor) == {"start": start_ms + 24 * 60 * 60 * 1000}


async def test_fetch_trades_caught_up(connector, monkeypatch):
    async def spot_page(credential, start_s, end_s, limit, page):
        return []

    monkeypatch.setattr(connector, "_fetch_spot_trades_page", spot_page)
    recent = int(datetime.now(timezone.utc).timestamp() * 1000) - 60_000
    page = await connector.fetch_trades(
        {"api_key": "k", "secret": "s"}, "spot", json.dumps({"start": recent})
    )
    assert page.items == []
    assert page.next_cursor is None


async def test_fetch_trades_defaults_to_three_day_backfill(connector, monkeypatch):
    starts = []

    async def futures_page(credential, settle, start_s, end_s, limit, offset):
        starts.append(start_s)
        return []

    monkeypatch.setattr(connector, "_fetch_futures_trades_page", futures_page)
    before = datetime.now(timezone.utc)
    await connector.fetch_trades({"api_key": "k", "secret": "s"}, "usdt", None)
    expected = int((before - DEFAULT_HISTORY_BACKFILL).timestamp())
    assert abs(starts[0] - expected) < 60


async def test_untracked_contracts_skipped(connector, monkeypatch):
    async def futures_page(credential, settle, start_s, end_s, limit, offset):
        return [FUTURES_TRADE, {**FUTURES_TRADE, "contract": "WEIRD_USDT", "trade_id": "x"}]

    monkeypatch.setattr(connector, "_fetch_futures_trades_page", futures_page)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    page = await connector.fetch_trades(
        {"api_key": "k", "secret": "s"}, "usdt", json.dumps({"start": now_ms - 60_000})
    )
    assert [t.native_trade_id for t in page.items] == ["121234231"]


async def test_fetch_orders_history_spot_omits_pair(connector, monkeypatch):
    calls = []

    async def call(func, *args, **kwargs):
        calls.append((func.__name__, args, kwargs))
        return [SPOT_ORDER]

    monkeypatch.setattr(connector, "_call", call)
    await connector.fetch_instruments()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    page = await connector.fetch_orders_history(
        {"api_key": "k", "secret": "s"}, "spot", json.dumps({"start": now_ms - 60_000})
    )
    name, args, kwargs = calls[-1]
    assert name == "list_orders"
    assert args == (None, "finished")            # account-wide query
    assert kwargs["limit"] == 100
    assert page.items[0].universal_symbol == "GATEIO_SPOT_BTCUSDT"


# -- transfers / withdrawals / deposits --------------------------------------------


async def test_internal_transfer_sets_settle(monkeypatch):
    c = GateIOConnector()
    calls = []

    async def call(func, *args, **kwargs):
        calls.append((func.__name__, args))
        return {"tx_id": 59636381286}

    monkeypatch.setattr(c, "_call", call)
    result = await c.transfer(
        {"api_key": "k", "secret": "s"}, "USDT", Decimal("100"),
        from_wallet=WalletType.SPOT, to_wallet=WalletType.FUTURES,
    )
    name, args = calls[0]
    assert name == "transfer"
    body = args[0]
    assert body.to_dict()["_from"] == "spot"
    assert body.to == "futures"
    assert body.settle == "usdt"                 # futures account of the asset
    assert body.amount == "100"
    assert result.status == "confirmed"
    assert result.native_transfer_id == "59636381286"


async def test_internal_transfer_same_wallet_rejected():
    c = GateIOConnector()
    with pytest.raises(ValueError):
        await c.transfer(
            {"api_key": "k", "secret": "s"}, "USDT", Decimal("1"),
            from_wallet=WalletType.SPOT, to_wallet=WalletType.SPOT,
        )


async def test_transfer_rejects_foreign_wallet_type():
    # Gate has no UNIFIED wallet — only SPOT and FUTURES exist.
    c = GateIOConnector()
    with pytest.raises(ValueError):
        await c.transfer(
            {"api_key": "k", "secret": "s"}, "USDT", Decimal("1"),
            from_wallet=WalletType.UNIFIED, to_wallet=WalletType.SPOT,
        )


async def test_main_to_sub_transfer_direction(monkeypatch):
    c = GateIOConnector()
    calls = []

    async def call(func, *args, **kwargs):
        calls.append((func.__name__, args))
        return {"tx_id": 59112382286}

    monkeypatch.setattr(c, "_call", call)
    result = await c.transfer(
        {"api_key": "k", "secret": "s"}, "USDT", Decimal("5"),
        to_subaccount="10001",
    )
    name, args = calls[0]
    assert name == "transfer_with_sub_account"
    body = args[0]
    assert body.sub_account == "10001"
    assert body.direction == "to"
    assert body.sub_account_type == "spot"
    assert result.status == "confirmed"


async def test_main_side_must_be_spot():
    c = GateIOConnector()
    with pytest.raises(ValueError):
        await c.transfer(
            {"api_key": "k", "secret": "s"}, "USDT", Decimal("5"),
            from_wallet=WalletType.FUTURES, to_subaccount="10001",
        )


CURRENCY_CHAINS = [
    {
        "chain": "TRX",
        "name_cn": "波场",
        "name_en": "TRON",
        "is_disabled": 0,
        "is_deposit_disabled": 0,
        "is_withdraw_disabled": 0,
    },
    {
        "chain": "ETH",
        "name_cn": "以太坊",
        "name_en": "ETH",
        "is_disabled": 0,
        "is_deposit_disabled": 0,
        "is_withdraw_disabled": 1,
    },
]

WITHDRAW_STATUS = {
    "currency": "USDT",
    "withdraw_fix": "4",
    "withdraw_amount_mini": "10",
    "withdraw_fix_on_chains": {"TRX": "1", "ETH": "4.6"},
}


def test_normalize_asset_networks_merges_status():
    networks = normalize_asset_networks(CURRENCY_CHAINS, WITHDRAW_STATUS)
    trx = next(n for n in networks if n.network == "TRX")
    assert trx.withdraw_enabled and trx.deposit_enabled
    assert trx.withdraw_min == Decimal("10")
    assert trx.withdraw_fee == Decimal("1")
    eth = next(n for n in networks if n.network == "ETH")
    assert eth.withdraw_enabled is False
    assert eth.withdraw_fee == Decimal("4.6")


def test_normalize_withdrawal_record_statuses():
    base = {
        "id": "w35980955", "currency": "USDT", "amount": "100",
        "address": "TAbc", "chain": "TRX", "fee": "1",
    }
    assert normalize_withdrawal_record({**base, "status": "DONE"}).status \
        == WithdrawalStatus.COMPLETED
    sent = normalize_withdrawal_record({**base, "status": "EXTPEND", "txid": "0xabc"})
    assert sent.status == WithdrawalStatus.SENT
    assert sent.tx_id == "0xabc"
    assert sent.fee == Decimal("1")
    assert normalize_withdrawal_record({**base, "status": "CANCEL"}).status \
        == WithdrawalStatus.FAILED
    assert normalize_withdrawal_record({**base, "status": "REQUEST"}).status \
        == WithdrawalStatus.PENDING
    # An unknown-but-pending status with a tx id means it went on-chain.
    assert normalize_withdrawal_record({**base, "status": "PEND", "txid": "0xabc"}).status \
        == WithdrawalStatus.SENT


def test_normalize_deposit_record_statuses():
    base = {"id": "d33624854", "currency": "USDT", "amount": "99",
            "chain": "TRX", "txid": "0xabc", "timestamp": "1636624854"}
    credited = normalize_deposit_record({**base, "status": "DONE"})
    assert credited.status == DepositStatus.CREDITED
    assert credited.native_id == "d33624854"
    assert credited.credited_at is not None
    assert credited.created_at is not None
    assert normalize_deposit_record({**base, "status": "PEND"}).status == DepositStatus.PENDING
    assert normalize_deposit_record({**base, "status": "TRACK"}).status == DepositStatus.PENDING
    assert normalize_deposit_record({**base, "status": "INVALID"}).status == DepositStatus.FAILED


def test_normalize_funding_fee():
    fee = normalize_funding_fee(
        {
            "time": 1636624854,
            "change": "-0.0123",
            "balance": "100.5",
            "type": "fund",
            "text": "",
            "contract": "BTC_USDT",
            "id": "ab12",
        },
        "GATEIO_PERP_BTCUSDT",
        settle="usdt",
    )
    assert fee.native_id == "usdt:ab12"
    assert fee.amount == Decimal("-0.0123")
    assert fee.asset == "USDT"
    assert fee.settled_at is not None


def test_funding_history_stream_keys():
    assert GateIOConnector().funding_history_stream_keys() == ["usdt", "btc"]


async def test_withdraw_builds_ledger_record(monkeypatch):
    c = GateIOConnector()
    calls = []

    async def call(func, *args, **kwargs):
        calls.append((func.__name__, args))
        return {"id": "w35980955", "status": "REQUEST"}

    monkeypatch.setattr(c, "_call", call)
    withdrawal = await c.withdraw(
        {"api_key": "k", "secret": "s"}, "USDT", Decimal("100"), "TAbc", "TRX"
    )
    name, args = calls[0]
    assert name == "withdraw"
    body = args[0]
    assert body.currency == "USDT"
    assert body.chain == "TRX"
    assert body.amount == "100"
    assert withdrawal.native_id == "w35980955"
    assert withdrawal.status == WithdrawalStatus.PENDING


async def test_fetch_deposit_address_matches_chain(monkeypatch):
    c = GateIOConnector()

    async def raw(credential, asset):
        return {
            "currency": "USDT",
            "address": "TDefault",
            "multichain_addresses": [
                {"chain": "TRX", "address": "TAbc123", "payment_id": "",
                 "payment_name": "", "obtain_failed": 0},
                {"chain": "ETH", "address": "0xdef", "payment_id": "",
                 "payment_name": "", "obtain_failed": 0},
            ],
        }

    monkeypatch.setattr(c, "_fetch_deposit_address_raw", raw)
    resolved = await c.fetch_deposit_address({"api_key": "k", "secret": "s"}, "USDT", "TRX")
    assert resolved.address == "TAbc123"
    assert resolved.tag is None


async def test_deposit_calls_reject_subaccounts():
    # No master-for-sub deposit endpoints on Gate.
    c = GateIOConnector()
    with pytest.raises(ValueError):
        await c.fetch_deposit_address(
            {"api_key": "k", "secret": "s"}, "USDT", "TRX", subaccount="10001"
        )
    with pytest.raises(ValueError):
        await c.fetch_deposits({"api_key": "k", "secret": "s"}, subaccount="10001")


# -- provisioning -------------------------------------------------------------------


async def test_create_api_key_maps_scopes(monkeypatch):
    c = GateIOConnector()
    captured = {}

    async def raw(credential, user_id, body):
        captured["user_id"] = user_id
        captured["body"] = body
        return {"key": "new-key", "secret": "new-secret"}

    monkeypatch.setattr(c, "_create_sub_key_raw", raw)
    result = await c.create_api_key(
        {"api_key": "k", "secret": "s"},
        subaccount="10001",
        scheme=KeyScheme.HMAC,
        permissions=["spot", "derivatives", "wallet"],
        read_only=False,
        ips=["1.2.3.4"],
        note="hedge key",
    )
    assert captured["user_id"] == 10001
    body = captured["body"]
    assert body.name == "hedge key"
    assert body.ip_whitelist == ["1.2.3.4"]
    perms = {p.name: p.read_only for p in body.perms}
    assert perms == {"spot": False, "futures": False, "wallet": False, "account": True}
    assert result.api_key == "new-key"
    assert result.secret == "new-secret"
    assert result.scheme == KeyScheme.HMAC


async def test_create_api_key_rejects_non_hmac():
    c = GateIOConnector()
    with pytest.raises(ValueError):
        await c.create_api_key(
            {"api_key": "k", "secret": "s"}, subaccount="10001",
            scheme=KeyScheme.ED25519, permissions=["spot"],
        )


def _gate_error(status: int, label: str, message: str):
    from gate_api.exceptions import ApiException, GateApiException

    parent = ApiException(status=status, reason="Forbidden")
    return GateApiException(label, message, None, parent)


async def test_verify_falls_back_to_trade_fee_without_account_scope(monkeypatch):
    """A key without the "account" scope gets a 403 from /account/detail;
    verification must fall back to a wallet-scope call for the uid."""
    c = GateIOConnector()

    async def detail(credential):
        raise _gate_error(403, "FORBIDDEN", "Request API key does not have account permission")

    async def trade_fee(credential):
        return {"user_id": 424242, "taker_fee": "0.002", "maker_fee": "0.002"}

    async def key_info(credential):
        return None

    monkeypatch.setattr(c, "_fetch_account_detail", detail)
    monkeypatch.setattr(c, "_fetch_trade_fee", trade_fee)
    monkeypatch.setattr(c, "_fetch_own_key_info", key_info)
    info = await c.verify_credential({"api_key": "k", "secret": "s"})
    assert info.valid
    assert info.native_uid == "424242"


async def test_verify_parses_main_keys_list(monkeypatch):
    """/account/main_keys returns a *list* of the account's keys (the SDK
    model mangles it); verification must pick this credential's entry and
    map its permission scopes."""
    import json as _json

    c = GateIOConnector()

    async def detail(credential):
        return {"user_id": 16208645, "key": {"mode": 1}}

    class _Response:
        # Raw shape observed live: withdraw is its own scope, wallet is not.
        data = _json.dumps([
            {
                "key": "other-key",
                "perms": [{"name": "spot", "read_only": True}],
            },
            {
                "key": "my-key",
                "perms": [
                    {"name": "account", "read_only": False},
                    {"name": "spot", "read_only": False},
                    {"name": "futures", "read_only": False},
                    {"name": "wallet", "read_only": False},
                    {"name": "withdraw", "read_only": True},
                ],
            },
        ])

    async def call(func, *args, **kwargs):
        assert kwargs.get("_preload_content") is False
        return _Response()

    monkeypatch.setattr(c, "_fetch_account_detail", detail)
    monkeypatch.setattr(c, "_call", call)
    info = await c.verify_credential({"api_key": "my-key", "secret": "s"})
    assert info.valid
    assert info.native_uid == "16208645"
    assert info.is_master is True
    assert info.read_only is False
    assert info.can_trade is True
    assert info.can_withdraw is False  # withdraw scope present but read-only
    assert info.permissions == {
        "account": "read-write",
        "spot": "read-write",
        "futures": "read-write",
        "wallet": "read-write",
        "withdraw": "read",
    }


async def test_verify_surfaces_real_rejection(monkeypatch):
    c = GateIOConnector()

    async def detail(credential):
        raise _gate_error(401, "INVALID_KEY", "Invalid key provided")

    monkeypatch.setattr(c, "_fetch_account_detail", detail)
    info = await c.verify_credential({"api_key": "k", "secret": "s"})
    assert not info.valid
    assert "401" in info.reason
    assert "Invalid key provided" in info.reason
    # A venue rejection must never be misreported as a malformed local key.
    assert "malformed" not in info.reason.lower()


async def test_verify_invalid_when_fallback_also_fails(monkeypatch):
    c = GateIOConnector()

    async def detail(credential):
        raise _gate_error(403, "FORBIDDEN", "Request API key does not have account permission")

    async def trade_fee(credential):
        raise _gate_error(401, "INVALID_SIGNATURE", "Signature mismatch")

    monkeypatch.setattr(c, "_fetch_account_detail", detail)
    monkeypatch.setattr(c, "_fetch_trade_fee", trade_fee)
    info = await c.verify_credential({"api_key": "k", "secret": "s"})
    assert not info.valid
    assert "Signature mismatch" in info.reason


def test_gate_login_name_sanitizes_and_suffixes():
    from app.connectors.gateio import _gate_login_name

    name = _gate_login_name("mm-01")
    assert name.startswith("mm_01_")
    assert name.replace("_", "").isalnum()
    # Two calls must not collide — Gate login names are globally unique.
    assert _gate_login_name("prom") != _gate_login_name("prom")


async def test_create_subaccount_uses_unique_login_keeps_remark(monkeypatch):
    c = GateIOConnector()
    captured = {}

    async def call(func, *args, **kwargs):
        body = args[0]
        captured["login_name"] = body.login_name
        captured["remark"] = body.remark
        return {"user_id": 10099, "login_name": body.login_name, "remark": body.remark}

    monkeypatch.setattr(c, "_call", call)
    sub = await c.create_subaccount({"api_key": "k", "secret": "s"}, "prom")
    assert sub.native_uid == "10099"
    assert sub.label == "prom"  # panel label, not the Gate username
    assert captured["remark"] == "prom"
    assert captured["login_name"].startswith("prom_")
    assert captured["login_name"] != "prom"


async def test_create_subaccount_surfaces_venue_rejection(monkeypatch):
    c = GateIOConnector()

    async def call(func, *args, **kwargs):
        raise _gate_error(400, "OPERATION_ERROR", "Username is taken. Please use another one")

    monkeypatch.setattr(c, "_call", call)
    with pytest.raises(ValueError, match="Username is taken"):
        await c.create_subaccount({"api_key": "k", "secret": "s"}, "prom")


async def test_list_subaccounts(monkeypatch):
    c = GateIOConnector()

    async def call(func, *args, **kwargs):
        return [
            {"remark": "hedge-1", "login_name": "sub1", "user_id": 10001, "state": 1},
            {"login_name": "sub2", "user_id": 10002, "state": 2},
        ]

    monkeypatch.setattr(c, "_call", call)
    subs = await c.list_subaccounts({"api_key": "k", "secret": "s"})
    assert subs[0].native_uid == "10001"
    assert subs[0].label == "hedge-1"
    assert subs[0].status == "normal"
    assert subs[1].label == "sub2"
    assert subs[1].status == "frozen"


async def test_set_subaccount_frozen(monkeypatch):
    c = GateIOConnector()
    calls = []

    async def call(func, *args, **kwargs):
        calls.append((func.__name__, args))
        return None

    monkeypatch.setattr(c, "_call", call)
    await c.set_subaccount_frozen({"api_key": "k", "secret": "s"}, "10001", True)
    await c.set_subaccount_frozen({"api_key": "k", "secret": "s"}, "10001", False)
    assert calls[0] == ("lock_sub_account", (10001,))
    assert calls[1] == ("unlock_sub_account", (10001,))
