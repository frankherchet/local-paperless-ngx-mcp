from __future__ import annotations

import httpx
import pytest

from paperless_ngx_mcp.client import PaperlessApiError, PaperlessClient, ReadOnlyError
from paperless_ngx_mcp.config import Settings


def make_settings(*, read_only: bool = True) -> Settings:
    return Settings.model_validate(
        {
            "PAPERLESS_URL": "http://paperless.test/paperless",
            "PAPERLESS_TOKEN": "test-token",
            "PAPERLESS_READ_ONLY": read_only,
        }
    )


@pytest.mark.asyncio
async def test_search_documents_sends_auth_and_summarizes_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/paperless/api/documents/"
        assert request.headers["Authorization"] == "Token test-token"
        assert request.headers["Accept"] == "application/json; version=10"
        assert request.url.params["text"] == "invoice"
        return httpx.Response(
            200,
            json={
                "count": 1,
                "results": [
                    {
                        "id": 7,
                        "title": "Invoice",
                        "created": "2026-01-01",
                        "content": "secret OCR text",
                        "tags": [2],
                    }
                ],
            },
        )

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.search_documents(
            query="invoice",
            mode="simple",
            page=1,
            page_size=20,
            ordering="-created",
            similar_to_id=None,
        )

    assert result == {
        "count": 1,
        "results": [
            {
                "id": 7,
                "title": "Invoice",
                "created": "2026-01-01",
                "tags": [2],
            }
        ],
    }


@pytest.mark.asyncio
async def test_get_document_truncates_ocr_content() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"id": 5, "title": "Long document", "content": "x" * 2_000},
        )
    )

    async with PaperlessClient(make_settings(), transport=transport) as client:
        result = await client.get_document(
            5,
            include_content=True,
            max_content_chars=1_000,
        )

    assert result["content"] == "x" * 1_000
    assert result["content_truncated"] is True
    assert result["content_total_chars"] == 2_000


@pytest.mark.asyncio
async def test_update_is_blocked_in_read_only_mode() -> None:
    async with PaperlessClient(make_settings()) as client:
        with pytest.raises(ReadOnlyError, match="PAPERLESS_READ_ONLY=false"):
            await client.update_document(5, {"title": "Changed"})


@pytest.mark.asyncio
async def test_api_errors_have_a_useful_message() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(401, json={"detail": "Invalid token."})
    )

    async with PaperlessClient(make_settings(), transport=transport) as client:
        with pytest.raises(PaperlessApiError, match="HTTP 401: Invalid token"):
            await client.get_document(1, include_content=False, max_content_chars=1_000)
