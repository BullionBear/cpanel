from sqlalchemy import Boolean, Column, ForeignKey, Integer, String

from . import Base


class WalletArchived(Base):
    """Legacy wallet table (renamed to wallet_archived_20240221)."""

    __tablename__ = "wallet_archived_20240221"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    wallet_type_id = Column(Integer, ForeignKey("ref_wallet.id", ondelete="CASCADE"), nullable=False, index=True)
    api_id = Column(Integer, ForeignKey("api_archived_20260221.id", ondelete="CASCADE"), nullable=True, index=True)
    is_active = Column(Boolean, default=True, nullable=False)

    def __repr__(self):
        return f"<WalletArchived(name='{self.name}', api_id={self.api_id}, wallet_type_id={self.wallet_type_id})>"
