"""API and APIArchived repositories for database operations."""
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models import API, APIArchived


class APIArchivedRepository:
    """Repository for APIArchived model operations (legacy api_archived_20260221 table)."""

    def __init__(self, db: Session):
        """Initialize repository with database session."""
        self.db = db

    def query(
        self,
        user_id: Optional[int] = None,
        exchange: Optional[str] = None,
        master_uid: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[APIArchived]:
        """
        Query APIArchiveds with optional filters.
        
        Args:
            user_id: Filter by user ID
            exchange: Filter by exchange name
            master_uid: Filter by master account UID (None for main accounts, UID for sub-accounts)
            skip: Number of records to skip (pagination)
            limit: Maximum number of records to return (pagination)
            
        Returns:
            List of APIArchived objects matching the criteria
        """
        query = self.db.query(APIArchived)
        
        if user_id is not None:
            query = query.filter(APIArchived.user_id == user_id)
        
        if exchange is not None:
            query = query.filter(APIArchived.exchange == exchange)
        
        if master_uid is not None:
            query = query.filter(APIArchived.master_uid == master_uid)
        
        return query.offset(skip).limit(limit).all()

    def get(self, api_id: int) -> Optional[APIArchived]:
        """
        Get APIArchived by ID.
        
        Args:
            api_id: APIArchived ID
            
        Returns:
            APIArchived object if found, None otherwise
        """
        return self.db.query(APIArchived).filter(APIArchived.id == api_id).first()

    def get_by_user_and_id(self, user_id: int, api_id: int) -> Optional[APIArchived]:
        """
        Get APIArchived by ID and user ID (for security - ensures user owns the APIArchived).
        
        Args:
            user_id: User ID
            api_id: APIArchived ID
            
        Returns:
            APIArchived object if found and owned by user, None otherwise
        """
        return self.db.query(APIArchived).filter(
            and_(APIArchived.id == api_id, APIArchived.user_id == user_id)
        ).first()

    def create(
        self,
        user_id: int,
        exchange: str,
        uid: str,
        name: str,
        api_type: str,
        api_key: str,
        api_secret: str,
        passphrase: Optional[str] = None,
        master_uid: Optional[str] = None,
    ) -> APIArchived:
        """
        Create a new APIArchived.
        
        Args:
            user_id: User ID who owns this APIArchived
            exchange: Exchange name (e.g., 'BINANCE', 'BYBIT')
            uid: Exchange account UID
            name: APIArchived name/identifier
            api_type: APIArchived type (e.g., 'HMAC', 'RSA')
            api_key: APIArchived key
            api_secret: APIArchived secret
            passphrase: Passphrase (optional, some exchanges don't use it)
            master_uid: Master account UID (None for main account, or UID for sub-account)
            
        Returns:
            Created APIArchived object
        """
        api = APIArchived(
            user_id=user_id,
            exchange=exchange,
            uid=uid,
            name=name,
            api_type=api_type,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            master_uid=master_uid,
        )
        self.db.add(api)
        self.db.commit()
        self.db.refresh(api)
        
        # If master_uid is None and this is a main account, set master_uid to its own uid
        if master_uid is None:
            api.master_uid = uid
            self.db.commit()
            self.db.refresh(api)
        
        return api

    def delete(self, api_id: int) -> bool:
        """
        Delete APIArchived by ID.
        
        Args:
            api_id: APIArchived ID to delete
            
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
        Delete APIArchived by ID and user ID (for security - ensures user owns the APIArchived).
        
        Args:
            user_id: User ID
            api_id: APIArchived ID to delete
            
        Returns:
            True if deleted, False if not found or not owned by user
        """
        api = self.get_by_user_and_id(user_id, api_id)
        if api:
            self.db.delete(api)
            self.db.commit()
            return True
        return False

    def update(self, api: APIArchived) -> APIArchived:
        """
        Update APIArchived (commit changes to database).
        
        Args:
            api: APIArchived object with updated fields
            
        Returns:
            Updated APIArchived object
        """
        self.db.commit()
        self.db.refresh(api)
        return api

    def get_main_accounts(self, user_id: int) -> List[APIArchived]:
        """
        Get all main accounts for a user (where master_uid equals uid).
        
        Args:
            user_id: User ID
            
        Returns:
            List of main account APIArchiveds
        """
        return self.db.query(APIArchived).filter(
            and_(
                APIArchived.user_id == user_id,
                APIArchived.master_uid == APIArchived.uid
            )
        ).all()

    def get_sub_accounts(self, master_uid: str) -> List[APIArchived]:
        """
        Get all sub-accounts for a master account.
        
        Args:
            master_uid: Master account UID
            
        Returns:
            List of sub-account APIArchiveds
        """
        return self.db.query(APIArchived).filter(
            and_(
                APIArchived.master_uid == master_uid,
                APIArchived.master_uid != APIArchived.uid  # Exclude the master itself
            )
        ).all()
    
    def get_by_uid(self, user_id: int, exchange: str, uid: str) -> Optional[APIArchived]:
        """
        Get APIArchived by exchange UID for a user.
        
        Args:
            user_id: User ID
            exchange: Exchange name
            uid: Exchange account UID
            
        Returns:
            APIArchived object if found, None otherwise
        """
        return self.db.query(APIArchived).filter(
            and_(
                APIArchived.user_id == user_id,
                APIArchived.exchange == exchange,
                APIArchived.uid == uid
            )
        ).first()


class APIRepository:
    """Repository for API model operations (api table)."""

    def __init__(self, db: Session):
        """Initialize repository with database session."""
        self.db = db

    def query(
        self,
        user_id: Optional[int] = None,
        acct_id: Optional[int] = None,
        exch_id: Optional[int] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[API]:
        """Query APIs with optional filters."""
        query = self.db.query(API)
        if user_id is not None:
            query = query.filter(API.user_id == user_id)
        if acct_id is not None:
            query = query.filter(API.acct_id == acct_id)
        if exch_id is not None:
            query = query.filter(API.exch_id == exch_id)
        return query.offset(skip).limit(limit).all()

    def get(self, api_id: int) -> Optional[API]:
        """Get API by ID."""
        return self.db.query(API).filter(API.id == api_id).first()

    def get_by_user_and_id(self, user_id: int, api_id: int) -> Optional[API]:
        """Get API by ID if it belongs to the user."""
        return self.db.query(API).filter(
            and_(API.id == api_id, API.user_id == user_id)
        ).first()

    def get_by_acct(self, acct_id: int) -> List[API]:
        """Get all APIs for an account."""
        return self.db.query(API).filter(API.acct_id == acct_id).all()

    def create(
        self,
        acct_id: int,
        user_id: int,
        exch_id: int,
        name: str,
        api_type: str,
        api_key: str,
        api_secret: str,
        passphrase: Optional[str] = None,
    ) -> API:
        """Create a new API."""
        api = API(
            acct_id=acct_id,
            user_id=user_id,
            exch_id=exch_id,
            name=name,
            api_type=api_type,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
        )
        self.db.add(api)
        self.db.commit()
        self.db.refresh(api)
        return api

    def update(self, api: API) -> API:
        """Update API (commit changes to database)."""
        self.db.commit()
        self.db.refresh(api)
        return api

    def delete(self, api_id: int) -> bool:
        """Delete API by ID."""
        api = self.get(api_id)
        if api:
            self.db.delete(api)
            self.db.commit()
            return True
        return False

    def delete_by_user_and_id(self, user_id: int, api_id: int) -> bool:
        """Delete API by ID if it belongs to the user."""
        api = self.get_by_user_and_id(user_id, api_id)
        if api:
            self.db.delete(api)
            self.db.commit()
            return True
        return False
