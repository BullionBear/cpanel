"""User preference repository for database operations."""
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models import UserPreference


class UserPreferenceRepository:
    """Repository for UserPreference model operations."""

    def __init__(self, db: Session):
        """Initialize repository with database session."""
        self.db = db

    def get_by_id(self, preference_id: int) -> Optional[UserPreference]:
        """
        Get user preference by ID.
        
        Args:
            preference_id: Preference ID
            
        Returns:
            UserPreference object if found, None otherwise
        """
        return self.db.query(UserPreference).filter(UserPreference.id == preference_id).first()

    def get_by_user_and_layout(self, user_id: int, layout: str) -> Optional[UserPreference]:
        """
        Get user preference by user ID and layout.
        
        Args:
            user_id: User ID
            layout: Layout name
            
        Returns:
            UserPreference object if found, None otherwise
        """
        return self.db.query(UserPreference).filter(
            and_(UserPreference.user_id == user_id, UserPreference.layout == layout)
        ).first()

    def get_all_by_user(self, user_id: int, skip: int = 0, limit: int = 100) -> List[UserPreference]:
        """
        Get all preferences for a user.
        
        Args:
            user_id: User ID
            skip: Number of records to skip (pagination)
            limit: Maximum number of records to return (pagination)
            
        Returns:
            List of UserPreference objects for the user
        """
        return self.db.query(UserPreference).filter(
            UserPreference.user_id == user_id
        ).offset(skip).limit(limit).all()

    def create(
        self,
        user_id: int,
        layout: str,
        version: int,
        preference: Dict[str, Any],
    ) -> UserPreference:
        """
        Create a new user preference.
        
        Args:
            user_id: User ID
            layout: Layout name
            version: Version number
            preference: Preference JSONB data (e.g., {"banner": [id1, id2, ...]})
            
        Returns:
            Created UserPreference object
        """
        user_pref = UserPreference(
            user_id=user_id,
            layout=layout,
            version=version,
            preference=preference,
        )
        self.db.add(user_pref)
        self.db.commit()
        self.db.refresh(user_pref)
        return user_pref

    def update(
        self,
        user_pref: UserPreference,
        layout: Optional[str] = None,
        version: Optional[int] = None,
        preference: Optional[Dict[str, Any]] = None,
    ) -> UserPreference:
        """
        Update user preference.
        
        Args:
            user_pref: UserPreference object to update
            layout: New layout name (optional)
            version: New version number (optional)
            preference: New preference JSONB data (optional)
            
        Returns:
            Updated UserPreference object
        """
        if layout is not None:
            user_pref.layout = layout
        if version is not None:
            user_pref.version = version
        if preference is not None:
            user_pref.preference = preference
        
        self.db.commit()
        self.db.refresh(user_pref)
        return user_pref

    def delete(self, preference_id: int) -> bool:
        """
        Delete user preference by ID.
        
        Args:
            preference_id: Preference ID
            
        Returns:
            True if deleted, False if not found
        """
        user_pref = self.get_by_id(preference_id)
        if user_pref:
            self.db.delete(user_pref)
            self.db.commit()
            return True
        return False

    def delete_by_user_and_layout(self, user_id: int, layout: str) -> bool:
        """
        Delete user preference by user ID and layout.
        
        Args:
            user_id: User ID
            layout: Layout name
            
        Returns:
            True if deleted, False if not found
        """
        user_pref = self.get_by_user_and_layout(user_id, layout)
        if user_pref:
            self.db.delete(user_pref)
            self.db.commit()
            return True
        return False

    def get_banner_ids(self, user_id: int, layout: str) -> List[int]:
        """
        Get banner IDs from preference JSONB.
        
        Args:
            user_id: User ID
            layout: Layout name
            
        Returns:
            List of banner IDs, empty list if not found
        """
        user_pref = self.get_by_user_and_layout(user_id, layout)
        if user_pref and isinstance(user_pref.preference, dict):
            return user_pref.preference.get("banner", [])
        return []

    def set_banner_ids(self, user_id: int, layout: str, banner_ids: List[int]) -> UserPreference:
        """
        Set banner IDs in preference JSONB. Creates preference if it doesn't exist.
        
        Args:
            user_id: User ID
            layout: Layout name
            banner_ids: List of banner IDs to set
            
        Returns:
            Updated or created UserPreference object
        """
        user_pref = self.get_by_user_and_layout(user_id, layout)
        
        if user_pref:
            # Update existing preference
            preference = user_pref.preference.copy() if isinstance(user_pref.preference, dict) else {}
            preference["banner"] = banner_ids
            return self.update(user_pref, preference=preference)
        else:
            # Create new preference
            return self.create(
                user_id=user_id,
                layout=layout,
                version=1,
                preference={"banner": banner_ids},
            )

    def add_banner_id(self, user_id: int, layout: str, banner_id: int) -> UserPreference:
        """
        Add a banner ID to the banner list in preference JSONB.
        
        Args:
            user_id: User ID
            layout: Layout name
            banner_id: Banner ID to add
            
        Returns:
            Updated or created UserPreference object
        """
        user_pref = self.get_by_user_and_layout(user_id, layout)
        
        if user_pref:
            # Update existing preference
            preference = user_pref.preference.copy() if isinstance(user_pref.preference, dict) else {}
            banner_list = preference.get("banner", [])
            if banner_id not in banner_list:
                banner_list.append(banner_id)
            preference["banner"] = banner_list
            return self.update(user_pref, preference=preference)
        else:
            # Create new preference
            return self.create(
                user_id=user_id,
                layout=layout,
                version=1,
                preference={"banner": [banner_id]},
            )

    def remove_banner_id(self, user_id: int, layout: str, banner_id: int) -> Optional[UserPreference]:
        """
        Remove a banner ID from the banner list in preference JSONB.
        
        Args:
            user_id: User ID
            layout: Layout name
            banner_id: Banner ID to remove
            
        Returns:
            Updated UserPreference object if found, None otherwise
        """
        user_pref = self.get_by_user_and_layout(user_id, layout)
        
        if user_pref and isinstance(user_pref.preference, dict):
            preference = user_pref.preference.copy()
            banner_list = preference.get("banner", [])
            if banner_id in banner_list:
                banner_list.remove(banner_id)
                preference["banner"] = banner_list
                return self.update(user_pref, preference=preference)
        
        return user_pref

    def upsert(
        self,
        user_id: int,
        layout: str,
        version: int,
        preference: Dict[str, Any],
    ) -> UserPreference:
        """
        Create or update user preference (upsert operation).
        
        Args:
            user_id: User ID
            layout: Layout name
            version: Version number
            preference: Preference JSONB data
            
        Returns:
            Created or updated UserPreference object
        """
        user_pref = self.get_by_user_and_layout(user_id, layout)
        
        if user_pref:
            return self.update(user_pref, version=version, preference=preference)
        else:
            return self.create(user_id=user_id, layout=layout, version=version, preference=preference)
