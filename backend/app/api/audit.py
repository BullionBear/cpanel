"""Read-only access to the audit trail (scoped to the signed-in actor)."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import Actor, current_actor
from app.core.db import get_db
from app.models.audit_log import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEntry(BaseModel):
    id: int
    actor_type: str
    actor_id: str
    actor_name: str | None
    action: str
    target: str | None
    params: dict | None
    status: str
    error: str | None
    ip_address: str | None
    user_agent: str | None
    auth_method: str | None
    return_code: int | None
    started_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class AuditPage(BaseModel):
    total: int
    items: list[AuditEntry]


@router.get("", response_model=AuditPage)
async def list_audit_entries(
    action: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    actor: Actor = Depends(current_actor),
    db: AsyncSession = Depends(get_db),
):
    """List the caller's own audit history (newest first)."""
    filters = [AuditLog.actor_id == actor.id]
    if action:
        filters.append(AuditLog.action == action)

    total = (
        await db.execute(select(func.count()).select_from(AuditLog).where(*filters))
    ).scalar_one()
    result = await db.execute(
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.started_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return AuditPage(
        total=total,
        items=[AuditEntry.model_validate(row) for row in result.scalars()],
    )
