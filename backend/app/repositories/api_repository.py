"""API repository for database operations."""
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models import API


class APIRepository:
    """Repository for API model operations."""

    def __init__(self, db: Session):
        """Initialize repository with database session."""
        self.db = db

    def query(
        self,
        user_id: Optional[int] = None,
        exchange: Optional[str] = None,
        master_is: Optional[int] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[API]:
        """
        Query APIs with optional filters.
        
        Args:
            user_id: Filter by user ID
            exchange: Filter by exchange name
            master_is: Filter by master API ID (None for main accounts, ID for sub-accounts)
            skip: Number of records to skip (pagination)
            limit: Maximum number of records to return (pagination)
            
        Returns:
            List of API objects matching the criteria
        """
        query = self.db.query(API)
        
        if user_id is not None:
            query = query.filter(API.user_id == user_id)
        
        if exchange is not None:
            query = query.filter(API.exchange == exchange)
        
        if master_is is not None:
            query = query.filter(API.master_is == master_is)
        
        return query.offset(skip).limit(limit).all()

    def get(self, api_id: int) -> Optional[API]:
        """
        Get API by ID.
        
        Args:
            api_id: API ID
            
        Returns:
            API object if found, None otherwise
        """
        return self.db.query(API).filter(API.id == api_id).first()

    def get_by_user_and_id(self, user_id: int, api_id: int) -> Optional[API]:
        """
        Get API by ID and user ID (for security - ensures user owns the API).
        
        Args:
            user_id: User ID
            api_id: API ID
            
        Returns:
            API object if found and owned by user, None otherwise
        """
        return self.db.query(API).filter(
            and_(API.id == api_id, API.user_id == user_id)
        ).first()

    def create(
        self,
        user_id: int,
        exchange: str,
        name: str,
        api_type: str,
        api_key: str,
        api_secret: str,
        passphrase: Optional[str] = None,
        master_is: Optional[int] = None,
    ) -> API:
        """
        Create a new API.
        
        Args:
            user_id: User ID who owns this API
            exchange: Exchange name (e.g., 'binance', 'okx')
            name: API name/identifier
            api_type: API type (e.g., 'HMAC', 'RSA')
            api_key: API key
            api_secret: API secret
            passphrase: Passphrase (optional, some exchanges don't use it)
            master_is: Master API ID (None for main account, or set to own ID for main account)
            
        Returns:
            Created API object
        """
        api = API(
            user_id=user_id,
            exchange=exchange,
            name=name,
            api_type=api_type,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            master_is=master_is,
        )
        self.db.add(api)
        self.db.commit()
        self.db.refresh(api)
        
        # If master_is is None and this is a main account, set master_is to its own id
        if master_is is None:
            api.master_is = api.id
            self.db.commit()
            self.db.refresh(api)
        
        return api

    def delete(self, api_id: int) -> bool:
        """
        Delete API by ID.
        
        Args:
            api_id: API ID to delete
            
        Returns:
            True if deleted, False if not found
        """
        api = self.get(api_id)
        if api:
            self.db.delete(api)
            self.db.commit()
            return True
        return False

    def delete_by_user_and_id(self, user_id: int, api_id: int) -> bool:
        """
        Delete API by ID and user ID (for security - ensures user owns the API).
        
        Args:
            user_id: User ID
            api_id: API ID to delete
            
        Returns:
            True if deleted, False if not found or not owned by user
        """
        api = self.get_by_user_and_id(user_id, api_id)
        if api:
            self.db.delete(api)
            self.db.commit()
            return True
        return False

    def update(self, api: API) -> API:
        """
        Update API (commit changes to database).
        
        Args:
            api: API object with updated fields
            
        Returns:
            Updated API object
        """
        self.db.commit()
        self.db.refresh(api)
        return api

    def get_main_accounts(self, user_id: int) -> List[API]:
        """
        Get all main accounts for a user (where master_is equals id).
        
        Args:
            user_id: User ID
            
        Returns:
            List of main account APIs
        """
        return self.db.query(API).filter(
            and_(
                API.user_id == user_id,
                API.master_is == API.id
            )
        ).all()

    def get_sub_accounts(self, master_id: int) -> List[API]:
        """
        Get all sub-accounts for a master API.
        
        Args:
            master_id: Master API ID
            
        Returns:
            List of sub-account APIs
        """
        return self.db.query(API).filter(
            and_(
                API.master_is == master_id,
                API.master_is != API.id  # Exclude the master itself
            )
        ).all()

