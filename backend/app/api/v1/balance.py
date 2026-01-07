"""Balance router."""
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
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
    exchange: str
    wallets: List[WalletBalance]


@router.get("/api/{api_id}", response_model=BalanceResponse)
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
            exchange=api.exchange,
            wallets=wallets
        )
        
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
