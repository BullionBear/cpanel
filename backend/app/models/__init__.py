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

__all__ = ["User", "Access", "API"]

