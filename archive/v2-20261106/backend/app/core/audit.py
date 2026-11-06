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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.request_context import get_request_meta
from app.core.security import decode_access_token
from app.models.audit_log import AuditLog
from app.models.panel_access_token import PanelAccessToken

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/discord/exchange")

AUTH_DISCORD = "discord_oauth"
AUTH_API_KEY = "api_key"


@dataclass(frozen=True)
class Actor:
    type: str  # "user" | "agent" | "system"
    id: str
    name: str = ""
    auth_method: str = ""  # discord_oauth | api_key | ""


SYSTEM_ACTOR = Actor(type="system", id="system", name="system")


def _actor_from_payload(payload: dict, auth_method: str) -> Actor:
    return Actor(
        type="user",
        id=str(payload["sub"]),
        name=payload.get("name") or "",
        auth_method=auth_method,
    )


async def _resolve_actor(token: str, db: AsyncSession) -> Actor:
    payload = decode_access_token(token)
    if payload is None or not payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    token_type = payload.get("type")
    if token_type == "ui_access":
        return _actor_from_payload(payload, AUTH_DISCORD)

    if token_type == "api_access":
        if not token.startswith("api_"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
        now = datetime.now(timezone.utc)
        row = (
            await db.execute(
                select(PanelAccessToken).where(
                    PanelAccessToken.access_token == token,
                    PanelAccessToken.expired_at > now,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key revoked or expired")
        return _actor_from_payload(payload, AUTH_API_KEY)

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


async def current_actor(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Actor:
    """Resolve the authenticated actor from a Discord UI JWT or panel API key."""
    return await _resolve_actor(token, db)


async def current_ui_actor(token: str = Depends(oauth2_scheme)) -> Actor:
    """Require a Discord OAuth UI session (ui_ JWT). Used to mint/revoke API keys."""
    payload = decode_access_token(token)
    if payload is None or payload.get("type") != "ui_access" or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in with Discord to manage API keys",
        )
    if not token.startswith("ui_"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API keys can only be managed with a Discord OAuth session",
        )
    return _actor_from_payload(payload, AUTH_DISCORD)


@asynccontextmanager
async def audit(
    db: AsyncSession,
    actor: Actor,
    action: str,
    target: str | None = None,
    params: dict | None = None,
    return_code: int | None = None,
):
    """Record a state-changing action in the append-only audit log."""
    meta = get_request_meta()
    entry = AuditLog(
        actor_type=actor.type,
        actor_id=actor.id,
        actor_name=actor.name or None,
        action=action,
        target=target,
        params=params,
        status="started",
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        auth_method=actor.auth_method or meta.auth_method,
        return_code=return_code,
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
        entry.return_code = 500 if return_code is None else return_code
        await db.commit()
        raise
    else:
        entry.status = "succeeded"
        entry.finished_at = datetime.now(timezone.utc)
        entry.return_code = 200 if return_code is None else return_code
        await db.commit()
