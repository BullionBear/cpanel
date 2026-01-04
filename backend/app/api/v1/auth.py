"""Authentication router."""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from app.db.session import get_db
from app.repositories.user_repository import UserRepository

router = APIRouter(prefix="/auth", tags=["authentication"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


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
    
    Note: Authentication logic not yet implemented.
    This endpoint is a placeholder for future implementation.
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
    
    # TODO: Implement password hashing and user creation
    # For now, return a placeholder response
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Registration not yet implemented"
    )


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """
    Login endpoint.
    
    Note: Authentication logic not yet implemented.
    This endpoint is a placeholder for future implementation.
    """
    repository = UserRepository(db)
    
    # TODO: Implement login logic
    # 1. Get user by username
    # 2. Verify password
    # 3. Create access token
    # 4. Update last_login
    # 5. Return token
    
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Login not yet implemented"
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """
    Get current authenticated user.
    
    Note: Authentication logic not yet implemented.
    This endpoint is a placeholder for future implementation.
    """
    # TODO: Implement token verification and user retrieval
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Get current user not yet implemented"
    )

