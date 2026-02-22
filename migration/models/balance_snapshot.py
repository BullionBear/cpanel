from sqlalchemy import Column, ForeignKey, Integer, Numeric, String

from . import Base


class BalanceSnapshot(Base):
    """Balance snapshot table: per-wallet, per-token snapshot with amount and ref price."""

    __tablename__ = "balance_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sid = Column(String(64), nullable=False, index=True)  # snapshot id
    wallet_id = Column(Integer, ForeignKey("wallet.id", ondelete="CASCADE"), nullable=False, index=True)
    token = Column(String(32), nullable=False, index=True)
    amount = Column(Numeric(36, 18), nullable=False)
    ref_price = Column(Numeric(36, 18), nullable=False)

    def __repr__(self):
        return f"<BalanceSnapshot(id={self.id}, sid='{self.sid}', wallet_id={self.wallet_id}, token='{self.token}', amount={self.amount}, ref_price={self.ref_price})>"
