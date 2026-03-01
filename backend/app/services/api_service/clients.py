import logging
import random
import string
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import binance as bn
from pybit.exceptions import FailedRequestError
from pybit.unified_trading import HTTP as BybitHTTP

from .base import APIContext, ExchangeClient
from .common import (
    ENDING_STATUSES,
    PRODUCT_TO_BYBIT_CATEGORY,
    ExchangeError,
    NetworkError,
    ORDER_SIDES,
    ORDER_TYPES,
    create_binance_client,
    create_bybit_client,
    exchange_info_cache,
    normalize_order_status,
    normalize_price,
    normalize_quantity,
)

logger = logging.getLogger(__name__)


def generate_client_order_id() -> str:
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S")
    random_str = "".join(random.choices(string.ascii_uppercase + string.ascii_lowercase, k=6))
    return f"cpanel-{date_str}-{time_str}-{random_str}"


class BinanceExchangeClient(ExchangeClient):
    def __init__(self, context: APIContext):
        self.context = context
        self.client = create_binance_client(context.api_key, context.api_secret, context.api_type)

    def verify_api(self) -> None:
        try:
            account = self.client.get_account()
            if account and "balances" in account:
                return
            raise ExchangeError("Account info response is invalid", exchange="BINANCE", error_code=None)
        except bn.exceptions.BinanceAPIException as e:
            raise ExchangeError(
                str(e.message) if hasattr(e, "message") else str(e),
                exchange="BINANCE",
                error_code=e.code,
                original_error=e,
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Binance: {e}", exchange="BINANCE", original_error=e)
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(
                f"Unexpected error during Binance API verification: {e}",
                exchange="BINANCE",
                error_code=None,
                original_error=e,
            )

    def get_account_uid(self) -> int:
        try:
            account = self.client.get_account()
            if account and "uid" in account:
                return int(account["uid"])
            raise ExchangeError("Account UID not found in Binance response", exchange="BINANCE", error_code=None)
        except bn.exceptions.BinanceAPIException as e:
            raise ExchangeError(
                str(e.message) if hasattr(e, "message") else str(e),
                exchange="BINANCE",
                error_code=e.code,
                original_error=e,
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Binance: {e}", exchange="BINANCE", original_error=e)

    def get_balance(self) -> List[Dict[str, Any]]:
        try:
            result: List[Dict[str, Any]] = []

            try:
                account = self.client.get_account()
                if account and "balances" in account:
                    spot_balances = []
                    for balance in account["balances"]:
                        free = float(balance.get("free", 0))
                        locked = float(balance.get("locked", 0))
                        total = free + locked
                        if total > 0:
                            spot_balances.append(
                                {
                                    "token": balance["asset"],
                                    "total": str(total),
                                    "frozen": str(locked),
                                    "available": str(free),
                                }
                            )
                    if spot_balances:
                        result.append({"wallet": "SPOT", "balance": spot_balances})
            except Exception as e:
                logger.warning("Failed to get Binance SPOT balance: %s", e)

            try:
                margin_account = self.client.get_margin_account()
                if margin_account and "userAssets" in margin_account:
                    margin_balances = []
                    for asset in margin_account["userAssets"]:
                        free = float(asset.get("free", 0))
                        locked = float(asset.get("locked", 0))
                        borrowed = float(asset.get("borrowed", 0))
                        total = free + locked - borrowed
                        if total > 0 or borrowed > 0:
                            margin_balances.append(
                                {
                                    "token": asset["asset"],
                                    "total": str(total),
                                    "frozen": str(locked),
                                    "available": str(free),
                                }
                            )
                    if margin_balances:
                        result.append({"wallet": "MARGIN", "balance": margin_balances})
            except Exception as e:
                logger.warning("Failed to get Binance MARGIN balance: %s", e)

            try:
                futures_balance = self.client.futures_account_balance()
                if futures_balance:
                    futures_balances = []
                    for account_balance in futures_balance:
                        balance_amt = float(account_balance.get("balance", 0))
                        available_balance = float(account_balance.get("availableBalance", 0))
                        frozen = balance_amt - available_balance
                        if balance_amt > 0:
                            futures_balances.append(
                                {
                                    "token": account_balance.get("asset", "USDT"),
                                    "total": str(balance_amt),
                                    "frozen": str(frozen),
                                    "available": str(available_balance),
                                }
                            )
                    if futures_balances:
                        result.append({"wallet": "FUTURES", "balance": futures_balances})
            except Exception as e:
                logger.warning("Failed to get Binance FUTURES balance: %s", e)

            return result
        except bn.exceptions.BinanceAPIException as e:
            raise ExchangeError(str(e), exchange="BINANCE", error_code=e.code, original_error=e)
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Binance: {e}", exchange="BINANCE", original_error=e)
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(
                f"Unexpected error getting Binance balance: {e}", exchange="BINANCE", error_code=None, original_error=e
            )

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        try:
            exchange_info = exchange_info_cache.get("binance_exchange_info")
            if exchange_info is None:
                exchange_info = bn.Client().get_exchange_info()
                exchange_info_cache.set("binance_exchange_info", exchange_info)

            if not exchange_info or "symbols" not in exchange_info:
                raise ExchangeError("Invalid response from Binance exchange info", exchange="BINANCE", error_code=None)

            symbol_info = None
            for item in exchange_info["symbols"]:
                if item["symbol"] == symbol and item["status"] == "TRADING":
                    symbol_info = item
                    break

            if not symbol_info:
                raise ExchangeError(f"Symbol {symbol} not found or not trading on Binance", exchange="BINANCE")

            price_precision = 8
            size_precision = 8
            for filter_item in symbol_info.get("filters", []):
                if filter_item.get("filterType") == "PRICE_FILTER":
                    tick_size = filter_item.get("tickSize", "1")
                    price_precision = len(tick_size.split(".")[-1].rstrip("0")) if "." in tick_size else 0
                elif filter_item.get("filterType") == "LOT_SIZE":
                    step = filter_item.get("stepSize", "1")
                    size_precision = len(step.split(".")[-1].rstrip("0")) if "." in step else 0

            return {
                "symbol": symbol,
                "base_asset": symbol_info.get("baseAsset", ""),
                "quote_asset": symbol_info.get("quoteAsset", ""),
                "price_precision": price_precision,
                "size_precision": size_precision,
                "exists": True,
            }
        except ExchangeError:
            raise
        except bn.exceptions.BinanceAPIException as e:
            raise ExchangeError(str(e), exchange="BINANCE", error_code=e.code, original_error=e)
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Binance: {e}", exchange="BINANCE", original_error=e)
        except Exception as e:
            raise ExchangeError(
                f"Unexpected error getting Binance symbol info: {e}", exchange="BINANCE", error_code=None, original_error=e
            )

    def get_order_status(
        self, symbol: str, order_id: Optional[str] = None, client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            if client_order_id:
                order = self.client.get_order(symbol=symbol, origClientOrderId=client_order_id)
            elif order_id:
                order = self.client.get_order(symbol=symbol, orderId=int(order_id))
            else:
                raise ExchangeError("Either order_id or client_order_id must be provided", exchange="BINANCE")

            return {
                "order_id": str(order.get("orderId", "")),
                "status": order.get("status", ""),
                "symbol": order.get("symbol", symbol),
                "side": order.get("side", ""),
                "type": order.get("type", ""),
                "quantity": order.get("executedQty", ""),
                "price": order.get("price", ""),
            }
        except bn.exceptions.BinanceAPIException as e:
            raise ExchangeError(str(e), exchange="BINANCE", error_code=e.code, original_error=e)
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Binance: {e}", exchange="BINANCE", original_error=e)

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            open_orders = self.client.get_open_orders(symbol=symbol) if symbol else self.client.get_open_orders()
            normalized_orders = []
            for order in open_orders:
                created_at = int(order.get("time", datetime.now(timezone.utc).timestamp() * 1000) / 1000)
                updated_at = int(order.get("updateTime", order.get("time", created_at * 1000)) / 1000)
                normalized_orders.append(
                    {
                        "order_id": str(order.get("orderId", "")),
                        "symbol": order.get("symbol", ""),
                        "side": order.get("side", ""),
                        "type": order.get("type", ""),
                        "quantity": normalize_quantity(order.get("origQty", "0")),
                        "price": normalize_price(order.get("price", "0")),
                        "status": normalize_order_status("BINANCE", order.get("status", "")),
                        "client_order_id": order.get("clientOrderId", ""),
                        "created_at": created_at,
                        "updated_at": updated_at,
                    }
                )
            return normalized_orders
        except bn.exceptions.BinanceAPIException as e:
            raise ExchangeError(
                str(e.message) if hasattr(e, "message") else str(e),
                exchange="BINANCE",
                error_code=e.code,
                original_error=e,
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Binance: {e}", exchange="BINANCE", original_error=e)

    def cancel_order(
        self, symbol: str, order_id: Optional[str] = None, client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            cancel_params: Dict[str, Any] = {"symbol": symbol}
            if client_order_id:
                cancel_params["origClientOrderId"] = client_order_id
            elif order_id:
                cancel_params["orderId"] = int(order_id)
            else:
                raise ExchangeError("Either order_id or client_order_id must be provided", exchange="BINANCE")

            cancel_result = self.client.cancel_order(**cancel_params)
            order_time_ms = cancel_result.get("time")
            created_at = int(order_time_ms / 1000) if order_time_ms else int(datetime.now(timezone.utc).timestamp())
            update_time_ms = cancel_result.get("updateTime", order_time_ms)
            updated_at = int(update_time_ms / 1000) if update_time_ms else created_at
            return {
                "order_id": str(cancel_result.get("orderId", order_id or "")),
                "symbol": cancel_result.get("symbol", symbol),
                "side": cancel_result.get("side", ""),
                "type": cancel_result.get("type", ""),
                "quantity": normalize_quantity(cancel_result.get("origQty", "0")),
                "price": normalize_price(cancel_result.get("price", "0")),
                "status": normalize_order_status("BINANCE", cancel_result.get("status", "")),
                "client_order_id": cancel_result.get("clientOrderId", client_order_id or ""),
                "created_at": created_at,
                "updated_at": updated_at,
                "exchange_response": cancel_result,
            }
        except bn.exceptions.BinanceAPIException as e:
            raise ExchangeError(
                str(e.message) if hasattr(e, "message") else str(e),
                exchange="BINANCE",
                error_code=e.code,
                original_error=e,
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Binance: {e}", exchange="BINANCE", original_error=e)

    def create_order(
        self, symbol: str, side: ORDER_SIDES, order_type: ORDER_TYPES, quantity: str, price: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            client_order_id = generate_client_order_id()
            params: Dict[str, Any] = {
                "symbol": symbol,
                "side": side,
                "type": "MARKET" if order_type == "MARKET" else "LIMIT",
                "quantity": quantity,
                "newClientOrderId": client_order_id,
            }
            if order_type == "LIMIT":
                if price is None:
                    raise ExchangeError("Price is required for LIMIT orders", exchange="BINANCE")
                params["price"] = price
                params["timeInForce"] = "GTC"

            order_result = self.client.create_order(**params)
            raw_status = order_result.get("status", "")
            normalized_status = normalize_order_status("BINANCE", raw_status)
            if normalized_status not in ENDING_STATUSES:
                try:
                    status_info = self.get_order_status(
                        symbol=symbol,
                        order_id=str(order_result.get("orderId", "")),
                        client_order_id=client_order_id,
                    )
                    normalized_status = normalize_order_status("BINANCE", status_info.get("status", raw_status))
                except Exception as e:
                    logger.warning("Failed to fetch Binance order status for %s: %s", client_order_id, e)

            order_time_ms = order_result.get("transactTime", order_result.get("time"))
            created_at = int(order_time_ms / 1000) if order_time_ms else int(datetime.now(timezone.utc).timestamp())
            return {
                "order_id": str(order_result.get("orderId", "")),
                "symbol": order_result.get("symbol", symbol),
                "side": order_result.get("side", side),
                "type": order_result.get("type", order_type),
                "quantity": normalize_quantity(order_result.get("executedQty", quantity)),
                "price": normalize_price(order_result.get("price", price)),
                "status": normalized_status,
                "client_order_id": client_order_id,
                "created_at": created_at,
                "updated_at": created_at,
                "exchange_response": order_result,
            }
        except ExchangeError:
            raise
        except bn.exceptions.BinanceAPIException as e:
            raise ExchangeError(
                str(e.message) if hasattr(e, "message") else str(e),
                exchange="BINANCE",
                error_code=e.code,
                original_error=e,
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Binance: {e}", exchange="BINANCE", original_error=e)

    def get_mytrades(
        self, category: str, start_ms: int, end_ms: int, symbol: Optional[str] = None, limit: int = 200
    ) -> List[Dict[str, Any]]:
        try:
            cap = min(limit, 1000)
            if category.upper() in ("SPOT", "MARGIN"):
                if not symbol:
                    raise ExchangeError(
                        "symbol is required for Binance spot/margin trade history", exchange="BINANCE", error_code=None
                    )
                raw = self.client.get_my_trades(symbol=symbol, startTime=start_ms, endTime=end_ms, limit=cap)
                result = [
                    {
                        "exch_tid": int(t.get("id", 0)),
                        "order_id": int(t.get("orderId", 0)),
                        "symbol": t.get("symbol", ""),
                        "side": "Buy" if t.get("isBuyer") else "Sell",
                        "exec_px": str(t.get("price", "0")),
                        "exec_qty": str(t.get("qty", "0")),
                        "exec_fee": str(t.get("commission", "0")),
                        "fee_currency": str(t.get("commissionAsset", "")),
                        "is_maker": bool(t.get("isMaker", False)),
                        "exec_time_ms": int(t.get("time", 0)),
                    }
                    for t in raw
                ]
                result.sort(key=lambda x: x["exec_time_ms"], reverse=True)
                return result

            if not symbol:
                raise ExchangeError("symbol is required for Binance futures trade history", exchange="BINANCE")
            raw = self.client.futures_account_trades(symbol=symbol, startTime=start_ms, endTime=end_ms, limit=cap)
            result = [
                {
                    "exch_tid": int(t.get("id", 0)),
                    "order_id": int(t.get("orderId", 0)),
                    "symbol": t.get("symbol", ""),
                    "side": "Buy" if t.get("buyer") else "Sell",
                    "exec_px": str(t.get("price", "0")),
                    "exec_qty": str(t.get("qty", "0")),
                    "exec_fee": str(t.get("commission", "0")),
                    "fee_currency": str(t.get("commissionAsset", "")),
                    "is_maker": bool(t.get("maker", False)),
                    "exec_time_ms": int(t.get("time", 0)),
                }
                for t in raw
            ]
            result.sort(key=lambda x: x["exec_time_ms"], reverse=True)
            return result
        except ExchangeError:
            raise
        except bn.exceptions.BinanceAPIException as e:
            raise ExchangeError(
                str(e.message) if hasattr(e, "message") else str(e),
                exchange="BINANCE",
                error_code=e.code,
                original_error=e,
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error connecting to Binance: {e}", exchange="BINANCE", original_error=e)


class BybitExchangeClient(ExchangeClient):
    def __init__(self, context: APIContext):
        self.context = context
        self.client = create_bybit_client(context.api_key, context.api_secret, context.api_type)

    def verify_api(self) -> None:
        try:
            response = self.client.get_wallet_balance(accountType="UNIFIED")
            if not response:
                raise ExchangeError("Empty response from Bybit API", exchange="BYBIT", error_code=None)
            if response.get("retCode") == 0:
                return
            raise ExchangeError(
                response.get("retMsg", "Unknown error"),
                exchange="BYBIT",
                error_code=response.get("retCode"),
            )
        except FailedRequestError as e:
            raise ExchangeError(str(e), exchange="BYBIT", error_code=None, original_error=e)
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Bybit: {e}", exchange="BYBIT", original_error=e)

    def get_account_uid(self) -> int:
        try:
            response = self.client.get_api_key_information()
            if not response:
                raise ExchangeError("Empty response from Bybit API", exchange="BYBIT", error_code=None)
            if response.get("retCode") != 0:
                raise ExchangeError(
                    response.get("retMsg", "Unknown error"),
                    exchange="BYBIT",
                    error_code=response.get("retCode"),
                )
            user_id = response.get("result", {}).get("userID")
            if user_id is None:
                raise ExchangeError("userID not found in Bybit response", exchange="BYBIT")
            return int(user_id)
        except ExchangeError:
            raise
        except FailedRequestError as e:
            raise ExchangeError(str(e), exchange="BYBIT", error_code=None, original_error=e)
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Bybit: {e}", exchange="BYBIT", original_error=e)

    def get_balance(self) -> List[Dict[str, Any]]:
        try:
            result: List[Dict[str, Any]] = []
            try:
                unified_response = self.client.get_wallet_balance(accountType="UNIFIED")
                if unified_response and unified_response.get("retCode") == 0:
                    for account in unified_response.get("result", {}).get("list", []):
                        balances = []
                        for coin_data in account.get("coin", []):
                            wallet_balance = float((coin_data.get("walletBalance", "0") or "0"))
                            locked = float((coin_data.get("locked", "0") or "0"))
                            available_balance = wallet_balance - locked
                            if wallet_balance > 0:
                                balances.append(
                                    {
                                        "token": coin_data.get("coin", ""),
                                        "total": str(wallet_balance),
                                        "frozen": str(locked),
                                        "available": str(available_balance),
                                    }
                                )
                        if balances:
                            result.append({"wallet": "UNIFIED", "balance": balances})
            except Exception as e:
                logger.warning("Failed to get Bybit UNIFIED balance: %s", e)

            try:
                funding_response = self.client.get_coins_balance(accountType="FUND")
                if funding_response and funding_response.get("retCode") == 0:
                    balances = []
                    for coin_data in funding_response.get("result", {}).get("balance", []):
                        wallet_balance = float((coin_data.get("walletBalance", "0") or "0"))
                        transfer_balance = float((coin_data.get("transferBalance", "0") or "0"))
                        frozen = wallet_balance - transfer_balance
                        if wallet_balance > 0:
                            balances.append(
                                {
                                    "token": coin_data.get("coin", ""),
                                    "total": str(wallet_balance),
                                    "frozen": str(frozen),
                                    "available": str(transfer_balance),
                                }
                            )
                    if balances:
                        result.append({"wallet": "FUNDING", "balance": balances})
            except Exception as e:
                logger.warning("Failed to get Bybit FUNDING balance: %s", e)
            return result
        except FailedRequestError as e:
            raise ExchangeError(str(e), exchange="BYBIT", error_code=None, original_error=e)
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Bybit: {e}", exchange="BYBIT", original_error=e)

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        try:
            response = exchange_info_cache.get("bybit_instruments_spot")
            if response is None:
                client = BybitHTTP(testnet=False)
                response = client.get_instruments_info(category="spot")
                if response and response.get("retCode") == 0:
                    exchange_info_cache.set("bybit_instruments_spot", response)
                else:
                    response = client.get_instruments_info(category="spot", symbol=symbol)
            else:
                symbol_info = next(
                    (s for s in response.get("result", {}).get("list", []) if s.get("symbol") == symbol), None
                )
                if symbol_info:
                    return self._normalize_symbol(symbol, symbol_info)
                response = BybitHTTP(testnet=False).get_instruments_info(category="spot", symbol=symbol)

            if not response or response.get("retCode") != 0:
                raise ExchangeError(
                    f"Symbol {symbol} not found on Bybit: {response.get('retMsg', 'Unknown error') if response else 'Empty response'}",
                    exchange="BYBIT",
                    error_code=response.get("retCode") if response else None,
                )

            list_data = response.get("result", {}).get("list", [])
            if not list_data:
                raise ExchangeError(f"Symbol {symbol} not found on Bybit", exchange="BYBIT", error_code=None)
            return self._normalize_symbol(symbol, list_data[0])
        except ExchangeError:
            raise
        except FailedRequestError as e:
            raise ExchangeError(str(e), exchange="BYBIT", error_code=None, original_error=e)
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Bybit: {e}", exchange="BYBIT", original_error=e)

    def _normalize_symbol(self, symbol: str, symbol_info: Dict[str, Any]) -> Dict[str, Any]:
        lot_size_filter = symbol_info.get("lotSizeFilter", {})
        qty_step = lot_size_filter.get("qtyStep", "1")
        size_precision = len(qty_step.split(".")[-1].rstrip("0")) if "." in qty_step else 0
        return {
            "symbol": symbol,
            "base_asset": symbol_info.get("baseCoin", ""),
            "quote_asset": symbol_info.get("quoteCoin", ""),
            "price_precision": symbol_info.get("priceScale", 2),
            "size_precision": size_precision,
            "exists": True,
        }

    def get_order_status(
        self, symbol: str, order_id: Optional[str] = None, client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            if not order_id and not client_order_id:
                raise ExchangeError("Either order_id or client_order_id must be provided", exchange="BYBIT")

            params = {"category": "spot", "symbol": symbol}
            if client_order_id:
                params["orderLinkId"] = client_order_id
            elif order_id:
                params["orderId"] = order_id

            response = self.client.get_open_orders(**params)
            if response and response.get("retCode") == 0 and not response.get("result", {}).get("list", []):
                response = self.client.get_order_history(**params)

            if not response or response.get("retCode") != 0:
                raise ExchangeError(
                    f"Failed to get order status from Bybit: {response.get('retMsg', 'Unknown error') if response else 'Empty response'}",
                    exchange="BYBIT",
                    error_code=response.get("retCode") if response else None,
                )
            order = (response.get("result", {}).get("list") or [None])[0]
            if not order:
                raise ExchangeError("Order not found on Bybit", exchange="BYBIT")

            return {
                "order_id": str(order.get("orderId", "")),
                "status": order.get("orderStatus", ""),
                "symbol": order.get("symbol", symbol),
                "side": order.get("side", ""),
                "type": order.get("orderType", ""),
                "quantity": order.get("qty", ""),
                "price": order.get("price", ""),
            }
        except ExchangeError:
            raise
        except FailedRequestError as e:
            raise ExchangeError(str(e), exchange="BYBIT", error_code=None, original_error=e)
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Bybit: {e}", exchange="BYBIT", original_error=e)

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            params: Dict[str, Any] = {"category": "spot"}
            if symbol:
                params["symbol"] = symbol
            response = self.client.get_open_orders(**params)
            if not response:
                raise ExchangeError("Empty response from Bybit API", exchange="BYBIT")
            if response.get("retCode") != 0:
                raise ExchangeError(
                    f"Failed to get open orders from Bybit: {response.get('retMsg', 'Unknown error')}",
                    exchange="BYBIT",
                    error_code=response.get("retCode"),
                )

            normalized_orders = []
            for order in response.get("result", {}).get("list", []):
                created_at = int(float(order.get("createdTime", "0")) / 1000) if order.get("createdTime") else int(
                    datetime.now(timezone.utc).timestamp()
                )
                updated_at = int(float(order.get("updatedTime", order.get("createdTime", "0"))) / 1000) if order.get(
                    "updatedTime"
                ) else created_at
                side = "BUY" if order.get("side") == "Buy" else "SELL" if order.get("side") == "Sell" else order.get("side", "")
                order_type = (
                    "MARKET"
                    if order.get("orderType") == "Market"
                    else "LIMIT" if order.get("orderType") == "Limit" else order.get("orderType", "")
                )
                normalized_orders.append(
                    {
                        "order_id": str(order.get("orderId", "")),
                        "symbol": order.get("symbol", ""),
                        "side": side,
                        "type": order_type,
                        "quantity": normalize_quantity(order.get("qty", "0")),
                        "price": normalize_price(order.get("price", "0")),
                        "status": normalize_order_status("BYBIT", order.get("orderStatus", "")),
                        "client_order_id": order.get("orderLinkId", ""),
                        "created_at": created_at,
                        "updated_at": updated_at,
                    }
                )
            return normalized_orders
        except ExchangeError:
            raise
        except FailedRequestError as e:
            raise ExchangeError(str(e), exchange="BYBIT", error_code=None, original_error=e)
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Bybit: {e}", exchange="BYBIT", original_error=e)

    def cancel_order(
        self, symbol: str, order_id: Optional[str] = None, client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            params: Dict[str, Any] = {"category": "spot", "symbol": symbol}
            if client_order_id:
                params["orderLinkId"] = client_order_id
            elif order_id:
                params["orderId"] = order_id
            else:
                raise ExchangeError("Either order_id or client_order_id must be provided", exchange="BYBIT")

            response = self.client.cancel_order(**params)
            if not response or response.get("retCode") != 0:
                raise ExchangeError(
                    f"Failed to cancel order on Bybit: {response.get('retMsg', 'Unknown error') if response else 'Empty response'}",
                    exchange="BYBIT",
                    error_code=response.get("retCode") if response else None,
                )
            result = response.get("result", {})
            created_at = (
                int(float(result.get("createdTime", "0")) / 1000)
                if result.get("createdTime")
                else int(datetime.now(timezone.utc).timestamp())
            )
            updated_at = (
                int(float(result.get("updatedTime", "0")) / 1000)
                if result.get("updatedTime")
                else int(datetime.now(timezone.utc).timestamp())
            )
            side = "BUY" if result.get("side") == "Buy" else "SELL" if result.get("side") == "Sell" else result.get("side", "")
            order_type = (
                "MARKET"
                if result.get("orderType") == "Market"
                else "LIMIT" if result.get("orderType") == "Limit" else result.get("orderType", "")
            )
            return {
                "order_id": str(result.get("orderId", order_id or "")),
                "symbol": result.get("symbol", symbol),
                "side": side,
                "type": order_type,
                "quantity": normalize_quantity(result.get("qty", "0")),
                "price": normalize_price(result.get("price", "0")),
                "status": normalize_order_status("BYBIT", result.get("orderStatus", "Canceled")),
                "client_order_id": result.get("orderLinkId", client_order_id or ""),
                "created_at": created_at,
                "updated_at": updated_at,
                "exchange_response": response,
            }
        except ExchangeError:
            raise
        except FailedRequestError as e:
            raise ExchangeError(str(e), exchange="BYBIT", error_code=None, original_error=e)
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Bybit: {e}", exchange="BYBIT", original_error=e)

    def create_order(
        self, symbol: str, side: ORDER_SIDES, order_type: ORDER_TYPES, quantity: str, price: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            client_order_id = generate_client_order_id()
            params: Dict[str, Any] = {
                "category": "spot",
                "symbol": symbol,
                "side": "Buy" if side == "BUY" else "Sell",
                "orderType": "Market" if order_type == "MARKET" else "Limit",
                "qty": quantity,
                "orderLinkId": client_order_id,
            }
            # Standardize spot market qty semantics: always treat qty as base coin.
            if order_type == "MARKET":
                params["marketUnit"] = "baseCoin"
            if order_type == "LIMIT":
                if price is None:
                    raise ExchangeError("Price is required for LIMIT orders", exchange="BYBIT")
                params["price"] = price

            response = self.client.place_order(**params)
            if not response or response.get("retCode") != 0:
                raise ExchangeError(
                    f"Failed to create order on Bybit: {response.get('retMsg', 'Unknown error') if response else 'Empty response'}",
                    exchange="BYBIT",
                    error_code=response.get("retCode") if response else None,
                )
            result = response.get("result", {})
            order_id = result.get("orderId", "")
            raw_status = result.get("orderStatus", "New")
            normalized_status = normalize_order_status("BYBIT", raw_status)
            if normalized_status not in ENDING_STATUSES:
                try:
                    status_info = self.get_order_status(symbol=symbol, order_id=str(order_id), client_order_id=client_order_id)
                    normalized_status = normalize_order_status("BYBIT", status_info.get("status", raw_status))
                except Exception as e:
                    logger.warning("Failed to fetch Bybit order status for %s: %s", client_order_id, e)

            created_at = (
                int(float(result.get("createdTime", "0")) / 1000)
                if result.get("createdTime")
                else int(datetime.now(timezone.utc).timestamp())
            )
            return {
                "order_id": str(order_id),
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "quantity": normalize_quantity(result.get("qty", quantity)),
                "price": normalize_price(result.get("price", price)),
                "status": normalized_status,
                "client_order_id": client_order_id,
                "created_at": created_at,
                "updated_at": created_at,
                "exchange_response": response,
            }
        except ExchangeError:
            raise
        except FailedRequestError as e:
            raise ExchangeError(str(e), exchange="BYBIT", error_code=None, original_error=e)
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error while connecting to Bybit: {e}", exchange="BYBIT", original_error=e)

    def get_mytrades(
        self, category: str, start_ms: int, end_ms: int, symbol: Optional[str] = None, limit: int = 200
    ) -> List[Dict[str, Any]]:
        try:
            bybit_cat = PRODUCT_TO_BYBIT_CATEGORY.get(category.upper(), "spot")
            cap = min(limit, 100)
            params: Dict[str, Any] = {"category": bybit_cat, "startTime": start_ms, "endTime": end_ms, "limit": cap}
            if symbol:
                params["symbol"] = symbol

            all_trades: List[Dict[str, Any]] = []
            while True:
                response = self.client.get_executions(**params)
                if not response or response.get("retCode") != 0:
                    raise ExchangeError(
                        f"Bybit get_executions failed: {(response or {}).get('retMsg', 'Unknown error')}",
                        exchange="BYBIT",
                        error_code=(response or {}).get("retCode"),
                    )
                result = response.get("result", {})
                page = result.get("list", [])
                all_trades.extend(self._normalize_bybit_trades(page))
                cursor = result.get("nextPageCursor", "")
                if not cursor or len(all_trades) >= limit:
                    break
                params["cursor"] = cursor

            all_trades.sort(key=lambda x: x["exec_time_ms"], reverse=True)
            return all_trades[:limit]
        except ExchangeError:
            raise
        except FailedRequestError as e:
            raise ExchangeError(str(e), exchange="BYBIT", error_code=None, original_error=e)
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(f"Network error connecting to Bybit: {e}", exchange="BYBIT", original_error=e)

    def _normalize_bybit_trades(self, page: list) -> List[Dict[str, Any]]:
        results = []
        for t in page:
            try:
                exec_time_ms = int(t.get("execTime", "0"))
            except (TypeError, ValueError):
                exec_time_ms = 0
            order_id_raw = str(t.get("orderId", ""))
            results.append(
                {
                    "exch_tid": int(t.get("execId", 0)),
                    "order_id": int(order_id_raw) if order_id_raw.isdigit() else 0,
                    "symbol": t.get("symbol", ""),
                    "side": t.get("side", ""),
                    "exec_px": str(t.get("execPrice", "0")),
                    "exec_qty": str(t.get("execQty", "0")),
                    "exec_fee": str(t.get("execFee", "0")),
                    "fee_currency": str(t.get("feeCurrency", "")),
                    "is_maker": t.get("isMaker") == "true" or t.get("isMaker") is True,
                    "exec_time_ms": exec_time_ms,
                }
            )
        return results
