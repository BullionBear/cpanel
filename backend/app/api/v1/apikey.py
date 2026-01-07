"""API key router."""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.models import User
from app.middleware.auth import get_current_user
from app.repositories.api_repository import APIRepository
from app.services.api_service import ApiService, NetworkError, ExchangeError

router = APIRouter(prefix="/apikey", tags=["api-keys"])


# Pydantic models for request/response
class APIKeyCreate(BaseModel):
    """API key creation request model."""
    exchange: str
    name: str
    api_key: str
    api_secret: str
    passphrase: Optional[str] = None
    master_is: Optional[str] = None  # Can be string (ID) or None for main account


class APIKeyResponse(BaseModel):
    """API key response model."""
    id: int
    user_id: int
    exchange: str
    name: str
    master_is: Optional[int] = None
    created_at: str

    class Config:
        from_attributes = True


@router.post("/register", response_model=APIKeyResponse, status_code=status.HTTP_201_CREATED)
async def register_api_key(
    api_data: APIKeyCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Register a new API key for an exchange.
    
    Verifies the API credentials with the exchange before saving to database.
    """
    # Normalize exchange name to uppercase
    exchange = api_data.exchange.upper()
    
    # Validate exchange
    if exchange not in ['BINANCE', 'BYBIT']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported exchange: {api_data.exchange}. Supported exchanges: BINANCE, BYBIT"
        )
    
    # Verify API credentials with exchange
    api_service = ApiService()
    try:
        api_service.verify_api(
            exchange=exchange,  # type: ignore
            api_key=api_data.api_key,
            api_secret=api_data.api_secret,
            passphrase=api_data.passphrase
        )
    except NetworkError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error while verifying API: {e.message}"
        )
    except ExchangeError as e:
        error_detail = f"Exchange error: {e.message}"
        if e.error_code is not None:
            error_detail += f" (Error code: {e.error_code})"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail
        )
    
    # Create API repository
    api_repo = APIRepository(db)
    
    # Parse master_is - convert to int if provided, None if not (main account)
    master_is: Optional[int] = None
    if api_data.master_is:
        try:
            master_is = int(api_data.master_is)
            # Verify that the master API exists and belongs to the user
            master_api = api_repo.get_by_user_and_id(current_user.id, master_is)
            if not master_api:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Master API with ID {master_is} not found or does not belong to you"
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid master_is value: {api_data.master_is}. Must be a valid integer ID."
            )
    
    # Create API record in database
    try:
        api = api_repo.create(
            user_id=current_user.id,
            exchange=exchange,
            name=api_data.name,
            api_key=api_data.api_key,
            api_secret=api_data.api_secret,
            passphrase=api_data.passphrase,
            master_is=master_is
        )
        
        return APIKeyResponse(
            id=api.id,
            user_id=api.user_id,
            exchange=api.exchange,
            name=api.name,
            master_is=api.master_is,
            created_at=api.created_at.isoformat() if api.created_at else ""
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save API key: {str(e)}"
        )


@router.get("", response_model=List[APIKeyResponse])
async def get_api_keys(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get all API keys for the current authenticated user.
    
    Returns a list of all API keys registered by the user.
    """
    api_repo = APIRepository(db)
    
    # Get all APIs for the current user
    apis = api_repo.query(user_id=current_user.id)
    
    # Convert to response models
    return [
        APIKeyResponse(
            id=api.id,
            user_id=api.user_id,
            exchange=api.exchange,
            name=api.name,
            master_is=api.master_is,
            created_at=api.created_at.isoformat() if api.created_at else ""
        )
        for api in apis
    ]


@router.delete("/{api_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    api_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Delete an API key by ID.
    
    Verifies that the API key belongs to the current user before deletion.
    """
    api_repo = APIRepository(db)
    
    # Delete API - this method verifies ownership automatically
    deleted = api_repo.delete_by_user_and_id(current_user.id, api_id)
    
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key with ID {api_id} not found or does not belong to you"
        )
    
    # Return 204 No Content on successful deletion
    return None
