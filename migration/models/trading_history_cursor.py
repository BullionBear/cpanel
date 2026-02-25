from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer
from sqlalchemy.sql import func

from . import Base


class TradingHistoryCursor(Base):
    """Cursor for trading history sync per account and product."""

    __tablename__ = "trading_history_cursor"

    id = Column(Integer, primary_key=True, autoincrement=True)
    acct_id = Column(Integer, ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("ref_product.id", ondelete="CASCADE"), nullable=False, index=True)
    cursor_start = Column(BigInteger, nullable=True)
    cursor_end = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<TradingHistoryCursor(id={self.id}, acct_id={self.acct_id}, product_id={self.product_id}, cursor_start={self.cursor_start}, cursor_end={self.cursor_end})>"
