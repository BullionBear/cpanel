"""Database models."""
import sys
from pathlib import Path

# Import models from migration component
migration_models_path = Path(__file__).resolve().parent.parent.parent.parent / "migration" / "models"
sys.path.insert(0, str(migration_models_path))

try:
    from user import User  # noqa: E402
except ImportError:
    # Fallback: define User model here if migration models not available
    from sqlalchemy import Boolean, Column, DateTime, Integer, String
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

__all__ = ["User"]

