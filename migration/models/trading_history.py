from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String

from . import Base


class TradingHistory(Base):
    """Trading history: per-exchange trade records."""

    __tablename__ = "trading_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    exch_id = Column(Integer, ForeignKey("ref_exchange.id", ondelete="CASCADE"), nullable=False, index=True)
    exch_tid = Column(Integer, nullable=False, index=True)  # trading uid with respect to the exchange
    acct_id = Column(Integer, ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True)
    order_id = Column(Integer, nullable=False, index=True)  # order id of the trade in exchange
    symbol = Column(String(32), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("ref_product.id", ondelete="CASCADE"), nullable=False, index=True)
    side = Column(String(8), nullable=False)  # 'Buy' or 'Sell'
    exec_px = Column(Numeric(36, 18), nullable=False)  # execution price
    exec_qty = Column(Numeric(36, 18), nullable=False)  # execution quantity
    exec_fee = Column(Numeric(36, 18), nullable=False)
    fee_currency = Column(String(16), nullable=False)
    is_maker = Column(Boolean, nullable=False)
    exec_time = Column(DateTime(timezone=True), nullable=False, index=True)

    def __repr__(self):
        return f"<TradingHistory(id={self.id}, exch_id={self.exch_id}, exch_tid={self.exch_tid}, symbol='{self.symbol}', side='{self.side}', exec_time={self.exec_time})>"
