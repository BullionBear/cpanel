from typing import Optional, List, Dict, Any
from typing import Literal
import binance as bn
from pybit.unified_trading import HTTP as BybitHTTP
from pybit.exceptions import FailedRequestError
import logging
import random
import string
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from app.models import API
from app.repositories.api_repository import APIRepository

logger = logging.getLogger(__name__)

# Normalized order statuses
ORDER_STATUS = Literal['NEW', 'FILLED', 'PARTIAL_FILLED', 'REJECT', 'CANCEL']
ENDING_STATUSES = {'FILLED', 'REJECT', 'CANCEL'}

def generate_client_order_id() -> str:
    """
    Generate custom client order ID in format: cpanel-yyyymmdd-hhmmss-{6 digit random alphabet string}
    
    Returns:
        Client order ID string
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S")
    random_str = ''.join(random.choices(string.ascii_uppercase + string.ascii_lowercase, k=6))
    return f"cpanel-{date_str}-{time_str}-{random_str}"

def normalize_order_status(exchange: str, status: str) -> str:
    """
    Normalize order status from exchange-specific to standard format.
    
    Args:
        exchange: Exchange name (BINANCE or BYBIT)
        status: Exchange-specific status string
    
    Returns:
        Normalized status: NEW, FILLED, PARTIAL_FILLED, REJECT, CANCEL
    """
    status_upper = status.upper() if status else ""
    
    # Binance status mapping
    binance_status_map = {
        'NEW': 'NEW',
        'PARTIALLY_FILLED': 'PARTIAL_FILLED',
        'FILLED': 'FILLED',
        'CANCELED': 'CANCEL',
        'PENDING_CANCEL': 'CANCEL',
        'REJECTED': 'REJECT',
        'EXPIRED': 'CANCEL',
    }
    
    # Bybit status mapping
    bybit_status_map = {
        'NEW': 'NEW',
        'PARTIALLYFILLED': 'PARTIAL_FILLED',
        'FILLED': 'FILLED',
        'CANCELED': 'CANCEL',
        'REJECTED': 'REJECT',
    }
    
    if exchange.upper() == 'BINANCE':
        return binance_status_map.get(status_upper, 'NEW')
    elif exchange.upper() == 'BYBIT':
        return bybit_status_map.get(status_upper, 'NEW')
    
    return 'NEW'  # Default

def normalize_numeric_string(value: Optional[str]) -> str:
    """
    Normalize numeric string by removing trailing zeros after decimal point.
    
    Examples:
        "1.00" -> "1"
        "1.00000000" -> "1"
        "0.50" -> "0.5"
        "100.00" -> "100"
        "-1" -> "-1" (special case, keep as is)
        None -> "-1"
        "0" -> "-1" (zero becomes -1)
    
    Args:
        value: Numeric string or None
    
    Returns:
        Normalized numeric string
    """
    if value is None:
        return "-1"
    
    value_stripped = value.strip() if isinstance(value, str) else str(value)
    
    # Handle special case "-1"
    if value_stripped == "-1":
        return "-1"
    
    # Check if it's zero or null
    try:
        value_float = float(value_stripped)
        if value_float == 0.0:
            return "-1"
    except (ValueError, TypeError):
        # If not a valid number, return as is
        return value_stripped
    
    # Check if it's a string representation of zero
    if value_stripped in ['0', '0.0', '0.00', '0.000', '0.0000', '0.00000', '0.000000', '0.0000000', '0.00000000']:
        return "-1"
    
    # Remove trailing zeros after decimal point
    if '.' in value_stripped:
        # Split into integer and decimal parts
        parts = value_stripped.split('.')
        integer_part = parts[0]
        decimal_part = parts[1].rstrip('0')  # Remove trailing zeros
        
        if decimal_part:
            # If there are remaining decimal digits, keep the decimal point
            return f"{integer_part}.{decimal_part}"
        else:
            # If all decimal digits were zeros, return just the integer part
            return integer_part
    
    return value_stripped

def normalize_price(price: Optional[str]) -> str:
    """
    Normalize price: convert 0, "0", "0.0000", or null to "-1", then trim trailing zeros.
    
    Args:
        price: Price string or None
    
    Returns:
        Normalized price string ("-1" for null/zero, trimmed version otherwise)
    """
    return normalize_numeric_string(price)

def normalize_quantity(quantity: Optional[str]) -> str:
    """
    Normalize quantity: trim trailing zeros after decimal point.
    
    Args:
        quantity: Quantity string or None
    
    Returns:
        Normalized quantity string (trimmed version)
    """
    if quantity is None:
        return "0"
    
    quantity_stripped = quantity.strip() if isinstance(quantity, str) else str(quantity)
    
    # Remove trailing zeros after decimal point
    if '.' in quantity_stripped:
        # Split into integer and decimal parts
        parts = quantity_stripped.split('.')
        integer_part = parts[0]
        decimal_part = parts[1].rstrip('0')  # Remove trailing zeros
        
        if decimal_part:
            # If there are remaining decimal digits, keep the decimal point
            return f"{integer_part}.{decimal_part}"
        else:
            # If all decimal digits were zeros, return just the integer part
            return integer_part
    
    return quantity_stripped

# TTL Cache for exchange info (24 hours)
class TTLCache:
    """Simple TTL cache implementation."""
    def __init__(self, ttl_seconds: int = 86400):  # 24 hours default
        self.cache: Dict[str, tuple[Any, datetime]] = {}
        self.ttl_seconds = ttl_seconds
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        if key not in self.cache:
            return None
        
        value, expiry = self.cache[key]
        if datetime.now(timezone.utc) > expiry:
            # Expired, remove from cache
            del self.cache[key]
            return None
        
        return value
    
    def set(self, key: str, value: Any) -> None:
        """Set value in cache with TTL."""
        expiry = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)
        self.cache[key] = (value, expiry)
    
    def clear(self) -> None:
        """Clear all cache entries."""
        self.cache.clear()

# Global cache instance (24 hours TTL)
_exchange_info_cache = TTLCache(ttl_seconds=86400)  # 24 hours

EXCHANGES = Literal['BINANCE', 'BYBIT']
WALLETS = Literal['SPOT', 'MARGIN', 'FUTURES', 'UNIFIED', 'FUNDING']
ORDER_TYPES = Literal['MARKET', 'LIMIT']
ORDER_SIDES = Literal['BUY', 'SELL']

# Custom Exceptions for API Verification
class NetworkError(Exception):
    """Raised when there's a network or connection error."""
    def __init__(self, message: str, exchange: str, original_error: Optional[Exception] = None):
        self.message = message
        self.exchange = exchange
        self.original_error = original_error
        super().__init__(self.message)


class ExchangeError(Exception):
    """Raised when there's an exchange API error."""
    def __init__(self, message: str, exchange: str, error_code: Optional[int] = None, original_error: Optional[Exception] = None):
        self.message = message
        self.exchange = exchange
        self.error_code = error_code
        self.original_error = original_error
        super().__init__(self.message)


class ApiService:
    def __init__(self):
        pass

    def verify_api(self, exchange: EXCHANGES, api_key: str, api_secret: str, passphrase: Optional[str] = None):
        """
        Verify API credentials for the specified exchange.
        
        Raises:
            NetworkError: If there's a network or connection error
            ExchangeError: If there's an exchange API error (includes error code and message)
        """
        match exchange:
            case 'BINANCE':
                self.verify_binance_api(api_key, api_secret, passphrase)
            case 'BYBIT':
                self.verify_bybit_api(api_key, api_secret, passphrase)
            case _:
                raise ValueError(f"Invalid exchange: {exchange}")
    
    def get_balance(self, exchange: EXCHANGES, api_key: str, api_secret: str, passphrase: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get normalized balance from the specified exchange.
        
        Returns:
            List of wallet balances in normalized format:
            [
                {
                    "wallet": "SPOT" | "MARGIN" | "FUTURES" | "UNIFIED" | "FUNDING",
                    "balance": [
                        {
                            "token": str,
                            "total": str,
                            "frozen": str,
                            "available": str
                        },
                        ...
                    ]
                },
                ...
            ]
        
        Raises:
            NetworkError: If there's a network or connection error
            ExchangeError: If there's an exchange API error (includes error code and message)
        """
        match exchange:
            case 'BINANCE':
                return self.get_binance_balance(api_key, api_secret, passphrase)
            case 'BYBIT':
                return self.get_bybit_balance(api_key, api_secret, passphrase)
            case _:
                raise ValueError(f"Invalid exchange: {exchange}")
    
    def get_binance_balance(self, api_key: str, api_secret: str, passphrase: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get normalized balance from Binance (SPOT, MARGIN, FUTURES).
        
        Returns normalized balance format.
        
        Raises:
            NetworkError: If there's a network or connection error
            ExchangeError: If there's a Binance API error (includes error code and message)
        """
        try:
            client = bn.Client(api_key, api_secret)
            result = []
            
            # Get SPOT balance
            try:
                account = client.get_account()
                if account and 'balances' in account:
                    spot_balances = []
                    for balance in account['balances']:
                        free = float(balance.get('free', 0))
                        locked = float(balance.get('locked', 0))
                        total = free + locked
                        
                        if total > 0:  # Only include non-zero balances
                            spot_balances.append({
                                "token": balance['asset'],
                                "total": str(total),
                                "frozen": str(locked),
                                "available": str(free)
                            })
                    
                    if spot_balances:
                        result.append({
                            "wallet": "SPOT",
                            "balance": spot_balances
                        })
            except Exception as e:
                logger.warning(f"Failed to get Binance SPOT balance: {e}")
            
            # Get MARGIN balance
            try:
                margin_account = client.get_margin_account()
                if margin_account and 'userAssets' in margin_account:
                    margin_balances = []
                    for asset in margin_account['userAssets']:
                        free = float(asset.get('free', 0))
                        locked = float(asset.get('locked', 0))
                        borrowed = float(asset.get('borrowed', 0))
                        total = free + locked - borrowed
                        
                        if total > 0 or borrowed > 0:  # Include if has balance or borrowed
                            margin_balances.append({
                                "token": asset['asset'],
                                "total": str(total),
                                "frozen": str(locked),
                                "available": str(free)
                            })
                    
                    if margin_balances:
                        result.append({
                            "wallet": "MARGIN",
                            "balance": margin_balances
                        })
            except Exception as e:
                logger.warning(f"Failed to get Binance MARGIN balance: {e}")
            
            # Get FUTURES balance
            try:
                futures_balance = client.futures_account_balance()
                if futures_balance:
                    futures_balances = []
                    for account_balance in futures_balance:
                        balance_amt = float(account_balance.get('balance', 0))
                        available_balance = float(account_balance.get('availableBalance', 0))
                        frozen = balance_amt - available_balance
                        
                        if balance_amt > 0:
                            # Binance futures returns balance per asset
                            asset = account_balance.get('asset', 'USDT')
                            futures_balances.append({
                                "token": asset,
                                "total": str(balance_amt),
                                "frozen": str(frozen),
                                "available": str(available_balance)
                            })
                    
                    if futures_balances:
                        result.append({
                            "wallet": "FUTURES",
                            "balance": futures_balances
                        })
            except Exception as e:
                logger.warning(f"Failed to get Binance FUTURES balance: {e}")
            
            return result
            
        except bn.exceptions.BinanceAPIException as e:
            raise ExchangeError(
                str(e),
                exchange="BINANCE",
                error_code=e.code,
                original_error=e
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(
                f"Network error while connecting to Binance: {str(e)}",
                exchange="BINANCE",
                original_error=e
            )
        except Exception as e:
            raise ExchangeError(
                f"Unexpected error getting Binance balance: {str(e)}",
                exchange="BINANCE",
                error_code=None,
                original_error=e
            )

    def get_bybit_balance(self, api_key: str, api_secret: str, passphrase: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get normalized balance from Bybit (UNIFIED, FUNDING).
        
        Returns normalized balance format.
        
        Raises:
            NetworkError: If there's a network or connection error
            ExchangeError: If there's a Bybit API error (includes error code and message)
        """
        try:
            client = BybitHTTP(
                api_key=api_key,
                api_secret=api_secret,
                testnet=False,
            )
            result = []
            
            # Get UNIFIED balance
            try:
                unified_response = client.get_wallet_balance(accountType="UNIFIED")
                if unified_response and unified_response.get('retCode') == 0:
                    unified_list = unified_response.get('result', {}).get('list', [])
                    if unified_list:
                        for account in unified_list:
                            coins = account.get('coin', [])
                            unified_balances = []
                            for coin_data in coins:
                                # Handle empty strings and convert to float safely
                                wallet_balance_str = coin_data.get('walletBalance', '0') or '0'
                                available_balance_str = coin_data.get('availableToWithdraw', '0') or '0'
                                
                                try:
                                    wallet_balance = float(wallet_balance_str)
                                    available_balance = float(available_balance_str)
                                    frozen = wallet_balance - available_balance
                                    
                                    if wallet_balance > 0:
                                        unified_balances.append({
                                            "token": coin_data.get('coin', ''),
                                            "total": str(wallet_balance),
                                            "frozen": str(frozen),
                                            "available": str(available_balance)
                                        })
                                except (ValueError, TypeError) as e:
                                    logger.warning(f"Failed to parse UNIFIED balance for coin {coin_data.get('coin', 'unknown')}: {e}")
                                    continue
                            
                            if unified_balances:
                                result.append({
                                    "wallet": "UNIFIED",
                                    "balance": unified_balances
                                })
            except Exception as e:
                logger.warning(f"Failed to get Bybit UNIFIED balance: {e}")
            
            # Get FUNDING balance (uses get_coins_balance endpoint)
            try:
                # Bybit FUNDING uses get_coins_balance endpoint with accountType="FUND"
                funding_response = client.get_coins_balance(accountType="FUND")
                if funding_response and funding_response.get('retCode') == 0:
                    funding_balances_list = funding_response.get('result', {}).get('balance', [])
                    if funding_balances_list:
                        funding_balances = []
                        for coin_data in funding_balances_list:
                            # Handle empty strings and convert to float safely
                            wallet_balance_str = coin_data.get('walletBalance', '0') or '0'
                            transfer_balance_str = coin_data.get('transferBalance', '0') or '0'
                            
                            try:
                                wallet_balance = float(wallet_balance_str)
                                transfer_balance = float(transfer_balance_str)
                                # For FUNDING, frozen = walletBalance - transferBalance
                                frozen = wallet_balance - transfer_balance
                                
                                if wallet_balance > 0:
                                    funding_balances.append({
                                        "token": coin_data.get('coin', ''),
                                        "total": str(wallet_balance),
                                        "frozen": str(frozen),
                                        "available": str(transfer_balance)
                                    })
                            except (ValueError, TypeError) as e:
                                logger.warning(f"Failed to parse FUNDING balance for coin {coin_data.get('coin', 'unknown')}: {e}")
                                continue
                        
                        if funding_balances:
                            result.append({
                                "wallet": "FUNDING",
                                "balance": funding_balances
                            })
            except Exception as e:
                logger.warning(f"Failed to get Bybit FUNDING balance: {e}")
            
            return result
            
        except FailedRequestError as e:
            error_str = str(e)
            error_code = None
            if "ErrCode:" in error_str:
                try:
                    code_part = error_str.split("ErrCode:")[1].split(")")[0].strip()
                    error_code = int(code_part)
                except (ValueError, IndexError):
                    pass
            
            raise ExchangeError(
                error_str,
                exchange="BYBIT",
                error_code=error_code,
                original_error=e
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(
                f"Network error while connecting to Bybit: {str(e)}",
                exchange="BYBIT",
                original_error=e
            )
        except Exception as e:
            raise ExchangeError(
                f"Unexpected error getting Bybit balance: {str(e)}",
                exchange="BYBIT",
                error_code=None,
                original_error=e
            )

    def verify_binance_api(self, api_key: str, api_secret: str, passphrase: Optional[str] = None):
        """
        Verify Binance API credentials.
        
        Raises:
            NetworkError: If there's a network or connection error
            ExchangeError: If there's a Binance API error (includes error code and message)
        """
        try:
            client = bn.Client(api_key, api_secret)
            # Test API by getting account info
            # This requires valid API key with account read permissions
            account = client.get_account()
            if account and 'balances' in account:
                return  # Success, no exception raised
            raise ExchangeError(
                "Account info response is invalid",
                exchange="BINANCE",
                error_code=None
            )
        except bn.exceptions.BinanceAPIException as e:
            error_code = e.code
            error_message = str(e.message) if hasattr(e, 'message') else str(e)
            raise ExchangeError(
                error_message,
                exchange="BINANCE",
                error_code=error_code,
                original_error=e
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(
                f"Network error while connecting to Binance: {str(e)}",
                exchange="BINANCE",
                original_error=e
            )
        except Exception as e:
            # Catch-all for other unexpected errors
            logger.error(f"Unexpected error during Binance API verification: {e}", exc_info=True)
            raise ExchangeError(
                f"Unexpected error during Binance API verification: {str(e)}",
                exchange="BINANCE",
                error_code=None,
                original_error=e
            )

    def get_symbol_info(self, exchange: EXCHANGES, symbol: str, api_key: Optional[str] = None, api_secret: Optional[str] = None) -> Dict[str, Any]:
        """
        Get symbol information from the specified exchange.
        
        Args:
            exchange: Exchange name (BINANCE or BYBIT)
            symbol: Symbol name (e.g., "BTCUSDT")
            api_key: Optional API key (not required for public endpoints)
            api_secret: Optional API secret (not required for public endpoints)
        
        Returns:
            Dictionary with symbol information:
            {
                "symbol": str,
                "base_asset": str,  # Base token (e.g., "BTC")
                "quote_asset": str,  # Quote token (e.g., "USDT")
                "price_precision": int,  # Price decimal places
                "size_precision": int,  # Size/quantity decimal places
                "exists": bool  # Whether symbol exists on exchange
            }
        
        Raises:
            NetworkError: If there's a network or connection error
            ExchangeError: If there's an exchange API error
        """
        match exchange:
            case 'BINANCE':
                return self.get_binance_symbol_info(symbol)
            case 'BYBIT':
                return self.get_bybit_symbol_info(symbol)
            case _:
                raise ValueError(f"Invalid exchange: {exchange}")
    
    def get_binance_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get symbol information from Binance.
        
        Uses cached exchange info to reduce API calls (24 hour TTL).
        
        Raises:
            NetworkError: If there's a network or connection error
            ExchangeError: If symbol doesn't exist or there's an API error
        """
        try:
            # Check cache first
            cache_key = f"binance_exchange_info"
            exchange_info = _exchange_info_cache.get(cache_key)
            
            if exchange_info is None:
                # Cache miss, fetch from API
                logger.info("Fetching Binance exchange info from API (cache miss)")
                client = bn.Client()
                exchange_info = client.get_exchange_info()
                # Cache the result
                _exchange_info_cache.set(cache_key, exchange_info)
            else:
                logger.debug("Using cached Binance exchange info")
            
            if not exchange_info or 'symbols' not in exchange_info:
                raise ExchangeError(
                    "Invalid response from Binance exchange info",
                    exchange="BINANCE",
                    error_code=None
                )
            
            # Find the symbol
            symbol_info = None
            for s in exchange_info['symbols']:
                if s['symbol'] == symbol and s['status'] == 'TRADING':
                    symbol_info = s
                    break
            
            if not symbol_info:
                raise ExchangeError(
                    f"Symbol {symbol} not found or not trading on Binance",
                    exchange="BINANCE",
                    error_code=None
                )
            
            # Extract base and quote assets
            base_asset = symbol_info.get('baseAsset', '')
            quote_asset = symbol_info.get('quoteAsset', '')
            
            # Calculate price precision from tickSize
            filters = symbol_info.get('filters', [])
            price_precision = 8  # Default
            size_precision = 8  # Default
            
            for filter_item in filters:
                if filter_item.get('filterType') == 'PRICE_FILTER':
                    tick_size_str = filter_item.get('tickSize', '1')
                    # Count decimal places
                    if '.' in tick_size_str:
                        price_precision = len(tick_size_str.split('.')[-1].rstrip('0'))
                    else:
                        price_precision = 0
                elif filter_item.get('filterType') == 'LOT_SIZE':
                    step_size_str = filter_item.get('stepSize', '1')
                    # Count decimal places
                    if '.' in step_size_str:
                        size_precision = len(step_size_str.split('.')[-1].rstrip('0'))
                    else:
                        size_precision = 0
            
            return {
                "symbol": symbol,
                "base_asset": base_asset,
                "quote_asset": quote_asset,
                "price_precision": price_precision,
                "size_precision": size_precision,
                "exists": True
            }
            
        except ExchangeError:
            raise
        except bn.exceptions.BinanceAPIException as e:
            raise ExchangeError(
                str(e),
                exchange="BINANCE",
                error_code=e.code,
                original_error=e
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(
                f"Network error while connecting to Binance: {str(e)}",
                exchange="BINANCE",
                original_error=e
            )
        except Exception as e:
            raise ExchangeError(
                f"Unexpected error getting Binance symbol info: {str(e)}",
                exchange="BINANCE",
                error_code=None,
                original_error=e
            )
    
    def get_bybit_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get symbol information from Bybit.
        
        Uses cached instruments info to reduce API calls (24 hour TTL).
        
        Raises:
            NetworkError: If there's a network or connection error
            ExchangeError: If symbol doesn't exist or there's an API error
        """
        try:
            # Check cache first - Bybit returns all instruments, so we cache the full response
            cache_key = "bybit_instruments_spot"
            cached_response = _exchange_info_cache.get(cache_key)
            
            if cached_response is None:
                # Cache miss, fetch from API
                logger.info("Fetching Bybit instruments info from API (cache miss)")
                client = BybitHTTP(testnet=False)
                # Get all instruments (without symbol filter to cache full list)
                response = client.get_instruments_info(category="spot")
                
                if response and response.get('retCode') == 0:
                    # Cache the result
                    _exchange_info_cache.set(cache_key, response)
                else:
                    # If full list fails, try with symbol
                    response = client.get_instruments_info(category="spot", symbol=symbol)
            else:
                logger.debug("Using cached Bybit instruments info")
                response = cached_response
                # Filter for the specific symbol from cached data
                result = response.get('result', {})
                list_data = result.get('list', [])
                symbol_info = next((s for s in list_data if s.get('symbol') == symbol), None)
                
                if symbol_info:
                    # Found in cache, return early
                    base_asset = symbol_info.get('baseCoin', '')
                    quote_asset = symbol_info.get('quoteCoin', '')
                    price_precision = symbol_info.get('priceScale', 2)
                    size_precision = 8
                    
                    lot_size_filter = symbol_info.get('lotSizeFilter', {})
                    if lot_size_filter:
                        qty_step = lot_size_filter.get('qtyStep', '1')
                        if '.' in qty_step:
                            size_precision = len(qty_step.split('.')[-1].rstrip('0'))
                        else:
                            size_precision = 0
                    
                    return {
                        "symbol": symbol,
                        "base_asset": base_asset,
                        "quote_asset": quote_asset,
                        "price_precision": price_precision,
                        "size_precision": size_precision,
                        "exists": True
                    }
                else:
                    # Symbol not found in cached data, fetch fresh
                    logger.info(f"Symbol {symbol} not found in cache, fetching from API")
                    client = BybitHTTP(testnet=False)
                    response = client.get_instruments_info(category="spot", symbol=symbol)
            
            # Process response (either from cache or fresh API call)
            if not response or response.get('retCode') != 0:
                ret_msg = response.get('retMsg', 'Unknown error') if response else 'Empty response'
                raise ExchangeError(
                    f"Symbol {symbol} not found on Bybit: {ret_msg}",
                    exchange="BYBIT",
                    error_code=response.get('retCode') if response else None
                )
            
            result = response.get('result', {})
            list_data = result.get('list', [])
            
            if not list_data:
                raise ExchangeError(
                    f"Symbol {symbol} not found on Bybit",
                    exchange="BYBIT",
                    error_code=None
                )
            
            symbol_info = list_data[0]
            
            # Extract base and quote assets
            base_asset = symbol_info.get('baseCoin', '')
            quote_asset = symbol_info.get('quoteCoin', '')
            
            # Get precision from priceScale and lotSizeFilter
            price_precision = symbol_info.get('priceScale', 2)  # Default to 2
            size_precision = 8  # Default
            
            # Try to get from lotSizeFilter
            lot_size_filter = symbol_info.get('lotSizeFilter', {})
            if lot_size_filter:
                qty_step = lot_size_filter.get('qtyStep', '1')
                if '.' in qty_step:
                    size_precision = len(qty_step.split('.')[-1].rstrip('0'))
                else:
                    size_precision = 0
            
            return {
                "symbol": symbol,
                "base_asset": base_asset,
                "quote_asset": quote_asset,
                "price_precision": price_precision,
                "size_precision": size_precision,
                "exists": True
            }
            
        except ExchangeError:
            raise
        except FailedRequestError as e:
            error_str = str(e)
            error_code = None
            if "ErrCode:" in error_str:
                try:
                    code_part = error_str.split("ErrCode:")[1].split(")")[0].strip()
                    error_code = int(code_part)
                except (ValueError, IndexError):
                    pass
            
            raise ExchangeError(
                error_str,
                exchange="BYBIT",
                error_code=error_code,
                original_error=e
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(
                f"Network error while connecting to Bybit: {str(e)}",
                exchange="BYBIT",
                original_error=e
            )
        except Exception as e:
            raise ExchangeError(
                f"Unexpected error getting Bybit symbol info: {str(e)}",
                exchange="BYBIT",
                error_code=None,
                original_error=e
            )

    def get_order_status(
        self,
        exchange: EXCHANGES,
        api_key: str,
        api_secret: str,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        passphrase: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get order status from exchange.
        
        Args:
            exchange: Exchange name (BINANCE or BYBIT)
            api_key: API key
            api_secret: API secret
            symbol: Symbol name
            order_id: Exchange order ID
            client_order_id: Client order ID
            passphrase: Optional passphrase
        
        Returns:
            Dictionary with order status information
        
        Raises:
            NetworkError: If there's a network or connection error
            ExchangeError: If there's an exchange API error
        """
        match exchange:
            case 'BINANCE':
                return self.get_binance_order_status(api_key, api_secret, symbol, order_id, client_order_id)
            case 'BYBIT':
                return self.get_bybit_order_status(api_key, api_secret, symbol, order_id, client_order_id, passphrase)
            case _:
                raise ValueError(f"Invalid exchange: {exchange}")
    
    def get_binance_order_status(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get order status from Binance."""
        try:
            client = bn.Client(api_key, api_secret)
            
            if client_order_id:
                order = client.get_order(symbol=symbol, origClientOrderId=client_order_id)
            elif order_id:
                order = client.get_order(symbol=symbol, orderId=int(order_id))
            else:
                raise ExchangeError(
                    "Either order_id or client_order_id must be provided",
                    exchange="BINANCE",
                    error_code=None
                )
            
            return {
                "order_id": str(order.get('orderId', '')),
                "status": order.get('status', ''),
                "symbol": order.get('symbol', symbol),
                "side": order.get('side', ''),
                "type": order.get('type', ''),
                "quantity": order.get('executedQty', ''),
                "price": order.get('price', ''),
            }
        except bn.exceptions.BinanceAPIException as e:
            raise ExchangeError(
                str(e),
                exchange="BINANCE",
                error_code=e.code,
                original_error=e
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(
                f"Network error while connecting to Binance: {str(e)}",
                exchange="BINANCE",
                original_error=e
            )
        except Exception as e:
            raise ExchangeError(
                f"Unexpected error getting Binance order status: {str(e)}",
                exchange="BINANCE",
                error_code=None,
                original_error=e
            )
    
    def get_bybit_order_status(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        passphrase: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get order status from Bybit."""
        try:
            client = BybitHTTP(
                api_key=api_key,
                api_secret=api_secret,
                testnet=False,
            )
            
            # Try open orders first
            if client_order_id:
                response = client.get_open_orders(
                    category="spot",
                    symbol=symbol,
                    orderLinkId=client_order_id
                )
            elif order_id:
                response = client.get_open_orders(
                    category="spot",
                    symbol=symbol,
                    orderId=order_id
                )
            else:
                raise ExchangeError(
                    "Either order_id or client_order_id must be provided",
                    exchange="BYBIT",
                    error_code=None
                )
            
            # If not found in open orders, try order history
            if response and response.get('retCode') == 0:
                result = response.get('result', {})
                list_data = result.get('list', [])
                if not list_data:
                    # Try order history
                    if client_order_id:
                        response = client.get_order_history(
                            category="spot",
                            symbol=symbol,
                            orderLinkId=client_order_id
                        )
                    elif order_id:
                        response = client.get_order_history(
                            category="spot",
                            symbol=symbol,
                            orderId=order_id
                        )
            
            if not response or response.get('retCode') != 0:
                ret_msg = response.get('retMsg', 'Unknown error') if response else 'Empty response'
                raise ExchangeError(
                    f"Failed to get order status from Bybit: {ret_msg}",
                    exchange="BYBIT",
                    error_code=response.get('retCode') if response else None
                )
            
            result = response.get('result', {})
            list_data = result.get('list', [])
            
            if not list_data:
                raise ExchangeError(
                    f"Order not found on Bybit",
                    exchange="BYBIT",
                    error_code=None
                )
            
            order = list_data[0]
            
            return {
                "order_id": str(order.get('orderId', '')),
                "status": order.get('orderStatus', ''),
                "symbol": order.get('symbol', symbol),
                "side": order.get('side', ''),
                "type": order.get('orderType', ''),
                "quantity": order.get('qty', ''),
                "price": order.get('price', ''),
            }
        except ExchangeError:
            raise
        except FailedRequestError as e:
            error_str = str(e)
            error_code = None
            if "ErrCode:" in error_str:
                try:
                    code_part = error_str.split("ErrCode:")[1].split(")")[0].strip()
                    error_code = int(code_part)
                except (ValueError, IndexError):
                    pass
            
            raise ExchangeError(
                error_str,
                exchange="BYBIT",
                error_code=error_code,
                original_error=e
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(
                f"Network error while connecting to Bybit: {str(e)}",
                exchange="BYBIT",
                original_error=e
            )
        except Exception as e:
            raise ExchangeError(
                f"Unexpected error getting Bybit order status: {str(e)}",
                exchange="BYBIT",
                error_code=None,
                original_error=e
            )

    def create_order(
        self,
        exchange: EXCHANGES,
        api_key: str,
        api_secret: str,
        symbol: str,
        side: ORDER_SIDES,
        order_type: ORDER_TYPES,
        quantity: str,
        price: Optional[str] = None,
        passphrase: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create an order on the specified exchange.
        
        Args:
            exchange: Exchange name (BINANCE or BYBIT)
            api_key: API key
            api_secret: API secret
            symbol: Symbol name (e.g., "BTCUSDT")
            side: Order side (BUY or SELL)
            order_type: Order type (MARKET or LIMIT)
            quantity: Order quantity (as string)
            price: Order price (as string, required for LIMIT orders)
        
        Returns:
            Dictionary with order information from exchange
        
        Raises:
            NetworkError: If there's a network or connection error
            ExchangeError: If there's an exchange API error (includes error code and message)
        """
        match exchange:
            case 'BINANCE':
                return self.create_binance_order(api_key, api_secret, symbol, side, order_type, quantity, price)
            case 'BYBIT':
                return self.create_bybit_order(api_key, api_secret, symbol, side, order_type, quantity, price, passphrase)
            case _:
                raise ValueError(f"Invalid exchange: {exchange}")
    
    def create_binance_order(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        side: ORDER_SIDES,
        order_type: ORDER_TYPES,
        quantity: str,
        price: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create an order on Binance.
        
        Raises:
            NetworkError: If there's a network or connection error
            ExchangeError: If there's a Binance API error (includes error code and message)
        """
        try:
            client = bn.Client(api_key, api_secret)
            
            # Convert side to Binance format (BUY -> BUY, SELL -> SELL)
            binance_side = side
            # Convert order type to Binance format
            binance_order_type = "MARKET" if order_type == "MARKET" else "LIMIT"
            
            # Generate custom client order ID
            client_order_id = generate_client_order_id()
            
            # Build order parameters
            order_params = {
                "symbol": symbol,
                "side": binance_side,
                "type": binance_order_type,
                "quantity": quantity,
                "newClientOrderId": client_order_id,  # Use custom client order ID
            }
            
            # Add price for LIMIT orders
            if order_type == "LIMIT":
                if price is None:
                    raise ExchangeError(
                        "Price is required for LIMIT orders",
                        exchange="BINANCE",
                        error_code=None
                    )
                order_params["price"] = price
                order_params["timeInForce"] = "GTC"  # Good Till Cancel
            
            # Place the order
            order_result = client.create_order(**order_params)
            
            # Normalize status
            raw_status = order_result.get('status', '')
            normalized_status = normalize_order_status('BINANCE', raw_status)
            
            # Normalize price
            raw_price = order_result.get('price', price)
            normalized_price = normalize_price(raw_price)
            
            # Normalize quantity
            raw_quantity = order_result.get('executedQty', quantity)
            normalized_quantity = normalize_quantity(raw_quantity)
            
            # If status is not ending, fetch actual status
            if normalized_status not in ENDING_STATUSES:
                try:
                    status_info = self.get_binance_order_status(
                        api_key, api_secret, symbol,
                        order_id=str(order_result.get('orderId', '')),
                        client_order_id=client_order_id
                    )
                    normalized_status = normalize_order_status('BINANCE', status_info.get('status', raw_status))
                except Exception as e:
                    logger.warning(f"Failed to fetch order status for {client_order_id}: {e}")
            
            # Get timestamps from order result or use current time
            # Binance returns time in milliseconds, convert to seconds
            order_time_ms = order_result.get('transactTime', order_result.get('time', None))
            if order_time_ms:
                created_at = int(order_time_ms / 1000)  # Convert milliseconds to seconds
            else:
                created_at = int(datetime.now(timezone.utc).timestamp())
            
            # Updated_at is same as created_at for new orders
            updated_at = created_at
            
            return {
                "order_id": str(order_result.get('orderId', '')),
                "symbol": order_result.get('symbol', symbol),
                "side": order_result.get('side', side),
                "type": order_result.get('type', order_type),
                "quantity": normalized_quantity,
                "price": normalized_price,
                "status": normalized_status,
                "client_order_id": client_order_id,
                "created_at": created_at,
                "updated_at": updated_at,
                "exchange_response": order_result
            }
            
        except bn.exceptions.BinanceAPIException as e:
            error_code = e.code
            error_message = str(e.message) if hasattr(e, 'message') else str(e)
            raise ExchangeError(
                error_message,
                exchange="BINANCE",
                error_code=error_code,
                original_error=e
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(
                f"Network error while connecting to Binance: {str(e)}",
                exchange="BINANCE",
                original_error=e
            )
        except Exception as e:
            raise ExchangeError(
                f"Unexpected error creating Binance order: {str(e)}",
                exchange="BINANCE",
                error_code=None,
                original_error=e
            )
    
    def create_bybit_order(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        side: ORDER_SIDES,
        order_type: ORDER_TYPES,
        quantity: str,
        price: Optional[str] = None,
        passphrase: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create an order on Bybit.
        
        Raises:
            NetworkError: If there's a network or connection error
            ExchangeError: If there's a Bybit API error (includes error code and message)
        """
        try:
            client = BybitHTTP(
                api_key=api_key,
                api_secret=api_secret,
                testnet=False,
            )
            
            # Generate custom client order ID
            client_order_id = generate_client_order_id()
            
            # Convert side to Bybit format (BUY -> Buy, SELL -> Sell)
            bybit_side = "Buy" if side == "BUY" else "Sell"
            # Convert order type to Bybit format
            bybit_order_type = "Market" if order_type == "MARKET" else "Limit"
            
            # Build order parameters
            order_params = {
                "category": "spot",
                "symbol": symbol,
                "side": bybit_side,
                "orderType": bybit_order_type,
                "qty": quantity,
                "orderLinkId": client_order_id,  # Use custom client order ID
            }
            
            # Add price for LIMIT orders
            if order_type == "LIMIT":
                if price is None:
                    raise ExchangeError(
                        "Price is required for LIMIT orders",
                        exchange="BYBIT",
                        error_code=None
                    )
                order_params["price"] = price
            
            # Place the order
            response = client.place_order(**order_params)
            
            if not response or response.get('retCode') != 0:
                ret_msg = response.get('retMsg', 'Unknown error') if response else 'Empty response'
                raise ExchangeError(
                    f"Failed to create order on Bybit: {ret_msg}",
                    exchange="BYBIT",
                    error_code=response.get('retCode') if response else None
                )
            
            result = response.get('result', {})
            order_id = result.get('orderId', '')
            
            # Get initial status from response
            raw_status = result.get('orderStatus', 'New')
            normalized_status = normalize_order_status('BYBIT', raw_status)
            
            # Normalize price
            raw_price = result.get('price', price)
            normalized_price = normalize_price(raw_price)
            
            # Normalize quantity
            raw_quantity = result.get('qty', quantity)
            normalized_quantity = normalize_quantity(raw_quantity)
            
            # If status is not ending, fetch actual status
            if normalized_status not in ENDING_STATUSES:
                try:
                    status_info = self.get_bybit_order_status(
                        api_key, api_secret, symbol,
                        order_id=str(order_id),
                        client_order_id=client_order_id,
                        passphrase=passphrase
                    )
                    normalized_status = normalize_order_status('BYBIT', status_info.get('status', raw_status))
                except Exception as e:
                    logger.warning(f"Failed to fetch order status for {client_order_id}: {e}")
            
            # Get timestamps from order result or use current time
            # Bybit returns createdTime in milliseconds, convert to seconds
            result_time_ms = result.get('createdTime', None)
            if result_time_ms:
                created_at = int(result_time_ms / 1000)  # Convert milliseconds to seconds
            else:
                created_at = int(datetime.now(timezone.utc).timestamp())
            
            # Updated_at is same as created_at for new orders
            updated_at = created_at
            
            return {
                "order_id": str(order_id),
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "quantity": normalized_quantity,
                "price": normalized_price,
                "status": normalized_status,
                "client_order_id": client_order_id,
                "created_at": created_at,
                "updated_at": updated_at,
                "exchange_response": response
            }
            
        except ExchangeError:
            raise
        except FailedRequestError as e:
            error_str = str(e)
            error_code = None
            if "ErrCode:" in error_str:
                try:
                    code_part = error_str.split("ErrCode:")[1].split(")")[0].strip()
                    error_code = int(code_part)
                except (ValueError, IndexError):
                    pass
            
            raise ExchangeError(
                error_str,
                exchange="BYBIT",
                error_code=error_code,
                original_error=e
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(
                f"Network error while connecting to Bybit: {str(e)}",
                exchange="BYBIT",
                original_error=e
            )
        except Exception as e:
            raise ExchangeError(
                f"Unexpected error creating Bybit order: {str(e)}",
                exchange="BYBIT",
                error_code=None,
                original_error=e
            )

    def verify_bybit_api(self, api_key: str, api_secret: str, passphrase: Optional[str] = None):
        """
        Verify Bybit API credentials.
        
        Raises:
            NetworkError: If there's a network or connection error
            ExchangeError: If there's a Bybit API error (includes error code and message)
        """
        try:
            client = BybitHTTP(
                api_key=api_key,
                api_secret=api_secret,
                testnet=False,
            )
            # Test API by getting wallet balance
            response = client.get_wallet_balance(
                accountType="UNIFIED"
            )
            
            if not response:
                raise ExchangeError(
                    "Empty response from Bybit API",
                    exchange="BYBIT",
                    error_code=None
                )
            
            ret_code = response.get('retCode')
            ret_msg = response.get('retMsg', 'Unknown error')
            
            if ret_code == 0:
                return  # Success, no exception raised
            
            raise ExchangeError(
                ret_msg,
                exchange="BYBIT",
                error_code=ret_code
            )
        except FailedRequestError as e:
            # Handle pybit FailedRequestError
            error_str = str(e)
            # Try to extract error code from error message if available
            error_code = None
            if "ErrCode:" in error_str:
                try:
                    # Extract error code from string like "ErrCode: 401"
                    code_part = error_str.split("ErrCode:")[1].split(")")[0].strip()
                    error_code = int(code_part)
                except (ValueError, IndexError):
                    pass
            
            raise ExchangeError(
                error_str,
                exchange="BYBIT",
                error_code=error_code,
                original_error=e
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise NetworkError(
                f"Network error while connecting to Bybit: {str(e)}",
                exchange="BYBIT",
                original_error=e
            )
        except Exception as e:
            # Catch-all for other unexpected errors
            logger.error(f"Unexpected error during Bybit API verification: {e}", exc_info=True)
            raise ExchangeError(
                f"Unexpected error during Bybit API verification: {str(e)}",
                exchange="BYBIT",
                error_code=None,
                original_error=e
            )