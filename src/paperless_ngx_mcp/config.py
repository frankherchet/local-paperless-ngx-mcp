"""Runtime configuration and local credential-file handling."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from dotenv import dotenv_values
from platformdirs import user_config_dir
from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "local-paperless-ngx-mcp"
CONFIG_FILENAME = "config.json"
CREDENTIAL_ENV_NAMES = ("PAPERLESS_URL", "PAPERLESS_TOKEN")
OPTIONAL_ENV_NAMES = (
    "PAPERLESS_API_VERSION",
    "PAPERLESS_REQUEST_TIMEOUT_MS",
    "PAPERLESS_READ_ONLY",
)
ALL_ENV_NAMES = CREDENTIAL_ENV_NAMES + OPTIONAL_ENV_NAMES


class ConfigurationError(RuntimeError):
    """Raised when runtime configuration is absent, invalid, or unsafe."""


class Settings(BaseSettings):
    """Validated Paperless connection settings.

    Settings are assembled explicitly by :func:`get_settings`.  In particular, an
    ``.env`` file is never loaded implicitly.
    """

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

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

    def as_config_values(self) -> dict[str, Any]:
        """Return serializable values for the local configuration file."""
        return {
            "PAPERLESS_URL": str(self.paperless_url),
            "PAPERLESS_TOKEN": self.paperless_token.get_secret_value(),
            "PAPERLESS_API_VERSION": self.paperless_api_version,
            "PAPERLESS_REQUEST_TIMEOUT_MS": self.paperless_request_timeout_ms,
            "PAPERLESS_READ_ONLY": self.paperless_read_only,
        }


def config_path() -> Path:
    """Return the user-specific configuration-file path without creating it."""
    return Path(user_config_dir(APP_NAME, appauthor=False)) / CONFIG_FILENAME


def load_config_values(path: Path | None = None) -> dict[str, Any]:
    """Read and validate the local configuration file, if present."""
    target = path or config_path()
    if not target.exists():
        return {}
    if target.is_symlink():
        raise ConfigurationError(f"Refusing symlinked configuration file: {target}")
    _require_safe_permissions(target)
    try:
        decoded = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"Could not read configuration file {target}: {error}") from error
    if not isinstance(decoded, dict):
        raise ConfigurationError(f"Configuration file {target} must contain a JSON object")
    return {name: decoded[name] for name in ALL_ENV_NAMES if name in decoded}


def write_config(settings: Settings, path: Path | None = None) -> Path:
    """Atomically persist validated settings with user-only POSIX permissions."""
    target = path or config_path()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        target.parent.chmod(0o700)

    serialized = json.dumps(settings.as_config_values(), indent=2, sort_keys=True) + "\n"
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            if os.name != "nt":
                os.chmod(temporary.name, 0o600)
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
        temporary_name = None
        if os.name != "nt":
            target.chmod(0o600)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return target


def reset_config(path: Path | None = None) -> bool:
    """Delete the local configuration file and return whether it existed."""
    target = path or config_path()
    if not target.exists():
        return False
    if target.is_symlink():
        raise ConfigurationError(f"Refusing symlinked configuration file: {target}")
    target.unlink()
    return True


def load_env_file(path: Path) -> dict[str, Any]:
    """Load explicitly requested ``.env`` values for one-time migration only."""
    if not path.is_file():
        raise ConfigurationError(f"Environment file does not exist: {path}")
    values = dotenv_values(path)
    return {
        name: value for name, value in values.items() if name in ALL_ENV_NAMES and value is not None
    }


def make_settings(values: Mapping[str, Any]) -> Settings:
    """Validate values and report compact configuration errors."""
    try:
        return Settings.model_validate(values)
    except ValidationError as error:
        raise ConfigurationError(f"Invalid Paperless configuration: {error}") from error


def masked_config_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return config values suitable for a terminal display without exposing the token."""
    displayed = {name: values[name] for name in ALL_ENV_NAMES if name in values}
    token = displayed.get("PAPERLESS_TOKEN")
    if isinstance(token, str):
        displayed["PAPERLESS_TOKEN"] = _mask_token(token)
    return displayed


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings from explicit environment overrides or the user config file."""
    values = load_config_values()
    environment = {name: os.environ[name] for name in ALL_ENV_NAMES if name in os.environ}
    credential_overrides = [name for name in CREDENTIAL_ENV_NAMES if name in environment]
    if credential_overrides and len(credential_overrides) != len(CREDENTIAL_ENV_NAMES):
        raise ConfigurationError(
            "PAPERLESS_URL and PAPERLESS_TOKEN must be supplied together when using "
            "environment overrides."
        )
    values.update(environment)
    if not values:
        raise ConfigurationError(
            "Paperless is not configured. Run 'paperless-ngx-mcp setup' in a terminal first."
        )
    return make_settings(values)


def _require_safe_permissions(path: Path) -> None:
    """Reject group- or world-accessible local secret files on POSIX."""
    if os.name == "nt":
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigurationError(
            f"Configuration file {path} has unsafe permissions {mode:03o}; expected 600."
        )


def _mask_token(token: str) -> str:
    if len(token) <= 4:
        return "*" * len(token)
    return f"{token[:2]}…{token[-2:]}"
