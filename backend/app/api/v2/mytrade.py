"""MyTrade router (v2): query trading history from DB or exchange."""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

logger = logging.getLogger(__name__)
from pydantic import BaseModel, field_serializer
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User, RefExchange, RefProduct, TradingHistory
from app.middleware.auth import get_current_user
from app.repositories.account_repository import AccountRepository
from app.repositories.api_repository import APIRepository
from app.repositories.symbol_repository import SymbolRepository
from app.repositories.trading_history_repository import TradingHistoryRepository
from app.services.api_service import APIService, NetworkError, ExchangeError

router = APIRouter(prefix="/mytrades", tags=["mytrades-v2"])

API_TYPE_PRIORITY = {"HMAC": 0, "ED25519": 1, "RSA": 2}
MAX_EXCHANGE_WINDOW_MS = 24 * 60 * 60 * 1000


def _format_exec_time(dt: datetime) -> str:
    """Format datetime as ISO 8601 with milliseconds, e.g. 2026-02-20T12:00:00.996Z."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ms = int(dt.microsecond / 1000)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"


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

    @field_serializer("exec_time")
    def serialize_exec_time(self, dt: datetime) -> str:
        return _format_exec_time(dt)


SIDE_MAP = {"buy": "Buy", "sell": "Sell"}


class CursorCreatePayload(BaseModel):
    acct_id: int
    symbol_id: int


class CursorUpdateFields(BaseModel):
    cursor_end: Optional[int] = None


class CursorUpdatePayload(BaseModel):
    acct_id: int
    symbol_id: int
    update: CursorUpdateFields


class CursorResponse(BaseModel):
    acct_id: int
    symbol_id: int
    cursor_start: int
    cursor_end: int


def _parse_csv_ints(value: Optional[str], field_name: str) -> List[int]:
    """Parse comma-separated integer query param."""
    if value is None or value.strip() == "":
        return []
    try:
        parsed = [int(v.strip()) for v in value.split(",") if v.strip()]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name}. Must be comma-separated integers.",
        )
    if not parsed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must include at least one integer.",
        )
    return parsed


def _start_of_month_utc_ms() -> int:
    """Start of current month in UTC, as epoch milliseconds."""
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp() * 1000)


def _now_minus_12h_utc_ms() -> int:
    """Now minus 12 hours in UTC, as epoch milliseconds."""
    now = datetime.now(timezone.utc)
    past = now - timedelta(hours=12)
    return int(past.timestamp() * 1000)


@router.post("/cursor", response_model=CursorResponse, status_code=status.HTTP_201_CREATED)
async def create_cursor(
    payload: CursorCreatePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create or reset cursor for (acct_id, symbol_id)."""
    acct_repo = AccountRepository(db)
    account = acct_repo.get_by_user_and_id(current_user.id, payload.acct_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found or does not belong to user",
        )

    symbol_repo = SymbolRepository(db)
    symbol = symbol_repo.get(payload.symbol_id)
    if not symbol:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Symbol not found",
        )
    if symbol.exchange_id != account.exch_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Symbol does not belong to the account exchange",
        )
    repo = TradingHistoryRepository(db)
    existing = repo.get_cursor(payload.acct_id, payload.symbol_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cursor already exists for this account and symbol. Use PUT to update.",
        )

    start_ms = _now_minus_12h_utc_ms()
    repo.create_cursor(
        acct_id=payload.acct_id,
        symbol_id=payload.symbol_id,
        cursor_start=start_ms,
        cursor_end=start_ms,
    )
    return CursorResponse(
        acct_id=payload.acct_id,
        symbol_id=payload.symbol_id,
        cursor_start=start_ms,
        cursor_end=start_ms,
    )


@router.get("/cursors", response_model=List[CursorResponse])
async def list_cursors(
    acct_id: Optional[int] = Query(None, description="Optional filter by account ID"),
    symbol_id: Optional[int] = Query(None, description="Optional filter by symbol ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all cursors for the current user (scoped via account.user_id)."""
    repo = TradingHistoryRepository(db)
    cursors = repo.query_cursors_by_user_id(
        user_id=current_user.id,
        acct_id=acct_id,
        symbol_id=symbol_id,
        skip=skip,
        limit=limit,
    )
    start_ms = _start_of_month_utc_ms()
    return [
        CursorResponse(
            acct_id=c.acct_id,
            symbol_id=c.symbol_id,
            cursor_start=c.cursor_start if c.cursor_start is not None else start_ms,
            cursor_end=c.cursor_end if c.cursor_end is not None else start_ms,
        )
        for c in cursors
    ]


@router.get("/cursor", response_model=CursorResponse)
async def get_cursor(
    acct_id: int = Query(..., description="Account ID"),
    symbol_id: int = Query(..., description="Symbol ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get cursor for (acct_id, symbol_id). If missing, create automatically."""
    acct_repo = AccountRepository(db)
    account = acct_repo.get_by_user_and_id(current_user.id, acct_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found or does not belong to user",
        )

    symbol_repo = SymbolRepository(db)
    symbol = symbol_repo.get(symbol_id)
    if not symbol:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Symbol not found",
        )
    if symbol.exchange_id != account.exch_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Symbol does not belong to the account exchange",
        )
    repo = TradingHistoryRepository(db)
    cursor = repo.get_cursor(acct_id, symbol_id)

    if cursor:
        start_ms = _start_of_month_utc_ms()
        cs = cursor.cursor_start if cursor.cursor_start is not None else start_ms
        ce = cursor.cursor_end if cursor.cursor_end is not None else start_ms
        return CursorResponse(acct_id=acct_id, symbol_id=symbol_id, cursor_start=cs, cursor_end=ce)

    start_ms = _start_of_month_utc_ms()
    repo.create_cursor(
        acct_id=acct_id,
        symbol_id=symbol_id,
        cursor_start=start_ms,
        cursor_end=start_ms,
    )
    return CursorResponse(
        acct_id=acct_id,
        symbol_id=symbol_id,
        cursor_start=start_ms,
        cursor_end=start_ms,
    )


@router.put("/cursor", response_model=CursorResponse)
async def update_cursor(
    payload: CursorUpdatePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update cursor for (acct_id, symbol_id). Supports update.cursor_end."""
    acct_repo = AccountRepository(db)
    account = acct_repo.get_by_user_and_id(current_user.id, payload.acct_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found or does not belong to user",
        )

    symbol_repo = SymbolRepository(db)
    symbol = symbol_repo.get(payload.symbol_id)
    if not symbol:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Symbol not found",
        )
    if symbol.exchange_id != account.exch_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Symbol does not belong to the account exchange",
        )
    repo = TradingHistoryRepository(db)
    cursor = repo.get_cursor(payload.acct_id, payload.symbol_id)
    if not cursor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cursor not found for this account and symbol",
        )

    if payload.update.cursor_end is not None:
        repo.update_cursor(cursor, cursor_end=payload.update.cursor_end)

    start_ms = _start_of_month_utc_ms()
    cs = cursor.cursor_start if cursor.cursor_start is not None else start_ms
    ce = cursor.cursor_end if cursor.cursor_end is not None else start_ms
    return CursorResponse(
        acct_id=payload.acct_id,
        symbol_id=payload.symbol_id,
        cursor_start=cs,
        cursor_end=ce,
    )


@router.get("/db", response_model=List[TradeItem])
async def get_mytrades_db(
    acct_id: Optional[str] = Query(None, description="Optional account IDs CSV (e.g. '1,2,3'); empty means all user accounts"),
    product_id: Optional[int] = Query(None, description="Optional product ID filter"),
    symbol_id: Optional[str] = Query(None, description="Optional symbol IDs CSV (e.g. '10,12')"),
    side: str = Query("all", description="'buy', 'sell', or 'all'"),
    start_ms: Optional[int] = Query(None, description="Optional start time in epoch milliseconds (inclusive)"),
    end_ms: Optional[int] = Query(None, description="Optional end time in epoch milliseconds (inclusive)"),
    limit: int = Query(1000, ge=1, le=5000, description="Max records to return"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Query mytrades from DB. Supports multi-account and multi-symbol filtering."""
    side_lower = side.lower()
    if side_lower not in ("buy", "sell", "all"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="side must be 'buy', 'sell', or 'all'",
        )
    side_filter = SIDE_MAP.get(side_lower)  # None when 'all'

    acct_repo = AccountRepository(db)
    user_accounts = acct_repo.query(user_id=current_user.id, skip=0, limit=5000)
    user_acct_ids = {a.id for a in user_accounts}
    if not user_acct_ids:
        return []

    requested_acct_ids = _parse_csv_ints(acct_id, "acct_id")
    if requested_acct_ids:
        requested_set = set(requested_acct_ids)
        if not requested_set.issubset(user_acct_ids):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="One or more account IDs not found or do not belong to user",
            )
        target_acct_ids = requested_set
    else:
        target_acct_ids = user_acct_ids

    symbol_names: List[str] = []
    symbol_ids = _parse_csv_ints(symbol_id, "symbol_id")
    if symbol_ids:
        symbol_repo = SymbolRepository(db)
        symbols = []
        for sid in symbol_ids:
            sym = symbol_repo.get(sid)
            if not sym:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Symbol with ID {sid} not found",
                )
            symbols.append(sym)
        symbol_names = [s.name for s in symbols]

    if start_ms is not None and end_ms is not None and start_ms > end_ms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_ms must be less than or equal to end_ms",
        )
    since_dt = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc) if start_ms is not None else None
    until_dt = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc) if end_ms is not None else None

    query = db.query(TradingHistory).filter(TradingHistory.acct_id.in_(target_acct_ids))
    if product_id is not None:
        product = db.query(RefProduct).filter(RefProduct.id == product_id).first()
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found",
            )
        query = query.filter(TradingHistory.product_id == product_id)
    if side_filter is not None:
        query = query.filter(TradingHistory.side == side_filter)
    if symbol_names:
        query = query.filter(TradingHistory.symbol.in_(symbol_names))
    if since_dt is not None:
        query = query.filter(TradingHistory.exec_time >= since_dt)
    if until_dt is not None:
        query = query.filter(TradingHistory.exec_time <= until_dt)

    rows = query.order_by(TradingHistory.exec_time.desc()).limit(limit).all()
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


@router.get("/exch", response_model=List[TradeItem])
async def get_mytrades_exch(
    acct_id: int = Query(..., description="Account ID (required)"),
    symbol_id: int = Query(..., description="Symbol ID (required)"),
    start_ms: int = Query(..., description="Start time in epoch milliseconds (inclusive)"),
    end_ms: int = Query(..., description="End time in epoch milliseconds (inclusive)"),
    side: str = Query("all", description="'buy', 'sell', or 'all'"),
    limit: int = Query(100, ge=1, le=199, description="Max records to return (<200)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Query mytrades from exchange with strict input restrictions."""
    if start_ms > end_ms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_ms must be less than or equal to end_ms",
        )
    if end_ms - start_ms > MAX_EXCHANGE_WINDOW_MS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="For /mytrades/exch, time range cannot exceed 24 hours",
        )

    side_lower = side.lower()
    if side_lower not in ("buy", "sell", "all"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="side must be 'buy', 'sell', or 'all'",
        )

    acct_repo = AccountRepository(db)
    account = acct_repo.get_by_user_and_id(current_user.id, acct_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found or does not belong to user",
        )

    symbol_repo = SymbolRepository(db)
    symbol = symbol_repo.get(symbol_id)
    if not symbol:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Symbol not found",
        )
    if symbol.exchange_id != account.exch_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Symbol does not belong to the account exchange",
        )
    product = db.query(RefProduct).filter(RefProduct.id == symbol.product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product for symbol not found",
        )
    return _query_from_exchange(
        db=db,
        account=account,
        product=product,
        side=side_lower,
        symbol=symbol.name,
        start_ms=start_ms,
        end_ms=end_ms,
        limit=limit,
    )


# ── exch helper ──────────────────────────────────────────────────────

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
    symbol: str,
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

    if start_ms > end_ms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_ms must be less than or equal to end_ms",
        )

    try:
        client = api_service.get_exchange_client_from_api_info(exchange_slug, api)
        raw_trades = client.get_mytrades(
            category=slug,
            start_ms=start_ms,
            end_ms=end_ms,
            symbol=symbol,
            limit=limit,
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
    except (NetworkError, ExchangeError) as exc:
        logger.warning("_query_from_exchange: failed %s/%s: %s", exchange_slug, slug, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Exchange error: {exc}",
        )

    all_items.sort(key=lambda x: x.exec_time, reverse=True)
    result = all_items[:limit]
    logger.info("_query_from_exchange: total %d items after side filter, returning %d", len(all_items), len(result))
    return result
