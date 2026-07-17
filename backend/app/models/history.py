"""History ingestion: trade/order/deposit/withdrawal history and sync cursors.

``trade_history`` and ``order_history`` are written by **upsert** keyed by
``(account, universal_symbol, native id)`` — venue ids are only guaranteed
unique per symbol on some exchanges (Binance), so the symbol is part of the
key. ``deposit_history`` / ``withdrawal_history`` are keyed by
``(account, native id)`` — fund movements are account-wide, not per symbol.
Re-fetching an overlapping window can never create duplicates, which is
what makes every sync job idempotent (ARCHITECTURE.md "History Ingestion").

All four tables are filled from **venue history endpoints**, not from
panel-orchestrated transfer rows.

``sync_cursor`` records ingestion progress per ``(account, data kind,
stream key)``. The stream key exists because venues shard their history
endpoints differently: Binance queries trades/orders per symbol (one stream
per active symbol), Bybit per account-wide category; deposit/withdrawal
streams are account-wide on every venue. The cursor value is opaque here —
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


class DepositHistory(Base):
    """On-chain deposits as reported by the venue's deposit-history endpoint.

    Sourced from the exchange, not from panel transfer orchestration — covers
    deposits that arrive outside the panel as well as those matched mid-transfer.
    """

    __tablename__ = "deposit_history"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "native_deposit_id",
            name="uq_deposit_history_account_native",
        ),
        Index("ix_deposit_history_account_created", "account_id", "created_at"),
        Index("ix_deposit_history_account_asset", "account_id", "asset"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)

    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    native_deposit_id: Mapped[str] = mapped_column(String(128), nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Num, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    network: Mapped[str | None] = mapped_column(String(32))
    tx_id: Mapped[str | None] = mapped_column(String(128))

    credited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WithdrawalHistory(Base):
    """On-chain withdrawals as reported by the venue's withdrawal-history endpoint.

    Sourced from the exchange — includes withdrawals submitted outside the panel.
    """

    __tablename__ = "withdrawal_history"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "native_withdrawal_id",
            name="uq_withdrawal_history_account_native",
        ),
        Index("ix_withdrawal_history_account_created", "account_id", "created_at"),
        Index("ix_withdrawal_history_account_asset", "account_id", "asset"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)

    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    native_withdrawal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Num, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    address: Mapped[str | None] = mapped_column(Text)
    network: Mapped[str | None] = mapped_column(String(32))
    tx_id: Mapped[str | None] = mapped_column(String(128))
    fee: Mapped[Decimal | None] = mapped_column(Num)
    fee_asset: Mapped[str | None] = mapped_column(String(32))

    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
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
    # trades | orders | deposits | withdrawals
    data_kind: Mapped[str] = mapped_column(String(16), nullable=False)
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
