"""Application configuration settings."""
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    DATABASE_URL: str

    # API Settings
    API_PREFIX: str = "/api"
    PROJECT_NAME: str = "CPanel API"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    HOST_URL: str = "http://localhost:8000"  # Base URL for activation links

    # Build metadata (injected at docker build time)
    GIT_COMMIT: str = "unknown"
    BUILD_TIME: str = "unknown"

    # Security
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    MAX_UI_TOKEN: int = 5  # Maximum number of UI prefix tokens per user

    # Local development
    DEV_MODE: bool = False
    DEV_USER: str = "eddy"  # username used as Remote-User when DEV_MODE=true

    # CORS
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "https://cpanel.lynxlinkage.com",
    ]

    # Email Settings
    SMTP_URL: str = "smtp.hostinger.com:465"
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_FROM_NAME: str = "CPanel"

    class Config:
        env_file_encoding = "utf-8"
        case_sensitive = True


# Load .env file from backend directory or migration directory
backend_env = Path(__file__).resolve().parent.parent.parent / ".env"
migration_env = Path(__file__).resolve().parent.parent.parent.parent / "migration" / ".env"

if backend_env.exists():
    load_dotenv(backend_env)
elif migration_env.exists():
    load_dotenv(migration_env)

settings = Settings()

