from sqlalchemy import Column, Integer, String

from . import Base


class Product(Base):
    __tablename__ = "product"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)

    def __repr__(self):
        return f"<Product(name='{self.name}', slug='{self.slug}')>"
