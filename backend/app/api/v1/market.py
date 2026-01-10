"""Market router."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Path
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.repositories.symbol_repository import SymbolRepository
from app.services.market_service import MarketService

router = APIRouter(prefix="/market", tags=["market"])


# Pydantic models for response
class BestBidAskResponse(BaseModel):
    """Best bid and ask response model."""
    symbol_id: int
    symbol: str
    exchange: str
    bid: List[str]  # [price, quantity]
    ask: List[str]  # [price, quantity]


@router.get("/bbo/{symbol_id}", response_model=BestBidAskResponse)
async def get_best_bid_ask(
    symbol_id: int = Path(..., description="Symbol ID"),
    db: Session = Depends(get_db),
):
    """
    Get the best bid and ask (BBO) for a given symbol.
    
    Uses public market data endpoint, no authentication required.
    """
    # Get symbol from database
    symbol_repo = SymbolRepository(db)
    symbol = symbol_repo.get(symbol_id)
    
    if not symbol:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol with ID {symbol_id} not found"
        )
    
    # Get exchange slug for the market service
    exchange_slug = symbol.exchange.slug.upper()
    
    # Get best bid and ask using market service
    market_service = MarketService()
    
    try:
        bbo_result = market_service.get_best_bid_ask(
            exchange=exchange_slug,  # type: ignore
            symbol=symbol.name
        )
        
        return BestBidAskResponse(
            symbol_id=symbol.id,
            symbol=symbol.name,
            exchange=exchange_slug,
            bid=bbo_result["bid"],
            ask=bbo_result["ask"]
        )
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get best bid/ask: {str(e)}"
        )
