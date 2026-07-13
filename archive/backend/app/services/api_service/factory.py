from .base import APIContext, ExchangeClient
from .clients import BinanceExchangeClient, BitgetExchangeClient, BybitExchangeClient
from .common import EXCHANGES


class ExchangeClientFactory:
    @staticmethod
    def create(exchange: EXCHANGES, context: APIContext) -> ExchangeClient:
        exchange_slug = exchange.upper()
        if exchange_slug == "BINANCE":
            return BinanceExchangeClient(context)
        if exchange_slug == "BYBIT":
            return BybitExchangeClient(context)
        if exchange_slug == "BITGET":
            return BitgetExchangeClient(context)
        raise ValueError(f"Invalid exchange: {exchange}")
