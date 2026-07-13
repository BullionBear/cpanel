"""Order router."""
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, field_validator

from app.db.session import get_db
from app.models import User
from app.middleware.auth import get_current_user
from app.repositories.api_repository import APIArchivedRepository
from app.repositories.symbol_repository import SymbolRepository
from app.services.api_service import APIService, NetworkError, ExchangeError

router = APIRouter(prefix="/order", tags=["orders"])

# API type priority: HMAC > ED25519 > RSA (lower number = higher priority)
API_TYPE_PRIORITY = {'HMAC': 0, 'ED25519': 1, 'RSA': 2}


def _select_best_api_per_uid(apis: List[Any]) -> List[Any]:
    """
    Select the best API for each unique UID based on api_type priority.
    
    Priority: HMAC > ED25519 > RSA
    
    Args:
        apis: List of API model instances
        
    Returns:
        List of API model instances with one per unique (exchange, uid) combination
    """
    # Group APIs by (exchange, uid)
    uid_to_apis: Dict[tuple, List[Any]] = {}
    for api in apis:
        key = (api.exchange, api.uid)
        if key not in uid_to_apis:
            uid_to_apis[key] = []
        uid_to_apis[key].append(api)
    
    # Select best API for each UID based on priority
    best_apis = []
    for key, api_list in uid_to_apis.items():
        # Sort by api_type priority (lower = better)
        api_list.sort(key=lambda a: API_TYPE_PRIORITY.get(a.api_type, 999))
        best_apis.append(api_list[0])
    
    return best_apis


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
    account_name: str
    created_at: int  # Unix timestamp (seconds since epoch)
    updated_at: int  # Unix timestamp (seconds since epoch)


class OrderCancel(BaseModel):
    """Order cancellation request model."""
    account_id: int = Field(..., description="API key ID (account ID)")
    symbol_id: int = Field(..., description="Symbol ID")
    order_id: Optional[str] = Field(None, description="Exchange order ID (optional if client_order_id provided)")
    client_order_id: Optional[str] = Field(None, description="Client order ID (optional if order_id provided)")


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
    api_repo = APIArchivedRepository(db)
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
    api_service = APIService()
    try:
        client = api_service.get_exchange_client_from_api_info(exchange_slug, api)  # type: ignore
        order_result = client.create_order(
            symbol=symbol.name,
            side=order_data.side,  # type: ignore
            order_type=order_data.type,  # type: ignore
            quantity=order_data.qty,
            price=order_data.price,
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
            account_id=order_data.account_id,
            account_name=api.name,
            created_at=order_result.get('created_at', 0),
            updated_at=order_result.get('updated_at', 0)
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


def _get_open_orders_for_api(
    api,
    api_service: APIService,
    symbol_name: Optional[str] = None,
) -> List[OrderResponse]:
    """
    Helper function to get open orders for a single API and convert to response model.
    
    Args:
        api: API model instance
        api_service: APIService instance
        symbol_name: Optional symbol name to filter orders
        
    Returns:
        List of OrderResponse objects
        
    Raises:
        NetworkError: If network error occurs
        ExchangeError: If exchange error occurs
    """
    exchange_slug = api.exchange.upper()
    
    client = api_service.get_exchange_client_from_api_info(exchange_slug, api)  # type: ignore
    open_orders = client.get_open_orders(symbol=symbol_name)
    
    # Convert to OrderResponse format
    return [
        OrderResponse(
            order_id=order.get('order_id', ''),
            symbol=order.get('symbol', ''),
            side=order.get('side', ''),
            type=order.get('type', ''),
            quantity=order.get('quantity', '0'),
            price=order.get('price', '-1'),
            status=order.get('status', 'NEW'),
            client_order_id=order.get('client_order_id', ''),
            account_id=api.id,
            account_name=api.name,
            created_at=order.get('created_at', 0),
            updated_at=order.get('updated_at', 0)
        )
        for order in open_orders
    ]


@router.get("/open", response_model=List[OrderResponse])
async def get_open_orders(
    account_id: Optional[int] = Query(None, description="Optional API key ID (account ID). If not provided, returns orders for all user accounts"),
    symbol_id: Optional[int] = Query(None, description="Optional symbol ID to filter orders"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get all open orders.
    
    If account_id is provided, returns open orders for that specific API key.
    If account_id is not provided, returns open orders for all API keys belonging to the current user.
    Optionally filtered by symbol_id.
    """
    api_repo = APIArchivedRepository(db)
    api_service = APIService()
    
    # If symbol_id is provided, get symbol info (we'll validate against each exchange)
    symbol = None
    if symbol_id:
        symbol_repo = SymbolRepository(db)
        symbol = symbol_repo.get(symbol_id)
        
        if not symbol:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Symbol with ID {symbol_id} not found"
            )
    
    all_orders = []
    
    if account_id:
        # Single API key mode - existing behavior
        api = api_repo.get_by_user_and_id(current_user.id, account_id)
        
        if not api:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"API key with ID {account_id} not found or does not belong to you"
            )
        
        # If symbol_id is provided, verify it matches the exchange
        symbol_name = None
        if symbol:
            exchange_slug = api.exchange.upper()
            if symbol.exchange.slug.upper() != exchange_slug:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Symbol belongs to exchange '{symbol.exchange.slug}', but API key is for '{exchange_slug}'"
                )
            symbol_name = symbol.name
        
        # Get open orders for this API
        try:
            orders = _get_open_orders_for_api(api, api_service, symbol_name)
            all_orders.extend(orders)
        except NetworkError:
            # Skip on network errors
            pass
        except ExchangeError:
            # Skip on exchange errors
            pass
    else:
        # All accounts mode - get all API keys for the user
        apis = api_repo.query(user_id=current_user.id)
        
        if not apis:
            return []
        
        # Filter APIs by exchange if symbol is provided
        filtered_apis = []
        for api in apis:
            if symbol:
                # Only include APIs that match the symbol's exchange
                exchange_slug = api.exchange.upper()
                if symbol.exchange.slug.upper() == exchange_slug:
                    filtered_apis.append(api)
            else:
                filtered_apis.append(api)
        
        # Deduplicate by UID - select best API per UID based on priority
        # Priority: HMAC > ED25519 > RSA
        deduplicated_apis = _select_best_api_per_uid(filtered_apis)
        
        # Get open orders for each deduplicated API
        for api in deduplicated_apis:
            symbol_name = symbol.name if symbol else None
            
            try:
                orders = _get_open_orders_for_api(api, api_service, symbol_name)
                all_orders.extend(orders)
            except NetworkError:
                # Skip APIs with network errors
                continue
            except ExchangeError:
                # Skip APIs with exchange errors
                continue
    
    return all_orders


@router.delete("", response_model=OrderResponse)
async def cancel_order(
    cancel_data: OrderCancel,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Cancel an order.
    
    Requires either order_id or client_order_id, and verifies API key ownership.
    """
    # Validate that at least one identifier is provided
    if not cancel_data.order_id and not cancel_data.client_order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either order_id or client_order_id must be provided"
        )
    
    # Get API key and verify ownership
    api_repo = APIArchivedRepository(db)
    api = api_repo.get_by_user_and_id(current_user.id, cancel_data.account_id)
    
    if not api:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key with ID {cancel_data.account_id} not found or does not belong to you"
        )
    
    # Get symbol and verify it exists and matches the exchange
    symbol_repo = SymbolRepository(db)
    symbol = symbol_repo.get(cancel_data.symbol_id)
    
    if not symbol:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol with ID {cancel_data.symbol_id} not found"
        )
    
    # Verify symbol matches the exchange
    exchange_slug = api.exchange.upper()
    if symbol.exchange.slug.upper() != exchange_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Symbol belongs to exchange '{symbol.exchange.slug}', but API key is for '{exchange_slug}'"
        )
    
    # Cancel order using API service
    api_service = APIService()
    
    try:
        client = api_service.get_exchange_client_from_api_info(exchange_slug, api)  # type: ignore
        cancel_result = client.cancel_order(
            symbol=symbol.name,
            order_id=cancel_data.order_id,
            client_order_id=cancel_data.client_order_id,
        )
        
        return OrderResponse(
            order_id=cancel_result.get('order_id', ''),
            symbol=cancel_result.get('symbol', symbol.name),
            side=cancel_result.get('side', ''),
            type=cancel_result.get('type', ''),
            quantity=cancel_result.get('quantity', '0'),
            price=cancel_result.get('price', '-1'),
            status=cancel_result.get('status', 'CANCEL'),
            client_order_id=cancel_result.get('client_order_id', cancel_data.client_order_id or ''),
            account_id=cancel_data.account_id,
            account_name=api.name,
            created_at=cancel_result.get('created_at', 0),
            updated_at=cancel_result.get('updated_at', 0)
        )
        
    except NetworkError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error while canceling order: {e.message}"
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
            detail=f"Failed to cancel order: {str(e)}"
        )
