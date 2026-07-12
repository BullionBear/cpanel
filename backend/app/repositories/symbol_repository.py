"""Symbol repository for database operations."""
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import and_

from app.models import (
    RefExchange,
    RefProduct,
    RefSymbol,
    RefSymbolDerivative,
    RefSymbolFilter,
    RefToken,
    RefUniversalSymbol,
)


class SymbolRepository:
    """Repository for RefSymbol model operations."""

    def __init__(self, db: Session):
        """Initialize repository with database session."""
        self.db = db

    def _base_query(self):
        return self.db.query(RefSymbol).options(
            joinedload(RefSymbol.exchange),
            joinedload(RefSymbol.product),
            joinedload(RefSymbol.base_token),
            joinedload(RefSymbol.quote_token),
            joinedload(RefSymbol.universal),
            joinedload(RefSymbol.derivative),
            selectinload(RefSymbol.filters),
        )

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
            List of RefSymbol objects with loaded relationships
            (exchange, product, base_token, quote_token, universal, derivative, filters)
        """
        query = self._base_query()

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
            query = query.join(RefUniversalSymbol, RefSymbol.universal_id == RefUniversalSymbol.id).filter(
                RefUniversalSymbol.universal_ticker == universal_ticker
            )
        if name is not None:
            query = query.filter(RefSymbol.name.ilike(f"%{name}%"))

        return query.offset(skip).limit(limit).all()

    def get(self, symbol_id: int) -> Optional[RefSymbol]:
        """Get symbol by ID with all relationships loaded."""
        return self._base_query().filter(RefSymbol.id == symbol_id).first()

    def list_by_universal_ticker(self, universal_ticker: str) -> List[RefSymbol]:
        """List all exchange symbols mapped to the given universal ticker."""
        return (
            self._base_query()
            .join(RefUniversalSymbol, RefSymbol.universal_id == RefUniversalSymbol.id)
            .filter(RefUniversalSymbol.universal_ticker == universal_ticker)
            .all()
        )

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
        return self._base_query().filter(and_(*filters)).first()

    def get_or_create_universal(
        self,
        universal_ticker: str,
        base_id: int,
        quote_id: int,
        instrument_type: str,
        expiry_date: Optional[date] = None,
    ) -> RefUniversalSymbol:
        """Get the canonical instrument row by ticker, creating it if missing.

        Does not commit; the row is flushed so its id is available to the caller.
        """
        universal = (
            self.db.query(RefUniversalSymbol)
            .filter(RefUniversalSymbol.universal_ticker == universal_ticker)
            .first()
        )
        if universal:
            return universal
        universal = RefUniversalSymbol(
            universal_ticker=universal_ticker,
            base_id=base_id,
            quote_id=quote_id,
            instrument_type=instrument_type,
            expiry_date=expiry_date,
        )
        self.db.add(universal)
        self.db.flush()
        return universal

    def create(
        self,
        name: str,
        exchange_id: int,
        product_id: int,
        base_id: int,
        quote_id: int,
        price_tick_size: str,
        status: str,
        universal_id: int,
        listing_timestamp: Optional[datetime] = None,
        raw_meta: Optional[Dict[str, Any]] = None,
        filters: Optional[List[Dict[str, Any]]] = None,
        derivative: Optional[Dict[str, Any]] = None,
    ) -> RefSymbol:
        """Create a symbol with its filter and derivative rows in one transaction.

        Args:
            filters: list of dicts with filter_type/unit_type/step_size/min_value/max_value
            derivative: dict with margin_id/expiry_timestamp/settlement_type (non-spot only)

        Returns:
            Created RefSymbol with relationships loaded.
        """
        symbol = RefSymbol(
            name=name,
            exchange_id=exchange_id,
            product_id=product_id,
            base_id=base_id,
            quote_id=quote_id,
            price_tick_size=price_tick_size,
            status=status,
            listing_timestamp=listing_timestamp,
            universal_id=universal_id,
            raw_meta=raw_meta,
        )
        self.db.add(symbol)
        self.db.flush()

        for f in filters or []:
            self.db.add(RefSymbolFilter(
                symbol_id=symbol.id,
                filter_type=f["filter_type"],
                unit_type=f["unit_type"],
                step_size=f.get("step_size"),
                min_value=f.get("min_value"),
                max_value=f.get("max_value"),
            ))

        if derivative is not None:
            self.db.add(RefSymbolDerivative(
                symbol_id=symbol.id,
                margin_id=derivative["margin_id"],
                expiry_timestamp=derivative.get("expiry_timestamp"),
                settlement_type=derivative.get("settlement_type"),
            ))

        self.db.commit()
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
        Delete symbol by ID (filter/derivative rows cascade via FK).

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
