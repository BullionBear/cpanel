from sqlalchemy import Column, ForeignKey, Integer, String

from . import Base


class Account(Base):
    """Account table: exchange account (uid per exchange)."""

    __tablename__ = "account"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(String, nullable=False, index=True)
    exch_id = Column(Integer, ForeignKey("ref_exchange.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    parent_uid = Column(String, nullable=True, index=True)

    def __repr__(self):
        return f"<Account(id={self.id}, uid='{self.uid}', exch_id={self.exch_id}, user_id={self.user_id}, name='{self.name}', parent_uid={self.parent_uid})>"
