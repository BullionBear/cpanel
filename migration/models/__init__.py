from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

# Import all models here so Alembic can detect them
from .user import User  # noqa: E402
from .access import Access  # noqa: E402
from .api import API, APIArchived  # noqa: E402
from .ref_exchange import RefExchange  # noqa: E402
from .ref_product import RefProduct  # noqa: E402
from .ref_token import RefToken  # noqa: E402
from .ref_universal_symbol import RefUniversalSymbol  # noqa: E402
from .ref_symbol import RefSymbol  # noqa: E402
from .ref_symbol_derivative import RefSymbolDerivative  # noqa: E402
from .ref_symbol_filter import RefSymbolFilter  # noqa: E402
from .user_preference import UserPreference  # noqa: E402
from .ref_wallet import RefWallet  # noqa: E402
from .wallet import Wallet, WalletArchived  # noqa: E402
from .account import Account  # noqa: E402
from .balance_snapshot import BalanceSnapshot  # noqa: E402
from .trading_history import TradingHistory  # noqa: E402
from .trading_history_cursor import TradingHistoryCursor  # noqa: E402

__all__ = ["Base", "User", "Access", "API", "APIArchived", "RefExchange", "RefProduct", "RefToken", "RefUniversalSymbol", "RefSymbol", "RefSymbolDerivative", "RefSymbolFilter", "UserPreference", "RefWallet", "Wallet", "WalletArchived", "Account", "BalanceSnapshot", "TradingHistory", "TradingHistoryCursor"]

