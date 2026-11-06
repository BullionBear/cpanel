"""Universal instrument registry: one row per tradable instrument, ever.

Rows are never deleted — instruments that disappear from a venue are marked
delisted so history and orders can keep referencing them. Numeric columns
use Numeric to preserve exact tick/step values.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, PreciseDecimal

Num = PreciseDecimal()


class UniversalTicker(Base):
    __tablename__ = "universal_ticker"
    __table_args__ = (
        UniqueConstraint("exchange", "market_type", "native_symbol", name="uq_ticker_native"),
    )

    universal_symbol: Mapped[str] = mapped_column(String(64), primary_key=True)

    exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    native_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    base_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(32), nullable=False)

    # Normalized trading filters
    price_tick: Mapped[Decimal] = mapped_column(Num, nullable=False)
    qty_step: Mapped[Decimal] = mapped_column(Num, nullable=False)
    min_qty: Mapped[Decimal] = mapped_column(Num, nullable=False)
    max_qty: Mapped[Decimal | None] = mapped_column(Num)
    min_notional: Mapped[Decimal | None] = mapped_column(Num)

    # Contract metadata (derivatives only)
    contract_size: Mapped[Decimal | None] = mapped_column(Num)
    settle_asset: Mapped[str | None] = mapped_column(String(32))
    expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # active | halted | delisted
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)

    # Reference price (operational data, not a market data product)
    last_price: Mapped[Decimal | None] = mapped_column(Num)
    price_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
