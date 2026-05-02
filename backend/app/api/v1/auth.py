"""Authentication router."""
import asyncio
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.models import User
from app.middleware.auth import get_current_user, oauth2_scheme
from app.repositories.user_repository import UserRepository
from app.repositories.access_repository import AccessRepository

router = APIRouter(prefix="/auth", tags=["authentication"])


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


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


@router.get("/session", response_model=Token)
async def get_session(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Exchange an Authelia session for a JWT token.

    Traefik's Authelia middleware injects Remote-User after validating the
    session cookie. The browser calls this endpoint (credentials: include)
    on app boot to obtain a ui_ JWT without a custom login form.
    """
    remote_user = request.headers.get("Remote-User")
    if not remote_user:
        if not settings.DEV_MODE:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
        remote_user = settings.DEV_USER  # dev bypass: Authelia not running locally

    user_repo = UserRepository(db)
    user = user_repo.get_by_username(remote_user)
    if not user:
        if not settings.DEV_MODE:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        # First-time dev setup: create the user so migrations alone are enough to start
        user = user_repo.create(
            username=remote_user,
            email=f"{remote_user}@dev.local",
            hashed_password="dev-mode-no-password",
            is_active=True,
        )

    user_repo.update_last_login(user.id)
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
