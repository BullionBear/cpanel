import logging
import secrets

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Historic placeholder value; must never be accepted as a real signing key.
_PLACEHOLDER_SECRET_KEYS = {"your-secret-key-change-in-production", "dev-secret-key"}


class Settings(BaseSettings):
    PROJECT_NAME: str = "CPanel API"
    DEBUG: bool = False

    # Injected at docker build time (see docker/Dockerfile.backend)
    VERSION: str = "0.1.0"
    GIT_COMMIT: str = "unknown"
    BUILD_TIME: str = "unknown"

    DATABASE_URL: str = ""
    FRONTEND_URL: str = "http://localhost:5173"

    # Background scheduler (disable for one-off scripts / certain tests)
    SCHEDULER_ENABLED: bool = True

    # Envelope-encryption master key for stored credentials.
    # Generate with: python -c "from app.core.crypto import generate_master_key; print(generate_master_key())"
    CREDENTIAL_ENCRYPTION_KEY: str = ""

    # Order guardrails (enforced before every placement).
    #   ORDER_MAX_NOTIONAL       max price*qty per order in the quote asset; 0 = no cap.
    #   ORDER_SYMBOL_ALLOWLIST   comma-separated universal symbols; empty = allow all.
    ORDER_MAX_NOTIONAL: float = 0
    ORDER_SYMBOL_ALLOWLIST: str = ""

    # Transfer guardrails.
    #   TRANSFER_MAX_VALUE       max transfer size valued in the quote asset
    #                            (USDT) via reference prices; 0 = no cap.
    #   TRANSFER_STUCK_MINUTES   an unfinished transfer with no state change
    #                            for this long is flagged stuck for the operator.
    TRANSFER_MAX_VALUE: float = 0
    TRANSFER_STUCK_MINUTES: int = 120

    # Security. JWT signing key — set a strong value in production:
    # python -c "import secrets; print(secrets.token_urlsafe(64))"
    SECRET_KEY: str = ""
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

    @model_validator(mode="after")
    def _ensure_secret_key(self) -> "Settings":
        """Never run with a guessable JWT signing key.

        Missing/placeholder keys previously fell back to a public default,
        letting anyone forge access tokens. Now: hard error in production,
        ephemeral random key (tokens reset on restart) in DEBUG.
        """
        if not self.SECRET_KEY or self.SECRET_KEY in _PLACEHOLDER_SECRET_KEYS:
            if not self.DEBUG:
                raise ValueError(
                    "SECRET_KEY must be set to a strong random value in production. "
                    'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"'
                )
            logger.warning(
                "SECRET_KEY is not set; using an ephemeral key (all tokens invalidate on restart)"
            )
            self.SECRET_KEY = secrets.token_urlsafe(64)
        return self


settings = Settings()
