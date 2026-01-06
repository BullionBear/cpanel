"""User repository for database operations."""
from typing import Optional
from sqlalchemy.orm import Session

from app.models import User


class UserRepository:
    """Repository for User model operations."""

    def __init__(self, db: Session):
        """Initialize repository with database session."""
        self.db = db

    def get_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        return self.db.query(User).filter(User.id == user_id).first()

    def get_by_username(self, username: str) -> Optional[User]:
        """Get user by username."""
        return self.db.query(User).filter(User.username == username).first()

    def get_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        return self.db.query(User).filter(User.email == email).first()

    def create(self, username: str, email: str, hashed_password: str, is_active: bool = False) -> User:
        """Create a new user."""
        user = User(
            username=username,
            email=email,
            hash_password=hashed_password,
            is_active=is_active,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def update_last_login(self, user_id: int) -> Optional[User]:
        """Update user's last login timestamp."""
        user = self.get_by_id(user_id)
        if user:
            from datetime import datetime, timezone
            user.last_login = datetime.now(timezone.utc)
            self.db.commit()
            self.db.refresh(user)
        return user

    def update(self, user: User) -> User:
        """Update user."""
        self.db.commit()
        self.db.refresh(user)
        return user

    def delete(self, user_id: int) -> bool:
        """Delete user by ID."""
        user = self.get_by_id(user_id)
        if user:
            self.db.delete(user)
            self.db.commit()
            return True
        return False

    def list_all(self, skip: int = 0, limit: int = 100) -> list[User]:
        """List all users with pagination."""
        return self.db.query(User).offset(skip).limit(limit).all()

    def activate_user(self, user_id: int) -> Optional[User]:
        """Activate a user by setting is_active to True."""
        user = self.get_by_id(user_id)
        if user:
            user.is_active = True
            self.db.commit()
            self.db.refresh(user)
        return user

