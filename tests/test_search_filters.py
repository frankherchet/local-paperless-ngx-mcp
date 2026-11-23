import json
from datetime import date

import httpx
import pytest
from fastmcp import Client

from paperless_ngx_mcp.client import PaperlessClient
from paperless_ngx_mcp.config import Settings
from paperless_ngx_mcp.search_filters import document_filter_params
from paperless_ngx_mcp.server import create_server

SETTINGS = Settings(PAPERLESS_URL="http://paperless.test", PAPERLESS_TOKEN="test-token")


def test_no_filters_produce_no_params() -> None:
    assert document_filter_params() == {}
    assert document_filter_params(tag_ids=[], exclude_tag_ids=None) == {}


def test_filters_map_to_upstream_django_filter_names() -> None:
    assert document_filter_params(
        created_from=date(2026, 1, 1),
        created_to=date(2026, 1, 1),
        tag_ids=[3, 1, 3],
        exclude_tag_ids=[9],
        correspondent_id=4,
        document_type_id=5,
        storage_path_id=6,
    ) == {
        "created__gte": "2026-01-01",
        "created__lte": "2026-01-01",
        "tags__id__all": "3,1",
        "tags__id__none": "9",
        "correspondent__id": 4,
        "document_type__id": 5,
        "storage_path__id": 6,
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"created_from": date(2026, 2, 1), "created_to": date(2026, 1, 1)}, "created_from"),
        ({"tag_ids": [0]}, "tag_ids"),
        ({"exclude_tag_ids": [-1]}, "exclude_tag_ids"),
        ({"tag_ids": [1, 2], "exclude_tag_ids": [2]}, "overlap"),
        ({"correspondent_id": 0}, "correspondent_id"),
        ({"document_type_id": -3}, "document_type_id"),
        ({"storage_path_id": 0}, "storage_path_id"),
    ],
)
def test_invalid_filters_are_rejected(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        document_filter_params(**kwargs)  # type: ignore[arg-type]


async def test_client_combines_filters_with_mode_custom_fields_and_pagination() -> None:
    seen: list[httpx.Request] = []
    custom = ["due", "range", ["today", "in 30 days"]]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"count": 0, "results": []})

    async with PaperlessClient(SETTINGS, transport=httpx.MockTransport(handler)) as client:
        await client.search_documents(
            query="invoice",
            mode="title",
            page=2,
            page_size=5,
            ordering="-created",
            similar_to_id=None,
            custom_field_query=custom,
            created_from=date(2026, 1, 1),
            created_to=date(2026, 3, 31),
            tag_ids=[1, 2],
            exclude_tag_ids=[3],
            correspondent_id=4,
            document_type_id=5,
            storage_path_id=6,
        )

    params = seen[0].url.params
    assert params["title_search"] == "invoice"
    assert params["page"] == "2"
    assert params["page_size"] == "5"
    assert json.loads(params["custom_field_query"]) == custom
    assert params["created__gte"] == "2026-01-01"
    assert params["created__lte"] == "2026-03-31"
    assert params["tags__id__all"] == "1,2"
    assert params["tags__id__none"] == "3"
    assert params["correspondent__id"] == "4"
    assert params["document_type__id"] == "5"
    assert params["storage_path__id"] == "6"


async def test_legacy_call_sends_no_filter_params() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"count": 0, "results": []})

    async with PaperlessClient(SETTINGS, transport=httpx.MockTransport(handler)) as client:
        await client.search_documents(
            query="x",
            mode="simple",
            page=1,
            page_size=20,
            ordering="-created",
            similar_to_id=None,
        )

    names = set(seen[0].url.params.keys())
    assert names == {"page", "page_size", "ordering", "truncate_content", "text"}


async def test_tool_exposes_typed_filters_and_forwards_them() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"count": 1, "results": [{"id": 7}]})

    async with (
        PaperlessClient(SETTINGS, transport=httpx.MockTransport(handler)) as paperless,
        Client(create_server(paperless)) as client,
    ):
        tool = next(t for t in await client.list_tools() if t.name == "search_documents")
        properties = tool.inputSchema["properties"]
        for name in (
            "created_from",
            "created_to",
            "tag_ids",
            "exclude_tag_ids",
            "correspondent_id",
            "document_type_id",
            "storage_path_id",
        ):
            assert name in properties
        result = await client.call_tool(
            "search_documents",
            {
                "created_from": "2026-01-01",
                "created_to": "2026-01-31",
                "tag_ids": [1],
                "exclude_tag_ids": [2],
                "correspondent_id": 3,
            },
        )

    assert result.structured_content is not None
    assert result.structured_content["results"] == [{"id": 7}]
    params = seen[0].url.params
    assert params["created__gte"] == "2026-01-01"
    assert params["created__lte"] == "2026-01-31"
    assert params["tags__id__all"] == "1"
    assert params["tags__id__none"] == "2"
    assert params["correspondent__id"] == "3"


async def test_tool_rejects_inverted_range_without_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"count": 0, "results": []})

    async with (
        PaperlessClient(SETTINGS, transport=httpx.MockTransport(handler)) as paperless,
        Client(create_server(paperless)) as client,
    ):
        result = await client.call_tool(
            "search_documents",
            {"created_from": "2026-02-01", "created_to": "2026-01-01"},
            raise_on_error=False,
        )

    assert result.is_error
    assert seen == []
