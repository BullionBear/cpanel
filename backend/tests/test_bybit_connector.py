"""Bybit normalization tests with fixtures shaped like the v5 API docs."""
from decimal import Decimal

import pytest

from app.connectors.bybit import (
    BybitConnector,
    normalize_instrument,
    normalize_positions,
    normalize_wallet_balances,
)
from app.connectors.types import MarketType, WalletType

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
    assert usdt.wallet == WalletType.DERIVATIVES
    assert usdt.locked == Decimal("500")
    assert usdt.free == Decimal("9500")
    assert usdt.total == Decimal("10000")


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

    monkeypatch.setattr(connector, "_fetch_wallet_coins", wallet_coins)
    balances = await connector.fetch_balances({"api_key": "k", "secret": "s"})
    assert {b.asset for b in balances} == {"USDT", "BTC"}


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
