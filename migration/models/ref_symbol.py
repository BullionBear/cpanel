from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from . import Base


class RefSymbol(Base):
    """Reference: symbol (exchange-specific trading pair definition, core table)."""
    __tablename__ = "ref_symbol"
    __table_args__ = (
        UniqueConstraint("exchange_id", "product_id", "name", name="uq_ref_symbol_exchange_product_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    exchange_id = Column(Integer, ForeignKey("ref_exchange.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("ref_product.id"), nullable=False, index=True)
    base_id = Column(Integer, ForeignKey("ref_token.id"), nullable=False, index=True)
    quote_id = Column(Integer, ForeignKey("ref_token.id"), nullable=False, index=True)
    price_tick_size = Column(Numeric(36, 18), nullable=False)
    # 'trading' / 'halted' / 'delisted'
    status = Column(String, nullable=False)
    listing_timestamp = Column(DateTime(timezone=True), nullable=True)
    universal_id = Column(
        Integer, ForeignKey("ref_universal_symbol.id"), nullable=False, index=True
    )
    # Exchange-specific one-off fields not yet worth normalizing
    raw_meta = Column(JSONB, nullable=True)

    # Relationships
    exchange = relationship("RefExchange", backref="symbols")
    product = relationship("RefProduct", backref="symbols")
    base_token = relationship("RefToken", foreign_keys=[base_id], backref="base_symbols")
    quote_token = relationship("RefToken", foreign_keys=[quote_id], backref="quote_symbols")
    universal = relationship("RefUniversalSymbol", backref="symbols")
    derivative = relationship(
        "RefSymbolDerivative",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
        backref="symbol",
    )
    filters = relationship(
        "RefSymbolFilter",
        cascade="all, delete-orphan",
        passive_deletes=True,
        backref="symbol",
    )

    def __repr__(self):
        return f"<RefSymbol(name='{self.name}', exchange_id={self.exchange_id}, universal_id={self.universal_id})>"
