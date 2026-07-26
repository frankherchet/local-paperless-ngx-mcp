from paperless_ngx_mcp.config import Settings


def test_settings_normalize_subpath_and_timeout() -> None:
    settings = Settings.model_validate(
        {
            "PAPERLESS_URL": "https://example.test/paperless",
            "PAPERLESS_TOKEN": "secret",
            "PAPERLESS_REQUEST_TIMEOUT_MS": 2_500,
        }
    )

    assert settings.base_url == "https://example.test/paperless/"
    assert settings.timeout_seconds == 2.5
    assert settings.paperless_read_only is True
