"""Account repository for database operations."""
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models import Account


class AccountRepository:
    """Repository for Account model operations."""

    def __init__(self, db: Session):
        """Initialize repository with database session."""
        self.db = db

    def query(
        self,
        user_id: Optional[int] = None,
        exch_id: Optional[int] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Account]:
        """
        Query accounts with optional filters.

        Args:
            user_id: Filter by user ID
            exch_id: Filter by exchange ID (ref_exchange)
            skip: Number of records to skip (pagination)
            limit: Maximum number of records to return (pagination)

        Returns:
            List of Account objects matching the criteria
        """
        query = self.db.query(Account)
        if user_id is not None:
            query = query.filter(Account.user_id == user_id)
        if exch_id is not None:
            query = query.filter(Account.exch_id == exch_id)
        return query.offset(skip).limit(limit).all()

    def get(self, account_id: int) -> Optional[Account]:
        """Get account by ID."""
        return self.db.query(Account).filter(Account.id == account_id).first()

    def get_by_user_and_id(self, user_id: int, account_id: int) -> Optional[Account]:
        """Get account by ID if it belongs to the user."""
        return self.db.query(Account).filter(
            and_(Account.id == account_id, Account.user_id == user_id)
        ).first()

    def get_by_user_and_uid(self, user_id: int, exch_id: int, uid: int) -> Optional[Account]:
        """Get account by user, exchange and uid."""
        return self.db.query(Account).filter(
            and_(
                Account.user_id == user_id,
                Account.exch_id == exch_id,
                Account.uid == uid,
            )
        ).first()

    def create(
        self,
        user_id: int,
        exch_id: int,
        uid: int,
        name: str,
        parent_uid: Optional[int] = None,
    ) -> Account:
        """Create a new account."""
        account = Account(
            user_id=user_id,
            exch_id=exch_id,
            uid=uid,
            name=name,
            parent_uid=parent_uid,
        )
        self.db.add(account)
        self.db.commit()
        self.db.refresh(account)
        return account

    def update(self, account: Account) -> Account:
        """Update account (commit changes to database)."""
        self.db.commit()
        self.db.refresh(account)
        return account

    def delete(self, account_id: int) -> bool:
        """Delete account by ID."""
        account = self.get(account_id)
        if account:
            self.db.delete(account)
            self.db.commit()
            return True
        return False

    def delete_by_user_and_id(self, user_id: int, account_id: int) -> bool:
        """Delete account by ID if it belongs to the user."""
        account = self.get_by_user_and_id(user_id, account_id)
        if account:
            self.db.delete(account)
            self.db.commit()
            return True
        return False
