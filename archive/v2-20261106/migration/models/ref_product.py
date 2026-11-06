from sqlalchemy import Column, Integer, String

from . import Base


class RefProduct(Base):
    """Reference: product (e.g. SPOT, FUTURES)."""
    __tablename__ = "ref_product"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)

    def __repr__(self):
        return f"<RefProduct(name='{self.name}', slug='{self.slug}')>"
