from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from . import Base


class RefSymbolDerivative(Base):
    """Derivative-specific attributes for a symbol (1:0..1 with ref_symbol).

    Only non-spot symbols (UMARGIN / CMARGIN) have a row here.
    """
    __tablename__ = "ref_symbol_derivative"

    symbol_id = Column(
        Integer,
        ForeignKey("ref_symbol.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # linear: quote asset; inverse: base asset
    margin_id = Column(Integer, ForeignKey("ref_token.id"), nullable=False, index=True)
    # NULL for perpetuals (semantically valid NULL: "no expiry")
    expiry_timestamp = Column(DateTime(timezone=True), nullable=True)
    # 'cash' / 'physical'
    settlement_type = Column(String, nullable=True)

    margin_token = relationship("RefToken", foreign_keys=[margin_id], backref="margin_symbols")

    def __repr__(self):
        return f"<RefSymbolDerivative(symbol_id={self.symbol_id}, margin_id={self.margin_id})>"
