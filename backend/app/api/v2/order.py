"""Order router (v2): uses APIRepository (API linked to account)."""
from typing import Optional, List, Dict, Any, Tuple
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, field_validator

from app.db.session import get_db
from app.models import User, RefExchange
from app.middleware.auth import get_current_user
from app.repositories.api_repository import APIRepository
from app.repositories.account_repository import AccountRepository
from app.repositories.symbol_repository import SymbolRepository
from app.services.api_service import APIService, NetworkError, ExchangeError

router = APIRouter(prefix="/order", tags=["orders-v2"])

# API type priority: HMAC > ED25519 > RSA (lower number = higher priority)
API_TYPE_PRIORITY = {"HMAC": 0, "ED25519": 1, "RSA": 2}


def _select_best_api_per_uid(
    api_exchange_list: List[Tuple[Any, str, int]],
) -> List[Tuple[Any, str]]:
    """
    Select the best API for each unique (exchange, uid) based on api_type priority.
    Input: list of (api, exchange_name, uid). Output: list of (api, exchange_name).
    """
    key_to_apis: Dict[tuple, List[Tuple[Any, str]]] = {}
    for api, exchange_name, uid in api_exchange_list:
        key = (exchange_name, uid)
        if key not in key_to_apis:
            key_to_apis[key] = []
        key_to_apis[key].append((api, exchange_name))
    best = []
    for api_list in key_to_apis.values():
        api_list.sort(key=lambda t: API_TYPE_PRIORITY.get(t[0].api_type, 999))
        best.append(api_list[0])
    return best


def _get_exchange_slug(db: Session, exch_id: int) -> str:
    """Get exchange slug (uppercase) by exch_id."""
    exchange = db.query(RefExchange).filter(RefExchange.id == exch_id).first()
    if not exchange:
        return ""
    return (exchange.slug or exchange.name or "").upper()


# --- Pydantic models ---
class OrderCreate(BaseModel):
    """Order creation request model."""
    symbol_id: int = Field(..., description="Symbol ID")
    account_id: int = Field(..., description="API key ID")
    type: str = Field(..., description="Order type: MARKET or LIMIT")
    price: Optional[str] = Field(None, description="Order price (required for LIMIT orders)")
    qty: str = Field(..., description="Order quantity")
    side: str = Field(..., description="Order side: BUY or SELL")

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        v_upper = v.upper() if isinstance(v, str) else str(v).upper()
        if v_upper not in ["MARKET", "LIMIT"]:
            raise ValueError("Order type must be MARKET or LIMIT")
        return v_upper

    @field_validator("side")
    @classmethod
    def validate_side(cls, v: str) -> str:
        v_upper = v.upper() if isinstance(v, str) else str(v).upper()
        if v_upper not in ["BUY", "SELL"]:
            raise ValueError("Order side must be BUY or SELL")
        return v_upper


class OrderResponse(BaseModel):
    """Order response model."""
    order_id: str
    symbol: str
    side: str
    type: str
    quantity: str
    price: str
    status: str
    client_order_id: str
    account_id: int
    account_name: str
    created_at: int
    updated_at: int


class OrderCancel(BaseModel):
    """Order cancellation request model."""
    account_id: int = Field(..., description="API key ID")
    symbol_id: int = Field(..., description="Symbol ID")
    order_id: Optional[str] = Field(None, description="Exchange order ID")
    client_order_id: Optional[str] = Field(None, description="Client order ID")


# --- Endpoints ---
@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order_data: OrderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new order. Validates symbol and API key ownership, then places order on the exchange."""
    if order_data.type == "LIMIT" and not order_data.price:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Price is required for LIMIT orders",
        )

    api_repo = APIRepository(db)
    api = api_repo.get_by_user_and_id(current_user.id, order_data.account_id)
    if not api:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key with ID {order_data.account_id} not found or does not belong to you",
        )

    account_repo = AccountRepository(db)
    account = account_repo.get(api.acct_id)
    acct_name = account.name if account else api.name

    symbol_repo = SymbolRepository(db)
    symbol = symbol_repo.get(order_data.symbol_id)
    if not symbol:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol with ID {order_data.symbol_id} not found",
        )

    exchange_slug = _get_exchange_slug(db, api.exch_id)
    if symbol.exchange.slug.upper() != exchange_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Symbol belongs to exchange '{symbol.exchange.slug}', but API key is for '{exchange_slug}'",
        )

    api_service = APIService()
    try:
        client = api_service.get_exchange_client_from_api_info(exchange_slug, api)
        order_result = client.create_order(
            symbol=symbol.name,
            side=order_data.side,
            order_type=order_data.type,
            quantity=order_data.qty,
            price=order_data.price,
        )
        return OrderResponse(
            order_id=order_result.get("order_id", ""),
            symbol=order_result.get("symbol", symbol.name),
            side=order_result.get("side", order_data.side),
            type=order_result.get("type", order_data.type),
            quantity=order_result.get("quantity", order_data.qty),
            price=order_result.get("price", "-1"),
            status=order_result.get("status", "NEW"),
            client_order_id=order_result.get("client_order_id", ""),
            account_id=order_data.account_id,
            account_name=acct_name,
            created_at=order_result.get("created_at", 0),
            updated_at=order_result.get("updated_at", 0),
        )
    except NetworkError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error while placing order: {e.message}",
        )
    except ExchangeError as e:
        error_detail = f"Exchange error: {e.message}"
        if e.error_code is not None:
            error_detail += f" (Error code: {e.error_code})"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create order: {str(e)}",
        )


def _get_open_orders_for_api(
    api: Any,
    exchange_slug: str,
    api_service: APIService,
    account_name: str,
    symbol_name: Optional[str] = None,
) -> List[OrderResponse]:
    """Get open orders for a single API and convert to response model."""
    client = api_service.get_exchange_client_from_api_info(exchange_slug, api)
    open_orders = client.get_open_orders(symbol=symbol_name)
    return [
        OrderResponse(
            order_id=order.get("order_id", ""),
            symbol=order.get("symbol", ""),
            side=order.get("side", ""),
            type=order.get("type", ""),
            quantity=order.get("quantity", "0"),
            price=order.get("price", "-1"),
            status=order.get("status", "NEW"),
            client_order_id=order.get("client_order_id", ""),
            account_id=api.id,
            account_name=account_name,
            created_at=order.get("created_at", 0),
            updated_at=order.get("updated_at", 0),
        )
        for order in open_orders
    ]


@router.get("/open", response_model=List[OrderResponse])
async def get_open_orders(
    account_id: Optional[int] = Query(None, description="Optional API key ID. If not provided, returns orders for all user APIs"),
    symbol_id: Optional[int] = Query(None, description="Optional symbol ID to filter orders"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all open orders. Optionally filter by account_id (API key ID) and symbol_id."""
    api_repo = APIRepository(db)
    account_repo = AccountRepository(db)
    api_service = APIService()

    symbol = None
    if symbol_id:
        symbol_repo = SymbolRepository(db)
        symbol = symbol_repo.get(symbol_id)
        if not symbol:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Symbol with ID {symbol_id} not found",
            )

    all_orders: List[OrderResponse] = []

    if account_id:
        api = api_repo.get_by_user_and_id(current_user.id, account_id)
        if not api:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"API key with ID {account_id} not found or does not belong to you",
            )
        account = account_repo.get(api.acct_id)
        acct_name = account.name if account else api.name
        exchange_slug = _get_exchange_slug(db, api.exch_id)
        symbol_name = None
        if symbol:
            if symbol.exchange.slug.upper() != exchange_slug:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Symbol belongs to exchange '{symbol.exchange.slug}', but API key is for '{exchange_slug}'",
                )
            symbol_name = symbol.name
        try:
            orders = _get_open_orders_for_api(api, exchange_slug, api_service, acct_name, symbol_name)
            all_orders.extend(orders)
        except (NetworkError, ExchangeError):
            pass
    else:
        apis = api_repo.query(user_id=current_user.id)
        if not apis:
            return []

        api_exchange_list: List[Tuple[Any, str, int]] = []
        api_id_to_account_name: Dict[int, str] = {}
        for api in apis:
            account = account_repo.get(api.acct_id)
            if not account or account.user_id != current_user.id:
                continue
            exchange_slug = _get_exchange_slug(db, api.exch_id)
            if not exchange_slug:
                continue
            if symbol and symbol.exchange.slug.upper() != exchange_slug:
                continue
            api_exchange_list.append((api, exchange_slug, account.uid))
            api_id_to_account_name[api.id] = account.name

        deduplicated = _select_best_api_per_uid(api_exchange_list)
        symbol_name = symbol.name if symbol else None
        for api, exchange_slug in deduplicated:
            try:
                acct_name = api_id_to_account_name.get(api.id, api.name)
                orders = _get_open_orders_for_api(api, exchange_slug, api_service, acct_name, symbol_name)
                all_orders.extend(orders)
            except (NetworkError, ExchangeError):
                continue

    return all_orders


@router.delete("", response_model=OrderResponse)
async def cancel_order(
    cancel_data: OrderCancel,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel an order. Requires either order_id or client_order_id."""
    if not cancel_data.order_id and not cancel_data.client_order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either order_id or client_order_id must be provided",
        )

    api_repo = APIRepository(db)
    api = api_repo.get_by_user_and_id(current_user.id, cancel_data.account_id)
    if not api:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key with ID {cancel_data.account_id} not found or does not belong to you",
        )

    account_repo = AccountRepository(db)
    account = account_repo.get(api.acct_id)
    acct_name = account.name if account else api.name

    symbol_repo = SymbolRepository(db)
    symbol = symbol_repo.get(cancel_data.symbol_id)
    if not symbol:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol with ID {cancel_data.symbol_id} not found",
        )

    exchange_slug = _get_exchange_slug(db, api.exch_id)
    if symbol.exchange.slug.upper() != exchange_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Symbol belongs to exchange '{symbol.exchange.slug}', but API key is for '{exchange_slug}'",
        )

    api_service = APIService()
    try:
        client = api_service.get_exchange_client_from_api_info(exchange_slug, api)
        cancel_result = client.cancel_order(
            symbol=symbol.name,
            order_id=cancel_data.order_id,
            client_order_id=cancel_data.client_order_id,
        )
        return OrderResponse(
            order_id=cancel_result.get("order_id", ""),
            symbol=cancel_result.get("symbol", symbol.name),
            side=cancel_result.get("side", ""),
            type=cancel_result.get("type", ""),
            quantity=cancel_result.get("quantity", "0"),
            price=cancel_result.get("price", "-1"),
            status=cancel_result.get("status", "CANCEL"),
            client_order_id=cancel_result.get("client_order_id", cancel_data.client_order_id or ""),
            account_id=cancel_data.account_id,
            account_name=acct_name,
            created_at=cancel_result.get("created_at", 0),
            updated_at=cancel_result.get("updated_at", 0),
        )
    except NetworkError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error while canceling order: {e.message}",
        )
    except ExchangeError as e:
        error_detail = f"Exchange error: {e.message}"
        if e.error_code is not None:
            error_detail += f" (Error code: {e.error_code})"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel order: {str(e)}",
        )
