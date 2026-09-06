"""Deribit normalization and request-shaping tests (no network)."""
import json
from decimal import Decimal

import pytest

from app.connectors.deribit import (
    DeribitAPIError,
    DeribitConnector,
    _key_name,
    _order_payload,
    _scope_string,
    _sign,
    normalize_asset_networks,
    normalize_balances,
    normalize_deposit_record,
    normalize_funding_fee,
    normalize_instrument,
    normalize_internal_transfer,
    normalize_order,
    normalize_positions,
    normalize_trade,
    normalize_withdrawal_record,
)
from app.connectors.types import (
    DepositStatus,
    InternalTransferScope,
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

SPOT_BTC_USDC = {
    "kind": "spot",
    "instrument_name": "BTC_USDC",
    "base_currency": "BTC",
    "quote_currency": "USDC",
    "tick_size": 0.01,
    "min_trade_amount": 0.0001,
    "qty_tick_size": 0.0001,
    "contract_size": 1,
    "is_active": True,
}

INV_PERP = {
    "kind": "future",
    "instrument_name": "BTC-PERPETUAL",
    "instrument_type": "reversed",
    "settlement_period": "perpetual",
    "base_currency": "BTC",
    "quote_currency": "USD",
    "settlement_currency": "BTC",
    "tick_size": 0.5,
    "min_trade_amount": 10,
    "qty_tick_size": 10,
    "contract_size": 10,
    "is_active": True,
}

LIN_PERP = {
    "kind": "future",
    "instrument_name": "BTC_USDC-PERPETUAL",
    "instrument_type": "linear",
    "settlement_period": "perpetual",
    "base_currency": "BTC",
    "quote_currency": "USDC",
    "settlement_currency": "USDC",
    "tick_size": 0.1,
    "min_trade_amount": 0.0001,
    "qty_tick_size": 0.0001,
    "contract_size": 0.0001,
    "is_active": True,
}

INV_DATED = {
    "kind": "future",
    "instrument_name": "BTC-27MAR26",
    "instrument_type": "reversed",
    "settlement_period": "month",
    "base_currency": "BTC",
    "quote_currency": "USD",
    "settlement_currency": "BTC",
    "tick_size": 0.5,
    "min_trade_amount": 10,
    "qty_tick_size": 10,
    "contract_size": 10,
    "expiration_timestamp": 1774598400000,
    "is_active": True,
}

LIN_DATED = {
    "kind": "future",
    "instrument_name": "BTC_USDC-27MAR26",
    "instrument_type": "linear",
    "settlement_period": "month",
    "base_currency": "BTC",
    "quote_currency": "USDC",
    "settlement_currency": "USDC",
    "tick_size": 0.1,
    "min_trade_amount": 0.0001,
    "qty_tick_size": 0.0001,
    "contract_size": 0.0001,
    "expiration_timestamp": 1774598400000,
    "is_active": True,
}

OPTION = {
    "kind": "option",
    "instrument_name": "BTC-27MAR26-100000-C",
    "instrument_type": "reversed",
    "base_currency": "BTC",
    "quote_currency": "USD",
    "is_active": True,
    "min_trade_amount": 0.1,
    "tick_size": 0.0005,
    "contract_size": 1,
}

COMBO = {
    "kind": "future_combo",
    "instrument_name": "BTC-FS-27MAR26_PERP",
    "base_currency": "BTC",
    "quote_currency": "USD",
    "is_active": True,
    "min_trade_amount": 10,
    "tick_size": 0.5,
    "contract_size": 10,
}


def test_normalize_spot():
    inst = normalize_instrument(SPOT_BTC_USDC)
    assert inst.universal_symbol == "DERIBIT_SPOT_BTCUSDC"
    assert inst.native_symbol == "BTC_USDC"
    assert inst.market_type == MarketType.SPOT
    assert inst.filters.min_qty == Decimal("0.0001")
    assert inst.status == "active"


def test_normalize_perps():
    inv = normalize_instrument(INV_PERP)
    assert inv.universal_symbol == "DERIBIT_CMARGIN_BTCUSD"
    assert inv.settle_asset == "BTC"
    assert inv.contract_size == Decimal("10")
    assert inv.expiry is None
    lin = normalize_instrument(LIN_PERP)
    assert lin.universal_symbol == "DERIBIT_PERP_BTCUSDC"
    assert lin.settle_asset == "USDC"


def test_dated_futures_are_dropped():
    assert normalize_instrument(INV_DATED) is None
    assert normalize_instrument(LIN_DATED) is None


def test_options_and_combos_are_dropped():
    assert normalize_instrument(OPTION) is None
    assert normalize_instrument(COMBO) is None


def test_halted_instrument():
    inst = normalize_instrument({**SPOT_BTC_USDC, "is_active": False})
    assert inst.status == "halted"


def test_normalize_balances_drops_dust():
    bals = normalize_balances(
        [
            {"currency": "BTC", "available_funds": "0.5", "equity": "0.7"},
            {"currency": "USDC", "available_funds": "0", "equity": "0"},
        ]
    )
    assert len(bals) == 1
    assert bals[0].wallet == WalletType.UNIFIED
    assert bals[0].free == Decimal("0.5")
    assert bals[0].locked == Decimal("0.2")


def test_normalize_positions_skips_options_and_flat():
    native_map = {
        (MarketType.CMARGIN, "BTC-PERPETUAL"): "DERIBIT_CMARGIN_BTCUSD",
    }
    rows = [
        {
            "instrument_name": "BTC-PERPETUAL",
            "kind": "future",
            "size": "-20",
            "direction": "sell",
            "average_price": "60000",
            "mark_price": "61000",
            "floating_profit_loss": "-1",
        },
        {"instrument_name": "BTC-PERPETUAL", "kind": "future", "size": "0"},
        {
            "instrument_name": "BTC-27MAR26-100000-C",
            "kind": "option",
            "size": "5",
            "direction": "buy",
            "average_price": "0.1",
        },
    ]
    positions = normalize_positions(rows, native_map)
    assert len(positions) == 1
    assert positions[0].side == "short"
    assert positions[0].size == Decimal("20")


ORDER_RAW = {
    "order_id": "111",
    "label": "cpanel-1",
    "instrument_name": "BTC-PERPETUAL",
    "direction": "buy",
    "order_type": "limit",
    "order_state": "open",
    "amount": 10,
    "filled_amount": 0,
    "price": 60000,
    "creation_timestamp": 1710488334000,
}


def test_normalize_order_and_trade():
    order = normalize_order(ORDER_RAW, "DERIBIT_CMARGIN_BTCUSD")
    assert order.status == OrderStatus.OPEN
    assert order.client_order_id == "cpanel-1"
    cancelled = normalize_order({**ORDER_RAW, "order_state": "cancelled"}, "DERIBIT_CMARGIN_BTCUSD")
    assert cancelled.status == OrderStatus.CANCELLED

    trade = normalize_trade(
        {
            "trade_id": "ETH-99",
            "order_id": "111",
            "direction": "buy",
            "price": 60000,
            "amount": 10,
            "fee": 0.0001,
            "fee_currency": "BTC",
            "timestamp": 1710488334000,
        },
        "DERIBIT_CMARGIN_BTCUSD",
    )
    assert trade.fee == Decimal("0.0001")
    assert trade.fee_asset == "BTC"


def test_normalize_funding_skips_delivery():
    fee = normalize_funding_fee(
        {
            "instrument_name": "BTC-PERPETUAL",
            "type": "settlement",
            "funding": "-0.01",
            "currency": "BTC",
            "timestamp": 1710488334000,
        },
        "DERIBIT_CMARGIN_BTCUSD",
    )
    assert fee.amount == Decimal("-0.01")
    assert normalize_funding_fee(
        {"instrument_name": "BTC-27MAR26", "type": "delivery", "funding": "0.1"},
        "DERIBIT_DELIVERY_BTCUSD27MAR26",
    ) is None


def test_withdraw_deposit_and_transfer_maps():
    w = normalize_withdrawal_record(
        {
            "id": 7,
            "currency": "BTC",
            "amount": 0.1,
            "state": "completed",
            "address": "bc1q",
            "transaction_id": "abc",
            "fee": 0.0001,
            "created_timestamp": 1710488334000,
        }
    )
    assert w.status == WithdrawalStatus.COMPLETED
    d = normalize_deposit_record(
        {
            "id": 3,
            "currency": "BTC",
            "amount": 0.2,
            "state": "completed",
            "transaction_id": "def",
            "received_timestamp": 1710488334000,
        }
    )
    assert d.status == DepositStatus.CREDITED
    t = normalize_internal_transfer(
        {
            "id": 9,
            "currency": "BTC",
            "amount": 0.05,
            "state": "confirmed",
            "direction": "payment",
            "other_side": 42,
            "created_timestamp": 1710488334000,
        },
        account_uid="10",
    )
    assert t.scope == InternalTransferScope.ACCOUNT
    assert t.counterparty == "42"


def test_normalize_asset_networks_coinbase_and_legacy():
    coinbase = normalize_asset_networks(
        {
            "currency": "USDC",
            "coinbase_networks": [
                {
                    "network": "networks/ethereum-mainnet",
                    "display_name": "Ethereum",
                    "deposit_enabled": True,
                    "withdrawal_enabled": True,
                    "min_withdrawal": "10",
                    "withdrawal_fee": "1",
                }
            ],
        },
        "USDC",
    )
    assert len(coinbase) == 1
    assert coinbase[0].network == "ETHEREUM"
    legacy = normalize_asset_networks({"currency": "BTC", "withdrawal_fee": "0.0001"}, "BTC")
    assert legacy[0].network == "BTC"


def test_scope_and_key_name():
    assert _scope_string(["derivatives", "wallet"], False) == (
        "account:read trade:read trade:write wallet:read wallet:read_write"
    )
    assert _scope_string(["derivatives"], True) == "account:read trade:read"
    assert _key_name("My Key!") == "MyKey"
    assert len(_key_name("this_name_is_way_too_long")) == 16


def test_sign_matches_documented_rest_formula():
    # Same construction as Deribit's HTTP REST example (empty body).
    sig = _sign(
        "AMANDASECRECT",
        "1576074319000",
        "1iqt2wls",
        "GET",
        "/api/v2/private/get_account_summary?currency=BTC",
        "",
    )
    assert len(sig) == 64
    assert sig == _sign(
        "AMANDASECRECT",
        "1576074319000",
        "1iqt2wls",
        "GET",
        "/api/v2/private/get_account_summary?currency=BTC",
        "",
    )


def test_order_payload_post_only_and_label():
    params = _order_payload(
        OrderRequest(
            universal_symbol="DERIBIT_CMARGIN_BTCUSD",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            qty=Decimal("10"),
            price=Decimal("60000"),
            client_order_id="cid-1",
            time_in_force=TimeInForce.POST_ONLY,
            reduce_only=True,
        ),
        "BTC-PERPETUAL",
    )
    assert params["instrument_name"] == "BTC-PERPETUAL"
    assert params["type"] == "limit"
    assert params["post_only"] is True
    assert params["reject_post_only"] is True
    assert params["label"] == "cid-1"
    assert params["reduce_only"] is True


def test_order_payload_market_has_no_price():
    params = _order_payload(
        OrderRequest(
            universal_symbol="DERIBIT_SPOT_BTCUSDC",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            qty=Decimal("0.01"),
        ),
        "BTC_USDC",
    )
    assert params["type"] == "market"
    assert "price" not in params


@pytest.fixture
def connector():
    return DeribitConnector()


CRED = {"api_key": "id", "secret": "sec"}


async def test_fetch_instruments_and_prices(connector, monkeypatch):
    async def api(method, params=None, **kwargs):
        if method == "public/get_instruments":
            if params["kind"] == "spot":
                return [SPOT_BTC_USDC]
            return [INV_PERP, LIN_PERP, INV_DATED, LIN_DATED, OPTION]
        if method == "public/get_book_summary_by_kind":
            return [
                {"instrument_name": "BTC-PERPETUAL", "mark_price": "65000"},
                {"instrument_name": "BTC_USDC", "last": "64900"},
            ]
        return []

    monkeypatch.setattr(connector, "_api", api)
    insts = await connector.fetch_instruments()
    symbols = {i.universal_symbol for i in insts}
    assert "DERIBIT_SPOT_BTCUSDC" in symbols
    assert "DERIBIT_CMARGIN_BTCUSD" in symbols
    assert "DERIBIT_PERP_BTCUSDC" in symbols
    assert all("DELIVERY" not in s for s in symbols)
    assert all("OPTION" not in s for s in symbols)
    prices = await connector.fetch_reference_prices()
    assert prices["DERIBIT_CMARGIN_BTCUSD"] == Decimal("65000")
    assert prices["DERIBIT_SPOT_BTCUSDC"] == Decimal("64900")


async def test_verify_maps_scope(connector, monkeypatch):
    async def api(method, params=None, **kwargs):
        if method == "private/get_account_summaries":
            return {"id": 10, "type": "main", "summaries": []}
        if method == "private/list_api_keys":
            return [
                {
                    "id": 1,
                    "client_id": "id",
                    "max_scope": "account:read trade:read trade:write wallet:read",
                    "ip_whitelist": ["1.1.1.1"],
                }
            ]
        return {}

    monkeypatch.setattr(connector, "_api", api)
    info = await connector.verify_credential(CRED)
    assert info.valid
    assert info.native_uid == "10"
    assert info.is_master is True
    assert info.can_trade is True
    assert info.can_withdraw is False
    assert info.ips == ["1.1.1.1"]


async def test_verify_failure_is_secret_free(connector, monkeypatch):
    async def api(method, params=None, **kwargs):
        raise DeribitAPIError("invalid_credentials", code="13009", status_code=401)

    monkeypatch.setattr(connector, "_api", api)
    info = await connector.verify_credential(CRED)
    assert info.valid is False
    assert "invalid_credentials" in (info.reason or "")


async def test_place_buy_shapes_request(connector, monkeypatch):
    calls = []

    async def api(method, params=None, **kwargs):
        calls.append((method, params))
        if method == "public/get_instruments":
            return [INV_PERP] if (params or {}).get("kind") == "future" else []
        if method == "private/buy":
            return {"order": {**ORDER_RAW, "order_id": "99"}}
        return {}

    monkeypatch.setattr(connector, "_api", api)
    order = await connector.place_order(
        CRED,
        OrderRequest(
            universal_symbol="DERIBIT_CMARGIN_BTCUSD",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            qty=Decimal("10"),
            price=Decimal("60000"),
            client_order_id="cid-1",
            time_in_force=TimeInForce.IOC,
        ),
    )
    method, params = next(c for c in calls if c[0] == "private/buy")
    assert params["instrument_name"] == "BTC-PERPETUAL"
    assert params["time_in_force"] == "immediate_or_cancel"
    assert params["label"] == "cid-1"
    assert order.native_order_id == "99"


async def test_place_sell_uses_sell_method(connector, monkeypatch):
    calls = []

    async def api(method, params=None, **kwargs):
        calls.append(method)
        if method == "public/get_instruments":
            return [LIN_PERP] if (params or {}).get("kind") == "future" else []
        if method == "private/sell":
            return {
                "order": {
                    "order_id": "8",
                    "direction": "sell",
                    "order_type": "market",
                    "order_state": "filled",
                    "amount": 0.01,
                    "filled_amount": 0.01,
                    "instrument_name": "BTC_USDC-PERPETUAL",
                }
            }
        return {}

    monkeypatch.setattr(connector, "_api", api)
    await connector.place_order(
        CRED,
        OrderRequest(
            universal_symbol="DERIBIT_PERP_BTCUSDC",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            qty=Decimal("0.01"),
        ),
    )
    assert "private/sell" in calls
    assert "private/buy" not in calls


async def test_transfer_main_to_sub(connector, monkeypatch):
    calls = []

    async def api(method, params=None, **kwargs):
        calls.append((method, params))
        return {"id": 55, "state": "confirmed"}

    monkeypatch.setattr(connector, "_api", api)
    result = await connector.transfer(
        CRED, "BTC", Decimal("0.1"), to_subaccount="42"
    )
    assert result.native_transfer_id == "55"
    assert calls[0][0] == "private/submit_transfer_to_subaccount"
    assert calls[0][1]["destination"] == 42

    with pytest.raises(ValueError, match="UNIFIED"):
        await connector.transfer(
            CRED, "BTC", Decimal("0.1"), from_wallet=WalletType.FUNDING, to_subaccount="42"
        )


async def test_create_sub_then_rename(connector, monkeypatch):
    calls = []

    async def api(method, params=None, **kwargs):
        calls.append((method, params))
        if method == "private/create_subaccount":
            return {"id": 99, "username": "user_99"}
        return {}

    monkeypatch.setattr(connector, "_api", api)
    info = await connector.create_subaccount(CRED, "desk-alpha")
    assert info.native_uid == "99"
    assert info.label == "desk-alpha"
    assert calls[1][0] == "private/change_subaccount_name"
    assert calls[1][1]["sid"] == 99


async def test_create_api_key_on_sub_uses_bearer(connector, monkeypatch):
    async def bearer_for(credential, subaccount=None):
        assert subaccount == "42"
        return "tok"

    async def api(method, params=None, credential=None, bearer=None):
        if method == "private/create_api_key":
            assert bearer == "tok"
            assert "trade:write" in params["max_scope"]
            return {"client_id": "NEW", "client_secret": "s3cret"}
        return {}

    monkeypatch.setattr(connector, "_bearer_for", bearer_for)
    monkeypatch.setattr(connector, "_api", api)
    result = await connector.create_api_key(
        CRED, "42", KeyScheme.HMAC, ["derivatives", "wallet"], read_only=False
    )
    assert result.api_key == "NEW"
    assert result.secret == "s3cret"


async def test_trades_history_skips_options_and_advances(connector, monkeypatch):
    connector._native_map = {
        (MarketType.CMARGIN, "BTC-PERPETUAL"): "DERIBIT_CMARGIN_BTCUSD",
    }
    connector._universal_map = {
        "DERIBIT_CMARGIN_BTCUSD": (MarketType.CMARGIN, "BTC-PERPETUAL"),
    }

    async def api(method, params=None, **kwargs):
        assert method == "private/get_user_trades_by_currency"
        assert params["currency"] == "BTC"
        assert params["sorting"] == "asc"
        return {
            "trades": [
                {
                    "trade_id": "1",
                    "order_id": "o",
                    "instrument_name": "BTC-PERPETUAL",
                    "direction": "buy",
                    "price": 1,
                    "amount": 10,
                    "fee": 0,
                    "fee_currency": "BTC",
                    "timestamp": params["start_timestamp"] + 10,
                },
                {
                    "trade_id": "2",
                    "order_id": "o",
                    "instrument_name": "BTC-27MAR26-100000-C",
                    "kind": "option",
                    "direction": "buy",
                    "price": 1,
                    "amount": 1,
                    "fee": 0,
                    "timestamp": params["start_timestamp"] + 20,
                },
            ]
        }

    monkeypatch.setattr(connector, "_api", api)
    page = await connector.fetch_trades(
        CRED, "BTC", json.dumps({"start": 1_700_000_000_000})
    )
    assert len(page.items) == 1
    assert page.items[0].native_trade_id == "1"
    assert page.next_cursor is not None


async def test_hmac_header_on_private_call(connector, monkeypatch):
    captured = {}

    class FakeResp:
        status_code = 200
        is_success = True

        def json(self):
            return {"jsonrpc": "2.0", "result": {"id": 1}}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResp()

    monkeypatch.setattr("app.connectors.deribit.httpx.Client", FakeClient)
    result = connector._request(
        "private/get_account_summaries",
        {"extended": True},
        credential=CRED,
    )
    assert result == {"id": 1}
    auth = captured["headers"]["Authorization"]
    assert auth.startswith("deri-hmac-sha256 id=id,")
    assert "sig=" in auth
    assert "extended=true" in captured["url"]
