from sqlalchemy import Column, Integer, String

from . import Base


class RefWallet(Base):
    """Reference: wallet type (e.g. SPOT, UNIFIED)."""
    __tablename__ = "ref_wallet"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)

    def __repr__(self):
        return f"<RefWallet(name='{self.name}', slug='{self.slug}')>"
