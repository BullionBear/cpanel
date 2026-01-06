"""Authentication router."""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
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
from app.middleware.auth import get_current_user
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
    
    # Send welcome email to newly registered user
    email_subject = "Welcome to CPanel - Activate Your Account"
    email_body = f"""
Hello {user_data.username},

Thank you for registering with CPanel!

To activate your account, please log in using your credentials. You will receive an activation link via email after logging in.

If you did not create this account, please ignore this email.

Best regards,
CPanel Team
    """.strip()
    
    email_html = f"""
    <html>
      <body>
        <h2>Welcome to CPanel</h2>
        <p>Hello {user_data.username},</p>
        <p>Thank you for registering with CPanel!</p>
        <p>To activate your account, please <strong>log in</strong> using your credentials. You will receive an activation link via email after logging in.</p>
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


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """
    Login endpoint.
    
    For activated users: JWT token expires in 7 days
    For inactive users: JWT token expires in 30 minutes (for activation)
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
    
    # Determine token expiry based on user activation status
    if user.is_active:
        # Activated user: 7 days
        expires_delta = timedelta(days=7)
    else:
        # Inactive user: 30 minutes (for activation)
        expires_delta = timedelta(minutes=30)
    
    # Create JWT token
    token_data = {
        "sub": str(user.id),
        "username": user.username,
        "email": user.email,
        "is_active": user.is_active,
    }
    access_token = create_access_token(data=token_data, expires_delta=expires_delta)
    
    # Calculate expired_at datetime
    expired_at = datetime.now(timezone.utc) + expires_delta
    
    # Save token to database
    access_repo.create(
        user_id=user.id,
        access_token=access_token,
        expired_at=expired_at,
    )
    
    # Update last_login
    user_repo.update_last_login(user.id)
    
    # Send activation email if user is inactive
    if not user.is_active:
        activation_url = f"{settings.HOST_URL}{settings.API_V1_PREFIX}/auth/activate?token={access_token}"
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
    
    return Token(access_token=access_token, token_type="bearer")


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
    
    # Check if token is for inactive user (should have is_active=False in payload)
    if payload.get("is_active", True):
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

