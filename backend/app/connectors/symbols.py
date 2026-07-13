"""Universal symbol codec: <EXCHANGE>_<MARKET_TYPE>_<PAIR>.

Examples:
    BINANCE_SPOT_BTCUSDT
    BYBIT_PERP_ETHUSDT           (Bybit "linear")
    BINANCE_CMARGIN_SOLUSD       (COIN-margined / "inverse")
    BINANCE_SPOT_币安人生USDT     (native Unicode tickers kept verbatim)

PAIR is base asset + quote asset with no separator. ASCII letters must be
uppercase; non-ASCII word characters (e.g. Chinese tickers like 币安人生)
are allowed verbatim — the venue's name is the identity, we don't invent a
romanization. Separators (underscore, dash, whitespace) are forbidden in
components, so the underscore-delimited structure stays unambiguous.
"""
import re
from dataclasses import dataclass

from app.connectors.types import MarketType

# Unicode word characters, minus underscore and ASCII lowercase
_PART = re.compile(r"^[^\W_a-z]+$", re.UNICODE)


class InvalidUniversalSymbol(ValueError):
    pass


@dataclass(frozen=True)
class ParsedSymbol:
    exchange: str
    market_type: MarketType
    pair: str


def build_universal_symbol(exchange: str, market_type: MarketType, base: str, quote: str) -> str:
    exchange = exchange.upper()
    pair = f"{base}{quote}".upper()
    for part in (exchange, pair):
        if not _PART.match(part):
            raise InvalidUniversalSymbol(f"Invalid symbol component: {part!r}")
    return f"{exchange}_{market_type.value}_{pair}"


def parse_universal_symbol(symbol: str) -> ParsedSymbol:
    parts = symbol.split("_", 2)
    if len(parts) != 3:
        raise InvalidUniversalSymbol(f"Expected EXCHANGE_MARKETTYPE_PAIR, got {symbol!r}")
    exchange, market_type_raw, pair = parts
    if not _PART.match(exchange) or not _PART.match(pair):
        raise InvalidUniversalSymbol(f"Invalid characters in {symbol!r}")
    try:
        market_type = MarketType(market_type_raw)
    except ValueError:
        raise InvalidUniversalSymbol(f"Unknown market type {market_type_raw!r} in {symbol!r}") from None
    return ParsedSymbol(exchange=exchange, market_type=market_type, pair=pair)
