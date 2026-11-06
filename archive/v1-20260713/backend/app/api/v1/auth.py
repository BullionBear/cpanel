"""Authentication router."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import (
    consume_discord_exchange_token,
    create_access_token,
    create_discord_exchange_token,
    create_oauth_state_token,
    verify_oauth_state_token,
)
from app.db.session import get_db
from app.models import User
from app.middleware.auth import get_current_user, oauth2_scheme
from app.repositories.user_repository import UserRepository
from app.repositories.access_repository import AccessRepository
from app.services.discord_oauth import (
    DiscordOAuthError,
    DiscordUser,
    build_authorize_url,
    exchange_code,
    fetch_current_user,
    unique_username,
    verify_guild_membership,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ExchangeRequest(BaseModel):
    code: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool

    class Config:
        from_attributes = True


async def _issue_ui_token(user_id: int, access_repo: AccessRepository) -> str:
    """Create a ui_ JWT, enforcing the MAX_UI_TOKEN per-user limit."""
    ui_tokens = access_repo.get_ui_tokens_by_user(user_id)
    while len(ui_tokens) >= settings.MAX_UI_TOKEN:
        access_repo.delete_oldest_ui_token_by_user(user_id)
        ui_tokens = access_repo.get_ui_tokens_by_user(user_id)

    expires_delta = timedelta(days=7)
    token_data = {"sub": str(user_id), "type": "ui_access"}

    for attempt in range(3):
        try:
            token = create_access_token(data=token_data, expires_delta=expires_delta, prefix="ui_")
            expired_at = datetime.now(timezone.utc) + expires_delta
            access_repo.create(user_id=user_id, access_token=token, expired_at=expired_at)
            return token
        except IntegrityError as e:
            error_str = str(e.orig) if hasattr(e, "orig") else str(e)
            if "UniqueViolation" in error_str or "duplicate key" in error_str.lower():
                if attempt < 2:
                    await asyncio.sleep(0.001 * (attempt + 1))
                    continue
            raise HTTPException(status_code=500, detail="Failed to create access token.")
    raise HTTPException(status_code=500, detail="Failed to create access token after retries.")


def _redirect_with_error(error: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/?error={quote(error)}",
        status_code=status.HTTP_302_FOUND,
    )


def _resolve_or_create_user(db: Session, discord_user: DiscordUser) -> User:
    user_repo = UserRepository(db)
    user = user_repo.get_by_discord_id(discord_user.id)
    if user:
        return user

    if discord_user.email:
        user = user_repo.get_by_email(discord_user.email)
        if user:
            return user_repo.link_discord_id(user, discord_user.id)

    base_username = discord_user.global_name or discord_user.username
    username = unique_username(base_username, discord_user.id)
    if user_repo.get_by_username(username):
        username = unique_username(f"{username}_{discord_user.id[-4:]}", discord_user.id)

    email = discord_user.email or f"{discord_user.id}@discord.local"
    if user_repo.get_by_email(email):
        email = f"{discord_user.id}@discord.local"

    return user_repo.create(
        username=username,
        email=email,
        hashed_password="discord-oauth-no-password",
        is_active=True,
        discord_id=discord_user.id,
    )


@router.get("/session", response_model=Token)
async def get_session(
    request: Request,
    db: Session = Depends(get_db),
):
    """Dev-only session bypass. Production uses Discord OAuth."""
    if not settings.DEV_MODE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Use Discord OAuth to sign in")

    user_repo = UserRepository(db)
    user = user_repo.get_by_username(settings.DEV_USER)
    if not user:
        user = user_repo.create(
            username=settings.DEV_USER,
            email=f"{settings.DEV_USER}@dev.local",
            hashed_password="dev-mode-no-password",
            is_active=True,
        )

    user_repo.update_last_login(user.id)
    access_repo = AccessRepository(db)
    token = await _issue_ui_token(user.id, access_repo)
    return Token(access_token=token, token_type="bearer")


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
    db: Session = Depends(get_db),
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
        user = _resolve_or_create_user(db, discord_user)
        user_repo = UserRepository(db)
        user_repo.update_last_login(user.id)
        exchange_code_token = create_discord_exchange_token(user.id)
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/auth/callback?code={quote(exchange_code_token)}",
            status_code=status.HTTP_302_FOUND,
        )
    except DiscordOAuthError as exc:
        return _redirect_with_error(exc.message)
    except IntegrityError:
        return _redirect_with_error("Failed to create or link your account")


@router.post("/discord/exchange", response_model=Token)
async def discord_exchange(
    body: ExchangeRequest,
    db: Session = Depends(get_db),
):
    """Exchange a one-time Discord callback code for a ui_ JWT."""
    user_id = consume_discord_exchange_token(body.code)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired sign-in code")

    user_repo = UserRepository(db)
    user = user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access_repo = AccessRepository(db)
    token = await _issue_ui_token(user.id, access_repo)
    return Token(access_token=token, token_type="bearer")


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
):
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_active=current_user.is_active,
    )


@router.get("/api-token", response_model=Token)
async def get_api_token(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    access_repo = AccessRepository(db)
    api_token_access = access_repo.get_api_token_by_user(current_user.id)
    if not api_token_access:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No API token found.")
    return Token(access_token=api_token_access.access_token, token_type="bearer")


@router.post("/api-token", response_model=Token)
async def create_api_token(
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create or refresh the permanent API token (10-year expiry). Requires a ui_ token."""
    if not token.startswith("ui_"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API tokens can only be created using UI tokens (ui_ prefix)",
        )

    access_repo = AccessRepository(db)
    if access_repo.get_api_token_by_user(current_user.id):
        access_repo.delete_api_tokens_by_user(current_user.id)

    expires_delta = timedelta(days=365 * 10)
    token_data = {"sub": str(current_user.id), "type": "api_access"}
    api_token = create_access_token(data=token_data, expires_delta=expires_delta, prefix="api_")
    expired_at = datetime.now(timezone.utc) + expires_delta
    access_repo.create(user_id=current_user.id, access_token=api_token, expired_at=expired_at)
    return Token(access_token=api_token, token_type="bearer")


@router.delete("/api-token")
async def delete_api_token(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    access_repo = AccessRepository(db)
    if not access_repo.get_api_token_by_user(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No API token found")
    deleted = access_repo.delete_api_tokens_by_user(current_user.id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete token")
    return {"message": "API token deleted successfully"}
