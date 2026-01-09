"""Order router."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, field_validator

from app.db.session import get_db
from app.models import User
from app.middleware.auth import get_current_user
from app.repositories.api_repository import APIRepository
from app.repositories.symbol_repository import SymbolRepository
from app.services.api_service import ApiService, NetworkError, ExchangeError

router = APIRouter(prefix="/order", tags=["orders"])


# Pydantic models for request/response
class OrderCreate(BaseModel):
    """Order creation request model."""
    symbol_id: int = Field(..., description="Symbol ID")
    account_id: int = Field(..., description="API key ID (account ID)")
    type: str = Field(..., description="Order type: MARKET or LIMIT")
    price: Optional[str] = Field(None, description="Order price (required for LIMIT orders)")
    qty: str = Field(..., description="Order quantity")
    side: str = Field(..., description="Order side: BUY or SELL")
    
    @field_validator('type')
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate order type."""
        v_upper = v.upper() if isinstance(v, str) else str(v).upper()
        if v_upper not in ['MARKET', 'LIMIT']:
            raise ValueError("Order type must be MARKET or LIMIT")
        return v_upper
    
    @field_validator('side')
    @classmethod
    def validate_side(cls, v: str) -> str:
        """Validate order side."""
        v_upper = v.upper() if isinstance(v, str) else str(v).upper()
        if v_upper not in ['BUY', 'SELL']:
            raise ValueError("Order side must be BUY or SELL")
        return v_upper


class OrderResponse(BaseModel):
    """Order response model."""
    order_id: str
    symbol: str
    side: str
    type: str
    quantity: str
    price: str  # Always string, "-1" for MARKET orders
    status: str  # Normalized: NEW, FILLED, PARTIAL_FILLED, REJECT, CANCEL
    client_order_id: str
    account_id: int


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order_data: OrderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new order.
    
    Validates symbol and API key ownership, then places order on the exchange.
    """
    # Validate price for LIMIT orders
    if order_data.type == "LIMIT" and not order_data.price:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Price is required for LIMIT orders"
        )
    
    # Get API key and verify ownership
    api_repo = APIRepository(db)
    api = api_repo.get_by_user_and_id(current_user.id, order_data.account_id)
    
    if not api:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key with ID {order_data.account_id} not found or does not belong to you"
        )
    
    # Get symbol and verify it exists
    symbol_repo = SymbolRepository(db)
    symbol = symbol_repo.get(order_data.symbol_id)
    
    if not symbol:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol with ID {order_data.symbol_id} not found"
        )
    
    # Verify symbol matches the exchange
    exchange_slug = api.exchange.upper() if hasattr(api, 'exchange') else api.exchange.upper()
    if symbol.exchange.slug.upper() != exchange_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Symbol belongs to exchange '{symbol.exchange.slug}', but API key is for '{exchange_slug}'"
        )
    
    # Place order using API service
    api_service = ApiService()
    try:
        order_result = api_service.create_order(
            exchange=exchange_slug,  # type: ignore
            api_key=api.api_key,
            api_secret=api.api_secret,
            symbol=symbol.name,
            side=order_data.side,  # type: ignore
            order_type=order_data.type,  # type: ignore
            quantity=order_data.qty,
            price=order_data.price,
            passphrase=api.passphrase
        )
        
        return OrderResponse(
            order_id=order_result.get('order_id', ''),
            symbol=order_result.get('symbol', symbol.name),
            side=order_result.get('side', order_data.side),
            type=order_result.get('type', order_data.type),
            quantity=order_result.get('quantity', order_data.qty),
            price=order_result.get('price', '-1'),  # Already normalized by service
            status=order_result.get('status', 'NEW'),  # Already normalized by service
            client_order_id=order_result.get('client_order_id', ''),
            account_id=order_data.account_id
        )
        
    except NetworkError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error while placing order: {e.message}"
        )
    except ExchangeError as e:
        error_detail = f"Exchange error: {e.message}"
        if e.error_code is not None:
            error_detail += f" (Error code: {e.error_code})"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create order: {str(e)}"
        )
