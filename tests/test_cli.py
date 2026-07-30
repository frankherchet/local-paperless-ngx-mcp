from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pytest

import paperless_ngx_mcp.cli as cli
import paperless_ngx_mcp.config as config
from paperless_ngx_mcp import __version__
from paperless_ngx_mcp.config import ConfigurationError, Settings, load_config_values


def _setup_args(*, from_env: Path | None = None, read_only: bool = False) -> argparse.Namespace:
    return argparse.Namespace(from_env=from_env, read_only=read_only)


def test_setup_verifies_before_storing_and_hides_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "config.json"
    monkeypatch.setattr(config, "config_path", lambda: target)
    observed = {}

    def verify(settings: Settings) -> dict[str, Any]:
        observed["token"] = settings.paperless_token.get_secret_value()
        return {"paperless_version": "2.19.3"}

    cli._run_setup(
        _setup_args(),
        input_func=lambda _: "https://paperless.example",
        secret_input=lambda _: "very-secret-token",
        verify=verify,
    )

    values = load_config_values(target)
    assert observed["token"] == "very-secret-token"
    assert values["PAPERLESS_URL"] == "https://paperless.example/"
    assert values["PAPERLESS_TOKEN"] == "very-secret-token"
    assert values["PAPERLESS_READ_ONLY"] is False
    assert "very-secret-token" not in capsys.readouterr().out


def test_setup_does_not_replace_config_when_connection_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.json"
    monkeypatch.setattr(config, "config_path", lambda: target)
    target.write_text(
        '{"PAPERLESS_URL":"https://old.example","PAPERLESS_TOKEN":"old"}', encoding="utf-8"
    )
    target.chmod(0o600)

    def reject(_settings: Settings) -> dict[str, Any]:
        raise ConfigurationError("authentication failed")

    with pytest.raises(ConfigurationError, match="authentication failed"):
        cli._run_setup(
            _setup_args(),
            input_func=lambda _: "https://new.example",
            secret_input=lambda _: "new-token",
            verify=reject,
        )

    assert "old" in target.read_text(encoding="utf-8")
    assert "new-token" not in target.read_text(encoding="utf-8")


def test_setup_can_explicitly_import_dotenv_and_preserve_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.json"
    dotenv = tmp_path / "legacy.env"
    dotenv.write_text(
        "PAPERLESS_URL=https://legacy.example\nPAPERLESS_TOKEN=legacy-token\nPAPERLESS_READ_ONLY=true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "config_path", lambda: target)

    cli._run_setup(
        _setup_args(from_env=dotenv),
        input_func=lambda _: pytest.fail(
            "complete imported configuration must not prompt for a URL"
        ),
        secret_input=lambda _: pytest.fail(
            "complete imported configuration must not prompt for a token"
        ),
        verify=lambda _: {"paperless_version": "2.0"},
    )

    assert load_config_values(target)["PAPERLESS_READ_ONLY"] is True
    assert dotenv.exists()


def test_main_returns_non_interactive_setup_hint_when_configuration_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli, "get_settings", lambda: (_ for _ in ()).throw(ConfigurationError("missing"))
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

    with pytest.raises(SystemExit) as exit_info:
        cli.main([])

    assert exit_info.value.code == 2
    assert "missing" in capsys.readouterr().err


def test_config_reset_requires_explicit_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "config.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    monkeypatch.setattr(config, "config_path", lambda: target)
    monkeypatch.setattr(cli, "config_path", lambda: target)

    cli.main(["config", "reset", "--yes"])

    assert not target.exists()
    assert "removed" in capsys.readouterr().out


def test_version_is_printed(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["--version"])

    assert capsys.readouterr().out.strip() == __version__
