from sqlalchemy import Boolean, Column, ForeignKey, Integer, String

from . import Base


class WalletAccount(Base):
    """Wallet table: exchange wallet (e.g. SPOT per exchange)."""

    __tablename__ = "wallet"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    exchange_id = Column(Integer, ForeignKey("ref_exchange.id", ondelete="CASCADE"), nullable=False, index=True)
    wallet_type_id = Column(Integer, ForeignKey("ref_wallet.id", ondelete="CASCADE"), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)

    def __repr__(self):
        return f"<WalletAccount(name='{self.name}', exchange_id={self.exchange_id}, wallet_type_id={self.wallet_type_id})>"
