"""Universal symbol browsing (read-only)."""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import current_actor
from app.core.db import get_db
from app.models.universal_ticker import UniversalTicker

router = APIRouter(prefix="/symbols", tags=["symbols"], dependencies=[Depends(current_actor)])


class SymbolInfo(BaseModel):
    universal_symbol: str
    exchange: str
    market_type: str
    native_symbol: str
    base_asset: str
    quote_asset: str
    price_tick: Decimal
    qty_step: Decimal
    min_qty: Decimal
    max_qty: Decimal | None
    min_notional: Decimal | None
    contract_size: Decimal | None
    settle_asset: str | None
    expiry: datetime | None
    status: str
    last_price: Decimal | None
    price_updated_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime

    model_config = {"from_attributes": True}


class SymbolPage(BaseModel):
    total: int
    items: list[SymbolInfo]


@router.get("", response_model=SymbolPage)
async def list_symbols(
    exchange: str | None = None,
    market_type: str | None = None,
    status: str | None = "active",
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if exchange:
        filters.append(UniversalTicker.exchange == exchange.upper())
    if market_type:
        filters.append(UniversalTicker.market_type == market_type.upper())
    if status and status != "all":
        filters.append(UniversalTicker.status == status)
    if search:
        needle = f"%{search.upper()}%"
        filters.append(
            or_(
                UniversalTicker.universal_symbol.like(needle),
                UniversalTicker.base_asset.like(needle),
                UniversalTicker.quote_asset.like(needle),
            )
        )

    total = (
        await db.execute(select(func.count()).select_from(UniversalTicker).where(*filters))
    ).scalar_one()
    rows = (
        await db.execute(
            select(UniversalTicker)
            .where(*filters)
            .order_by(UniversalTicker.universal_symbol)
            .limit(min(limit, 500))
            .offset(offset)
        )
    ).scalars()
    return SymbolPage(total=total, items=[SymbolInfo.model_validate(r) for r in rows])


@router.get("/{universal_symbol}", response_model=SymbolInfo)
async def get_symbol(universal_symbol: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(UniversalTicker, universal_symbol.upper())
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown symbol {universal_symbol!r}")
    return SymbolInfo.model_validate(row)
