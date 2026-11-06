from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from . import Base


class Access(Base):
    __tablename__ = "access"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    access_token = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expired_at = Column(DateTime(timezone=True), nullable=False, index=True)

    # Relationship to User
    user = relationship("User", backref="access_tokens")

    def __repr__(self):
        return f"<Access(user_id={self.user_id}, access_token='{self.access_token[:20]}...', expired_at={self.expired_at})>"

