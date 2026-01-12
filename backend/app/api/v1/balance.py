"""Balance router."""
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.models import User
from app.middleware.auth import get_current_user
from app.repositories.api_repository import APIRepository
from app.services.api_service import ApiService, NetworkError, ExchangeError

router = APIRouter(prefix="/balance", tags=["balance"])


# Pydantic models for response
class TokenBalance(BaseModel):
    """Token balance model."""
    token: str
    total: str
    frozen: str
    available: str


class WalletBalance(BaseModel):
    """Wallet balance model."""
    wallet: str  # SPOT, MARGIN, FUTURES, UNIFIED, FUNDING
    balance: List[TokenBalance]


class BalanceResponse(BaseModel):
    """Balance response model."""
    api_id: int
    name: str
    exchange: str
    wallets: List[WalletBalance]


def _get_balance_for_api(api, api_service: ApiService) -> BalanceResponse:
    """
    Helper function to get balance for a single API and convert to response model.
    
    Args:
        api: API model instance
        api_service: ApiService instance
        
    Returns:
        BalanceResponse object
        
    Raises:
        NetworkError: If network error occurs
        ExchangeError: If exchange error occurs
    """
    balance_data = api_service.get_balance(
        exchange=api.exchange,  # type: ignore
        api_key=api.api_key,
        api_secret=api.api_secret,
        passphrase=api.passphrase
    )
    
    # Convert to response model
    wallets = [
        WalletBalance(
            wallet=wallet["wallet"],
            balance=[
                TokenBalance(
                    token=token["token"],
                    total=token["total"],
                    frozen=token["frozen"],
                    available=token["available"]
                )
                for token in wallet["balance"]
            ]
        )
        for wallet in balance_data
    ]
    
    return BalanceResponse(
        api_id=api.id,
        name=api.name,
        exchange=api.exchange,
        wallets=wallets
    )


@router.get("", response_model=List[BalanceResponse])
async def get_balances(
    id: Optional[str] = Query(None, description="Comma-separated list of API IDs (e.g., '1,2,4')"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get balances for multiple API keys.
    
    Accepts a comma-separated list of API IDs and returns balances for all valid API keys
    that belong to the current user. Invalid or non-existent IDs are silently skipped.
    
    Example: /balance?id=1,2,4
    """
    if not id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameter 'id' is required (e.g., ?id=1,2,4)"
        )
    
    # Parse comma-separated IDs
    try:
        api_ids = [int(api_id.strip()) for api_id in id.split(",") if api_id.strip()]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid API ID format. IDs must be integers separated by commas (e.g., '1,2,4')"
        )
    
    if not api_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one valid API ID is required"
        )
    
    # Get API repository and service
    api_repo = APIRepository(db)
    api_service = ApiService()
    
    results = []
    
    # Process each API ID
    for api_id in api_ids:
        # Get API from database and verify ownership
        api = api_repo.get_by_user_and_id(current_user.id, api_id)
        
        if not api:
            # Skip APIs that don't exist or don't belong to the user
            continue
        
        # Get balance using API service
        try:
            balance_response = _get_balance_for_api(api, api_service)
            results.append(balance_response)
        except NetworkError:
            # Skip APIs with network errors
            continue
        except ExchangeError:
            # Skip APIs with exchange errors
            continue
    
    if not results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No valid API keys found for the provided IDs or all requests failed"
        )
    
    return results


@router.get("/{api_id}", response_model=BalanceResponse)
async def get_balance_by_api_id(
    api_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get balance for a specific API key.
    
    Verifies that the API key belongs to the current user before fetching balance.
    """
    # Get API from database and verify ownership
    api_repo = APIRepository(db)
    api = api_repo.get_by_user_and_id(current_user.id, api_id)
    
    if not api:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key with ID {api_id} not found or does not belong to you"
        )
    
    # Get balance using API service
    api_service = ApiService()
    try:
        return _get_balance_for_api(api, api_service)
        
    except NetworkError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error while fetching balance: {e.message}"
        )
    except ExchangeError as e:
        error_detail = f"Exchange error: {e.message}"
        if e.error_code is not None:
            error_detail += f" (Error code: {e.error_code})"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail
        )
