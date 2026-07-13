"""Audit trail plumbing.

Usage in a state-changing endpoint:

    @router.post("/something")
    async def do_something(actor: Actor = Depends(current_actor), db=Depends(get_db)):
        async with audit(db, actor, "module.action", target="...", params={...}):
            ...  # the actual work

The "started" row is committed before the action runs — durable even if the
process dies mid-action — and updated to "succeeded" or "failed" afterwards.
No transaction is held open across the action itself.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.models.audit_log import AuditLog

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/discord/exchange")


@dataclass(frozen=True)
class Actor:
    type: str  # "user" | "agent" | "system"
    id: str
    name: str = ""


SYSTEM_ACTOR = Actor(type="system", id="system", name="system")


async def current_actor(token: str = Depends(oauth2_scheme)) -> Actor:
    """Resolve the authenticated actor from the request's bearer token."""
    payload = decode_access_token(token)
    if payload is None or payload.get("type") != "ui_access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return Actor(type="user", id=payload["sub"], name=payload.get("name") or "")


@asynccontextmanager
async def audit(
    db: AsyncSession,
    actor: Actor,
    action: str,
    target: str | None = None,
    params: dict | None = None,
):
    """Record a state-changing action in the append-only audit log."""
    entry = AuditLog(
        actor_type=actor.type,
        actor_id=actor.id,
        actor_name=actor.name or None,
        action=action,
        target=target,
        params=params,
        status="started",
    )
    db.add(entry)
    await db.commit()
    try:
        yield entry
    except BaseException as exc:
        # Discard the action's uncommitted work, then record the failure.
        await db.rollback()
        entry.status = "failed"
        entry.error = str(exc)[:2000]
        entry.finished_at = datetime.now(timezone.utc)
        await db.commit()
        raise
    else:
        entry.status = "succeeded"
        entry.finished_at = datetime.now(timezone.utc)
        await db.commit()
