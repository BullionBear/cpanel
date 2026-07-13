from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "CPanel API"
    API_PREFIX: str = "/api"
    DEBUG: bool = False

    # Injected at docker build time (see docker/Dockerfile.backend)
    VERSION: str = "0.1.0"
    GIT_COMMIT: str = "unknown"
    BUILD_TIME: str = "unknown"

    DATABASE_URL: str = ""
    FRONTEND_URL: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
