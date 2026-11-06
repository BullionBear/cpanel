from sqlalchemy import Column, ForeignKey, Integer, Numeric, String

from . import Base


class RefSymbolFilter(Base):
    """Conditionally existing numeric attributes of a symbol (long format).

    The same symbol may carry the same filter_type in multiple units
    simultaneously (e.g. QTY_STEP in both 'base' and 'quote').
    """
    __tablename__ = "ref_symbol_filter"

    symbol_id = Column(
        Integer,
        ForeignKey("ref_symbol.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # 'QTY_STEP' / 'MIN_NOTIONAL' / 'MAX_QTY' / 'CONTRACT_SIZE' ...
    filter_type = Column(String, primary_key=True)
    # 'base' / 'quote' / 'contract'
    unit_type = Column(String, primary_key=True)
    step_size = Column(Numeric(36, 18), nullable=True)
    min_value = Column(Numeric(36, 18), nullable=True)
    max_value = Column(Numeric(36, 18), nullable=True)

    def __repr__(self):
        return (
            f"<RefSymbolFilter(symbol_id={self.symbol_id}, "
            f"filter_type='{self.filter_type}', unit_type='{self.unit_type}')>"
        )
