"""Bitget normalization and request-shaping tests (no network)."""
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.connectors.bitget import (
    BitgetAPIError,
    BitgetConnector,
    normalize_asset_networks,
    normalize_deposit_record,
    normalize_funding_balances,
    normalize_funding_fee,
    normalize_mix_instrument,
    normalize_mix_order,
    normalize_mix_trade,
    normalize_positions,
    normalize_uta_balances,
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
    TimeInForce,
    WalletType,
    WithdrawalStatus,
)

SPOT_BTC = {
    "symbol": "BTCUSDT",
    "baseCoin": "BTC",
    "quoteCoin": "USDT",
    "pricePrecision": "2",
    "quantityPrecision": "6",
    "minTradeAmount": "0.0001",
    "maxTradeAmount": "1000",
    "minTradeUSDT": "5",
    "status": "online",
}

SPOT_HALTED = {**SPOT_BTC, "symbol": "ETHUSDT", "baseCoin": "ETH", "status": "halt"}

USDT_PERP = {
    "symbol": "ETHUSDT",
    "baseCoin": "ETH",
    "quoteCoin": "USDT",
    "symbolType": "perpetual",
    "symbolStatus": "normal",
    "pricePlace": "2",
    "priceEndStep": "1",
    "volumePlace": "0",
    "minTradeNum": "1",
    "sizeMultiplier": "0.01",
}

COIN_PERP = {
    "symbol": "BTCUSD",
    "baseCoin": "BTC",
    "quoteCoin": "USD",
    "symbolType": "perpetual",
    "symbolStatus": "normal",
    "pricePlace": "1",
    "priceEndStep": "1",
    "volumePlace": "0",
    "minTradeNum": "1",
}

DELIVERY = {**USDT_PERP, "symbol": "ETHUSDCM24", "symbolType": "delivery"}


def test_normalize_spot_instrument():
    inst = normalize_spot_instrument(SPOT_BTC)
    assert inst.universal_symbol == "BITGET_SPOT_BTCUSDT"
    assert inst.native_symbol == "BTCUSDT"
    assert inst.filters.price_tick == Decimal("0.01")
    assert inst.filters.qty_step == Decimal("0.000001")
    assert inst.filters.min_notional == Decimal("5")
    assert inst.status == "active"


def test_halted_spot_instrument():
    assert normalize_spot_instrument(SPOT_HALTED).status == "halted"


def test_v3_spot_limit_open_and_order_limits():
    inst = normalize_spot_instrument(
        {
            "symbol": "BTCUSDT",
            "baseCoin": "BTC",
            "quoteCoin": "USDT",
            "pricePrecision": "2",
            "quantityPrecision": "4",
            "minOrderQty": "0.0001",
            "maxOrderQty": "120",
            "minOrderAmount": "1",
            "status": "limit_open",
        }
    )
    assert inst.status == "active"
    assert inst.filters.min_qty == Decimal("0.0001")
    assert inst.filters.max_qty == Decimal("120")
    assert inst.filters.min_notional == Decimal("1")
    assert inst.filters.qty_step == Decimal("0.0001")


def test_v3_mix_uses_multipliers_and_skips_delivery():
    perp = normalize_mix_instrument(
        {
            "symbol": "SHIBUSDT",
            "baseCoin": "SHIB",
            "quoteCoin": "USDT",
            "type": "perpetual",
            "status": "online",
            "pricePrecision": "6",
            "quantityPrecision": "0",
            "priceMultiplier": "0.000001",
            "quantityMultiplier": "10000",
            "minOrderQty": "10000",
        },
        MarketType.PERP,
    )
    assert perp.filters.price_tick == Decimal("0.000001")
    assert perp.filters.qty_step == Decimal("10000")
    assert perp.native_symbol == "SHIBUSDT"
    coin = normalize_mix_instrument(
        {
            "symbol": "BTCUSD_CM",
            "baseCoin": "BTC",
            "quoteCoin": "USD",
            "type": "perpetual",
            "status": "online",
            "priceMultiplier": "0.1",
            "quantityMultiplier": "1",
            "deliveryPeriod": "",
        },
        MarketType.CMARGIN,
    )
    assert coin.native_symbol == "BTCUSD_CM"
    assert coin.universal_symbol == "BITGET_CMARGIN_BTCUSD"
    dated = {
        "symbol": "BTCCMZ26",
        "baseCoin": "BTC",
        "quoteCoin": "USD",
        "type": "delivery",
        "status": "online",
        "deliveryPeriod": "next_quarter",
        "priceMultiplier": "0.1",
        "quantityMultiplier": "1",
    }
    assert normalize_mix_instrument(dated, MarketType.CMARGIN) is None


def test_normalize_mix_instruments():
    perp = normalize_mix_instrument(USDT_PERP, MarketType.PERP)
    assert perp.universal_symbol == "BITGET_PERP_ETHUSDT"
    assert perp.settle_asset == "USDT"
    assert perp.filters.qty_step == Decimal("1")
    inv = normalize_mix_instrument(COIN_PERP, MarketType.CMARGIN)
    assert inv.universal_symbol == "BITGET_CMARGIN_BTCUSD"
    assert inv.settle_asset == "BTC"
    assert normalize_mix_instrument(DELIVERY, MarketType.PERP) is None


@pytest.fixture
def connector(monkeypatch):
    c = BitgetConnector()

    async def spot_symbols():
        return [SPOT_BTC, SPOT_HALTED]

    async def mix_contracts(product_type):
        return {
            "USDT-FUTURES": [USDT_PERP, DELIVERY],
            "COIN-FUTURES": [COIN_PERP],
        }[product_type]

    async def spot_tickers():
        return [{"symbol": "BTCUSDT", "lastPr": "62600.5"}]

    async def mix_tickers(product_type):
        return {
            "USDT-FUTURES": [{"symbol": "ETHUSDT", "lastPr": "3301"}],
            "COIN-FUTURES": [{"symbol": "BTCUSD", "lastPr": "62601"}],
        }[product_type]

    monkeypatch.setattr(c, "_fetch_spot_symbols", spot_symbols)
    monkeypatch.setattr(c, "_fetch_mix_contracts", mix_contracts)
    monkeypatch.setattr(c, "_fetch_spot_tickers", spot_tickers)
    monkeypatch.setattr(c, "_fetch_mix_tickers", mix_tickers)
    return c


async def test_fetch_instruments(connector):
    instruments = await connector.fetch_instruments()
    assert {i.universal_symbol for i in instruments} == {
        "BITGET_SPOT_BTCUSDT",
        "BITGET_SPOT_ETHUSDT",
        "BITGET_PERP_ETHUSDT",
        "BITGET_CMARGIN_BTCUSD",
    }


async def test_reference_prices(connector):
    prices = await connector.fetch_reference_prices()
    assert prices["BITGET_SPOT_BTCUSDT"] == Decimal("62600.5")
    assert prices["BITGET_PERP_ETHUSDT"] == Decimal("3301")
    assert prices["BITGET_CMARGIN_BTCUSD"] == Decimal("62601")


def test_normalize_uta_balances_drops_zero():
    rows = [
        {"coin": "USDT", "available": "10", "frozen": "1", "locked": "1"},
        {"coin": "BTC", "available": "0", "equity": "0", "locked": "0"},
        {"coin": "ETH", "available": "0", "locked": "0", "equity": "0.2"},
    ]
    bals = normalize_uta_balances(rows)
    assert len(bals) == 2
    assert bals[0].wallet == WalletType.UNIFIED
    assert bals[0].free == Decimal("10")
    assert bals[0].locked == Decimal("2")
    assert bals[1].asset == "ETH"
    assert bals[1].free == Decimal("0.2")


def test_normalize_funding_balances():
    funding = normalize_funding_balances(
        [{"coin": "USDT", "available": "5", "frozen": "0"}]
    )
    assert funding[0].wallet == WalletType.FUNDING


def test_normalize_positions():
    native_map = {(MarketType.PERP, "ETHUSDT"): "BITGET_PERP_ETHUSDT"}
    rows = [
        {
            "symbol": "ETHUSDT",
            "holdSide": "long",
            "total": "2",
            "openPriceAvg": "3000",
            "markPrice": "3100",
            "unrealizedPL": "200",
        },
        {"symbol": "ETHUSDT", "holdSide": "long", "total": "0"},
    ]
    positions = normalize_positions(rows, "USDT-FUTURES", native_map)
    assert len(positions) == 1
    assert positions[0].side == "long"
    assert positions[0].size == Decimal("2")


SPOT_ORDER = {
    "orderId": "111",
    "clientOid": "cpanel-1",
    "symbol": "BTCUSDT",
    "side": "buy",
    "orderType": "limit",
    "status": "live",
    "size": "0.01",
    "price": "60000",
    "baseVolume": "0",
    "cTime": "1710488334000",
    "uTime": "1710488334000",
}


def test_normalize_spot_order_statuses():
    open_o = normalize_spot_order(SPOT_ORDER, "BITGET_SPOT_BTCUSDT")
    assert open_o.status == OrderStatus.OPEN
    assert open_o.client_order_id == "cpanel-1"
    filled = normalize_spot_order(
        {**SPOT_ORDER, "status": "filled", "baseVolume": "0.01"},
        "BITGET_SPOT_BTCUSDT",
    )
    assert filled.status == OrderStatus.FILLED
    assert filled.filled_qty == Decimal("0.01")
    cancelled = normalize_spot_order(
        {**SPOT_ORDER, "status": "cancelled"}, "BITGET_SPOT_BTCUSDT"
    )
    assert cancelled.status == OrderStatus.CANCELLED


def test_normalize_mix_order_and_trade():
    order = normalize_mix_order(
        {
            "orderId": "222",
            "clientOid": "x",
            "side": "sell",
            "orderType": "market",
            "status": "partially_filled",
            "size": "10",
            "baseVolume": "4",
            "priceAvg": "3300",
        },
        "BITGET_PERP_ETHUSDT",
    )
    assert order.side == OrderSide.SELL
    assert order.type == OrderType.MARKET
    assert order.status == OrderStatus.PARTIALLY_FILLED
    trade = normalize_mix_trade(
        {
            "tradeId": "t1",
            "orderId": "222",
            "side": "sell",
            "price": "3300",
            "baseVolume": "4",
            "feeDetail": {"totalFee": "-0.1", "feeCoin": "USDT"},
            "cTime": "1710488334000",
        },
        "BITGET_PERP_ETHUSDT",
    )
    assert trade.fee == Decimal("0.1")
    assert trade.fee_asset == "USDT"


def test_normalize_spot_trade_fee_json_string():
    trade = normalize_spot_trade(
        {
            "tradeId": "9",
            "orderId": "1",
            "side": "buy",
            "priceAvg": "100",
            "size": "1",
            "feeDetail": json.dumps({"totalFee": "0.02", "feeCoin": "USDT"}),
            "cTime": "1710488334000",
        },
        "BITGET_SPOT_BTCUSDT",
    )
    assert trade.fee == Decimal("0.02")


def test_withdraw_deposit_status_maps():
    w = normalize_withdrawal_record(
        {
            "orderId": "w1",
            "coin": "USDT",
            "size": "10",
            "status": "success",
            "chain": "TRC20",
            "address": "Txxx",
            "fee": "1",
        }
    )
    assert w.status == WithdrawalStatus.COMPLETED
    d = normalize_deposit_record(
        {"orderId": "d1", "coin": "USDT", "size": "5", "status": "2", "chain": "TRC20"}
    )
    assert d.status == DepositStatus.CREDITED


def test_normalize_asset_networks():
    nets = normalize_asset_networks(
        {
            "coin": "USDT",
            "chains": [
                {
                    "chain": "TRC20",
                    "withdrawable": "true",
                    "rechargeable": "true",
                    "minWithdrawAmount": "10",
                    "withdrawFee": "1",
                }
            ],
        }
    )
    assert len(nets) == 1
    assert nets[0].network == "TRC20"
    assert nets[0].withdraw_min == Decimal("10")


def test_normalize_funding_fee():
    fee = normalize_funding_fee(
        {
            "billId": "b1",
            "coin": "USDT",
            "amount": "-0.5",
            "fundingRate": "0.0001",
            "cTime": "1710488334000",
        },
        "BITGET_PERP_ETHUSDT",
    )
    assert fee.amount == Decimal("-0.5")
    assert fee.funding_rate == Decimal("0.0001")


async def test_fetch_balances(connector, monkeypatch):
    async def uta(cred):
        return [{"coin": "USDT", "available": "10", "locked": "0", "equity": "10"}]

    async def funding(cred):
        return [{"coin": "USDT", "available": "3", "frozen": "0"}]

    monkeypatch.setattr(connector, "_fetch_uta_assets", uta)
    monkeypatch.setattr(connector, "_fetch_funding_assets", funding)
    bals = await connector.fetch_balances(
        {"api_key": "k", "secret": "s", "passphrase": "p"}
    )
    assert {(b.wallet, b.asset) for b in bals} == {
        (WalletType.UNIFIED, "USDT"),
        (WalletType.FUNDING, "USDT"),
    }


async def test_verify_credential_maps_authorities(connector, monkeypatch):
    async def api(method, path, **kwargs):
        assert path == "/api/v3/account/info"
        assert kwargs["credential"]["passphrase"] == "pp"
        return {
            "userId": "12345",
            "parentId": "",
            "permType": "read-and-write",
            "permissions": ["uta_mgt", "uta_trade", "withdraw"],
        }

    monkeypatch.setattr(connector, "_api", api)
    info = await connector.verify_credential(
        {"api_key": "k", "secret": "s", "passphrase": "pp"}
    )
    assert info.valid
    assert info.native_uid == "12345"
    assert info.is_master is True
    assert info.can_trade is True
    assert info.can_withdraw is True
    assert info.read_only is False


async def test_verify_requires_passphrase(connector):
    info = await connector.verify_credential({"api_key": "k", "secret": "s"})
    assert info.valid is False
    assert "passphrase" in (info.reason or "").lower() or "rejected" in (
        info.reason or ""
    ).lower()


async def test_place_spot_limit_shapes_request(connector, monkeypatch):
    calls = []

    async def api(method, path, **kwargs):
        calls.append((method, path, kwargs.get("body")))
        if path.endswith("place-order"):
            return {"orderId": "99"}
        if path.endswith("order-info"):
            return {**SPOT_ORDER, "orderId": "99", "orderStatus": "live"}
        return []

    monkeypatch.setattr(connector, "_api", api)
    await connector.fetch_instruments()
    order = await connector.place_order(
        {"api_key": "k", "secret": "s", "passphrase": "p"},
        OrderRequest(
            universal_symbol="BITGET_SPOT_BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            qty=Decimal("0.01"),
            price=Decimal("60000"),
            client_order_id="cid-1",
            time_in_force=TimeInForce.POST_ONLY,
        ),
    )
    method, path, body = calls[0]
    assert method == "POST" and path == "/api/v3/trade/place-order"
    assert body["category"] == "SPOT"
    assert body["timeInForce"] == "post_only"
    assert body["qty"] == "0.01"
    assert body["clientOid"] == "cid-1"
    assert body["price"] == "60000"
    assert order.native_order_id == "99"


async def test_spot_market_buy_converts_base_qty_to_quote(connector, monkeypatch):
    calls = []

    async def api(method, path, **kwargs):
        calls.append(kwargs.get("body"))
        if path.endswith("place-order"):
            return {"orderId": "100"}
        return None

    async def prices():
        return {"BITGET_SPOT_BTCUSDT": Decimal("60000")}

    monkeypatch.setattr(connector, "_api", api)
    monkeypatch.setattr(connector, "fetch_reference_prices", prices)
    await connector.fetch_instruments()
    await connector.place_order(
        {"api_key": "k", "secret": "s", "passphrase": "p"},
        OrderRequest(
            universal_symbol="BITGET_SPOT_BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            qty=Decimal("0.01"),
        ),
    )
    body = calls[0]
    assert body["orderType"] == "market"
    assert body["timeInForce"] == "ioc"
    assert body["qty"] == "600.00"
    assert "price" not in body


async def test_transfer_wallet_legs(connector, monkeypatch):
    calls = []

    async def api(method, path, **kwargs):
        calls.append((path, kwargs.get("body")))
        return {"transferId": "tr1"}

    monkeypatch.setattr(connector, "_api", api)
    result = await connector.transfer(
        {"api_key": "k", "secret": "s", "passphrase": "p"},
        "USDT",
        Decimal("1"),
        from_wallet=WalletType.UNIFIED,
        to_wallet=WalletType.FUNDING,
    )
    assert result.native_transfer_id == "tr1"
    assert calls[0][0] == "/api/v3/account/transfer"
    assert calls[0][1]["fromType"] == "uta"
    assert calls[0][1]["toType"] == "spot"

    with pytest.raises(ValueError, match="distinct"):
        await connector.transfer(
            {"api_key": "k", "secret": "s", "passphrase": "p"},
            "USDT",
            Decimal("1"),
            from_wallet=WalletType.UNIFIED,
            to_wallet=WalletType.UNIFIED,
        )


async def test_transfer_rejects_classic_wallet(connector):
    with pytest.raises(ValueError, match="UTA"):
        connector._wallet_leg(WalletType.UMARGIN)


def test_wallet_leg_is_uta_or_funding(connector):
    assert connector._wallet_leg(WalletType.UNIFIED) == "uta"
    assert connector._wallet_leg(WalletType.FUNDING) == "spot"
    assert connector._wallet_leg(None) == "uta"


async def test_sub_transfer_sends_both_user_ids(connector, monkeypatch):
    calls = []

    async def api(method, path, **kwargs):
        calls.append((path, kwargs.get("body")))
        if path.endswith("/account/info"):
            return {"userId": "111"}
        return {"transferId": "tr2"}

    monkeypatch.setattr(connector, "_api", api)
    await connector.transfer(
        {"api_key": "k", "secret": "s", "passphrase": "p"},
        "USDT",
        Decimal("50"),
        from_wallet=WalletType.UNIFIED,
        to_wallet=WalletType.UNIFIED,
        to_subaccount="222",
    )
    path, body = calls[-1]
    assert path == "/api/v3/account/sub-transfer"
    assert body["fromUserId"] == "111"
    assert body["toUserId"] == "222"
    assert body["fromType"] == "uta"
    assert body["toType"] == "uta"
    assert body["clientOid"]


def test_history_stream_keys(connector):
    assert connector.history_stream_keys([]) == [
        "SPOT",
        "USDT-FUTURES",
        "COIN-FUTURES",
    ]
    assert connector.funding_history_stream_keys() == [
        "USDT-FUTURES",
        "COIN-FUTURES",
    ]


async def test_history_cursor_round_trip(connector, monkeypatch):
    pages = []

    async def api(method, path, **kwargs):
        params = kwargs.get("params") or {}
        pages.append(params)
        if params.get("cursor"):
            return {"list": [], "cursor": ""}
        # Full page so the next cursor keeps the window and the page token.
        return {
            "list": [
                {
                    "execId": str(i),
                    "orderId": "o",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "execPrice": "1",
                    "execQty": "1",
                    "feeDetail": [],
                    "createdTime": params["startTime"],
                }
                for i in range(100)
            ],
            "cursor": "99",
        }

    monkeypatch.setattr(connector, "_api", api)
    await connector.fetch_instruments()
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    cursor = connector.initial_history_cursor(start)
    page = await connector.fetch_trades(
        {"api_key": "k", "secret": "s", "passphrase": "p"}, "SPOT", cursor
    )
    assert len(page.items) == 100
    assert page.next_cursor is not None
    decoded = json.loads(page.next_cursor)
    assert decoded["cursor"] == "99"
    assert decoded["start"] == int(start.timestamp() * 1000)
    assert pages[0]["category"] == "SPOT"

    page2 = await connector.fetch_trades(
        {"api_key": "k", "secret": "s", "passphrase": "p"}, "SPOT", page.next_cursor
    )
    assert pages[1]["cursor"] == "99"
    assert page2.items == []


async def test_create_api_key_includes_passphrase(connector, monkeypatch):
    async def api(method, path, **kwargs):
        body = kwargs["body"]
        assert "passphrase" in body
        assert body["passphrase"].isalnum()
        assert any(c.isalpha() for c in body["passphrase"])
        assert any(c.isdigit() for c in body["passphrase"])
        assert "read" in body["permList"]
        assert "spot_trade" in body["permList"]
        # Venue response uses subAccountApiKey / secretKey — not apiKey / secret.
        return {
            "subAccountUid": "999",
            "subAccountApiKey": "bg_ak",
            "secretKey": "sk",
            "permList": body["permList"],
            "label": body["label"],
        }

    monkeypatch.setattr(connector, "_api", api)
    key = await connector.create_api_key(
        {"api_key": "k", "secret": "s", "passphrase": "p"},
        subaccount="999",
        scheme=KeyScheme.HMAC,
        permissions=["spot"],
    )
    assert key.api_key == "bg_ak"
    assert key.secret == "sk"
    assert key.passphrase and key.passphrase.isalnum()
    assert key.scheme == KeyScheme.HMAC


async def test_create_api_key_rejects_empty_response(connector, monkeypatch):
    async def api(method, path, **kwargs):
        return {"subAccountUid": "999", "label": "x"}

    monkeypatch.setattr(connector, "_api", api)
    with pytest.raises(BitgetAPIError, match="no api key"):
        await connector.create_api_key(
            {"api_key": "k", "secret": "s", "passphrase": "p"},
            subaccount="999",
            scheme=KeyScheme.HMAC,
            permissions=["spot"],
        )


def test_bitget_api_error_attrs():
    err = BitgetAPIError("bad key", code="40001", status_code=400)
    assert err.message == "bad key"
    assert err.status_code == 400
