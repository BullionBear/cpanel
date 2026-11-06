"""Discord OAuth authentication (same flow as the archived backend, unversioned paths).

No users table exists yet in the rebuilt schema, so the signed-in identity
(Discord id + display name) is embedded in the issued JWT instead of a DB row.

Panel API keys are long-lived ``api_`` JWTs stored in ``panel_access_token`` so
they can be revoked. Minting requires an active Discord OAuth UI session.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import Actor, audit, current_actor, current_ui_actor
from app.core.config import settings
from app.core.db import get_db
from app.core.security import (
    consume_discord_exchange_token,
    create_access_token,
    create_discord_exchange_token,
    create_oauth_state_token,
    verify_oauth_state_token,
)
from app.models.panel_access_token import PanelAccessToken
from app.services.discord_oauth import (
    DiscordOAuthError,
    build_authorize_url,
    exchange_code,
    fetch_current_user,
    verify_guild_membership,
)

router = APIRouter(prefix="/auth", tags=["authentication"])

API_TOKEN_TTL = timedelta(days=365 * 10)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ExchangeRequest(BaseModel):
    code: str


class UserResponse(BaseModel):
    id: str
    username: str


class MessageResponse(BaseModel):
    message: str


def _redirect_with_error(error: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/?error={quote(error)}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/discord/login")
async def discord_login():
    """Redirect the browser to Discord OAuth."""
    if not settings.DISCORD_CLIENT_ID or not settings.DISCORD_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Discord OAuth is not configured")
    state = create_oauth_state_token()
    return RedirectResponse(url=build_authorize_url(state), status_code=status.HTTP_302_FOUND)


@router.get("/discord/callback")
async def discord_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Handle Discord OAuth callback and redirect to the frontend with a one-time code."""
    if error:
        return _redirect_with_error("Discord sign-in was cancelled")
    if not code or not state or not verify_oauth_state_token(state):
        return _redirect_with_error("Invalid Discord OAuth state")

    try:
        token_data = await exchange_code(code)
        discord_user = await fetch_current_user(token_data["access_token"])
        await verify_guild_membership(discord_user.id)
        exchange_token = create_discord_exchange_token(discord_user.id, discord_user.display_name)
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/auth/callback?code={quote(exchange_token)}",
            status_code=status.HTTP_302_FOUND,
        )
    except DiscordOAuthError as exc:
        return _redirect_with_error(exc.message)


@router.post("/discord/exchange", response_model=Token)
async def discord_exchange(body: ExchangeRequest):
    """Exchange a one-time Discord callback code for a ui_ JWT."""
    identity = consume_discord_exchange_token(body.code)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired sign-in code")

    token = create_access_token(
        data={"sub": identity["sub"], "name": identity.get("name", ""), "type": "ui_access"},
        expires_delta=timedelta(days=7),
        prefix="ui_",
    )
    return Token(access_token=token, token_type="bearer")


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(actor: Actor = Depends(current_actor)):
    return UserResponse(id=actor.id, username=actor.name or f"user_{actor.id}")


@router.get("/api-token", response_model=Token)
async def get_api_token(
    actor: Actor = Depends(current_ui_actor),
    db: AsyncSession = Depends(get_db),
):
    """Return the caller's active panel API key, if any."""
    now = datetime.now(timezone.utc)
    row = (
        await db.execute(
            select(PanelAccessToken)
            .where(PanelAccessToken.actor_id == actor.id, PanelAccessToken.expired_at > now)
            .order_by(PanelAccessToken.created_at.desc())
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No API token found")
    return Token(access_token=row.access_token, token_type="bearer")


@router.post("/api-token", response_model=Token)
async def create_api_token(
    actor: Actor = Depends(current_ui_actor),
    db: AsyncSession = Depends(get_db),
):
    """Create or replace the panel API key. Requires Discord OAuth (ui_ session)."""
    api_token = create_access_token(
        data={"sub": actor.id, "name": actor.name, "type": "api_access"},
        expires_delta=API_TOKEN_TTL,
        prefix="api_",
    )
    async with audit(db, actor, "auth.api_token.create", target=actor.id):
        await db.execute(delete(PanelAccessToken).where(PanelAccessToken.actor_id == actor.id))
        db.add(
            PanelAccessToken(
                actor_id=actor.id,
                actor_name=actor.name or None,
                access_token=api_token,
                expired_at=datetime.now(timezone.utc) + API_TOKEN_TTL,
            )
        )
        await db.commit()
    return Token(access_token=api_token, token_type="bearer")


@router.delete("/api-token", response_model=MessageResponse)
async def delete_api_token(
    actor: Actor = Depends(current_ui_actor),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the caller's panel API key."""
    existing = (
        await db.execute(select(PanelAccessToken).where(PanelAccessToken.actor_id == actor.id))
    ).scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No API token found")

    async with audit(db, actor, "auth.api_token.delete", target=actor.id):
        await db.execute(delete(PanelAccessToken).where(PanelAccessToken.actor_id == actor.id))
        await db.commit()
    return MessageResponse(message="API token deleted successfully")
