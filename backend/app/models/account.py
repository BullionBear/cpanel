"""Exchange accounts and their API credentials.

One `account` row per exchange account (main or subaccount, linked by
parent_id). Credentials hang off accounts; the secret is stored as an
envelope-encrypted blob (see app.core.crypto) and is never returned by any
API after creation.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.audit_log import BigIntPk, JsonDict
from app.models.base import Base


class Account(Base):
    __tablename__ = "account"
    __table_args__ = (UniqueConstraint("exchange", "label", name="uq_account_exchange_label"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)

    exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    account_type: Mapped[str] = mapped_column(String(8), nullable=False)  # main | sub
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("account.id"))
    # Venue-native identifier: Bybit uid, Binance subaccount email, …
    native_uid: Mapped[str | None] = mapped_column(String(128))

    # active | removed (no longer reported by the venue; kept for history)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    parent: Mapped["Account | None"] = relationship(
        remote_side=[id], back_populates="children"
    )
    children: Mapped[list["Account"]] = relationship(back_populates="parent")
    credentials: Mapped[list["ApiCredential"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class ApiCredential(Base):
    __tablename__ = "api_credential"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True
    )

    scheme: Mapped[str] = mapped_column(String(16), nullable=False)  # HMAC | ED25519 | RSA
    api_key: Mapped[str] = mapped_column(String(256), nullable=False)
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(String(128))

    # Normalized permission info captured at verification time
    permissions: Mapped[dict | None] = mapped_column(JsonDict)
    read_only: Mapped[bool | None] = mapped_column()
    can_withdraw: Mapped[bool | None] = mapped_column()

    # Operator-defined free-form tags for organizing keys (list of strings)
    tags: Mapped[list | None] = mapped_column(JsonDict)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    account: Mapped[Account] = relationship(back_populates="credentials")
