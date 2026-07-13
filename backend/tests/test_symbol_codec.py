import pytest

from app.connectors.symbols import (
    InvalidUniversalSymbol,
    build_universal_symbol,
    parse_universal_symbol,
)
from app.connectors.types import MarketType


def test_build_examples_from_design():
    assert build_universal_symbol("binance", MarketType.SPOT, "BTC", "USDT") == "BINANCE_SPOT_BTCUSDT"
    assert build_universal_symbol("BYBIT", MarketType.PERP, "ETH", "USDT") == "BYBIT_PERP_ETHUSDT"
    assert build_universal_symbol("BINANCE", MarketType.CMARGIN, "SOL", "USD") == "BINANCE_CMARGIN_SOLUSD"


def test_roundtrip():
    symbol = build_universal_symbol("BYBIT", MarketType.CMARGIN, "BTC", "USD")
    parsed = parse_universal_symbol(symbol)
    assert parsed.exchange == "BYBIT"
    assert parsed.market_type == MarketType.CMARGIN
    assert parsed.pair == "BTCUSD"


def test_1000x_tokens_parse():
    # Numerals are valid in asset names (1000PEPE, etc.)
    parsed = parse_universal_symbol("BINANCE_PERP_1000PEPEUSDT")
    assert parsed.pair == "1000PEPEUSDT"


def test_unicode_native_tickers_kept_verbatim():
    # Binance lists Chinese-ticker tokens (e.g. 币安人生); the venue's name
    # is the identity — no romanization is invented.
    symbol = build_universal_symbol("BINANCE", MarketType.SPOT, "币安人生", "USDT")
    assert symbol == "BINANCE_SPOT_币安人生USDT"
    parsed = parse_universal_symbol(symbol)
    assert parsed.pair == "币安人生USDT"
    assert parsed.market_type == MarketType.SPOT


@pytest.mark.parametrize(
    "bad",
    [
        "BINANCE_SPOT",              # missing pair
        "BINANCE_LINEAR_BTCUSDT",    # native market name, not universal
        "BINANCE_SPOT_BTC-USDT",     # invalid characters
        "binance_SPOT_BTCUSDT",      # lowercase exchange
    ],
)
def test_invalid_symbols_rejected(bad):
    with pytest.raises(InvalidUniversalSymbol):
        parse_universal_symbol(bad)
