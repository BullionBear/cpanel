from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from . import Base


class APIArchived(Base):
    """Legacy API keys (table renamed to api_archived_20260221)."""
    __tablename__ = "api_archived_20260221"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    exchange = Column(String, nullable=False, index=True)
    uid = Column(String, nullable=False, index=True)  # Exchange account UID
    name = Column(String, nullable=False)
    api_type = Column(String, nullable=False)  # e.g., 'HMAC', 'RSA', etc.
    api_key = Column(String, nullable=False)
    api_secret = Column(String, nullable=False)
    passphrase = Column(String, nullable=True)  # Some exchanges don't use passphrase
    master_uid = Column(String, nullable=True)  # Master account UID (for sub-accounts)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    user = relationship("User", backref="apis")

    def __repr__(self):
        return f"<APIArchived(id={self.id}, user_id={self.user_id}, exchange='{self.exchange}', uid='{self.uid}', name='{self.name}', master_uid={self.master_uid})>"

