"""Environment-based application configuration."""

from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    paperless_url: AnyHttpUrl = Field(alias="PAPERLESS_URL")
    paperless_token: SecretStr = Field(alias="PAPERLESS_TOKEN")
    paperless_api_version: int = Field(default=10, ge=1, alias="PAPERLESS_API_VERSION")
    paperless_request_timeout_ms: int = Field(
        default=15_000,
        ge=100,
        le=300_000,
        alias="PAPERLESS_REQUEST_TIMEOUT_MS",
    )
    paperless_read_only: bool = Field(default=True, alias="PAPERLESS_READ_ONLY")

    @property
    def base_url(self) -> str:
        """Return a normalized base URL that also supports Paperless sub-paths."""
        return f"{str(self.paperless_url).rstrip('/')}/"

    @property
    def timeout_seconds(self) -> float:
        return self.paperless_request_timeout_ms / 1000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache validated application settings."""
    return Settings()
