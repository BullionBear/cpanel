"""Bybit connector (Phase 1: public market data).

Unlike Binance, Bybit serves every market from one base URL (v5 API): the
market type is a ``category`` query parameter, and results are paginated
with a cursor:

    SPOT     /v5/market/instruments-info?category=spot
    PERP     /v5/market/instruments-info?category=linear   (perpetuals only)
    CMARGIN  /v5/market/instruments-info?category=inverse  (perpetuals only)

The linear and inverse categories also contain dated futures
(LinearFutures / InverseFutures contract types); those are skipped until
DELIVERY support lands.

Uses the official ``pybit`` SDK (synchronous, run in a worker thread).
"""
import asyncio
from decimal import Decimal

from pybit.unified_trading import HTTP

from app.connectors.base import Capability, ExchangeConnector
from app.connectors.symbols import build_universal_symbol
from app.connectors.types import Instrument, InstrumentFilters, MarketType
from app.core.ratelimit import Priority, TokenBucket

_CATEGORIES: list[tuple[str, MarketType]] = [
    ("spot", MarketType.SPOT),
    ("linear", MarketType.PERP),
    ("inverse", MarketType.CMARGIN),
]

_PERP_CONTRACT_TYPES = {"LinearPerpetual", "InversePerpetual"}


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _status(info: dict) -> str:
    return "active" if info.get("status") == "Trading" else "halted"


def normalize_instrument(info: dict, market_type: MarketType) -> Instrument | None:
    if market_type in (MarketType.PERP, MarketType.CMARGIN):
        # linear/inverse categories mix perpetuals and dated futures
        if info.get("contractType") not in _PERP_CONTRACT_TYPES:
            return None
    price_filter = info.get("priceFilter") or {}
    lot = info.get("lotSizeFilter") or {}
    tick = price_filter.get("tickSize")
    if tick is None or not lot:
        return None

    if market_type == MarketType.SPOT:
        # Spot lot filter: basePrecision is the qty step, minOrderAmt the notional floor
        qty_step = lot.get("basePrecision")
        min_notional = lot.get("minOrderAmt")
        settle = None
    else:
        qty_step = lot.get("qtyStep")
        min_notional = lot.get("minNotionalValue")
        settle = info.get("settleCoin")
    if qty_step is None:
        return None

    base, quote = info["baseCoin"], info["quoteCoin"]
    max_qty = lot.get("maxOrderQty")
    return Instrument(
        universal_symbol=build_universal_symbol("BYBIT", market_type, base, quote),
        exchange="BYBIT",
        market_type=market_type,
        native_symbol=info["symbol"],
        base_asset=base,
        quote_asset=quote,
        filters=InstrumentFilters(
            price_tick=_dec(tick),
            qty_step=_dec(qty_step),
            min_qty=_dec(lot.get("minOrderQty", "0")),
            max_qty=_dec(max_qty) if max_qty is not None else None,
            min_notional=_dec(min_notional) if min_notional is not None else None,
        ),
        status=_status(info),
        settle_asset=settle,
    )


class BybitConnector(ExchangeConnector):
    exchange = "BYBIT"
    capabilities = frozenset({Capability.INSTRUMENTS, Capability.REFERENCE_PRICES})

    def __init__(self, bucket: TokenBucket | None = None):
        self._bucket = bucket
        self._session = HTTP(timeout=30)
        self._native_map: dict[tuple[MarketType, str], str] = {}

    async def _call(self, func, **kwargs):
        if self._bucket is not None:
            await self._bucket.acquire(priority=Priority.SYNC)
        return await asyncio.to_thread(func, **kwargs)

    # -- raw fetches (overridden by tests with canned payloads) --------------

    async def _fetch_category_instruments(self, category: str) -> list[dict]:
        """Walk the cursor pagination of one category to exhaustion."""
        items: list[dict] = []
        cursor: str | None = None
        while True:
            kwargs = {"category": category, "limit": 1000}
            if cursor:
                kwargs["cursor"] = cursor
            response = await self._call(self._session.get_instruments_info, **kwargs)
            result = response.get("result", {})
            items.extend(result.get("list", []))
            cursor = result.get("nextPageCursor") or None
            if not cursor:
                return items

    async def _fetch_category_tickers(self, category: str) -> list[dict]:
        response = await self._call(self._session.get_tickers, category=category)
        return response.get("result", {}).get("list", [])

    # -- connector interface --------------------------------------------------

    async def fetch_instruments(self) -> list[Instrument]:
        instruments: list[Instrument] = []
        for category, market_type in _CATEGORIES:
            for info in await self._fetch_category_instruments(category):
                instrument = normalize_instrument(info, market_type)
                if instrument is not None:
                    instruments.append(instrument)
        self._native_map = {
            (i.market_type, i.native_symbol): i.universal_symbol for i in instruments
        }
        return instruments

    async def fetch_reference_prices(self) -> dict[str, Decimal]:
        if not self._native_map:
            await self.fetch_instruments()
        prices: dict[str, Decimal] = {}
        for category, market_type in _CATEGORIES:
            for row in await self._fetch_category_tickers(category):
                universal = self._native_map.get((market_type, row.get("symbol", "")))
                last = row.get("lastPrice")
                if universal is not None and last:
                    prices[universal] = _dec(last)
        return prices
