"""OKX normalization and request-shaping tests (no network)."""
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.connectors.okx import (
    OkxAPIError,
    OkxConnector,
    normalize_asset_networks,
    normalize_deposit_record,
    normalize_funding_balances,
    normalize_funding_fee,
    normalize_order,
    normalize_positions,
    normalize_spot_instrument,
    normalize_swap_instrument,
    normalize_trade,
    normalize_unified_balances,
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
    PositionSide,
    TimeInForce,
    WalletType,
    WithdrawalStatus,
)

SPOT_BTC = {
    "instId": "BTC-USDT",
    "baseCcy": "BTC",
    "quoteCcy": "USDT",
    "tickSz": "0.1",
    "lotSz": "0.00001",
    "minSz": "0.00001",
    "state": "live",
}

SPOT_HALTED = {**SPOT_BTC, "instId": "ETH-USDT", "baseCcy": "ETH", "state": "suspend"}

USDT_SWAP = {
    "instId": "ETH-USDT-SWAP",
    "uly": "ETH-USDT",
    "baseCcy": "ETH",
    "quoteCcy": "USDT",
    "settleCcy": "USDT",
    "ctType": "linear",
    "ctVal": "0.1",
    "tickSz": "0.01",
    "lotSz": "1",
    "minSz": "1",
    "state": "live",
}

COIN_SWAP = {
    "instId": "BTC-USD-SWAP",
    "uly": "BTC-USD",
    "baseCcy": "BTC",
    "quoteCcy": "USD",
    "settleCcy": "BTC",
    "ctType": "inverse",
    "ctVal": "100",
    "tickSz": "0.1",
    "lotSz": "1",
    "minSz": "1",
    "state": "live",
}

# Delivery-style row without ctType linear/inverse should be skipped.
UNKNOWN_SWAP = {**USDT_SWAP, "instId": "ETH-USDT-240628", "ctType": "something"}


def test_normalize_spot_instrument():
    inst = normalize_spot_instrument(SPOT_BTC)
    assert inst.universal_symbol == "OKX_SPOT_BTCUSDT"
    assert inst.native_symbol == "BTC-USDT"
    assert inst.filters.price_tick == Decimal("0.1")
    assert inst.filters.qty_step == Decimal("0.00001")
    assert inst.status == "active"


def test_halted_spot_instrument():
    assert normalize_spot_instrument(SPOT_HALTED).status == "halted"


def test_normalize_swap_instruments():
    perp = normalize_swap_instrument(USDT_SWAP)
    assert perp.universal_symbol == "OKX_PERP_ETHUSDT"
    assert perp.settle_asset == "USDT"
    assert perp.contract_size == Decimal("0.1")
    inv = normalize_swap_instrument(COIN_SWAP)
    assert inv.universal_symbol == "OKX_CMARGIN_BTCUSD"
    assert inv.settle_asset == "BTC"
    assert normalize_swap_instrument(UNKNOWN_SWAP) is None


@pytest.fixture
def connector(monkeypatch):
    c = OkxConnector()

    async def api(method, path, **kwargs):
        params = kwargs.get("params") or {}
        if path == "/api/v5/public/instruments":
            if params.get("instType") == "SPOT":
                return [SPOT_BTC, SPOT_HALTED]
            return [USDT_SWAP, COIN_SWAP, UNKNOWN_SWAP]
        if path == "/api/v5/public/tickers":
            if params.get("instType") == "SPOT":
                return [{"instId": "BTC-USDT", "last": "62600.5"}]
            return [
                {"instId": "ETH-USDT-SWAP", "last": "3301"},
                {"instId": "BTC-USD-SWAP", "last": "62601"},
            ]
        return []

    monkeypatch.setattr(c, "_api", api)
    return c


async def test_fetch_instruments(connector):
    instruments = await connector.fetch_instruments()
    assert {i.universal_symbol for i in instruments} == {
        "OKX_SPOT_BTCUSDT",
        "OKX_SPOT_ETHUSDT",
        "OKX_PERP_ETHUSDT",
        "OKX_CMARGIN_BTCUSD",
    }


async def test_reference_prices(connector):
    prices = await connector.fetch_reference_prices()
    assert prices["OKX_SPOT_BTCUSDT"] == Decimal("62600.5")
    assert prices["OKX_PERP_ETHUSDT"] == Decimal("3301")
    assert prices["OKX_CMARGIN_BTCUSD"] == Decimal("62601")


def test_normalize_balances_drop_zero():
    unified = normalize_unified_balances(
        [
            {"ccy": "USDT", "availBal": "10", "frozenBal": "2"},
            {"ccy": "BTC", "availBal": "0", "frozenBal": "0", "cashBal": "0"},
        ]
    )
    assert len(unified) == 1
    assert unified[0].wallet == WalletType.UNIFIED
    assert unified[0].free == Decimal("10")
    assert unified[0].locked == Decimal("2")

    funding = normalize_funding_balances(
        [{"ccy": "USDT", "availBal": "5", "frozenBal": "0"}]
    )
    assert funding[0].wallet == WalletType.FUNDING


def test_normalize_positions():
    native_map = {(MarketType.PERP, "ETH-USDT-SWAP"): "OKX_PERP_ETHUSDT"}
    rows = [
        {
            "instId": "ETH-USDT-SWAP",
            "ctType": "linear",
            "posSide": "long",
            "pos": "2",
            "avgPx": "3000",
            "markPx": "3100",
            "upl": "200",
        },
        {"instId": "ETH-USDT-SWAP", "ctType": "linear", "posSide": "net", "pos": "0"},
    ]
    positions = normalize_positions(rows, native_map)
    assert len(positions) == 1
    assert positions[0].side == "long"
    assert positions[0].size == Decimal("2")


ORDER_RAW = {
    "ordId": "111",
    "clOrdId": "cpanel-1",
    "instId": "BTC-USDT",
    "side": "buy",
    "ordType": "limit",
    "state": "live",
    "sz": "0.01",
    "accFillSz": "0",
    "px": "60000",
    "cTime": "1710488334000",
}


def test_normalize_order_and_trade():
    order = normalize_order(ORDER_RAW, "OKX_SPOT_BTCUSDT")
    assert order.status == OrderStatus.OPEN
    assert order.client_order_id == "cpanel-1"
    cancelled = normalize_order({**ORDER_RAW, "state": "canceled"}, "OKX_SPOT_BTCUSDT")
    assert cancelled.status == OrderStatus.CANCELLED

    trade = normalize_trade(
        {
            "tradeId": "t1",
            "ordId": "111",
            "side": "buy",
            "fillPx": "60000",
            "fillSz": "0.01",
            "fee": "-0.1",
            "feeCcy": "USDT",
            "ts": "1710488334000",
        },
        "OKX_SPOT_BTCUSDT",
    )
    assert trade.fee == Decimal("0.1")
    assert trade.fee_asset == "USDT"


def test_withdraw_deposit_status_maps():
    w = normalize_withdrawal_record(
        {
            "wdId": "w1",
            "ccy": "USDT",
            "amt": "10",
            "state": "10",
            "chain": "USDT-TRC20",
            "to": "Txxx",
            "fee": "1",
        }
    )
    assert w.status == WithdrawalStatus.COMPLETED
    assert w.network == "TRC20"
    d = normalize_deposit_record(
        {
            "depId": "d1",
            "ccy": "USDT",
            "amt": "5",
            "state": "2",
            "chain": "USDT-TRC20",
        }
    )
    assert d.status == DepositStatus.CREDITED


def test_normalize_asset_networks():
    nets = normalize_asset_networks(
        [
            {
                "ccy": "USDT",
                "chain": "USDT-TRC20",
                "canDep": "true",
                "canWd": "true",
                "minWd": "10",
                "fee": "1",
            },
            {
                "ccy": "BTC",
                "chain": "BTC-Bitcoin",
                "canDep": "true",
                "canWd": "false",
                "minWd": "0.001",
                "fee": "0.0001",
            },
        ],
        "USDT",
    )
    assert len(nets) == 1
    assert nets[0].network == "TRC20"
    assert nets[0].withdraw_min == Decimal("10")


def test_normalize_funding_fee():
    fee = normalize_funding_fee(
        {
            "billId": "b1",
            "ccy": "USDT",
            "balChg": "-0.5",
            "ts": "1710488334000",
        },
        "OKX_PERP_ETHUSDT",
    )
    assert fee.amount == Decimal("-0.5")


async def test_fetch_balances(connector, monkeypatch):
    async def api(method, path, **kwargs):
        if path == "/api/v5/account/balance":
            return [
                {
                    "details": [
                        {"ccy": "USDT", "availBal": "10", "frozenBal": "0"},
                    ]
                }
            ]
        if path == "/api/v5/asset/balances":
            return [{"ccy": "USDT", "availBal": "3", "frozenBal": "0"}]
        return []

    monkeypatch.setattr(connector, "_api", api)
    bals = await connector.fetch_balances(
        {"api_key": "k", "secret": "s", "passphrase": "p"}
    )
    assert {(b.wallet, b.asset) for b in bals} == {
        (WalletType.UNIFIED, "USDT"),
        (WalletType.FUNDING, "USDT"),
    }


async def test_verify_credential_maps_perm(connector, monkeypatch):
    async def api(method, path, **kwargs):
        assert path == "/api/v5/account/config"
        assert kwargs["credential"]["passphrase"] == "pp"
        return [
            {
                "uid": "12345",
                "mainUid": "12345",
                "perm": "read_only,trade,withdraw",
            }
        ]

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


async def test_place_spot_limit_shapes_request(connector, monkeypatch):
    calls = []

    async def api(method, path, **kwargs):
        calls.append((method, path, kwargs.get("body"), kwargs.get("params")))
        if path == "/api/v5/public/instruments":
            params = kwargs.get("params") or {}
            if params.get("instType") == "SPOT":
                return [SPOT_BTC]
            return [USDT_SWAP, COIN_SWAP]
        if path == "/api/v5/trade/order" and method == "POST":
            return [{"ordId": "99", "clOrdId": "cid-1"}]
        if path == "/api/v5/trade/order" and method == "GET":
            return [{**ORDER_RAW, "ordId": "99", "state": "live"}]
        return []

    monkeypatch.setattr(connector, "_api", api)
    order = await connector.place_order(
        {"api_key": "k", "secret": "s", "passphrase": "p"},
        OrderRequest(
            universal_symbol="OKX_SPOT_BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            qty=Decimal("0.01"),
            price=Decimal("60000"),
            client_order_id="cid-1",
            time_in_force=TimeInForce.POST_ONLY,
        ),
    )
    post = next(c for c in calls if c[0] == "POST" and c[1] == "/api/v5/trade/order")
    body = post[2]
    assert body["ordType"] == "post_only"
    assert body["tdMode"] == "cash"
    assert body["clOrdId"] == "cid-1"
    assert body["px"] == "60000"
    assert body["instId"] == "BTC-USDT"
    assert order.native_order_id == "99"


async def test_place_perp_hedge_pos_side(connector, monkeypatch):
    calls = []

    async def api(method, path, **kwargs):
        calls.append((method, path, kwargs.get("body")))
        if path == "/api/v5/public/instruments":
            params = kwargs.get("params") or {}
            if params.get("instType") == "SPOT":
                return [SPOT_BTC]
            return [USDT_SWAP, COIN_SWAP]
        if path == "/api/v5/trade/order" and method == "POST":
            return [{"ordId": "55"}]
        if path == "/api/v5/trade/order" and method == "GET":
            return [
                {
                    "ordId": "55",
                    "instId": "ETH-USDT-SWAP",
                    "side": "buy",
                    "ordType": "limit",
                    "state": "live",
                    "sz": "1",
                    "accFillSz": "0",
                    "px": "3000",
                }
            ]
        return []

    monkeypatch.setattr(connector, "_api", api)
    await connector.place_order(
        {"api_key": "k", "secret": "s", "passphrase": "p"},
        OrderRequest(
            universal_symbol="OKX_PERP_ETHUSDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            qty=Decimal("1"),
            price=Decimal("3000"),
            position_side=PositionSide.LONG,
            reduce_only=True,
        ),
    )
    body = next(c[2] for c in calls if c[0] == "POST" and c[1] == "/api/v5/trade/order")
    assert body["tdMode"] == "cross"
    assert body["posSide"] == "long"
    assert body["reduceOnly"] is True


async def test_transfer_wallet_legs(connector, monkeypatch):
    calls = []

    async def api(method, path, **kwargs):
        calls.append((path, kwargs.get("body")))
        return [{"transId": "tr1"}]

    monkeypatch.setattr(connector, "_api", api)
    result = await connector.transfer(
        {"api_key": "k", "secret": "s", "passphrase": "p"},
        "USDT",
        Decimal("1"),
        from_wallet=WalletType.FUNDING,
        to_wallet=WalletType.UNIFIED,
    )
    assert result.native_transfer_id == "tr1"
    assert calls[0][1]["from"] == "6"
    assert calls[0][1]["to"] == "18"
    assert calls[0][1]["type"] == "0"

    with pytest.raises(ValueError, match="FUNDING and UNIFIED"):
        await connector.transfer(
            {"api_key": "k", "secret": "s", "passphrase": "p"},
            "USDT",
            Decimal("1"),
            from_wallet=WalletType.UNIFIED,
            to_wallet=WalletType.UNIFIED,
        )


async def test_transfer_rejects_unknown_wallet(connector):
    with pytest.raises(ValueError, match="UNIFIED and FUNDING"):
        await connector.transfer(
            {"api_key": "k", "secret": "s", "passphrase": "p"},
            "USDT",
            Decimal("1"),
            from_wallet=WalletType.SPOT,
            to_wallet=WalletType.FUNDING,
        )


async def test_transfer_master_to_sub(connector, monkeypatch):
    calls = []

    async def api(method, path, **kwargs):
        calls.append((path, kwargs.get("body"), kwargs.get("params")))
        if path == "/api/v5/users/subaccount/list":
            return [
                {
                    "uid": "999",
                    "subAcct": "trading1",
                    "label": "t1",
                    "enable": True,
                }
            ]
        return [{"transId": "tr2"}]

    monkeypatch.setattr(connector, "_api", api)
    result = await connector.transfer(
        {"api_key": "k", "secret": "s", "passphrase": "p"},
        "USDT",
        Decimal("2"),
        from_wallet=WalletType.FUNDING,
        to_wallet=WalletType.FUNDING,
        to_subaccount="999",
    )
    assert result.native_transfer_id == "tr2"
    path, body, _ = calls[-1]
    assert path == "/api/v5/asset/transfer"
    assert body["type"] == "1"
    assert body["subAcct"] == "trading1"


def test_history_stream_keys(connector):
    assert connector.history_stream_keys([]) == [
        "spot",
        "swap:linear",
        "swap:inverse",
    ]


async def test_history_cursor_round_trip(connector, monkeypatch):
    pages = []

    async def api(method, path, **kwargs):
        params = kwargs.get("params") or {}
        if path == "/api/v5/public/instruments":
            if params.get("instType") == "SPOT":
                return [SPOT_BTC]
            return [USDT_SWAP]
        pages.append(params)
        if params.get("after"):
            return []
        return [
            {
                "billId": str(i),
                "tradeId": str(i),
                "ordId": "o",
                "instId": "BTC-USDT",
                "side": "buy",
                "fillPx": "1",
                "fillSz": "1",
                "fee": "0",
                "feeCcy": "USDT",
                "ts": params["begin"],
            }
            for i in range(100)
        ]

    monkeypatch.setattr(connector, "_api", api)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    cursor = connector.initial_history_cursor(start)
    page = await connector.fetch_trades(
        {"api_key": "k", "secret": "s", "passphrase": "p"}, "spot", cursor
    )
    assert len(page.items) == 100
    assert page.next_cursor is not None
    decoded = json.loads(page.next_cursor)
    assert decoded["after"] == "99"
    assert decoded["start"] == int(start.timestamp() * 1000)

    page2 = await connector.fetch_trades(
        {"api_key": "k", "secret": "s", "passphrase": "p"}, "spot", page.next_cursor
    )
    assert pages[1]["after"] == "99"
    assert page2.items == []


async def test_create_api_key_includes_passphrase(connector, monkeypatch):
    async def api(method, path, **kwargs):
        if path == "/api/v5/users/subaccount/list":
            return [{"uid": "999", "subAcct": "mysub01", "label": "x", "enable": True}]
        body = kwargs["body"]
        assert body["subAcct"] == "mysub01"
        assert "passphrase" in body
        assert any(c.isupper() for c in body["passphrase"])
        assert any(c.islower() for c in body["passphrase"])
        assert any(c.isdigit() for c in body["passphrase"])
        assert any(c in "!@#$%^&*" for c in body["passphrase"])
        assert "trade" in body["perm"]
        return [
            {
                "apiKey": "okx_ak",
                "secretKey": "sk",
                "passphrase": body["passphrase"],
                "perm": body["perm"],
            }
        ]

    monkeypatch.setattr(connector, "_api", api)
    key = await connector.create_api_key(
        {"api_key": "k", "secret": "s", "passphrase": "p"},
        subaccount="999",
        scheme=KeyScheme.HMAC,
        permissions=["spot"],
    )
    assert key.api_key == "okx_ak"
    assert key.secret == "sk"
    assert key.passphrase
    assert key.scheme == KeyScheme.HMAC


async def test_create_subaccount_59518(connector, monkeypatch):
    async def api(method, path, **kwargs):
        raise OkxAPIError("blocked", code="59518", status_code=200)

    monkeypatch.setattr(connector, "_api", api)
    with pytest.raises(OkxAPIError, match="59518|website"):
        await connector.create_subaccount(
            {"api_key": "k", "secret": "s", "passphrase": "p"}, "label1"
        )


async def test_deposit_address_rejects_subaccount(connector):
    with pytest.raises(ValueError, match="master-for-sub"):
        await connector.fetch_deposit_address(
            {"api_key": "k", "secret": "s", "passphrase": "p"},
            "USDT",
            "TRC20",
            subaccount="999",
        )


def test_okx_api_error_attrs():
    err = OkxAPIError("bad key", code="50111", status_code=401)
    assert err.code == "50111"
    assert err.status_code == 401


def test_sign_stable():
    from app.connectors.okx import _sign

    sig = _sign(
        "secret",
        "2020-12-08T09:08:57.715Z",
        "GET",
        "/api/v5/account/balance",
        "",
        "",
    )
    # Deterministic base64 HMAC.
    assert isinstance(sig, str) and len(sig) > 10
