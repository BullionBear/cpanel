from sqlalchemy import Column, Date, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from . import Base


class RefUniversalSymbol(Base):
    """Reference: cross-exchange canonical instrument.

    One row per canonical instrument (e.g. BTC/USDT.SPOT, BTC/USDT.PERP);
    multiple ref_symbol rows (one per exchange) can point at the same row.
    """
    __tablename__ = "ref_universal_symbol"

    id = Column(Integer, primary_key=True, autoincrement=True)
    universal_ticker = Column(String, nullable=False, unique=True, index=True)
    base_id = Column(Integer, ForeignKey("ref_token.id"), nullable=False, index=True)
    quote_id = Column(Integer, ForeignKey("ref_token.id"), nullable=False, index=True)
    # 'spot' / 'linear_perp' / 'inverse_perp' / 'future'
    instrument_type = Column(String, nullable=False)
    expiry_date = Column(Date, nullable=True)

    base_token = relationship("RefToken", foreign_keys=[base_id], backref="universal_base_symbols")
    quote_token = relationship("RefToken", foreign_keys=[quote_id], backref="universal_quote_symbols")

    def __repr__(self):
        return f"<RefUniversalSymbol(id={self.id}, universal_ticker='{self.universal_ticker}')>"
