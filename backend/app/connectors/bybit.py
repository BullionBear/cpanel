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
import logging
from decimal import Decimal

from pybit.unified_trading import HTTP

from app.connectors.base import Capability, Credential, ExchangeConnector
from app.connectors.errors import credential_error_reason
from app.connectors.symbols import build_universal_symbol
from app.connectors.types import (
    ApiKeyResult,
    CredentialInfo,
    Instrument,
    InstrumentFilters,
    KeyScheme,
    MarketType,
    SubaccountInfo,
)
from app.core.ratelimit import Priority, TokenBucket

logger = logging.getLogger(__name__)

# Permission sets granted to panel-created sub API keys
_TRADE_PERMISSIONS = {
    "ContractTrade": ["Order", "Position"],
    "Spot": ["SpotTrade"],
    "Wallet": ["AccountTransfer", "SubMemberTransferList"],
}
_READ_PERMISSIONS = {
    "ContractTrade": ["Position"],
    "Wallet": ["AccountTransfer"],
}

# Panel-level permission scope tokens → Bybit's per-module permission entries.
# The dialog lets the operator pick any combination of these when creating a key.
_PERMISSION_SCOPES: dict[str, dict[str, list[str]]] = {
    "spot": {"Spot": ["SpotTrade"]},
    "derivatives": {"ContractTrade": ["Order", "Position"]},
    "wallet": {"Wallet": ["AccountTransfer", "SubMemberTransferList"]},
    "options": {"Options": ["OptionsTrade"]},
    "copytrading": {"CopyTrading": ["CopyTrading"]},
    "exchange": {"Exchange": ["ExchangeHistory"]},
}


def _build_permissions(scopes: list[str]) -> dict[str, list[str]]:
    """Merge the selected scope tokens into Bybit's permission object.

    Falls back to a minimal read-only position permission when nothing usable is
    selected (Bybit rejects an empty permission set)."""
    merged: dict[str, list[str]] = {}
    for scope in scopes:
        for category, entries in _PERMISSION_SCOPES.get(scope, {}).items():
            bucket = merged.setdefault(category, [])
            for entry in entries:
                if entry not in bucket:
                    bucket.append(entry)
    return merged or {"ContractTrade": ["Position"]}

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
    # Bybit exposes full self-service account management over API, so the
    # whole provisioning surface is available (HMAC keys only — Bybit does
    # not issue Ed25519 keys via API).
    capabilities = frozenset(
        {
            Capability.INSTRUMENTS,
            Capability.REFERENCE_PRICES,
            Capability.CREDENTIAL_VERIFY,
            Capability.SUBACCOUNT_LIST,
            Capability.SUBACCOUNT_CREATE,
            Capability.SUBACCOUNT_FREEZE,
            Capability.API_KEY_CREATE,
            Capability.API_KEY_MODIFY,
            Capability.API_KEY_REVOKE,
        }
    )

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

    # -- accounts -----------------------------------------------------------

    @staticmethod
    def _auth_session(credential: Credential) -> HTTP:
        return HTTP(
            api_key=credential["api_key"],
            api_secret=credential["secret"],
            timeout=30,
        )

    async def verify_credential(self, credential: Credential) -> CredentialInfo:
        session = self._auth_session(credential)
        try:
            response = await self._call(session.get_api_key_information)
        except Exception as exc:
            reason = credential_error_reason(self.exchange, exc)
            logger.warning("Bybit credential verification failed: %s", reason, exc_info=True)
            return CredentialInfo(valid=False, reason=reason)
        info = response.get("result", {})
        permissions = info.get("permissions") or {}
        can_trade = bool(permissions.get("ContractTrade") or permissions.get("Spot"))
        can_withdraw = "Withdraw" in (permissions.get("Wallet") or [])
        return CredentialInfo(
            valid=True,
            native_uid=str(info.get("userID", "")) or None,
            is_master=bool(info.get("isMaster")),
            read_only=info.get("readOnly") == 1,
            can_trade=can_trade,
            can_withdraw=can_withdraw,
            permissions=permissions,
        )

    async def list_subaccounts(self, credential: Credential) -> list[SubaccountInfo]:
        session = self._auth_session(credential)
        response = await self._call(session.get_sub_uid_list)
        members = response.get("result", {}).get("subMembers", [])
        return [
            SubaccountInfo(
                native_uid=str(m["uid"]),
                label=m.get("username") or str(m["uid"]),
                status="normal" if m.get("status") == 1 else "frozen",
                raw=m,
            )
            for m in members
        ]

    async def create_subaccount(self, credential: Credential, label: str) -> SubaccountInfo:
        session = self._auth_session(credential)
        response = await self._call(
            session.create_sub_uid, username=label, memberType=1, note="created by cpanel"
        )
        result = response.get("result", {})
        return SubaccountInfo(
            native_uid=str(result.get("uid", "")),
            label=result.get("username") or label,
            status="normal",
        )

    async def create_api_key(
        self, credential: Credential, subaccount: str | None, scheme: KeyScheme,
        permissions: list[str],
        read_only: bool = False,
        ips: list[str] | None = None,
        note: str | None = None,
    ) -> ApiKeyResult:
        if scheme != KeyScheme.HMAC:
            raise ValueError("Bybit issues only HMAC keys via API")
        if not subaccount:
            raise ValueError("Bybit API key creation targets a subaccount uid")
        session = self._auth_session(credential)
        kwargs: dict = {
            "subuid": int(subaccount),
            "readOnly": 1 if read_only else 0,
            "permissions": _build_permissions(permissions),
            "note": note or "created by cpanel",
        }
        if ips:
            kwargs["ips"] = ",".join(ips)
        response = await self._call(session.create_sub_api_key, **kwargs)
        result = response.get("result", {})
        return ApiKeyResult(
            api_key=result["apiKey"],
            secret=result["secret"],
            scheme=KeyScheme.HMAC,
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
        """Change a sub key's read-only flag and/or permission set.

        Called with the master credential, so the target sub key is named via
        the ``apikey`` parameter (Bybit's convention for master-managed keys).
        """
        session = self._auth_session(credential)
        kwargs: dict = {"apikey": api_key}
        if read_only is not None:
            kwargs["readOnly"] = 1 if read_only else 0
        if permissions is not None:
            grant_trade = "trade" in permissions
            kwargs["permissions"] = _TRADE_PERMISSIONS if grant_trade else _READ_PERMISSIONS
        await self._call(session.modify_sub_api_key, **kwargs)

    async def revoke_api_key(
        self, credential: Credential, subaccount: str | None, api_key: str
    ) -> None:
        session = self._auth_session(credential)
        await self._call(session.delete_sub_api_key, apiKey=api_key)

    async def set_subaccount_frozen(
        self, credential: Credential, subaccount: str, frozen: bool
    ) -> None:
        session = self._auth_session(credential)
        await self._call(
            session.freeze_sub_uid, subuid=int(subaccount), frozen=1 if frozen else 0
        )

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
