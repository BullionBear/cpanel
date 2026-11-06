"""Preference router."""
from typing import Dict, Any, Literal
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.models import User
from app.middleware.auth import get_current_user
from app.repositories.user_preference_repository import UserPreferenceRepository

router = APIRouter(prefix="/preference", tags=["preference"])


# Pydantic models for response
class PreferenceResponse(BaseModel):
    """Preference response model."""
    preference: Dict[str, Any]

    class Config:
        json_schema_extra = {
            "example": {
                "preference": {
                    "banner": [1]
                }
            }
        }


@router.get("", response_model=PreferenceResponse)
async def get_preference(
    layout: Literal["execution"] = Query(default="execution", description="Layout name for the preference"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get user's preference.
    
    If the user preference is not found for the specified layout, creates a default preference
    with {banner: [1]}.
    
    Args:
        layout: Layout name (default: "execution"). Add new layout types to the Literal type hint as needed.
        current_user: Authenticated user (from JWT token)
        db: Database session
        
    Returns:
        PreferenceResponse containing the preference JSONB schema
    """
    preference_repo = UserPreferenceRepository(db)
    
    # Try to get existing preference
    user_pref = preference_repo.get_by_user_and_layout(current_user.id, layout)
    
    # If not found, create default preference
    if not user_pref:
        user_pref = preference_repo.create(
            user_id=current_user.id,
            layout=layout,
            version=1,
            preference={"banner": [1]},
        )
    
    # Return the preference JSONB schema
    preference_data = user_pref.preference if isinstance(user_pref.preference, dict) else {}
    
    return PreferenceResponse(preference=preference_data)

@router.put("", response_model=PreferenceResponse)
async def update_preference(
    preference: PreferenceResponse,
    layout: Literal["execution"] = Query(default="execution", description="Layout name for the preference"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Update user's preference.
    
    If the user preference is not found for the specified layout, creates a new preference
    with the provided data.
    
    Args:
        preference: Preference data to update
        layout: Layout name (default: "execution"). Add new layout types to the Literal type hint as needed.
        current_user: Authenticated user (from JWT token)
        db: Database session
        
    Returns:
        PreferenceResponse containing the updated preference JSONB schema
    """
    preference_repo = UserPreferenceRepository(db)
    
    # Get existing preference or create if it doesn't exist
    user_pref = preference_repo.get_by_user_and_layout(current_user.id, layout)
    
    if user_pref:
        # Update existing preference
        updated_pref = preference_repo.update(
            user_pref,
            preference=preference.preference
        )
        preference_data = updated_pref.preference if isinstance(updated_pref.preference, dict) else {}
    else:
        # Create new preference
        user_pref = preference_repo.create(
            user_id=current_user.id,
            layout=layout,
            version=1,
            preference=preference.preference,
        )
        preference_data = user_pref.preference if isinstance(user_pref.preference, dict) else {}
    
    return PreferenceResponse(preference=preference_data)