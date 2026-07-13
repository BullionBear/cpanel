"""Market router."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Path, Query
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


def _get_bbo_for_symbol(symbol, market_service: MarketService) -> BestBidAskResponse:
    """
    Helper function to get best bid/ask for a single symbol and convert to response model.
    
    Args:
        symbol: RefSymbol model instance
        market_service: MarketService instance
        
    Returns:
        BestBidAskResponse object
        
    Raises:
        ValueError: If invalid exchange or symbol
        Exception: For other errors
    """
    # Get exchange slug for the market service
    exchange_slug = symbol.exchange.slug.upper()
    
    # Get best bid and ask using market service
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


@router.get("/bbos", response_model=List[BestBidAskResponse])
async def get_best_bid_ask_multiple(
    symbol_id: Optional[str] = Query(None, description="Comma-separated list of Symbol IDs (e.g., '1,2,3,4')"),
    db: Session = Depends(get_db),
):
    """
    Get the best bid and ask (BBO) for multiple symbols.
    
    Accepts a comma-separated list of symbol IDs and returns BBO for all valid symbols.
    Invalid or non-existent symbol IDs are silently skipped.
    Uses public market data endpoint, no authentication required.
    
    Example: /market/bbos?symbol_id=1,2,3,4
    """
    if not symbol_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameter 'symbol_id' is required (e.g., ?symbol_id=1,2,3,4)"
        )
    
    # Parse comma-separated IDs
    try:
        symbol_ids = [int(sid.strip()) for sid in symbol_id.split(",") if sid.strip()]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid symbol ID format. IDs must be integers separated by commas (e.g., '1,2,3,4')"
        )
    
    if not symbol_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one valid symbol ID is required"
        )
    
    # Get symbol repository and market service
    symbol_repo = SymbolRepository(db)
    market_service = MarketService()
    
    results = []
    
    # Process each symbol ID
    for sid in symbol_ids:
        # Get symbol from database
        symbol = symbol_repo.get(sid)
        
        if not symbol:
            # Skip symbols that don't exist
            continue
        
        # Get best bid and ask using market service
        try:
            bbo_response = _get_bbo_for_symbol(symbol, market_service)
            results.append(bbo_response)
        except ValueError:
            # Skip symbols with invalid exchange or symbol errors
            continue
        except Exception:
            # Skip symbols with other errors
            continue
    
    if not results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No valid symbols found for the provided IDs or all requests failed"
        )
    
    return results


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
    
    # Get best bid and ask using market service
    market_service = MarketService()
    
    try:
        return _get_bbo_for_symbol(symbol, market_service)
        
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
