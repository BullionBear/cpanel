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


class Wallet(Base):
    """Wallet table (links to account and category/ref_wallet)."""

    __tablename__ = "wallet"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category_id = Column(Integer, ForeignKey("ref_wallet.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    acct_id = Column(Integer, ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    def __repr__(self):
        return f"<Wallet(id={self.id}, category_id={self.category_id}, user_id={self.user_id}, acct_id={self.acct_id}, name='{self.name}', is_active={self.is_active})>"
