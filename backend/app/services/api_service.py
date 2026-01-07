from typing import Optional, List, Dict, Any
from typing import Literal
import binance as bn
from pybit.unified_trading import HTTP as BybitHTTP
from pybit.exceptions import FailedRequestError
import logging
from app.models import API
from app.repositories.api_repository import APIRepository

logger = logging.getLogger(__name__)

EXCHANGES = Literal['BINANCE', 'BYBIT']
WALLETS = Literal['SPOT', 'MARGIN', 'FUTURES', 'UNIFIED', 'FUNDING']

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
        Get normalized balance from Bybit (UNIFIED, FUNDING, SPOT).
        
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
                                wallet_balance = float(coin_data.get('walletBalance', 0))
                                available_balance = float(coin_data.get('availableToWithdraw', 0))
                                frozen = wallet_balance - available_balance
                                
                                if wallet_balance > 0:
                                    unified_balances.append({
                                        "token": coin_data.get('coin', ''),
                                        "total": str(wallet_balance),
                                        "frozen": str(frozen),
                                        "available": str(available_balance)
                                    })
                            
                            if unified_balances:
                                result.append({
                                    "wallet": "UNIFIED",
                                    "balance": unified_balances
                                })
            except Exception as e:
                logger.warning(f"Failed to get Bybit UNIFIED balance: {e}")
            
            # Get FUNDING balance
            try:
                funding_response = client.get_wallet_balance(accountType="FUND")
                if funding_response and funding_response.get('retCode') == 0:
                    funding_list = funding_response.get('result', {}).get('list', [])
                    if funding_list:
                        for account in funding_list:
                            coins = account.get('coin', [])
                            funding_balances = []
                            for coin_data in coins:
                                wallet_balance = float(coin_data.get('walletBalance', 0))
                                available_balance = float(coin_data.get('availableToWithdraw', 0))
                                frozen = wallet_balance - available_balance
                                
                                if wallet_balance > 0:
                                    funding_balances.append({
                                        "token": coin_data.get('coin', ''),
                                        "total": str(wallet_balance),
                                        "frozen": str(frozen),
                                        "available": str(available_balance)
                                    })
                            
                            if funding_balances:
                                result.append({
                                    "wallet": "FUNDING",
                                    "balance": funding_balances
                                })
            except Exception as e:
                logger.warning(f"Failed to get Bybit FUNDING balance: {e}")
            
            # Get SPOT balance (if available)
            try:
                spot_response = client.get_wallet_balance(accountType="SPOT")
                if spot_response and spot_response.get('retCode') == 0:
                    spot_list = spot_response.get('result', {}).get('list', [])
                    if spot_list:
                        for account in spot_list:
                            coins = account.get('coin', [])
                            spot_balances = []
                            for coin_data in coins:
                                wallet_balance = float(coin_data.get('walletBalance', 0))
                                available_balance = float(coin_data.get('availableToWithdraw', 0))
                                frozen = wallet_balance - available_balance
                                
                                if wallet_balance > 0:
                                    spot_balances.append({
                                        "token": coin_data.get('coin', ''),
                                        "total": str(wallet_balance),
                                        "frozen": str(frozen),
                                        "available": str(available_balance)
                                    })
                            
                            if spot_balances:
                                result.append({
                                    "wallet": "SPOT",
                                    "balance": spot_balances
                                })
            except Exception as e:
                logger.warning(f"Failed to get Bybit SPOT balance: {e}")
            
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