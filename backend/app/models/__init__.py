"""Database models."""
import sys
from pathlib import Path

# Import models from migration component
migration_models_path = Path(__file__).resolve().parent.parent.parent.parent / "migration" / "models"
sys.path.insert(0, str(migration_models_path))

try:
    from user import User  # noqa: E402
    from access import Access  # noqa: E402
    from api import API  # noqa: E402
    from exchange import Exchange  # noqa: E402
    from product import Product  # noqa: E402
    from token import Token  # noqa: E402
    from symbol import Symbol  # noqa: E402
except ImportError:
    # Fallback: define User model here if migration models not available
    from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
    from sqlalchemy.sql import func
    from app.db.session import Base

    class User(Base):  # type: ignore
        __tablename__ = "users"

        id = Column(Integer, primary_key=True, autoincrement=True)
        username = Column(String, unique=True, nullable=False, index=True)
        hash_password = Column(String, nullable=False)
        email = Column(String, unique=True, nullable=False, index=True)
        created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
        last_login = Column(DateTime(timezone=True), nullable=True)
        is_active = Column(Boolean, default=True, nullable=False)

    class Access(Base):  # type: ignore
        __tablename__ = "access"

        id = Column(Integer, primary_key=True, autoincrement=True)
        user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
        access_token = Column(String, unique=True, nullable=False, index=True)
        created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
        expired_at = Column(DateTime(timezone=True), nullable=False, index=True)

    class API(Base):  # type: ignore
        __tablename__ = "api"

        id = Column(Integer, primary_key=True, autoincrement=True)
        user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
        exchange = Column(String, nullable=False, index=True)
        name = Column(String, nullable=False)
        api_key = Column(String, nullable=False)
        api_secret = Column(String, nullable=False)
        passphrase = Column(String, nullable=True)
        master_is = Column(Integer, ForeignKey("api.id", ondelete="SET NULL"), nullable=True, index=True)
        created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
        updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    class Exchange(Base):  # type: ignore
        __tablename__ = "exchange"

        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, nullable=False)
        slug = Column(String, unique=True, nullable=False, index=True)

    class Product(Base):  # type: ignore
        __tablename__ = "product"

        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, nullable=False)
        slug = Column(String, unique=True, nullable=False, index=True)

    class Token(Base):  # type: ignore
        __tablename__ = "token"

        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, nullable=False)
        slug = Column(String, unique=True, nullable=False, index=True)

    class Symbol(Base):  # type: ignore
        __tablename__ = "symbol"

        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, nullable=False)
        exchange_id = Column(Integer, ForeignKey("exchange.id"), nullable=False, index=True)
        product_id = Column(Integer, ForeignKey("product.id"), nullable=False, index=True)
        base_id = Column(Integer, ForeignKey("token.id"), nullable=False, index=True)
        quote_id = Column(Integer, ForeignKey("token.id"), nullable=False, index=True)
        price_precision = Column(Integer, nullable=False)
        size_precision = Column(Integer, nullable=False)
        universal_ticker = Column(String, nullable=False, unique=True, index=True)

        # Relationships
        from sqlalchemy.orm import relationship
        exchange = relationship("Exchange", backref="symbols")
        product = relationship("Product", backref="symbols")
        base_token = relationship("Token", foreign_keys=[base_id], backref="base_symbols")
        quote_token = relationship("Token", foreign_keys=[quote_id], backref="quote_symbols")

__all__ = ["User", "Access", "API", "Exchange", "Product", "Token", "Symbol"]

