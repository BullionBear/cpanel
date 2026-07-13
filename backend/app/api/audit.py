"""Read-only access to the audit trail."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import current_actor
from app.core.db import get_db
from app.models.audit_log import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(current_actor)])


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
    started_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[AuditEntry])
async def list_audit_entries(
    action: str | None = None,
    actor_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AuditLog).order_by(AuditLog.started_at.desc(), AuditLog.id.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    result = await db.execute(stmt.limit(limit))
    return [AuditEntry.model_validate(row) for row in result.scalars()]
