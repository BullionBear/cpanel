"""Orders placed through (or discovered by) the panel.

Unlike snapshots, order rows are **mutable**: one row per order, advanced in
place through the normalized lifecycle
(``submitting → open → partially_filled → filled / cancelled / rejected``).

Idempotency hinges on ``client_order_id``: the panel generates it, writes the
row as ``submitting`` *before* calling the venue, and sends it as the exchange
client id. If the placement response is lost, the order is recovered by that id
instead of being re-sent. Orders that originate outside the panel are found by
the open-order poller and stored with ``external = True`` (and usually no
client order id).
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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


class Order(Base):
    __tablename__ = "trade_order"
    __table_args__ = (
        # A panel client id is unique within an account (NULLs — external
        # orders — are allowed to repeat under SQL unique semantics).
        UniqueConstraint("account_id", "client_order_id", name="uq_order_account_client"),
        Index("ix_trade_order_account_status", "account_id", "status"),
        Index("ix_trade_order_account_native", "account_id", "native_order_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)

    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    universal_symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Panel-generated id (idempotency key); null for externally-originated orders.
    client_order_id: Mapped[str | None] = mapped_column(String(64))
    # Venue-assigned id; null until placement succeeds.
    native_order_id: Mapped[str | None] = mapped_column(String(64))

    side: Mapped[str] = mapped_column(String(8), nullable=False)   # BUY | SELL
    type: Mapped[str] = mapped_column(String(8), nullable=False)   # LIMIT | MARKET
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    qty: Mapped[Decimal] = mapped_column(Num, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Num)
    filled_qty: Mapped[Decimal] = mapped_column(
        Num, nullable=False, default=Decimal("0"), server_default="0"
    )

    external: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    reject_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
