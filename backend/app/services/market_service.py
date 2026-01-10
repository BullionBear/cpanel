from typing import Dict, Any, List
from pybit.unified_trading import HTTP as BybitHTTP
from pybit.exceptions import FailedRequestError
import binance as bn
import logging
from app.services.api_service import EXCHANGES
from app.utils import trim_decimal_string

logger = logging.getLogger(__name__)


class MarketService:
    def __init__(self):
        pass

    def get_best_bid_ask(self, exchange: EXCHANGES, symbol: str) -> Dict[str, List[str]]:
        """
        Get the best bid and ask for a given symbol on a given exchange.

        Args:
            exchange: The exchange to get the best bid and ask from.
            symbol: The symbol to get the best bid and ask for.

        Returns:
            A dictionary with the best bid and ask.
            {
                "bid": [price, quantity],
                "ask": [price, quantity]
            }
        """
        match exchange:
            case 'BYBIT':
                return self.get_bybit_best_bid_ask(symbol)
            case 'BINANCE':
                return self.get_binance_best_bid_ask(symbol)
            case _:
                raise ValueError(f"Unsupported exchange: {exchange}")

    def get_bybit_best_bid_ask(self, symbol: str) -> Dict[str, List[str]]:
        """
        Get the best bid and ask for a given symbol on Bybit.
        
        Uses public endpoint (get_orderbook), no authentication required.
        
        Args:
            symbol: Symbol name (e.g., "BTCUSDT")
        
        Returns:
            Dictionary with best bid and ask:
            {
                "bid": [price, quantity],
                "ask": [price, quantity]
            }
        
        Raises:
            Exception: If there's an error fetching the data
        """
        try:
            # Use public endpoint - no authentication required
            client = BybitHTTP(testnet=False)
            
            # Get order book with limit 1 to get best bid/ask
            response = client.get_orderbook(
                category="spot",
                symbol=symbol,
                limit=1
            )
            
            if not response or response.get('retCode') != 0:
                ret_msg = response.get('retMsg', 'Unknown error') if response else 'Empty response'
                raise Exception(f"Failed to get Bybit order book: {ret_msg}")
            
            result = response.get('result', {})
            bids = result.get('b', [])  # bids array: [[price, quantity], ...]
            asks = result.get('a', [])  # asks array: [[price, quantity], ...]
            
            if not bids or not asks:
                raise Exception(f"No bid/ask data found for symbol {symbol}")
            
            # Best bid is the first element in bids (highest price)
            # Format: [price, quantity] where both are strings
            best_bid = bids[0]
            bid_price = trim_decimal_string(str(best_bid[0]))  # Price at index 0
            bid_quantity = trim_decimal_string(str(best_bid[1]))  # Quantity at index 1
            
            # Best ask is the first element in asks (lowest price)
            # Format: [price, quantity] where both are strings
            best_ask = asks[0]
            ask_price = trim_decimal_string(str(best_ask[0]))  # Price at index 0
            ask_quantity = trim_decimal_string(str(best_ask[1]))  # Quantity at index 1
            
            return {
                "bid": [bid_price, bid_quantity],
                "ask": [ask_price, ask_quantity]
            }
            
        except FailedRequestError as e:
            error_str = str(e)
            raise Exception(f"Bybit API error: {error_str}")
        except (ConnectionError, TimeoutError, OSError) as e:
            raise Exception(f"Network error while connecting to Bybit: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting Bybit best bid/ask: {e}", exc_info=True)
            raise Exception(f"Unexpected error getting Bybit best bid/ask: {str(e)}")

    def get_binance_best_bid_ask(self, symbol: str) -> Dict[str, List[str]]:
        """
        Get the best bid and ask for a given symbol on Binance.
        
        Uses public endpoint, no authentication required.
        
        Args:
            symbol: Symbol name (e.g., "BTCUSDT")
        
        Returns:
            Dictionary with best bid and ask:
            {
                "bid": [price, quantity],
                "ask": [price, quantity]
            }
        
        Raises:
            Exception: If there's an error fetching the data
        """
        try:
            # Use public endpoint - no authentication required
            client = bn.Client()
            
            # Get order book with limit 5 (minimum for best bid/ask)
            order_book = client.get_order_book(symbol=symbol, limit=5)
            
            if not order_book:
                raise Exception(f"Empty order book response for symbol {symbol}")
            
            bids = order_book.get('bids', [])
            asks = order_book.get('asks', [])
            
            if not bids or not asks:
                raise Exception(f"No bid/ask data found for symbol {symbol}")
            
            # Best bid is the first element (highest price)
            best_bid = bids[0]
            bid_price = trim_decimal_string(str(best_bid[0]))  # Price
            bid_quantity = trim_decimal_string(str(best_bid[1]))  # Quantity
            
            # Best ask is the first element (lowest price)
            best_ask = asks[0]
            ask_price = trim_decimal_string(str(best_ask[0]))  # Price
            ask_quantity = trim_decimal_string(str(best_ask[1]))  # Quantity
            
            return {
                "bid": [bid_price, bid_quantity],
                "ask": [ask_price, ask_quantity]
            }
            
        except bn.exceptions.BinanceAPIException as e:
            raise Exception(f"Binance API error: {str(e)}")
        except (ConnectionError, TimeoutError, OSError) as e:
            raise Exception(f"Network error while connecting to Binance: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting Binance best bid/ask: {e}", exc_info=True)
            raise Exception(f"Unexpected error getting Binance best bid/ask: {str(e)}")
