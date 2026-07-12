"""Symbol router."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.repositories.symbol_repository import SymbolRepository
from app.models import RefExchange, RefProduct, RefToken
from app.services.api_service import APIService, NetworkError, ExchangeError

router = APIRouter(prefix="/symbol", tags=["symbols"])

# product slug -> canonical instrument type on ref_universal_symbol
PRODUCT_TO_INSTRUMENT_TYPE = {
    "SPOT": "spot",
    "UMARGIN": "linear_perp",
    "CMARGIN": "inverse_perp",
}

# instrument type -> universal ticker suffix
INSTRUMENT_TYPE_TICKER_SUFFIX = {
    "spot": "SPOT",
    "linear_perp": "PERP",
    "inverse_perp": "PERP.INV",
}


# Pydantic models for response
class ExchangeInfo(BaseModel):
    """Exchange information model."""
    id: int
    name: str
    slug: str

    class Config:
        from_attributes = True


class ProductInfo(BaseModel):
    """Product information model."""
    id: int
    name: str
    slug: str

    class Config:
        from_attributes = True


class TokenInfo(BaseModel):
    """Token information model."""
    id: int
    name: str
    slug: str

    class Config:
        from_attributes = True


class SymbolFilterInfo(BaseModel):
    """Symbol filter row (long format)."""
    filter_type: str
    unit_type: str
    step_size: Optional[str] = None
    min_value: Optional[str] = None
    max_value: Optional[str] = None


class SymbolResponse(BaseModel):
    """Symbol response model."""
    id: int
    name: str
    exchange: ExchangeInfo
    product: ProductInfo
    base_token: TokenInfo
    quote_token: TokenInfo
    price_tick_size: str
    qty_step: Optional[str] = None
    status: str
    universal_ticker: str
    filters: List[SymbolFilterInfo] = []
    # Derived from tick/step sizes, for display formatting
    price_precision: int
    size_precision: int

    class Config:
        from_attributes = True


def _decimal_str(value) -> str:
    """Render a numeric value without exponent notation or trailing zeros."""
    d = Decimal(str(value))
    if d == d.to_integral_value():
        return str(d.quantize(Decimal(1)))
    return format(d.normalize(), "f")


def _precision_from_step(step) -> int:
    """Number of decimal places implied by a step size (e.g. 0.001 -> 3, 0.5 -> 1)."""
    exponent = Decimal(str(step)).normalize().as_tuple().exponent
    return max(0, -int(exponent))


def _to_response(symbol) -> SymbolResponse:
    """Serialize a RefSymbol (with loaded relationships) into a SymbolResponse."""
    qty_step = None
    filter_rows: List[SymbolFilterInfo] = []
    for f in symbol.filters:
        filter_rows.append(SymbolFilterInfo(
            filter_type=f.filter_type,
            unit_type=f.unit_type,
            step_size=_decimal_str(f.step_size) if f.step_size is not None else None,
            min_value=_decimal_str(f.min_value) if f.min_value is not None else None,
            max_value=_decimal_str(f.max_value) if f.max_value is not None else None,
        ))
    # Prefer the base-unit qty step for display; fall back to contract unit (inverse).
    for unit in ("base", "contract"):
        step = next(
            (f.step_size for f in symbol.filters
             if f.filter_type == "QTY_STEP" and f.unit_type == unit and f.step_size is not None),
            None,
        )
        if step is not None:
            qty_step = _decimal_str(step)
            break

    return SymbolResponse(
        id=symbol.id,
        name=symbol.name,
        exchange=ExchangeInfo(
            id=symbol.exchange.id,
            name=symbol.exchange.name,
            slug=symbol.exchange.slug
        ),
        product=ProductInfo(
            id=symbol.product.id,
            name=symbol.product.name,
            slug=symbol.product.slug
        ),
        base_token=TokenInfo(
            id=symbol.base_token.id,
            name=symbol.base_token.name,
            slug=symbol.base_token.slug
        ),
        quote_token=TokenInfo(
            id=symbol.quote_token.id,
            name=symbol.quote_token.name,
            slug=symbol.quote_token.slug
        ),
        price_tick_size=_decimal_str(symbol.price_tick_size),
        qty_step=qty_step,
        status=symbol.status,
        universal_ticker=symbol.universal.universal_ticker,
        filters=filter_rows,
        price_precision=_precision_from_step(symbol.price_tick_size),
        size_precision=_precision_from_step(qty_step) if qty_step is not None else 0,
    )


@router.get("/universal_ticker/{ticker_name}", response_model=List[SymbolResponse])
async def get_symbols_by_universal_ticker(
    ticker_name: str,
    db: Session = Depends(get_db),
):
    """
    Get all exchange symbols mapped to a universal ticker.

    The same canonical instrument (e.g. BTC/USDT.SPOT) can be listed on multiple
    exchanges, so this returns a list.
    """
    symbol_repo = SymbolRepository(db)

    symbols = symbol_repo.list_by_universal_ticker(ticker_name)
    if not symbols:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No symbols found for universal ticker '{ticker_name}'"
        )

    return [_to_response(symbol) for symbol in symbols]


@router.get("", response_model=List[SymbolResponse])
async def list_symbols(
    exchange_id: Optional[int] = Query(None, description="Filter by exchange ID"),
    exchange_slug: Optional[str] = Query(None, description="Filter by exchange slug"),
    product_id: Optional[int] = Query(None, description="Filter by product ID"),
    product_slug: Optional[str] = Query(None, description="Filter by product slug"),
    base_id: Optional[int] = Query(None, description="Filter by base token ID"),
    base_slug: Optional[str] = Query(None, description="Filter by base token slug"),
    quote_id: Optional[int] = Query(None, description="Filter by quote token ID"),
    quote_slug: Optional[str] = Query(None, description="Filter by quote token slug"),
    universal_ticker: Optional[str] = Query(None, description="Filter by universal ticker"),
    name: Optional[str] = Query(None, description="Filter by symbol name (partial match)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
    db: Session = Depends(get_db),
):
    """
    List all symbols with optional filters.

    Returns symbols with their exchange, product, base token, and quote token information.
    """
    symbol_repo = SymbolRepository(db)

    try:
        symbols = symbol_repo.query(
            exchange_id=exchange_id,
            exchange_slug=exchange_slug,
            product_id=product_id,
            product_slug=product_slug,
            base_id=base_id,
            base_slug=base_slug,
            quote_id=quote_id,
            quote_slug=quote_slug,
            universal_ticker=universal_ticker,
            name=name,
            skip=skip,
            limit=limit,
        )

        return [_to_response(symbol) for symbol in symbols]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch symbols: {str(e)}"
        )


# Pydantic models for request
class SymbolCreate(BaseModel):
    """Symbol creation request model."""
    exchange_id: int
    product_id: int
    symbol: str  # Symbol name like "BTCUSDT"


def _find_token(db: Session, asset: str) -> Optional[RefToken]:
    """Find a ref_token by name first (most reliable), then by slug."""
    token = db.query(RefToken).filter(RefToken.name == asset).first()
    if not token:
        token = db.query(RefToken).filter(RefToken.slug == asset.lower()).first()
    return token


def _epoch_ms_to_datetime(value) -> Optional[datetime]:
    """Convert an exchange epoch-milliseconds value to an aware datetime."""
    if value in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


@router.post("", response_model=SymbolResponse, status_code=status.HTTP_201_CREATED)
async def create_symbol(
    symbol_data: SymbolCreate,
    db: Session = Depends(get_db),
):
    """
    Create a new symbol.

    Validates the symbol exists on the exchange, extracts base/quote tokens,
    and persists core metadata plus filter and derivative rows. The symbol is
    linked to a canonical ref_universal_symbol row (created on demand).
    """
    symbol_repo = SymbolRepository(db)

    # Get exchange and product
    exchange = db.query(RefExchange).filter(RefExchange.id == symbol_data.exchange_id).first()
    if not exchange:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exchange with ID {symbol_data.exchange_id} not found"
        )

    product = db.query(RefProduct).filter(RefProduct.id == symbol_data.product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {symbol_data.product_id} not found"
        )

    instrument_type = PRODUCT_TO_INSTRUMENT_TYPE.get(product.slug.upper())
    if instrument_type is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product '{product.slug}' is not supported for symbol creation"
        )

    # Check if symbol already exists for the same exchange + product
    existing_symbol = symbol_repo.get_by_exchange_and_name(
        exchange_id=symbol_data.exchange_id,
        name=symbol_data.symbol,
        product_id=symbol_data.product_id,
    )
    if existing_symbol:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Symbol {symbol_data.symbol} already exists for exchange {exchange.name} / product {product.name}"
        )

    # Get symbol info from exchange API
    api_service = APIService()
    try:
        client = api_service.get_exchange_client(exchange=exchange.slug.upper(), api_key="", api_secret="")  # type: ignore
        symbol_info = client.get_symbol_info(symbol_data.symbol, product=product.slug.upper())
    except NetworkError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error while fetching symbol info: {e.message}"
        )
    except ExchangeError as e:
        error_detail = f"Exchange error: {e.message}"
        if e.error_code is not None:
            error_detail += f" (Error code: {e.error_code})"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail
        )

    # Check if symbol exists
    if not symbol_info.get('exists', False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Symbol {symbol_data.symbol} does not exist on {exchange.name}"
        )

    # Get base and quote tokens from database
    base_asset = symbol_info.get('base_asset', '').upper()
    quote_asset = symbol_info.get('quote_asset', '').upper()

    base_token = _find_token(db, base_asset)
    if not base_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Base token '{base_asset}' not found in database. Please add it first."
        )

    quote_token = _find_token(db, quote_asset)
    if not quote_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quote token '{quote_asset}' not found in database. Please add it first."
        )

    # Derivative attributes (non-spot only)
    derivative: Optional[Dict[str, Any]] = None
    if instrument_type != "spot":
        margin_asset = (symbol_info.get('margin_asset') or '').upper()
        if not margin_asset:
            # linear: margined in quote; inverse: margined in base
            margin_asset = quote_asset if instrument_type == "linear_perp" else base_asset
        margin_token = _find_token(db, margin_asset)
        if not margin_token:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Margin token '{margin_asset}' not found in database. Please add it first."
            )
        derivative = {
            "margin_id": margin_token.id,
            "expiry_timestamp": _epoch_ms_to_datetime(symbol_info.get('expiry_timestamp')),
            "settlement_type": symbol_info.get('settlement_type'),
        }

    # Canonical instrument: {BASE}/{QUOTE}.{SUFFIX}, shared across exchanges
    ticker_suffix = INSTRUMENT_TYPE_TICKER_SUFFIX[instrument_type]
    universal_ticker = f"{base_token.name.upper()}/{quote_token.name.upper()}.{ticker_suffix}"

    # Create symbol (core + filters + derivative in one transaction)
    try:
        universal = symbol_repo.get_or_create_universal(
            universal_ticker=universal_ticker,
            base_id=base_token.id,
            quote_id=quote_token.id,
            instrument_type=instrument_type,
        )
        new_symbol = symbol_repo.create(
            name=symbol_data.symbol,
            exchange_id=symbol_data.exchange_id,
            product_id=symbol_data.product_id,
            base_id=base_token.id,
            quote_id=quote_token.id,
            price_tick_size=symbol_info.get('price_tick_size', '0.00000001'),
            status=symbol_info.get('status', 'trading'),
            listing_timestamp=_epoch_ms_to_datetime(symbol_info.get('listing_timestamp')),
            universal_id=universal.id,
            raw_meta=symbol_info.get('raw_meta'),
            filters=symbol_info.get('filters'),
            derivative=derivative,
        )

        return _to_response(new_symbol)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create symbol: {str(e)}"
        )


@router.delete("/{symbol_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_symbol(
    symbol_id: int,
    db: Session = Depends(get_db),
):
    """Delete a symbol by ID (filter/derivative rows cascade)."""
    symbol_repo = SymbolRepository(db)
    deleted = symbol_repo.delete(symbol_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol with ID {symbol_id} not found"
        )
