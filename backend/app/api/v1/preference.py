"""Preference router."""
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query
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
    layout: str = Query(default="default", description="Layout name for the preference"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get user's preference.
    
    If the user preference is not found for the specified layout, creates a default preference
    with {banner: [1]} and returns it.
    
    Args:
        layout: Layout name (default: "default")
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
