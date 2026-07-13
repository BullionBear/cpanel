from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "CPanel API"
    DEBUG: bool = False

    # Injected at docker build time (see docker/Dockerfile.backend)
    VERSION: str = "0.1.0"
    GIT_COMMIT: str = "unknown"
    BUILD_TIME: str = "unknown"

    DATABASE_URL: str = ""
    FRONTEND_URL: str = "http://localhost:5173"

    # Security
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Discord OAuth
    DISCORD_CLIENT_ID: str = ""
    DISCORD_CLIENT_SECRET: str = ""
    DISCORD_BOT_TOKEN: str = ""
    DISCORD_GUILD_ID: str = ""
    DISCORD_REQUIRED_ROLE_ID: str = ""
    DISCORD_REDIRECT_URI: str = "https://capi.lynxlinkage.com/auth/discord/callback"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
