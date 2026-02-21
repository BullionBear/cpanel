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
    from ref_exchange import RefExchange  # noqa: E402
    from ref_product import RefProduct  # noqa: E402
    from ref_token import RefToken  # noqa: E402
    from ref_symbol import RefSymbol  # noqa: E402
    from user_preference import UserPreference  # noqa: E402
    from ref_wallet import RefWallet  # noqa: E402
    from wallet import Wallet  # noqa: E402
    from account import Account  # noqa: E402
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
        __tablename__ = "api_archived_20260221"

        id = Column(Integer, primary_key=True, autoincrement=True)
        user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
        exchange = Column(String, nullable=False, index=True)
        uid = Column(String, nullable=False, index=True)  # Exchange account UID
        name = Column(String, nullable=False)
        api_type = Column(String, nullable=False)  # e.g., 'HMAC', 'RSA', etc.
        api_key = Column(String, nullable=False)
        api_secret = Column(String, nullable=False)
        passphrase = Column(String, nullable=True)
        master_uid = Column(String, nullable=True)  # Master account UID (for sub-accounts)
        created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
        updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    class RefExchange(Base):  # type: ignore
        __tablename__ = "ref_exchange"

        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, nullable=False)
        slug = Column(String, unique=True, nullable=False, index=True)

    class RefProduct(Base):  # type: ignore
        __tablename__ = "ref_product"

        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, nullable=False)
        slug = Column(String, unique=True, nullable=False, index=True)

    class RefToken(Base):  # type: ignore
        __tablename__ = "ref_token"

        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, nullable=False)
        slug = Column(String, unique=True, nullable=False, index=True)

    class RefSymbol(Base):  # type: ignore
        __tablename__ = "ref_symbol"

        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, nullable=False)
        exchange_id = Column(Integer, ForeignKey("ref_exchange.id"), nullable=False, index=True)
        product_id = Column(Integer, ForeignKey("ref_product.id"), nullable=False, index=True)
        base_id = Column(Integer, ForeignKey("ref_token.id"), nullable=False, index=True)
        quote_id = Column(Integer, ForeignKey("ref_token.id"), nullable=False, index=True)
        price_precision = Column(Integer, nullable=False)
        size_precision = Column(Integer, nullable=False)
        universal_ticker = Column(String, nullable=False, unique=True, index=True)

        # Relationships
        from sqlalchemy.orm import relationship
        exchange = relationship("RefExchange", backref="symbols")
        product = relationship("RefProduct", backref="symbols")
        base_token = relationship("RefToken", foreign_keys=[base_id], backref="base_symbols")
        quote_token = relationship("RefToken", foreign_keys=[quote_id], backref="quote_symbols")

    class UserPreference(Base):  # type: ignore
        __tablename__ = "user_preference"

        id = Column(Integer, primary_key=True, autoincrement=True)
        user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
        layout = Column(String, nullable=False)
        version = Column(Integer, nullable=False)
        from sqlalchemy.dialects.postgresql import JSONB
        preference = Column(JSONB, nullable=False)

    class RefWallet(Base):  # type: ignore
        __tablename__ = "ref_wallet"

        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, nullable=False)
        slug = Column(String, unique=True, nullable=False, index=True)

    class Wallet(Base):  # type: ignore
        __tablename__ = "wallet_archived_20240221"

        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, nullable=False)
        wallet_type_id = Column(Integer, ForeignKey("ref_wallet.id", ondelete="CASCADE"), nullable=False, index=True)
        api_id = Column(Integer, ForeignKey("api_archived_20260221.id", ondelete="CASCADE"), nullable=True, index=True)
        is_active = Column(Boolean, default=True, nullable=False)

    class Account(Base):  # type: ignore
        __tablename__ = "account"

        id = Column(Integer, primary_key=True, autoincrement=True)
        uid = Column(String, nullable=False, index=True)
        exch_id = Column(Integer, ForeignKey("ref_exchange.id", ondelete="CASCADE"), nullable=False, index=True)
        name = Column(String, nullable=False)
        parent_uid = Column(String, nullable=True, index=True)

__all__ = ["User", "Access", "API", "RefExchange", "RefProduct", "RefToken", "RefSymbol", "UserPreference", "RefWallet", "Wallet", "Account"]

