"""Binance normalization tests with fixtures shaped like the REST API docs.

Raw-fetch methods are overridden so no network is involved; the payload
shapes are exactly what api/fapi/dapi exchangeInfo return.
"""
from decimal import Decimal

import pytest

from app.connectors.binance import (
    BinanceConnector,
    _normalize_pem,
    normalize_cm_instrument,
    normalize_futures_balances,
    normalize_positions,
    normalize_spot_balances,
    normalize_spot_instrument,
    normalize_um_instrument,
)
from app.connectors.types import MarketType, WalletType

# A syntactically valid Ed25519 PKCS#8 private key (test-only, not a real key).
ED25519_BODY = "MC4CAQAwBQYDK2VwBCIEINaP1QKxqnkgM63/wvkTeuRg2MKi4iEbgNW9QxR4vzCI"
ED25519_PEM = f"-----BEGIN PRIVATE KEY-----\n{ED25519_BODY}\n-----END PRIVATE KEY-----\n"

SPOT_BTCUSDT = {
    "symbol": "BTCUSDT",
    "status": "TRADING",
    "baseAsset": "BTC",
    "quoteAsset": "USDT",
    "filters": [
        {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000000", "tickSize": "0.01"},
        {"filterType": "LOT_SIZE", "minQty": "0.00001", "maxQty": "9000", "stepSize": "0.00001"},
        {"filterType": "NOTIONAL", "minNotional": "5.00000000", "applyMinToMarket": True},
    ],
}

UM_ETHUSDT_PERP = {
    "symbol": "ETHUSDT",
    "status": "TRADING",
    "contractType": "PERPETUAL",
    "baseAsset": "ETH",
    "quoteAsset": "USDT",
    "marginAsset": "USDT",
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01", "minPrice": "39.86", "maxPrice": "306177"},
        {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "10000", "stepSize": "0.001"},
        {"filterType": "MIN_NOTIONAL", "notional": "20"},
    ],
}

UM_ETHUSDT_QUARTER = {**UM_ETHUSDT_PERP, "symbol": "ETHUSDT_260925", "contractType": "CURRENT_QUARTER"}

SPOT_CHINESE_TICKER = {
    **SPOT_BTCUSDT,
    "symbol": "币安人生USDT",
    "baseAsset": "币安人生",
    "quoteAsset": "USDT",
}

CM_SOLUSD_PERP = {
    "symbol": "SOLUSD_PERP",
    "pair": "SOLUSD",
    "contractType": "PERPETUAL",
    "contractStatus": "TRADING",
    "baseAsset": "SOL",
    "quoteAsset": "USD",
    "marginAsset": "SOL",
    "contractSize": 10,
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.010", "minPrice": "0.500", "maxPrice": "100000"},
        {"filterType": "LOT_SIZE", "minQty": "1", "maxQty": "1000000", "stepSize": "1"},
    ],
}

CM_SOLUSD_DELIVERY = {**CM_SOLUSD_PERP, "symbol": "SOLUSD_260925", "contractType": "CURRENT_QUARTER"}


def test_normalize_pem_restores_spaces_to_newlines():
    # PEM flattened onto one line with spaces (as some clients submit it).
    flattened = f"-----BEGIN PRIVATE KEY----- {ED25519_BODY} -----END PRIVATE KEY-----"
    assert _normalize_pem(flattened) == ED25519_PEM


def test_normalize_pem_wraps_long_body_at_64_cols():
    body = "A" * 200
    flattened = f"-----BEGIN PRIVATE KEY----- {body} -----END PRIVATE KEY-----"
    lines = _normalize_pem(flattened).splitlines()
    assert lines[0] == "-----BEGIN PRIVATE KEY-----"
    assert lines[-1] == "-----END PRIVATE KEY-----"
    assert [len(line) for line in lines[1:-1]] == [64, 64, 64, 8]


def test_normalize_pem_is_idempotent_on_valid_pem():
    assert _normalize_pem(ED25519_PEM) == ED25519_PEM
    # Surrounding whitespace is trimmed but structure is preserved.
    assert _normalize_pem(f"  \n{ED25519_PEM}\n  ") == ED25519_PEM


def test_normalize_pem_leaves_hmac_secret_untouched():
    assert _normalize_pem("plain-hmac-secret") == "plain-hmac-secret"
    assert _normalize_pem("  spaced secret  ") == "spaced secret"


def test_normalize_spot():
    inst = normalize_spot_instrument(SPOT_BTCUSDT)
    assert inst.universal_symbol == "BINANCE_SPOT_BTCUSDT"
    assert inst.market_type == MarketType.SPOT
    assert inst.native_symbol == "BTCUSDT"
    assert inst.filters.price_tick == Decimal("0.01")
    assert inst.filters.qty_step == Decimal("0.00001")
    assert inst.filters.min_notional == Decimal("5")
    assert inst.status == "active"


def test_spot_min_notional_legacy_filter():
    legacy = {
        **SPOT_BTCUSDT,
        "filters": SPOT_BTCUSDT["filters"][:2]
        + [{"filterType": "MIN_NOTIONAL", "minNotional": "10.0"}],
    }
    assert normalize_spot_instrument(legacy).filters.min_notional == Decimal("10")


def test_spot_halted_status():
    halted = {**SPOT_BTCUSDT, "status": "BREAK"}
    assert normalize_spot_instrument(halted).status == "halted"


def test_spot_unicode_ticker_normalized_verbatim():
    inst = normalize_spot_instrument(SPOT_CHINESE_TICKER)
    assert inst.universal_symbol == "BINANCE_SPOT_币安人生USDT"
    assert inst.native_symbol == "币安人生USDT"
    assert inst.base_asset == "币安人生"


def test_normalize_um_perp():
    inst = normalize_um_instrument(UM_ETHUSDT_PERP)
    assert inst.universal_symbol == "BINANCE_PERP_ETHUSDT"
    assert inst.market_type == MarketType.PERP
    assert inst.settle_asset == "USDT"
    # fapi MIN_NOTIONAL uses key "notional"
    assert inst.filters.min_notional == Decimal("20")


def test_um_delivery_contract_skipped():
    assert normalize_um_instrument(UM_ETHUSDT_QUARTER) is None


def test_normalize_cm_perp():
    inst = normalize_cm_instrument(CM_SOLUSD_PERP)
    assert inst.universal_symbol == "BINANCE_CMARGIN_SOLUSD"
    assert inst.market_type == MarketType.CMARGIN
    assert inst.native_symbol == "SOLUSD_PERP"
    assert inst.contract_size == Decimal("10")
    assert inst.settle_asset == "SOL"
    assert inst.filters.min_notional is None


def test_cm_delivery_contract_skipped():
    assert normalize_cm_instrument(CM_SOLUSD_DELIVERY) is None


@pytest.fixture
def connector(monkeypatch):
    c = BinanceConnector()

    async def spot_info():
        return [SPOT_BTCUSDT]

    async def um_info():
        return [UM_ETHUSDT_PERP, UM_ETHUSDT_QUARTER]

    async def cm_info():
        return [CM_SOLUSD_PERP, CM_SOLUSD_DELIVERY]

    async def spot_prices():
        return [{"symbol": "BTCUSDT", "price": "62598.00"}]

    async def um_prices():
        return [{"symbol": "ETHUSDT", "price": "3300.10"}, {"symbol": "ETHUSDT_260925", "price": "3400"}]

    async def cm_prices():
        return [{"symbol": "SOLUSD_PERP", "ps": "SOLUSD", "price": "150.500"}]

    monkeypatch.setattr(c, "_fetch_spot_exchange_info", spot_info)
    monkeypatch.setattr(c, "_fetch_um_exchange_info", um_info)
    monkeypatch.setattr(c, "_fetch_cm_exchange_info", cm_info)
    monkeypatch.setattr(c, "_fetch_spot_prices", spot_prices)
    monkeypatch.setattr(c, "_fetch_um_prices", um_prices)
    monkeypatch.setattr(c, "_fetch_cm_prices", cm_prices)
    return c


async def test_fetch_instruments_merges_three_surfaces(connector):
    instruments = await connector.fetch_instruments()
    assert {i.universal_symbol for i in instruments} == {
        "BINANCE_SPOT_BTCUSDT",
        "BINANCE_PERP_ETHUSDT",
        "BINANCE_CMARGIN_SOLUSD",
    }


async def test_reference_prices_mapped_to_universal(connector):
    prices = await connector.fetch_reference_prices()
    assert prices == {
        "BINANCE_SPOT_BTCUSDT": Decimal("62598.00"),
        "BINANCE_PERP_ETHUSDT": Decimal("3300.10"),
        "BINANCE_CMARGIN_SOLUSD": Decimal("150.500"),
    }


# -- balances / positions ----------------------------------------------------

SPOT_BALANCES = [
    {"asset": "BTC", "free": "0.5", "locked": "0.1"},
    {"asset": "USDT", "free": "1000", "locked": "0"},
    {"asset": "DUST", "free": "0", "locked": "0"},
]
UM_BALANCES = [{"asset": "USDT", "balance": "5000", "availableBalance": "4000"}]
CM_BALANCES = [{"asset": "BTC", "balance": "0.2", "availableBalance": "0.2"}]

UM_POSITIONS = [
    {
        "symbol": "ETHUSDT",
        "positionAmt": "2",
        "entryPrice": "3000",
        "markPrice": "3100",
        "unRealizedProfit": "200",
        "isolatedMargin": "600",
    },
    {"symbol": "ETHUSDT", "positionAmt": "0", "entryPrice": "0", "markPrice": "3100"},
]
CM_POSITIONS = [
    {
        "symbol": "SOLUSD_PERP",
        "positionAmt": "-10",
        "entryPrice": "150",
        "markPrice": "148",
        "unRealizedProfit": "20",
    }
]


def test_normalize_spot_balances_drops_zero():
    balances = normalize_spot_balances(SPOT_BALANCES)
    assert {b.asset for b in balances} == {"BTC", "USDT"}
    btc = next(b for b in balances if b.asset == "BTC")
    assert btc.wallet == WalletType.SPOT
    assert btc.total == Decimal("0.6")


def test_normalize_futures_balances_splits_available_and_margin():
    balances = normalize_futures_balances(UM_BALANCES)
    assert balances[0].wallet == WalletType.DERIVATIVES
    assert balances[0].free == Decimal("4000")
    assert balances[0].locked == Decimal("1000")
    assert balances[0].total == Decimal("5000")


def test_normalize_positions_maps_and_skips_zero():
    native_map = {(MarketType.PERP, "ETHUSDT"): "BINANCE_PERP_ETHUSDT"}
    positions = normalize_positions(UM_POSITIONS, MarketType.PERP, native_map)
    assert len(positions) == 1
    assert positions[0].universal_symbol == "BINANCE_PERP_ETHUSDT"
    assert positions[0].side == "long"
    assert positions[0].size == Decimal("2")


def test_normalize_positions_short_side():
    native_map = {(MarketType.CMARGIN, "SOLUSD_PERP"): "BINANCE_CMARGIN_SOLUSD"}
    positions = normalize_positions(CM_POSITIONS, MarketType.CMARGIN, native_map)
    assert positions[0].side == "short"
    assert positions[0].size == Decimal("10")


def test_positions_skip_untracked_symbols():
    positions = normalize_positions(UM_POSITIONS, MarketType.PERP, {})
    assert positions == []


async def test_fetch_balances_merges_wallets(connector, monkeypatch):
    async def spot_balances(cred):
        return SPOT_BALANCES

    async def um_balances(cred):
        return UM_BALANCES

    async def cm_balances(cred):
        return CM_BALANCES

    monkeypatch.setattr(connector, "_fetch_spot_balances", spot_balances)
    monkeypatch.setattr(connector, "_fetch_um_balances", um_balances)
    monkeypatch.setattr(connector, "_fetch_cm_balances", cm_balances)

    balances = await connector.fetch_balances({"api_key": "k", "secret": "s"})
    spot = [b for b in balances if b.wallet == WalletType.SPOT]
    deriv = [b for b in balances if b.wallet == WalletType.DERIVATIVES]
    assert {b.asset for b in spot} == {"BTC", "USDT"}
    assert {b.asset for b in deriv} == {"USDT", "BTC"}


async def test_fetch_positions_across_surfaces(connector, monkeypatch):
    async def um_positions(cred):
        return UM_POSITIONS

    async def cm_positions(cred):
        return CM_POSITIONS

    monkeypatch.setattr(connector, "_fetch_um_positions", um_positions)
    monkeypatch.setattr(connector, "_fetch_cm_positions", cm_positions)

    positions = await connector.fetch_positions({"api_key": "k", "secret": "s"})
    assert {p.universal_symbol for p in positions} == {
        "BINANCE_PERP_ETHUSDT",
        "BINANCE_CMARGIN_SOLUSD",
    }
