"""Symbol repository for database operations."""
from typing import Optional, List
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_

from app.models import RefSymbol, RefExchange, RefProduct, RefToken


class SymbolRepository:
    """Repository for RefSymbol model operations."""

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
    ) -> List[RefSymbol]:
        """
        Query symbols with optional filters and eager load relationships.

        Returns:
            List of RefSymbol objects with loaded relationships (exchange, product, base_token, quote_token)
        """
        query = self.db.query(RefSymbol).options(
            joinedload(RefSymbol.exchange),
            joinedload(RefSymbol.product),
            joinedload(RefSymbol.base_token),
            joinedload(RefSymbol.quote_token)
        )

        if exchange_id is not None:
            query = query.filter(RefSymbol.exchange_id == exchange_id)
        if exchange_slug is not None:
            query = query.join(RefExchange).filter(RefExchange.slug == exchange_slug)
        if product_id is not None:
            query = query.filter(RefSymbol.product_id == product_id)
        if product_slug is not None:
            query = query.join(RefProduct).filter(RefProduct.slug == product_slug)
        if base_id is not None:
            query = query.filter(RefSymbol.base_id == base_id)
        if base_slug is not None:
            query = query.join(RefToken, RefSymbol.base_id == RefToken.id).filter(RefToken.slug == base_slug)
        if quote_id is not None:
            query = query.filter(RefSymbol.quote_id == quote_id)
        if quote_slug is not None:
            query = query.join(RefToken, RefSymbol.quote_id == RefToken.id).filter(RefToken.slug == quote_slug)
        if universal_ticker is not None:
            query = query.filter(RefSymbol.universal_ticker == universal_ticker)
        if name is not None:
            query = query.filter(RefSymbol.name.ilike(f"%{name}%"))
        
        return query.offset(skip).limit(limit).all()

    def get(self, symbol_id: int) -> Optional[RefSymbol]:
        """Get symbol by ID with all relationships loaded."""
        return self.db.query(RefSymbol).options(
            joinedload(RefSymbol.exchange),
            joinedload(RefSymbol.product),
            joinedload(RefSymbol.base_token),
            joinedload(RefSymbol.quote_token)
        ).filter(RefSymbol.id == symbol_id).first()

    def get_by_universal_ticker(self, universal_ticker: str) -> Optional[RefSymbol]:
        """Get symbol by universal ticker with all relationships loaded."""
        return self.db.query(RefSymbol).options(
            joinedload(RefSymbol.exchange),
            joinedload(RefSymbol.product),
            joinedload(RefSymbol.base_token),
            joinedload(RefSymbol.quote_token)
        ).filter(RefSymbol.universal_ticker == universal_ticker).first()

    def get_by_exchange_and_name(
        self,
        exchange_id: int,
        name: str,
        product_id: Optional[int] = None,
    ) -> Optional[RefSymbol]:
        """Get symbol by exchange ID, name, and optionally product ID."""
        filters = [RefSymbol.exchange_id == exchange_id, RefSymbol.name == name]
        if product_id is not None:
            filters.append(RefSymbol.product_id == product_id)
        return self.db.query(RefSymbol).options(
            joinedload(RefSymbol.exchange),
            joinedload(RefSymbol.product),
            joinedload(RefSymbol.base_token),
            joinedload(RefSymbol.quote_token)
        ).filter(and_(*filters)).first()

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
    ) -> RefSymbol:
        """Create a new symbol. Returns created RefSymbol."""
        symbol = RefSymbol(
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

    def update(self, symbol: RefSymbol) -> RefSymbol:
        """Update symbol. Returns updated RefSymbol."""
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
