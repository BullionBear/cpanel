"""Long-lived panel API tokens issued after Discord OAuth.

Tokens are JWT strings with an ``api_`` prefix. The row is the revocation
source of truth: deleting it immediately invalidates the key even though the
JWT may not have expired yet.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

BigIntPk = BigInteger().with_variant(Integer(), "sqlite")


class PanelAccessToken(Base):
    __tablename__ = "panel_access_token"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_name: Mapped[str | None] = mapped_column(String(128))
    access_token: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
