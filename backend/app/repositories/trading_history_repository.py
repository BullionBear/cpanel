"""Trading history repository: APIs for trading_history and trading_history_cursor tables.

Each method operates on a single table; no API spans both tables.
"""
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models import TradingHistory, TradingHistoryCursor


class TradingHistoryRepository:
    """Repository for TradingHistory and TradingHistoryCursor model operations."""

    def __init__(self, db: Session):
        """Initialize repository with database session."""
        self.db = db

    # --- trading_history (TradingHistory) ---

    def get_trading_history(self, history_id: int) -> Optional[TradingHistory]:
        """Get a single trading history record by ID."""
        return self.db.query(TradingHistory).filter(TradingHistory.id == history_id).first()

    def query_trading_history(
        self,
        acct_id: Optional[int] = None,
        product_id: Optional[int] = None,
        exch_id: Optional[int] = None,
        side: Optional[str] = None,
        symbol: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[TradingHistory]:
        """
        Query trading_history with optional filters.

        Args:
            acct_id: Filter by account ID
            product_id: Filter by product ID (ref_product)
            exch_id: Filter by exchange ID (ref_exchange)
            side: Filter by side ('Buy' or 'Sell')
            symbol: Filter by symbol (exact match)
            since: Filter exec_time >= since (inclusive)
            until: Filter exec_time <= until (inclusive)
            skip: Pagination offset
            limit: Max records to return

        Returns:
            List of TradingHistory ordered by exec_time descending
        """
        query = self.db.query(TradingHistory)
        if acct_id is not None:
            query = query.filter(TradingHistory.acct_id == acct_id)
        if product_id is not None:
            query = query.filter(TradingHistory.product_id == product_id)
        if exch_id is not None:
            query = query.filter(TradingHistory.exch_id == exch_id)
        if side is not None:
            query = query.filter(TradingHistory.side == side)
        if symbol is not None:
            query = query.filter(TradingHistory.symbol == symbol)
        if since is not None:
            query = query.filter(TradingHistory.exec_time >= since)
        if until is not None:
            query = query.filter(TradingHistory.exec_time <= until)
        return query.order_by(TradingHistory.exec_time.desc()).offset(skip).limit(limit).all()

    def create_trading_history(
        self,
        exch_id: int,
        exch_tid: int,
        acct_id: int,
        order_id: int,
        symbol: str,
        product_id: int,
        side: str,
        exec_px: str,
        exec_qty: str,
        exec_fee: str,
        fee_currency: str,
        is_maker: bool,
        exec_time: datetime,
    ) -> TradingHistory:
        """Insert a single trading history record."""
        row = TradingHistory(
            exch_id=exch_id,
            exch_tid=exch_tid,
            acct_id=acct_id,
            order_id=order_id,
            symbol=symbol,
            product_id=product_id,
            side=side,
            exec_px=exec_px,
            exec_qty=exec_qty,
            exec_fee=exec_fee,
            fee_currency=fee_currency,
            is_maker=is_maker,
            exec_time=exec_time,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def create_trading_history_bulk(self, rows: List[TradingHistory]) -> None:
        """Bulk insert trading history records. Caller builds TradingHistory instances."""
        if not rows:
            return
        self.db.add_all(rows)
        self.db.commit()

    def delete_trading_history(self, history_id: int) -> bool:
        """Delete a trading history record by ID."""
        row = self.get_trading_history(history_id)
        if row:
            self.db.delete(row)
            self.db.commit()
            return True
        return False

    # --- trading_history_cursor (TradingHistoryCursor) ---

    def get_cursor(self, acct_id: int, symbol_id: int, product_id: int) -> Optional[TradingHistoryCursor]:
        """Get cursor for (acct_id, symbol_id, product_id)."""
        return (
            self.db.query(TradingHistoryCursor)
            .filter(
                and_(
                    TradingHistoryCursor.acct_id == acct_id,
                    TradingHistoryCursor.symbol_id == symbol_id,
                    TradingHistoryCursor.product_id == product_id,
                )
            )
            .first()
        )

    def get_cursor_by_id(self, cursor_id: int) -> Optional[TradingHistoryCursor]:
        """Get trading_history_cursor by primary key."""
        return self.db.query(TradingHistoryCursor).filter(TradingHistoryCursor.id == cursor_id).first()

    def query_cursors(
        self,
        acct_id: Optional[int] = None,
        symbol_id: Optional[int] = None,
        product_id: Optional[int] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[TradingHistoryCursor]:
        """Query trading_history_cursor with optional filters."""
        query = self.db.query(TradingHistoryCursor)
        if acct_id is not None:
            query = query.filter(TradingHistoryCursor.acct_id == acct_id)
        if symbol_id is not None:
            query = query.filter(TradingHistoryCursor.symbol_id == symbol_id)
        if product_id is not None:
            query = query.filter(TradingHistoryCursor.product_id == product_id)
        return query.offset(skip).limit(limit).all()

    def create_cursor(
        self,
        acct_id: int,
        symbol_id: int,
        product_id: int,
        cursor_start: Optional[int] = None,
        cursor_end: Optional[int] = None,
    ) -> TradingHistoryCursor:
        """Create a new cursor row for (acct_id, symbol_id, product_id)."""
        row = TradingHistoryCursor(
            acct_id=acct_id,
            symbol_id=symbol_id,
            product_id=product_id,
            cursor_start=cursor_start,
            cursor_end=cursor_end,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update_cursor(
        self,
        cursor: TradingHistoryCursor,
        cursor_start: Optional[int] = None,
        cursor_end: Optional[int] = None,
    ) -> TradingHistoryCursor:
        """Update cursor_start and/or cursor_end on an existing cursor. Commits and refreshes."""
        if cursor_start is not None:
            cursor.cursor_start = cursor_start
        if cursor_end is not None:
            cursor.cursor_end = cursor_end
        self.db.commit()
        self.db.refresh(cursor)
        return cursor

    def delete_cursor(self, cursor_id: int) -> bool:
        """Delete a trading_history_cursor by ID."""
        row = self.get_cursor_by_id(cursor_id)
        if row:
            self.db.delete(row)
            self.db.commit()
            return True
        return False
