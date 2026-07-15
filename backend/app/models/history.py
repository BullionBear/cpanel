"""History ingestion: trade/order history and per-stream sync cursors.

``trade_history`` and ``order_history`` are written by **upsert** keyed by
``(account, universal_symbol, native id)`` — venue ids are only guaranteed
unique per symbol on some exchanges (Binance), so the symbol is part of the
key. Re-fetching an overlapping window can never create duplicates, which is
what makes every sync job idempotent (ARCHITECTURE.md "History Ingestion").

``sync_cursor`` records ingestion progress per ``(account, data kind,
stream key)``. The stream key exists because venues shard their history
endpoints differently: Binance queries per symbol (one stream per active
symbol), Bybit per account-wide category. The cursor value is opaque here —
the connector defines its meaning (a timestamp, a trade id, or a native
pagination token). A stream with no cursor row yet is *new*: the sync worker
seeds it three days in the past so a freshly connected account backfills its
recent history before incremental sync takes over.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.audit_log import BigIntPk
from app.models.base import Base, PreciseDecimal

Num = PreciseDecimal()


class TradeHistory(Base):
    __tablename__ = "trade_history"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "universal_symbol", "native_trade_id",
            name="uq_trade_history_account_symbol_native",
        ),
        Index("ix_trade_history_account_traded", "account_id", "traded_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)

    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    universal_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    native_trade_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Venue order the fill belongs to; joins panel orders via their native id.
    native_order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    side: Mapped[str] = mapped_column(String(8), nullable=False)  # BUY | SELL
    price: Mapped[Decimal] = mapped_column(Num, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Num, nullable=False)
    fee: Mapped[Decimal] = mapped_column(Num, nullable=False, default=Decimal("0"))
    fee_asset: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    traded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OrderHistory(Base):
    """Orders as reported by the venue's history endpoints.

    Deliberately separate from ``trade_order`` (the panel's own lifecycle
    rows): history rows cover *everything* the venue reports, including
    orders placed outside the panel, and are advanced by upsert whenever a
    re-fetch shows a newer state.
    """

    __tablename__ = "order_history"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "universal_symbol", "native_order_id",
            name="uq_order_history_account_symbol_native",
        ),
        Index("ix_order_history_account_placed", "account_id", "placed_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)

    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    universal_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    native_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_order_id: Mapped[str | None] = mapped_column(String(64))

    side: Mapped[str] = mapped_column(String(8), nullable=False)   # BUY | SELL
    type: Mapped[str] = mapped_column(String(8), nullable=False)   # LIMIT | MARKET
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    qty: Mapped[Decimal] = mapped_column(Num, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Num)
    filled_qty: Mapped[Decimal] = mapped_column(Num, nullable=False, default=Decimal("0"))

    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SyncCursor(Base):
    __tablename__ = "sync_cursor"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "data_kind", "stream_key",
            name="uq_sync_cursor_account_kind_stream",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)

    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    data_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # trades | orders
    stream_key: Mapped[str] = mapped_column(String(80), nullable=False)

    # Opaque; the connector defines its meaning. Advanced only after the
    # fetched rows are stored, so a crash between fetch and store re-fetches
    # the same page next run.
    cursor: Mapped[str | None] = mapped_column(Text)

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
