"""Authentication router."""
import asyncio
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from app.core.config import settings
from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
)
from app.db.session import get_db
from app.models import User
from app.middleware.auth import get_current_user, oauth2_scheme
from app.repositories.user_repository import UserRepository
from app.repositories.access_repository import AccessRepository
from app.services.email_service import email_service

router = APIRouter(prefix="/auth", tags=["authentication"])


# Pydantic models for request/response
class Token(BaseModel):
    """Token response model."""
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    """User creation request model."""
    username: str
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """User response model."""
    id: int
    username: str
    email: str
    is_active: bool

    class Config:
        from_attributes = True


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    db: Session = Depends(get_db),
):
    """
    Register a new user.
    
    Creates a new user account with is_active=False.
    User must login and activate their account.
    """
    repository = UserRepository(db)
    
    # Check if username already exists
    if repository.get_by_username(user_data.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    # Check if email already exists
    if repository.get_by_email(user_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Hash password
    hashed_password = get_password_hash(user_data.password)
    
    # Create user with is_active=False (needs activation)
    user = repository.create(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hashed_password,
        is_active=False,  # User must activate via /activate endpoint
    )
    
    # Generate activation token (30 minutes expiry)
    expires_delta = timedelta(minutes=30)
    token_data = {
        "sub": str(user.id),
        "type": "activation",
    }
    activation_token = create_access_token(data=token_data, expires_delta=expires_delta)

    # Calculate expired_at datetime
    expired_at = datetime.now(timezone.utc) + expires_delta

    # Save token to database
    access_repo = AccessRepository(db)
    access_repo.create(
        user_id=user.id,
        access_token=activation_token,
        expired_at=expired_at,
    )

    # Create activation URL
    activation_url = f"{settings.HOST_URL}{settings.API_V1_PREFIX}/auth/activate?token={activation_token}"

    # Send activation email
    email_subject = "Activate Your CPanel Account"
    email_body = f"""
Hello {user_data.username},

Thank you for registering with CPanel!

Please click the link below to activate your account:
{activation_url}

This activation link will expire in 30 minutes.

If you did not create this account, please ignore this email.

Best regards,
CPanel Team
    """.strip()
    
    email_html = f"""
    <html>
      <body>
        <h2>Activate Your CPanel Account</h2>
        <p>Hello {user_data.username},</p>
        <p>Thank you for registering with CPanel!</p>
        <p>Please click the link below to activate your account:</p>
        <p><a href="{activation_url}">{activation_url}</a></p>
        <p><em>This activation link will expire in 30 minutes.</em></p>
        <p>If you did not create this account, please ignore this email.</p>
        <hr>
        <p><em>Best regards,<br>CPanel Team</em></p>
      </body>
    </html>
    """
    
    # Send email (don't fail registration if email fails)
    await email_service.send_email(
        to_email=user_data.email,
        subject=email_subject,
        body=email_body,
        html_body=email_html,
    )
    
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
    )


@router.post("/login")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """
    Login endpoint.
    
    For activated users: Returns JWT token (expires in 7 days)
    For inactive users: Sends activation email with token, returns success message (no token in response)
    """
    user_repo = UserRepository(db)
    access_repo = AccessRepository(db)
    
    # Get user by username
    user = user_repo.get_by_username(form_data.username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    # Verify password
    if not verify_password(form_data.password, user.hash_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    # Update last_login
    user_repo.update_last_login(user.id)
    
    if user.is_active:
        # Enforce MAX_UI_TOKEN limit: delete oldest tokens BEFORE creating new one
        # This prevents race conditions and ensures we have space for the new token
        ui_tokens = access_repo.get_ui_tokens_by_user(user.id)
        # Delete oldest tokens if we're at or above the limit (we'll add one more)
        while len(ui_tokens) >= settings.MAX_UI_TOKEN:
            access_repo.delete_oldest_ui_token_by_user(user.id)
            ui_tokens = access_repo.get_ui_tokens_by_user(user.id)
        
        # Activated user: Create UI access token (7 days) with ui_ prefix and return it
        expires_delta = timedelta(days=7)
        token_data = {
            "sub": str(user.id),
            "type": "ui_access",
        }
        
        # Retry logic in case of duplicate token (very rare but possible)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                access_token = create_access_token(data=token_data, expires_delta=expires_delta, prefix="ui_")
                
                # Calculate expired_at datetime
                expired_at = datetime.now(timezone.utc) + expires_delta
                
                # Save token to database
                access_repo.create(
                    user_id=user.id,
                    access_token=access_token,
                    expired_at=expired_at,
                )
                
                # Success - return token
                return Token(access_token=access_token, token_type="bearer")
            except IntegrityError as e:
                # Check if it's a unique constraint violation (duplicate token)
                error_str = str(e.orig) if hasattr(e, 'orig') else str(e)
                if "UniqueViolation" in error_str or "duplicate key" in error_str.lower():
                    if attempt < max_retries - 1:
                        # Wait a tiny bit and retry with a new token
                        await asyncio.sleep(0.001 * (attempt + 1))  # Small delay: 1ms, 2ms, 3ms
                        continue
                    else:
                        # Last attempt failed, raise the error
                        raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Failed to create access token. Please try again."
                        )
                else:
                    # Different integrity error, re-raise it
                    raise
            except Exception as e:
                # Other errors, re-raise immediately
                raise
        
        # Should not reach here, but just in case
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create access token after retries"
        )
    else:
        # Inactive user: Create activation token (30 minutes) and send via email, don't return token
        expires_delta = timedelta(minutes=30)
        token_data = {
            "sub": str(user.id),
            "type": "activation",
        }
        activation_token = create_access_token(data=token_data, expires_delta=expires_delta)
        
        # Calculate expired_at datetime
        expired_at = datetime.now(timezone.utc) + expires_delta
        
        # Save token to database
        access_repo.create(
            user_id=user.id,
            access_token=activation_token,
            expired_at=expired_at,
        )
        
        # Create activation URL
        activation_url = f"{settings.HOST_URL}{settings.API_V1_PREFIX}/auth/activate?token={activation_token}"
        
        # Send activation email
        email_subject = "Activate Your CPanel Account"
        email_body = f"""
Hello {user.username},

You have logged in to your CPanel account. To complete your registration, please activate your account by clicking the link below:

{activation_url}

This activation link will expire in 30 minutes.

If you did not request this, please ignore this email.

Best regards,
CPanel Team
        """.strip()
        
        email_html = f"""
        <html>
          <body>
            <h2>Activate Your CPanel Account</h2>
            <p>Hello {user.username},</p>
            <p>You have logged in to your CPanel account. To complete your registration, please activate your account by clicking the link below:</p>
            <p><a href="{activation_url}">{activation_url}</a></p>
            <p><em>This activation link will expire in 30 minutes.</em></p>
            <p>If you did not request this, please ignore this email.</p>
            <hr>
            <p><em>Best regards,<br>CPanel Team</em></p>
          </body>
        </html>
        """
        
        # Send activation email (don't fail login if email fails)
        await email_service.send_email(
            to_email=user.email,
            subject=email_subject,
            body=email_body,
            html_body=email_html,
        )
        
        # Return success message without token for inactive users
        return {
            "message": "Login successful. Please check your email for activation link.",
            "is_active": False,
        }


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
):
    """
    Get current authenticated user information.
    
    This is a protected endpoint that requires a valid JWT token.
    """
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_active=current_user.is_active,
    )

@router.get("/activate")
async def activate_user(
    token: str,
    db: Session = Depends(get_db),
):
    """
    Activate a user account using JWT token.
    
    Only works for inactive users with valid tokens (30 min expiry).
    """
    from app.core.security import decode_access_token
    
    user_repo = UserRepository(db)
    access_repo = AccessRepository(db)
    
    # Decode and verify JWT token
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    
    # Check if token exists in database and is valid
    access = access_repo.get_valid_token(token)
    if not access:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    
    # Get user from token payload
    user_id = int(payload.get("sub"))
    user = user_repo.get_by_id(user_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check if user is already activated
    if user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User account is already activated"
        )
    
    # Check if token is an activation token (type must be "activation")
    if payload.get("type") != "activation":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This token is not valid for activation"
        )
    
    # Activate the user
    activated_user = user_repo.activate_user(user.id)
    
    if not activated_user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to activate user account"
        )
    
    return {
        "message": "Account activated successfully",
        "user": UserResponse(
            id=activated_user.id,
            username=activated_user.username,
            email=activated_user.email,
            is_active=activated_user.is_active,
        )
    }


@router.get("/api-token", response_model=Token)
async def get_api_token(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get the current API token for the authenticated user.
    
    Returns the user's existing API token if it exists and is valid.
    Requires authentication (UI or API token).
    """
    access_repo = AccessRepository(db)
    
    # Get the user's API token
    api_token_access = access_repo.get_api_token_by_user(current_user.id)
    
    if not api_token_access:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No API token found. Create one using POST /api/v1/auth/api-token"
        )
    
    return Token(access_token=api_token_access.access_token, token_type="bearer")


@router.post("/api-token", response_model=Token)
async def create_api_token(
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create or refresh a permanent API token (expires in 10 years) using UI token.
    
    Requires a valid UI token (ui_ prefix). Returns an API token (api_ prefix)
    that can be used for API access. API tokens have a 10-year expiration.
    
    If the user already has an API token, it will be deleted and replaced with a new one.
    This enforces a one user-one API token model.
    """
    access_repo = AccessRepository(db)
    
    # Verify that the token is a UI token (not an API token)
    if not token.startswith("ui_"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API tokens can only be created using UI tokens (ui_ prefix)"
        )
    
    # Check if user already has an API token (one user-one token model)
    existing_api_token = access_repo.get_api_token_by_user(current_user.id)
    if existing_api_token:
        # Delete existing API token(s) to refresh
        access_repo.delete_api_tokens_by_user(current_user.id)
    
    # Create API token with 10 years expiration (as "never expire")
    expires_delta = timedelta(days=365 * 10)  # 10 years
    token_data = {
        "sub": str(current_user.id),
        "type": "api_access",
    }
    api_token = create_access_token(data=token_data, expires_delta=expires_delta, prefix="api_")
    
    # Calculate expired_at datetime
    expired_at = datetime.now(timezone.utc) + expires_delta
    
    # Save token to database
    access_repo.create(
        user_id=current_user.id,
        access_token=api_token,
        expired_at=expired_at,
    )
    
    return Token(access_token=api_token, token_type="bearer")


@router.delete("/api-token")
async def delete_api_token(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Delete the API token for the authenticated user.
    
    Since each user can only have one API token, this endpoint deletes
    the user's existing API token. Requires authentication.
    """
    access_repo = AccessRepository(db)
    
    # Check if user has an API token
    api_token_access = access_repo.get_api_token_by_user(current_user.id)
    if not api_token_access:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No API token found"
        )
    
    # Delete the user's API token(s)
    deleted_count = access_repo.delete_api_tokens_by_user(current_user.id)
    if deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete token"
        )
    
    return {"message": "API token deleted successfully"}

