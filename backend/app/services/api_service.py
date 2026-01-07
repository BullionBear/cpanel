from typing import Optional
from typing import Literal
import binance as bn
from pybit.unified_trading import HTTP as BybitHTTP
from pybit.exceptions import FailedRequestError
import logging
from app.models import API
from app.repositories.api_repository import APIRepository

logger = logging.getLogger(__name__)

EXCHANGES = Literal['BINANCE', 'BYBIT']
APIS = Literal['SPOT', 'MARGIN', 'FUTURES']


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