"""Bybit normalization tests with fixtures shaped like the v5 API docs."""
from decimal import Decimal

import pytest

from app.connectors.bybit import BybitConnector, normalize_instrument
from app.connectors.types import MarketType

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
