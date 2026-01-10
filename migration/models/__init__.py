from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

# Import all models here so Alembic can detect them
from .user import User  # noqa: E402
from .access import Access  # noqa: E402
from .api import API  # noqa: E402
from .exchange import Exchange  # noqa: E402
from .product import Product  # noqa: E402
from .token import Token  # noqa: E402
from .symbol import Symbol  # noqa: E402
from .user_preference import UserPreference  # noqa: E402

__all__ = ["Base", "User", "Access", "API", "Exchange", "Product", "Token", "Symbol", "UserPreference"]

