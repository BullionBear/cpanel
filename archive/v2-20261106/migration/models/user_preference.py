from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from . import Base


class UserPreference(Base):
    __tablename__ = "user_preference"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    layout = Column(String, nullable=False)
    version = Column(Integer, nullable=False)
    preference = Column(JSONB, nullable=False)

    # Relationship to User
    user = relationship("User", backref="preferences")

    def __repr__(self):
        return f"<UserPreference(user_id={self.user_id}, layout='{self.layout}', version={self.version})>"
