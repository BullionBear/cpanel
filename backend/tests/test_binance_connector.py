"""Binance normalization tests with fixtures shaped like the REST API docs.

Raw-fetch methods are overridden so no network is involved; the payload
shapes are exactly what api/fapi/dapi exchangeInfo return.
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.connectors.base import DEFAULT_HISTORY_BACKFILL
from app.connectors.binance import (
    BinanceConnector,
    _advance_window,
    _normalize_pem,
    normalize_asset_networks,
    normalize_cm_instrument,
    normalize_deposit,
    normalize_funding_balances,
    normalize_futures_balances,
    normalize_positions,
    normalize_spot_balances,
    normalize_spot_instrument,
    normalize_trade,
    normalize_um_instrument,
    normalize_withdrawal,
)
from app.connectors.types import (
    DepositStatus,
    MarketType,
    OrderSide,
    WalletType,
    WithdrawalStatus,
)

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
    assert balances[0].wallet == WalletType.FUTURES
    assert balances[0].free == Decimal("4000")
    assert balances[0].locked == Decimal("1000")
    assert balances[0].total == Decimal("5000")


def test_normalize_funding_balances_locks_frozen_and_withdrawing():
    balances = normalize_funding_balances(
        [
            {"asset": "USDT", "free": "70", "locked": "10", "freeze": "15", "withdrawing": "5"},
            {"asset": "BTC", "free": "0", "locked": "0"},
        ]
    )
    assert len(balances) == 1
    assert balances[0].wallet == WalletType.FUNDING
    assert balances[0].free == Decimal("70")
    assert balances[0].locked == Decimal("30")


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

    async def funding_balances(cred):
        return [{"asset": "USDT", "free": "42", "locked": "0"}]

    async def um_balances(cred):
        return UM_BALANCES

    async def cm_balances(cred):
        return CM_BALANCES

    monkeypatch.setattr(connector, "_fetch_spot_balances", spot_balances)
    monkeypatch.setattr(connector, "_fetch_funding_balances", funding_balances)
    monkeypatch.setattr(connector, "_fetch_um_balances", um_balances)
    monkeypatch.setattr(connector, "_fetch_cm_balances", cm_balances)

    balances = await connector.fetch_balances({"api_key": "k", "secret": "s"})
    spot = [b for b in balances if b.wallet == WalletType.SPOT]
    funding = [b for b in balances if b.wallet == WalletType.FUNDING]
    futures = [b for b in balances if b.wallet == WalletType.FUTURES]
    assert {b.asset for b in spot} == {"BTC", "USDT"}
    assert {b.asset for b in funding} == {"USDT"}
    assert {b.asset for b in futures} == {"USDT", "BTC"}


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


# -- history ------------------------------------------------------------------

SPOT_TRADE = {
    "symbol": "BTCUSDT",
    "id": 28457,
    "orderId": 100234,
    "price": "62000.10",
    "qty": "0.002",
    "commission": "0.124",
    "commissionAsset": "USDT",
    "time": 1752345600000,
    "isBuyer": True,
    "isMaker": False,
}

UM_TRADE = {
    "symbol": "ETHUSDT",
    "id": 6960,
    "orderId": 8389765,
    "side": "SELL",
    "price": "2999.9",
    "qty": "1",
    "commission": "1.2",
    "commissionAsset": "USDT",
    "time": 1752345700000,
}

UM_ORDER = {
    "symbol": "ETHUSDT",
    "orderId": 8389765,
    "clientOrderId": "cpanel-abc",
    "side": "SELL",
    "type": "LIMIT",
    "status": "FILLED",
    "origQty": "1",
    "executedQty": "1",
    "price": "2999.9",
    "time": 1752345600000,
    "updateTime": 1752345700000,
}


def test_normalize_trade_spot_uses_isbuyer():
    trade = normalize_trade(SPOT_TRADE, "BINANCE_SPOT_BTCUSDT")
    assert trade.side == OrderSide.BUY
    assert trade.native_trade_id == "28457"
    assert trade.native_order_id == "100234"
    assert trade.price == Decimal("62000.10")
    assert trade.fee == Decimal("0.124")
    assert trade.fee_asset == "USDT"
    assert trade.traded_at == datetime.fromtimestamp(1752345600, tz=timezone.utc)


def test_normalize_trade_futures_uses_side():
    trade = normalize_trade(UM_TRADE, "BINANCE_PERP_ETHUSDT")
    assert trade.side == OrderSide.SELL
    assert trade.qty == Decimal("1")


def test_history_stream_keys_one_per_symbol():
    keys = BinanceConnector().history_stream_keys(
        ["BINANCE_SPOT_BTCUSDT", "BINANCE_PERP_ETHUSDT"]
    )
    assert keys == ["BINANCE_SPOT_BTCUSDT", "BINANCE_PERP_ETHUSDT"]


def test_advance_window_full_page_stays_in_window():
    rows = [{"time": 1_000_500}] * 10
    assert _advance_window(rows, "time", 1_000_000, 2_000_000, 3_000_000, 10) == "1000500"


def test_advance_window_partial_page_jumps_to_window_end():
    rows = [{"time": 1_000_500}]
    assert _advance_window(rows, "time", 1_000_000, 2_000_000, 3_000_000, 10) == "2000000"


def test_advance_window_caught_up_returns_none():
    assert _advance_window([], "time", 1_000_000, 3_000_000, 3_000_000, 10) is None


def test_advance_window_guards_stuck_timestamp():
    # A full page whose rows all sit on the window start must still advance.
    rows = [{"time": 1_000_000}] * 10
    assert _advance_window(rows, "time", 1_000_000, 2_000_000, 3_000_000, 10) == "1000001"


async def test_fetch_trades_windowed_walk(connector, monkeypatch):
    calls = []

    async def my_trades(credential, market_type, native_symbol, start_ms, end_ms, limit):
        calls.append((market_type, native_symbol, start_ms, end_ms, limit))
        return [dict(SPOT_TRADE, time=start_ms + 1000)]

    monkeypatch.setattr(connector, "_fetch_my_trades", my_trades)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - 2 * 24 * 60 * 60 * 1000
    page = await connector.fetch_trades(
        {"api_key": "k", "secret": "s"}, "BINANCE_SPOT_BTCUSDT", str(start_ms)
    )

    market_type, native_symbol, called_start, called_end, _ = calls[0]
    assert (market_type, native_symbol) == (MarketType.SPOT, "BTCUSDT")
    assert called_start == start_ms
    assert called_end == start_ms + 24 * 60 * 60 * 1000  # 24h window
    # Partial page mid-window: the cursor jumps to the window end.
    assert page.next_cursor == str(called_end)
    assert page.items[0].universal_symbol == "BINANCE_SPOT_BTCUSDT"


async def test_fetch_trades_caught_up(connector, monkeypatch):
    async def my_trades(credential, market_type, native_symbol, start_ms, end_ms, limit):
        return []

    monkeypatch.setattr(connector, "_fetch_my_trades", my_trades)
    recent = int(datetime.now(timezone.utc).timestamp() * 1000) - 60_000
    page = await connector.fetch_trades(
        {"api_key": "k", "secret": "s"}, "BINANCE_SPOT_BTCUSDT", str(recent)
    )
    assert page.items == []
    assert page.next_cursor is None


async def test_fetch_trades_defaults_to_three_day_backfill(connector, monkeypatch):
    calls = []

    async def my_trades(credential, market_type, native_symbol, start_ms, end_ms, limit):
        calls.append(start_ms)
        return []

    monkeypatch.setattr(connector, "_fetch_my_trades", my_trades)
    before = datetime.now(timezone.utc)
    await connector.fetch_trades({"api_key": "k", "secret": "s"}, "BINANCE_SPOT_BTCUSDT", None)
    expected = int((before - DEFAULT_HISTORY_BACKFILL).timestamp() * 1000)
    assert abs(calls[0] - expected) < 60_000


async def test_fetch_orders_history_normalizes(connector, monkeypatch):
    async def all_orders(credential, market_type, native_symbol, start_ms, end_ms, limit):
        assert (market_type, native_symbol) == (MarketType.PERP, "ETHUSDT")
        return [UM_ORDER]

    monkeypatch.setattr(connector, "_fetch_all_orders", all_orders)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    page = await connector.fetch_orders_history(
        {"api_key": "k", "secret": "s"},
        "BINANCE_PERP_ETHUSDT",
        str(now_ms - 60_000),
    )
    order = page.items[0]
    assert order.universal_symbol == "BINANCE_PERP_ETHUSDT"
    assert order.native_order_id == "8389765"
    assert order.status.value == "FILLED"


# -- transfers / withdrawals / deposits ---------------------------------------

COIN_ROW = {
    "coin": "USDT",
    "networkList": [
        {
            "network": "TRX",
            "depositEnable": True,
            "withdrawEnable": True,
            "withdrawMin": "10",
            "withdrawFee": "1",
        },
        {
            "network": "ETH",
            "depositEnable": True,
            "withdrawEnable": False,
            "withdrawMin": "20",
            "withdrawFee": "4",
        },
    ],
}


def test_normalize_asset_networks():
    networks = normalize_asset_networks(COIN_ROW)
    trx = next(n for n in networks if n.network == "TRX")
    assert trx.withdraw_enabled and trx.deposit_enabled
    assert trx.withdraw_min == Decimal("10")
    assert trx.withdraw_fee == Decimal("1")
    eth = next(n for n in networks if n.network == "ETH")
    assert eth.withdraw_enabled is False


def test_normalize_withdrawal_statuses():
    completed = normalize_withdrawal(
        {"id": "w1", "coin": "USDT", "amount": "100", "status": 6,
         "txId": "0xabc", "transactionFee": "1", "network": "TRX"}
    )
    assert completed.status == WithdrawalStatus.COMPLETED
    assert completed.tx_id == "0xabc"
    assert completed.fee == Decimal("1")

    # Processing with a known hash is SENT (broadcast, not final).
    sent = normalize_withdrawal(
        {"id": "w2", "coin": "USDT", "amount": "100", "status": 4, "txId": "0xdef"}
    )
    assert sent.status == WithdrawalStatus.SENT

    rejected = normalize_withdrawal(
        {"id": "w3", "coin": "USDT", "amount": "100", "status": 3}
    )
    assert rejected.status == WithdrawalStatus.FAILED


def test_normalize_deposit_credited():
    deposit = normalize_deposit(
        {"id": "d1", "coin": "USDT", "amount": "99", "status": 1,
         "network": "TRX", "txId": "0xabc", "insertTime": 1_700_000_000_000}
    )
    assert deposit.status == DepositStatus.CREDITED
    assert deposit.amount == Decimal("99")
    assert deposit.credited_at is not None
    assert deposit.created_at is not None


def test_normalize_withdrawal_created_at_from_apply_time():
    withdrawal = normalize_withdrawal(
        {"id": "w1", "coin": "USDT", "amount": "100", "status": 6,
         "txId": "0xabc", "transactionFee": "1", "network": "TRX",
         "applyTime": "2024-01-15 12:30:00"}
    )
    assert withdrawal.created_at is not None
    assert withdrawal.created_at.year == 2024


async def test_transfer_wallet_to_wallet_builds_type(monkeypatch):
    c = BinanceConnector()
    calls = []

    async def submit(credential, transfer_type, asset, amount):
        calls.append((transfer_type, asset, amount))
        return {"tranId": 13526853623}

    monkeypatch.setattr(c, "_submit_universal_transfer", submit)
    result = await c.transfer(
        {}, "USDT", Decimal("100"),
        from_wallet=WalletType.SPOT, to_wallet=WalletType.FUTURES,
    )
    assert calls == [("MAIN_UMFUTURE", "USDT", 100.0)]
    assert result.native_transfer_id == "13526853623"
    assert result.status == "confirmed"


async def test_transfer_main_to_sub_uses_emails(monkeypatch):
    c = BinanceConnector()
    calls = []

    async def submit(credential, asset, amount, from_wallet, to_wallet, from_email, to_email):
        calls.append((asset, amount, from_wallet, to_wallet, from_email, to_email))
        return {"tranId": 99}

    monkeypatch.setattr(c, "_submit_sub_transfer", submit)
    result = await c.transfer(
        {}, "USDT", Decimal("5"), to_subaccount="sub@x.com"
    )
    assert calls == [("USDT", 5.0, "SPOT", "SPOT", None, "sub@x.com")]
    assert result.status == "confirmed"


async def test_fetch_withdrawal_finds_by_id(monkeypatch):
    c = BinanceConnector()

    async def history(credential):
        return [
            {"id": "other", "coin": "USDT", "amount": "1", "status": 4},
            {"id": "w42", "coin": "USDT", "amount": "100", "status": 6, "txId": "0xabc"},
        ]

    monkeypatch.setattr(c, "_fetch_withdraw_history", history)
    withdrawal = await c.fetch_withdrawal({}, "w42")
    assert withdrawal is not None and withdrawal.status == WithdrawalStatus.COMPLETED
    assert await c.fetch_withdrawal({}, "missing") is None


async def test_fetch_deposit_address(monkeypatch):
    c = BinanceConnector()

    async def raw(credential, asset, network, subaccount):
        assert subaccount is None
        return {"address": "TAbc123", "tag": ""}

    monkeypatch.setattr(c, "_fetch_deposit_address_raw", raw)
    resolved = await c.fetch_deposit_address({}, "USDT", "TRX")
    assert resolved.address == "TAbc123"
    assert resolved.tag is None
