"""Symbol repository for database operations."""
from typing import Optional, List
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_

from app.models import Symbol, Exchange, Product, Token


class SymbolRepository:
    """Repository for Symbol model operations."""

    def __init__(self, db: Session):
        """Initialize repository with database session."""
        self.db = db

    def query(
        self,
        exchange_id: Optional[int] = None,
        exchange_slug: Optional[str] = None,
        product_id: Optional[int] = None,
        product_slug: Optional[str] = None,
        base_id: Optional[int] = None,
        base_slug: Optional[str] = None,
        quote_id: Optional[int] = None,
        quote_slug: Optional[str] = None,
        universal_ticker: Optional[str] = None,
        name: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Symbol]:
        """
        Query symbols with optional filters and eager load relationships.
        
        Args:
            exchange_id: Filter by exchange ID
            exchange_slug: Filter by exchange slug
            product_id: Filter by product ID
            product_slug: Filter by product slug
            base_id: Filter by base token ID
            base_slug: Filter by base token slug
            quote_id: Filter by quote token ID
            quote_slug: Filter by quote token slug
            universal_ticker: Filter by universal ticker (exact match)
            name: Filter by symbol name (partial match)
            skip: Number of records to skip (pagination)
            limit: Maximum number of records to return (pagination)
            
        Returns:
            List of Symbol objects with loaded relationships (exchange, product, base_token, quote_token)
        """
        # Start with base query and eager load all relationships
        query = self.db.query(Symbol).options(
            joinedload(Symbol.exchange),
            joinedload(Symbol.product),
            joinedload(Symbol.base_token),
            joinedload(Symbol.quote_token)
        )
        
        # Apply filters
        if exchange_id is not None:
            query = query.filter(Symbol.exchange_id == exchange_id)
        
        if exchange_slug is not None:
            query = query.join(Exchange).filter(Exchange.slug == exchange_slug)
        
        if product_id is not None:
            query = query.filter(Symbol.product_id == product_id)
        
        if product_slug is not None:
            query = query.join(Product).filter(Product.slug == product_slug)
        
        if base_id is not None:
            query = query.filter(Symbol.base_id == base_id)
        
        if base_slug is not None:
            # Join with Token table for base token
            query = query.join(Token, Symbol.base_id == Token.id).filter(Token.slug == base_slug)
        
        if quote_id is not None:
            query = query.filter(Symbol.quote_id == quote_id)
        
        if quote_slug is not None:
            # Join with Token table for quote token
            query = query.join(Token, Symbol.quote_id == Token.id).filter(Token.slug == quote_slug)
        
        if universal_ticker is not None:
            query = query.filter(Symbol.universal_ticker == universal_ticker)
        
        if name is not None:
            query = query.filter(Symbol.name.ilike(f"%{name}%"))
        
        return query.offset(skip).limit(limit).all()

    def get(self, symbol_id: int) -> Optional[Symbol]:
        """
        Get symbol by ID with all relationships loaded.
        
        Args:
            symbol_id: Symbol ID
            
        Returns:
            Symbol object with loaded relationships if found, None otherwise
        """
        return self.db.query(Symbol).options(
            joinedload(Symbol.exchange),
            joinedload(Symbol.product),
            joinedload(Symbol.base_token),
            joinedload(Symbol.quote_token)
        ).filter(Symbol.id == symbol_id).first()

    def get_by_universal_ticker(self, universal_ticker: str) -> Optional[Symbol]:
        """
        Get symbol by universal ticker with all relationships loaded.
        
        Args:
            universal_ticker: Universal ticker string
            
        Returns:
            Symbol object with loaded relationships if found, None otherwise
        """
        return self.db.query(Symbol).options(
            joinedload(Symbol.exchange),
            joinedload(Symbol.product),
            joinedload(Symbol.base_token),
            joinedload(Symbol.quote_token)
        ).filter(Symbol.universal_ticker == universal_ticker).first()

    def get_by_exchange_and_name(
        self,
        exchange_id: int,
        name: str
    ) -> Optional[Symbol]:
        """
        Get symbol by exchange ID and name with all relationships loaded.
        
        Args:
            exchange_id: Exchange ID
            name: Symbol name
            
        Returns:
            Symbol object with loaded relationships if found, None otherwise
        """
        return self.db.query(Symbol).options(
            joinedload(Symbol.exchange),
            joinedload(Symbol.product),
            joinedload(Symbol.base_token),
            joinedload(Symbol.quote_token)
        ).filter(
            and_(Symbol.exchange_id == exchange_id, Symbol.name == name)
        ).first()

    def create(
        self,
        name: str,
        exchange_id: int,
        product_id: int,
        base_id: int,
        quote_id: int,
        price_precision: int,
        size_precision: int,
        universal_ticker: str,
    ) -> Symbol:
        """
        Create a new symbol.
        
        Args:
            name: Symbol name
            exchange_id: Exchange ID
            product_id: Product ID
            base_id: Base token ID
            quote_id: Quote token ID
            price_precision: Price precision (decimal places)
            size_precision: Size precision (decimal places)
            universal_ticker: Universal ticker string
            
        Returns:
            Created Symbol object
        """
        symbol = Symbol(
            name=name,
            exchange_id=exchange_id,
            product_id=product_id,
            base_id=base_id,
            quote_id=quote_id,
            price_precision=price_precision,
            size_precision=size_precision,
            universal_ticker=universal_ticker,
        )
        self.db.add(symbol)
        self.db.commit()
        self.db.refresh(symbol)
        # Reload with relationships
        return self.get(symbol.id) or symbol

    def update(self, symbol: Symbol) -> Symbol:
        """
        Update symbol.
        
        Args:
            symbol: Symbol object to update
            
        Returns:
            Updated Symbol object
        """
        self.db.commit()
        self.db.refresh(symbol)
        # Reload with relationships
        return self.get(symbol.id) or symbol

    def delete(self, symbol_id: int) -> bool:
        """
        Delete symbol by ID.
        
        Args:
            symbol_id: Symbol ID
            
        Returns:
            True if deleted, False if not found
        """
        symbol = self.get(symbol_id)
        if symbol:
            self.db.delete(symbol)
            self.db.commit()
            return True
        return False
