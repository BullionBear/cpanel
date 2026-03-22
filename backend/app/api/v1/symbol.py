"""Symbol router."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.repositories.symbol_repository import SymbolRepository
from app.models import RefExchange, RefProduct, RefToken
from app.services.api_service import APIService, NetworkError, ExchangeError

router = APIRouter(prefix="/symbol", tags=["symbols"])


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


class SymbolResponse(BaseModel):
    """Symbol response model."""
    id: int
    name: str
    exchange: ExchangeInfo
    product: ProductInfo
    base_token: TokenInfo
    quote_token: TokenInfo
    price_precision: int
    size_precision: int
    universal_ticker: str

    class Config:
        from_attributes = True


@router.get("/universal_ticker/{ticker_name}", response_model=SymbolResponse)
async def get_symbol_by_universal_ticker(
    ticker_name: str,
    db: Session = Depends(get_db),
):
    """
    Get a symbol by its universal ticker.
    
    Returns the symbol with its exchange, product, base token, and quote token information.
    """
    symbol_repo = SymbolRepository(db)
    
    symbol = symbol_repo.get_by_universal_ticker(ticker_name)
    if not symbol:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol with universal ticker '{ticker_name}' not found"
        )
    
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
        price_precision=symbol.price_precision,
        size_precision=symbol.size_precision,
        universal_ticker=symbol.universal_ticker
    )


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
        
        # Convert to response models
        return [
            SymbolResponse(
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
                price_precision=symbol.price_precision,
                size_precision=symbol.size_precision,
                universal_ticker=symbol.universal_ticker
            )
            for symbol in symbols
        ]
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


@router.post("", response_model=SymbolResponse, status_code=status.HTTP_201_CREATED)
async def create_symbol(
    symbol_data: SymbolCreate,
    db: Session = Depends(get_db),
):
    """
    Create a new symbol.
    
    Validates the symbol exists on the exchange and extracts base/quote tokens.
    Checks if tokens exist in the database before creating the symbol.
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
    
    # Check if symbol already exists
    existing_symbol = symbol_repo.get_by_exchange_and_name(
        exchange_id=symbol_data.exchange_id,
        name=symbol_data.symbol
    )
    if existing_symbol:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Symbol {symbol_data.symbol} already exists for exchange {exchange.name}"
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
    
    # Try to find token by name first (most reliable), then by slug
    base_token = db.query(RefToken).filter(RefToken.name == base_asset).first()
    if not base_token:
        # Fallback to slug lookup
        base_token = db.query(RefToken).filter(RefToken.slug == base_asset.lower()).first()
    
    if not base_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Base token '{base_asset}' not found in database. Please add it first."
        )
    
    # Try to find token by name first (most reliable), then by slug
    quote_token = db.query(RefToken).filter(RefToken.name == quote_asset).first()
    if not quote_token:
        # Fallback to slug lookup
        quote_token = db.query(RefToken).filter(RefToken.slug == quote_asset.lower()).first()
    
    if not quote_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quote token '{quote_asset}' not found in database. Please add it first."
        )
    
    # Generate universal ticker: {exchange_slug}_{product_slug}_{symbol}
    universal_ticker = f"{exchange.slug.upper()}_{product.slug.upper()}_{symbol_data.symbol}"
    
    # Check if universal ticker already exists
    existing_by_ticker = symbol_repo.get_by_universal_ticker(universal_ticker)
    if existing_by_ticker:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Symbol with universal ticker {universal_ticker} already exists"
        )
    
    # Create symbol
    try:
        new_symbol = symbol_repo.create(
            name=symbol_data.symbol,
            exchange_id=symbol_data.exchange_id,
            product_id=symbol_data.product_id,
            base_id=base_token.id,
            quote_id=quote_token.id,
            price_precision=symbol_info.get('price_precision', 2),
            size_precision=symbol_info.get('size_precision', 8),
            universal_ticker=universal_ticker
        )
        
        # Return response with relationships
        return SymbolResponse(
            id=new_symbol.id,
            name=new_symbol.name,
            exchange=ExchangeInfo(
                id=new_symbol.exchange.id,
                name=new_symbol.exchange.name,
                slug=new_symbol.exchange.slug
            ),
            product=ProductInfo(
                id=new_symbol.product.id,
                name=new_symbol.product.name,
                slug=new_symbol.product.slug
            ),
            base_token=TokenInfo(
                id=new_symbol.base_token.id,
                name=new_symbol.base_token.name,
                slug=new_symbol.base_token.slug
            ),
            quote_token=TokenInfo(
                id=new_symbol.quote_token.id,
                name=new_symbol.quote_token.name,
                slug=new_symbol.quote_token.slug
            ),
            price_precision=new_symbol.price_precision,
            size_precision=new_symbol.size_precision,
            universal_ticker=new_symbol.universal_ticker
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create symbol: {str(e)}"
        )
