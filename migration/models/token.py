from sqlalchemy import Column, Integer, String

from . import Base


class Token(Base):
    __tablename__ = "token"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)

    def __repr__(self):
        return f"<Token(name='{self.name}', slug='{self.slug}')>"
