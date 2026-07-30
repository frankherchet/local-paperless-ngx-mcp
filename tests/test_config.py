from __future__ import annotations

import os
from pathlib import Path

import pytest

import paperless_ngx_mcp.config as config
from paperless_ngx_mcp.config import ConfigurationError, Settings


def test_settings_normalize_subpath_and_timeout() -> None:
    settings = Settings.model_validate(
        {
            "PAPERLESS_URL": "https://example.test/paperless",
            "PAPERLESS_TOKEN": "secret",
            "PAPERLESS_REQUEST_TIMEOUT_MS": 2_500,
            "PAPERLESS_READ_ONLY": True,
        }
    )

    assert settings.base_url == "https://example.test/paperless/"
    assert settings.timeout_seconds == 2.5
    assert settings.paperless_read_only is True


def test_get_settings_uses_local_config_and_requires_complete_credential_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.json"
    monkeypatch.setattr(config, "config_path", lambda: target)
    monkeypatch.delenv("PAPERLESS_URL", raising=False)
    monkeypatch.delenv("PAPERLESS_TOKEN", raising=False)
    config.write_config(
        Settings.model_validate(
            {
                "PAPERLESS_URL": "https://stored.example",
                "PAPERLESS_TOKEN": "stored-token",
                "PAPERLESS_READ_ONLY": False,
            }
        )
    )
    config.get_settings.cache_clear()

    assert config.get_settings().base_url == "https://stored.example/"

    monkeypatch.setenv("PAPERLESS_URL", "https://override.example")
    config.get_settings.cache_clear()
    with pytest.raises(ConfigurationError, match="supplied together"):
        config.get_settings()

    monkeypatch.setenv("PAPERLESS_TOKEN", "override-token")
    config.get_settings.cache_clear()
    assert config.get_settings().base_url == "https://override.example/"
    config.get_settings.cache_clear()


def test_dotenv_is_not_loaded_implicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "PAPERLESS_URL=https://dotenv.example\nPAPERLESS_TOKEN=dotenv-token\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "config_path", lambda: tmp_path / "missing.json")
    monkeypatch.delenv("PAPERLESS_URL", raising=False)
    monkeypatch.delenv("PAPERLESS_TOKEN", raising=False)
    config.get_settings.cache_clear()

    with pytest.raises(ConfigurationError, match="not configured"):
        config.get_settings()
    config.get_settings.cache_clear()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission checks do not apply on Windows")
def test_local_config_rejects_broad_permissions(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text('{"PAPERLESS_URL": "https://example.test"}', encoding="utf-8")
    target.chmod(0o644)

    with pytest.raises(ConfigurationError, match="unsafe permissions"):
        config.load_config_values(target)


def test_masked_config_never_reveals_full_token() -> None:
    shown = config.masked_config_values(
        {"PAPERLESS_URL": "https://example.test", "PAPERLESS_TOKEN": "long-secret-token"}
    )

    assert shown["PAPERLESS_TOKEN"] == "lo…en"
    assert "long-secret-token" not in str(shown)
