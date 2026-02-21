from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from . import Base


class RefSymbol(Base):
    """Reference: symbol (trading pair definition)."""
    __tablename__ = "ref_symbol"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    exchange_id = Column(Integer, ForeignKey("ref_exchange.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("ref_product.id"), nullable=False, index=True)
    base_id = Column(Integer, ForeignKey("ref_token.id"), nullable=False, index=True)
    quote_id = Column(Integer, ForeignKey("ref_token.id"), nullable=False, index=True)
    price_precision = Column(Integer, nullable=False)
    size_precision = Column(Integer, nullable=False)
    universal_ticker = Column(String, nullable=False, unique=True, index=True)

    # Relationships
    exchange = relationship("RefExchange", backref="symbols")
    product = relationship("RefProduct", backref="symbols")
    base_token = relationship("RefToken", foreign_keys=[base_id], backref="base_symbols")
    quote_token = relationship("RefToken", foreign_keys=[quote_id], backref="quote_symbols")

    def __repr__(self):
        return f"<RefSymbol(name='{self.name}', exchange_id={self.exchange_id}, universal_ticker='{self.universal_ticker}')>"
