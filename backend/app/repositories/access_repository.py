"""Access repository for database operations."""
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.orm import Session

from app.models import Access


class AccessRepository:
    """Repository for Access model operations."""

    def __init__(self, db: Session):
        """Initialize repository with database session."""
        self.db = db

    def create(self, user_id: int, access_token: str, expired_at: datetime) -> Access:
        """Create a new access token record."""
        access = Access(
            user_id=user_id,
            access_token=access_token,
            expired_at=expired_at,
        )
        self.db.add(access)
        self.db.commit()
        self.db.refresh(access)
        return access

    def get_by_token(self, access_token: str) -> Optional[Access]:
        """Get access token by token string."""
        return self.db.query(Access).filter(Access.access_token == access_token).first()

    def get_valid_token(self, access_token: str) -> Optional[Access]:
        """Get access token if it's valid (not expired)."""
        access = self.get_by_token(access_token)
        if access and access.expired_at > datetime.now(timezone.utc):
            return access
        return None

    def delete_by_token(self, access_token: str) -> bool:
        """Delete access token by token string."""
        access = self.get_by_token(access_token)
        if access:
            self.db.delete(access)
            self.db.commit()
            return True
        return False

    def delete_expired_tokens(self, user_id: Optional[int] = None) -> int:
        """Delete expired tokens. Optionally filter by user_id."""
        query = self.db.query(Access).filter(Access.expired_at < datetime.now(timezone.utc))
        if user_id:
            query = query.filter(Access.user_id == user_id)
        count = query.count()
        query.delete(synchronize_session=False)
        self.db.commit()
        return count

    def get_api_token_by_user(self, user_id: int) -> Optional[Access]:
        """Get the API token (api_ prefix) for a user if it exists and is valid."""
        now = datetime.now(timezone.utc)
        return (
            self.db.query(Access)
            .filter(Access.user_id == user_id)
            .filter(Access.access_token.like("api_%"))
            .filter(Access.expired_at > now)
            .first()
        )

    def delete_api_tokens_by_user(self, user_id: int) -> int:
        """Delete all API tokens (api_ prefix) for a user."""
        count = (
            self.db.query(Access)
            .filter(Access.user_id == user_id)
            .filter(Access.access_token.like("api_%"))
            .count()
        )
        (
            self.db.query(Access)
            .filter(Access.user_id == user_id)
            .filter(Access.access_token.like("api_%"))
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return count

