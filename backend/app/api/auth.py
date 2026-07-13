"""Discord OAuth authentication (same flow as the archived backend, unversioned paths).

No users table exists yet in the rebuilt schema, so the signed-in identity
(Discord id + display name) is embedded in the issued JWT instead of a DB row.
"""
from datetime import timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from fastapi import Depends
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import (
    consume_discord_exchange_token,
    create_access_token,
    create_discord_exchange_token,
    create_oauth_state_token,
    decode_access_token,
    verify_oauth_state_token,
)
from app.services.discord_oauth import (
    DiscordOAuthError,
    build_authorize_url,
    exchange_code,
    fetch_current_user,
    verify_guild_membership,
)

router = APIRouter(prefix="/auth", tags=["authentication"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/discord/exchange")


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ExchangeRequest(BaseModel):
    code: str


class UserResponse(BaseModel):
    id: str
    username: str


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
async def get_current_user_info(token: str = Depends(oauth2_scheme)):
    payload = decode_access_token(token)
    if payload is None or payload.get("type") != "ui_access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return UserResponse(id=payload["sub"], username=payload.get("name") or f"user_{payload['sub']}")
