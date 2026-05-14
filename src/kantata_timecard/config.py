from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    kantata_client_id: str
    kantata_client_secret: str
    kantata_oauth_redirect_url: str = "http://localhost:8000/oauth/callback"
    kantata_api_base: str = "https://api.mavenlink.com/api/v1"
    kantata_authorize_url: str = "https://app.mavenlink.com/oauth/authorize"
    kantata_token_url: str = "https://app.mavenlink.com/oauth/token"

    kantata_admin_token: str | None = None

    database_url: str = "sqlite+aiosqlite:///./kantata_timecard.db"
    session_secret: str
    token_encryption_key: str

    base_url: str = "http://localhost:8000"
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
