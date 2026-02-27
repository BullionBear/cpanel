"""MyTrade router (v2): query trading history from DB or exchange."""
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

logger = logging.getLogger(__name__)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User, RefExchange, RefProduct
from app.middleware.auth import get_current_user
from app.repositories.account_repository import AccountRepository
from app.repositories.api_repository import APIRepository
from app.repositories.trading_history_repository import TradingHistoryRepository
from app.services.api_service import APIService, NetworkError, ExchangeError

router = APIRouter(prefix="/mytrades", tags=["mytrades-v2"])

API_TYPE_PRIORITY = {"HMAC": 0, "ED25519": 1, "RSA": 2}


class TradeItem(BaseModel):
    exch_id: int
    exch_tid: int
    acct_id: int
    order_id: int
    symbol: str
    product_id: int
    side: str
    exec_px: str
    exec_qty: str
    exec_fee: str
    fee_currency: str
    is_maker: bool
    exec_time: datetime


SIDE_MAP = {"buy": "Buy", "sell": "Sell"}


class CursorCreatePayload(BaseModel):
    acct_id: int
    product_id: int


class CursorUpdateFields(BaseModel):
    cursor_end: Optional[int] = None


class CursorUpdatePayload(BaseModel):
    acct_id: int
    product_id: int
    update: CursorUpdateFields


class CursorResponse(BaseModel):
    acct_id: int
    product_id: int
    cursor_start: int
    cursor_end: int


def _start_of_month_utc_ms() -> int:
    """Start of current month in UTC, as epoch milliseconds."""
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp() * 1000)


@router.post("/cursor", response_model=CursorResponse, status_code=status.HTTP_201_CREATED)
async def create_cursor(
    payload: CursorCreatePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create or reset cursor for (acct_id, product_id) with cursor_start = cursor_end = start of current month (UTC)."""
    acct_repo = AccountRepository(db)
    account = acct_repo.get_by_user_and_id(current_user.id, payload.acct_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found or does not belong to user",
        )

    product = db.query(RefProduct).filter(RefProduct.id == payload.product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    start_ms = _start_of_month_utc_ms()
    repo = TradingHistoryRepository(db)
    existing = repo.get_cursor(payload.acct_id, payload.product_id)

    if existing:
        repo.update_cursor(existing, cursor_start=start_ms, cursor_end=start_ms)
        return CursorResponse(
            acct_id=payload.acct_id,
            product_id=payload.product_id,
            cursor_start=start_ms,
            cursor_end=start_ms,
        )

    repo.create_cursor(
        acct_id=payload.acct_id,
        product_id=payload.product_id,
        cursor_start=start_ms,
        cursor_end=start_ms,
    )
    return CursorResponse(
        acct_id=payload.acct_id,
        product_id=payload.product_id,
        cursor_start=start_ms,
        cursor_end=start_ms,
    )


@router.get("/cursor", response_model=CursorResponse)
async def get_cursor(
    acct_id: int = Query(..., description="Account ID"),
    product_id: int = Query(..., description="Product ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get cursor for (acct_id, product_id). If it doesn't exist and account exists, create it automatically."""
    acct_repo = AccountRepository(db)
    account = acct_repo.get_by_user_and_id(current_user.id, acct_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found or does not belong to user",
        )

    product = db.query(RefProduct).filter(RefProduct.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    repo = TradingHistoryRepository(db)
    cursor = repo.get_cursor(acct_id, product_id)

    if cursor:
        start_ms = _start_of_month_utc_ms()
        cs = cursor.cursor_start if cursor.cursor_start is not None else start_ms
        ce = cursor.cursor_end if cursor.cursor_end is not None else start_ms
        return CursorResponse(acct_id=acct_id, product_id=product_id, cursor_start=cs, cursor_end=ce)

    start_ms = _start_of_month_utc_ms()
    repo.create_cursor(
        acct_id=acct_id,
        product_id=product_id,
        cursor_start=start_ms,
        cursor_end=start_ms,
    )
    return CursorResponse(
        acct_id=acct_id,
        product_id=product_id,
        cursor_start=start_ms,
        cursor_end=start_ms,
    )


@router.put("/cursor", response_model=CursorResponse)
async def update_cursor(
    payload: CursorUpdatePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update cursor for (acct_id, product_id). Supports update.cursor_end."""
    acct_repo = AccountRepository(db)
    account = acct_repo.get_by_user_and_id(current_user.id, payload.acct_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found or does not belong to user",
        )

    product = db.query(RefProduct).filter(RefProduct.id == payload.product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    repo = TradingHistoryRepository(db)
    cursor = repo.get_cursor(payload.acct_id, payload.product_id)
    if not cursor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cursor not found for this account and product",
        )

    if payload.update.cursor_end is not None:
        repo.update_cursor(cursor, cursor_end=payload.update.cursor_end)

    start_ms = _start_of_month_utc_ms()
    cs = cursor.cursor_start if cursor.cursor_start is not None else start_ms
    ce = cursor.cursor_end if cursor.cursor_end is not None else start_ms
    return CursorResponse(
        acct_id=payload.acct_id,
        product_id=payload.product_id,
        cursor_start=cs,
        cursor_end=ce,
    )


@router.get("", response_model=List[TradeItem])
async def get_mytrades(
    acct_id: int = Query(..., description="Account ID (required)"),
    product_id: int = Query(..., description="Product ID (required)"),
    source: str = Query(..., description="Data source: 'db' or 'exch'"),
    start_ms: int = Query(..., description="Start time in epoch milliseconds (inclusive)"),
    end_ms: Optional[int] = Query(None, description="End time in epoch milliseconds (inclusive, default now)"),
    side: str = Query("all", description="'buy', 'sell', or 'all'"),
    symbol: Optional[str] = Query(None, description="Symbol filter (exact match)"),
    limit: int = Query(200, ge=1, le=1000, description="Max records to return"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if source not in ("db", "exch"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source must be 'db' or 'exch'",
        )

    acct_repo = AccountRepository(db)
    account = acct_repo.get_by_user_and_id(current_user.id, acct_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found or does not belong to user",
        )

    product = db.query(RefProduct).filter(RefProduct.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    side_lower = side.lower()
    if side_lower not in ("buy", "sell", "all"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="side must be 'buy', 'sell', or 'all'",
        )

    end_ms_resolved = end_ms if end_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    since_dt = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc)
    until_dt = datetime.fromtimestamp(end_ms_resolved / 1000.0, tz=timezone.utc)

    if source == "db":
        return _query_from_db(
            db=db,
            acct_id=acct_id,
            product_id=product_id,
            side=side_lower,
            symbol=symbol,
            since=since_dt,
            until=until_dt,
            limit=limit,
        )

    # source == 'exch'
    return _query_from_exchange(
        db=db,
        account=account,
        product=product,
        side=side_lower,
        symbol=symbol,
        start_ms=start_ms,
        end_ms=end_ms_resolved,
        limit=limit,
    )


# ── source = db ──────────────────────────────────────────────────────

def _query_from_db(
    db: Session,
    acct_id: int,
    product_id: int,
    side: str,
    symbol: Optional[str],
    since: datetime,
    until: datetime,
    limit: int,
) -> List[TradeItem]:
    repo = TradingHistoryRepository(db)
    side_filter = SIDE_MAP.get(side)  # None when 'all'
    rows = repo.query_trading_history(
        acct_id=acct_id,
        product_id=product_id,
        side=side_filter,
        symbol=symbol,
        since=since,
        until=until,
        limit=limit,
    )
    return [
        TradeItem(
            exch_id=r.exch_id,
            exch_tid=r.exch_tid,
            acct_id=r.acct_id,
            order_id=r.order_id,
            symbol=r.symbol,
            product_id=r.product_id,
            side=r.side,
            exec_px=str(r.exec_px),
            exec_qty=str(r.exec_qty),
            exec_fee=str(r.exec_fee),
            fee_currency=r.fee_currency,
            is_maker=r.is_maker,
            exec_time=r.exec_time,
        )
        for r in rows
    ]


# ── source = exch ────────────────────────────────────────────────────

def _select_best_api(apis: list) -> object:
    """Pick the API key with highest priority (HMAC > ED25519 > RSA)."""
    if not apis:
        return None
    apis_sorted = sorted(apis, key=lambda a: API_TYPE_PRIORITY.get(a.api_type, 999))
    return apis_sorted[0]


def _query_from_exchange(
    db: Session,
    account,
    product,
    side: str,
    symbol: Optional[str],
    start_ms: int,
    end_ms: int,
    limit: int,
) -> List[TradeItem]:
    logger.info("_query_from_exchange: acct_id=%s exch_id=%s product_id=%s start_ms=%s end_ms=%s side=%s symbol=%s limit=%s",
        account.id, account.exch_id, product.id, start_ms, end_ms, side, symbol, limit)

    api_repo = APIRepository(db)
    apis = api_repo.get_by_acct(account.id)
    logger.info("_query_from_exchange: found %d API(s) for acct_id=%s", len(apis or []), account.id)

    api = _select_best_api(apis)
    if not api:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No API key found for this account",
        )

    exchange = db.query(RefExchange).filter(RefExchange.id == account.exch_id).first()
    if not exchange:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exchange not found for this account",
        )
    exchange_slug = (exchange.slug or exchange.name or "").upper()
    slug = (product.slug or "").upper()
    logger.info("_query_from_exchange: exchange=%s (id=%s) category=%s (product_id=%s)", exchange_slug, account.exch_id, slug, product.id)

    side_filter = SIDE_MAP.get(side)
    api_service = APIService()
    all_items: List[TradeItem] = []

    try:
        client = api_service.get_exchange_client_from_api_info(exchange_slug, api)
        raw_trades = client.get_mytrades(
            category=slug,
            start_ms=start_ms,
            end_ms=end_ms,
            symbol=symbol,
            limit=limit,
        )
    except (NetworkError, ExchangeError) as exc:
        logger.warning("_query_from_exchange: failed %s/%s: %s", exchange_slug, slug, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Exchange error: {exc}",
        )

    logger.info("_query_from_exchange: %s/%s returned %d raw trades", exchange_slug, slug, len(raw_trades))

    for t in raw_trades:
        trade_side = t.get("side", "")
        if side_filter and trade_side != side_filter:
            continue
        exec_time_ms = t.get("exec_time_ms", 0)
        exec_dt = datetime.fromtimestamp(exec_time_ms / 1000.0, tz=timezone.utc) if exec_time_ms else datetime.now(timezone.utc)
        all_items.append(
            TradeItem(
                exch_id=account.exch_id,
                exch_tid=t.get("exch_tid", 0),
                acct_id=account.id,
                order_id=t.get("order_id", 0),
                symbol=t.get("symbol", ""),
                product_id=product.id,
                side=trade_side,
                exec_px=t.get("exec_px", "0"),
                exec_qty=t.get("exec_qty", "0"),
                exec_fee=t.get("exec_fee", "0"),
                fee_currency=t.get("fee_currency", ""),
                is_maker=t.get("is_maker", False),
                exec_time=exec_dt,
            )
        )

    all_items.sort(key=lambda x: x.exec_time, reverse=True)
    result = all_items[:limit]
    logger.info("_query_from_exchange: total %d items after side filter, returning %d", len(all_items), len(result))
    return result
