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
            "PAPERLESS_API_VERSION": 10,
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


@pytest.mark.asyncio
async def test_list_objects_removes_all_ids_and_labels_matching_algorithm() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/paperless/api/correspondents/"
        return httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "all": [8],
                "results": [
                    {
                        "id": 8,
                        "name": "Bank",
                        "document_count": 12,
                        "matching_algorithm": 6,
                    }
                ],
            },
        )

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.list_objects(
            "correspondents",
            page=1,
            page_size=100,
            ordering="name",
        )

    assert "all" not in result
    assert result["results"][0]["matching_algorithm_label"] == "automatic"


@pytest.mark.asyncio
async def test_find_documents_missing_metadata_uses_document_filter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/paperless/api/documents/"
        assert request.url.params["correspondent__isnull"] == "true"
        return httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "id": 42,
                        "title": "Unassigned",
                        "content": "must not be returned",
                    }
                ],
            },
        )

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.find_documents_missing_metadata(
            "correspondent",
            page=1,
            page_size=20,
            ordering="-created",
        )

    assert result["missing_field"] == "correspondent"
    assert result["results"] == [{"id": 42, "title": "Unassigned"}]


@pytest.mark.asyncio
async def test_organization_overview_fetches_all_pages_and_assignment_counts() -> None:
    object_results = {
        "correspondents": [{"id": 1, "name": "Bank", "document_count": 10}],
        "custom_fields": [{"id": 2, "name": "Account", "document_count": 5, "data_type": "string"}],
        "document_types": [{"id": 3, "name": "Invoice", "document_count": 8}],
        "saved_views": [
            {
                "id": 4,
                "name": "Inbox",
                "show_on_dashboard": True,
                "show_in_sidebar": True,
            }
        ],
        "storage_paths": [{"id": 5, "name": "Archive", "document_count": 20}],
    }
    missing_counts = {
        "correspondent__isnull": 3,
        "document_type__isnull": 4,
        "storage_path__isnull": 5,
        "is_tagged": 6,
        "has_custom_fields": 7,
        "archive_serial_number__isnull": 8,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/paperless/api/documents/":
            for parameter, count in missing_counts.items():
                if parameter in request.url.params:
                    return httpx.Response(200, json={"count": count, "results": []})
            return httpx.Response(200, json={"count": 20, "results": []})

        object_type = request.url.path.removeprefix("/paperless/api/").strip("/")
        if object_type == "tags":
            page = int(request.url.params["page"])
            if page == 1:
                return httpx.Response(
                    200,
                    json={
                        "count": 2,
                        "next": "http://paperless.test/api/tags/?page=2",
                        "results": [{"id": 6, "name": "Tax", "document_count": 0}],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "next": None,
                    "results": [{"id": 7, "name": " tax! ", "document_count": 1}],
                },
            )
        return httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": object_results[object_type],
            },
        )

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.get_organization_overview(sample_size=10)

    assert result["documents"]["total"] == 20
    assert result["documents"]["missing_assignments"] == {
        "correspondent": 3,
        "document_type": 4,
        "storage_path": 5,
        "tags": 6,
        "custom_fields": 7,
        "archive_serial_number": 8,
    }
    assert result["organization"]["tags"]["total"] == 2
    assert result["organization"]["tags"]["unused"] == 1
    assert result["organization"]["tags"]["single_document"] == 1
    assert (
        result["organization"]["tags"]["normalized_duplicate_groups"][0]["normalized_name"] == "tax"
    )
