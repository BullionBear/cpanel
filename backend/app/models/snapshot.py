"""Append-only asset snapshots: balances and positions.

Snapshots are never updated or deleted — each capture appends a fresh set of
rows. Every row from one capture pass of one account shares a ``batch_id`` and
``taken_at`` so a cross-account portfolio view compares like with like (see
ARCHITECTURE.md "Asset Snapshots").

Two questions are answered from the same history:
- "what do we hold now" — the rows of the latest batch per account, and
- "what did the book look like last Tuesday" — the batch nearest that time.

Valuation into a common quote asset uses reference prices captured close to
the snapshot time; ``value_quote`` is that value and ``quote_asset`` names its
unit (best effort — ``None`` when no reference price was available).
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.audit_log import BigIntPk
from app.models.base import Base, PreciseDecimal

Num = PreciseDecimal()


class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshot"
    __table_args__ = (
        Index("ix_balance_snapshot_account_taken", "account_id", "taken_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)

    batch_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    wallet: Mapped[str] = mapped_column(String(16), nullable=False)  # SPOT | MARGIN | …
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    free: Mapped[Decimal] = mapped_column(Num, nullable=False)
    locked: Mapped[Decimal] = mapped_column(Num, nullable=False)
    total: Mapped[Decimal] = mapped_column(Num, nullable=False)

    # Valuation in a common quote asset (best effort; null when unpriced)
    value_quote: Mapped[Decimal | None] = mapped_column(Num)
    quote_asset: Mapped[str | None] = mapped_column(String(16))

    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class PositionSnapshot(Base):
    __tablename__ = "position_snapshot"
    __table_args__ = (
        Index("ix_position_snapshot_account_taken", "account_id", "taken_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)

    batch_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    universal_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # long | short
    size: Mapped[Decimal] = mapped_column(Num, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Num, nullable=False)
    mark_price: Mapped[Decimal | None] = mapped_column(Num)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Num)
    margin: Mapped[Decimal | None] = mapped_column(Num)

    # Notional value in a common quote asset (size * mark price; best effort)
    value_quote: Mapped[Decimal | None] = mapped_column(Num)
    quote_asset: Mapped[str | None] = mapped_column(String(16))

    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
