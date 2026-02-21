from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

# Import all models here so Alembic can detect them
from .user import User  # noqa: E402
from .access import Access  # noqa: E402
from .api import API  # noqa: E402
from .ref_exchange import RefExchange  # noqa: E402
from .ref_product import RefProduct  # noqa: E402
from .ref_token import RefToken  # noqa: E402
from .ref_symbol import RefSymbol  # noqa: E402
from .user_preference import UserPreference  # noqa: E402
from .ref_wallet import RefWallet  # noqa: E402
from .wallet_account import WalletAccount  # noqa: E402
from .account import Account  # noqa: E402

__all__ = ["Base", "User", "Access", "API", "RefExchange", "RefProduct", "RefToken", "RefSymbol", "UserPreference", "RefWallet", "WalletAccount", "Account"]

