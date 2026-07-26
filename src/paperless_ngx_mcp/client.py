"""Asynchronous client for the Paperless-ngx REST API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx

from paperless_ngx_mcp import __version__
from paperless_ngx_mcp.config import Settings

JsonObject = dict[str, Any]
QueryValue = str | int | bool

ORGANIZATION_OBJECT_TYPES = {
    "tags",
    "correspondents",
    "document_types",
    "storage_paths",
    "custom_fields",
    "saved_views",
}

MISSING_METADATA_FILTERS: dict[str, tuple[str, QueryValue]] = {
    "correspondent": ("correspondent__isnull", True),
    "document_type": ("document_type__isnull", True),
    "storage_path": ("storage_path__isnull", True),
    "tags": ("is_tagged", False),
    "custom_fields": ("has_custom_fields", False),
    "archive_serial_number": ("archive_serial_number__isnull", True),
}


class PaperlessError(RuntimeError):
    """Base error raised for Paperless API failures."""


class PaperlessApiError(PaperlessError):
    """An HTTP request to Paperless failed."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Paperless API returned HTTP {status_code}: {message}")


class ReadOnlyError(PaperlessError):
    """A write was attempted while read-only mode is active."""


class PaperlessClient:
    """Small typed wrapper around the Paperless-ngx REST API."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            headers={
                "Accept": f"application/json; version={settings.paperless_api_version}",
                "Authorization": f"Token {settings.paperless_token.get_secret_value()}",
                "User-Agent": f"local-paperless-ngx-mcp/{__version__}",
            },
            timeout=settings.timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> PaperlessClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, QueryValue] | None = None,
        json: JsonObject | None = None,
    ) -> JsonObject:
        response = await self._client.request(method, path.lstrip("/"), params=params, json=json)
        if response.is_error:
            raise PaperlessApiError(response.status_code, self._error_message(response))

        try:
            payload = response.json()
        except ValueError as exc:
            raise PaperlessApiError(response.status_code, "Response was not valid JSON") from exc

        if not isinstance(payload, dict):
            return {"result": payload}
        return payload

    async def check_connection(self) -> JsonObject:
        response = await self._client.get("api/documents/", params={"page_size": 1})
        if response.is_error:
            raise PaperlessApiError(response.status_code, self._error_message(response))

        try:
            payload = response.json()
        except ValueError as exc:
            raise PaperlessApiError(response.status_code, "Response was not valid JSON") from exc

        return {
            "connected": True,
            "paperless_version": response.headers.get("X-Version"),
            "api_version": response.headers.get("X-Api-Version"),
            "document_count": payload.get("count") if isinstance(payload, dict) else None,
            "read_only": self.settings.paperless_read_only,
            "base_url": self.settings.base_url,
        }

    async def search_documents(
        self,
        *,
        query: str,
        mode: str,
        page: int,
        page_size: int,
        ordering: str,
        similar_to_id: int | None,
    ) -> JsonObject:
        params: dict[str, QueryValue] = {
            "page": page,
            "page_size": page_size,
            "ordering": ordering,
            "truncate_content": True,
        }
        if mode == "similar":
            if similar_to_id is None:
                raise ValueError("similar_to_id is required when mode='similar'")
            params["more_like_id"] = similar_to_id
        elif mode == "simple":
            params["text"] = query
        elif mode == "title":
            params["title_search"] = query
        elif mode == "advanced":
            params["query"] = query
        else:
            raise ValueError(f"Unsupported search mode: {mode}")

        payload = await self.request("GET", "api/documents/", params=params)
        results = payload.get("results")
        if isinstance(results, list):
            payload["results"] = [
                self._document_summary(item) for item in results if isinstance(item, dict)
            ]
        return payload

    async def get_document(
        self,
        document_id: int,
        *,
        include_content: bool,
        max_content_chars: int,
    ) -> JsonObject:
        document = await self.request("GET", f"api/documents/{document_id}/")
        content = document.get("content")
        if not include_content:
            document.pop("content", None)
        elif isinstance(content, str) and len(content) > max_content_chars:
            document["content"] = content[:max_content_chars]
            document["content_truncated"] = True
            document["content_total_chars"] = len(content)
        return document

    async def list_objects(
        self,
        object_type: str,
        *,
        page: int,
        page_size: int,
        ordering: str,
    ) -> JsonObject:
        if object_type not in ORGANIZATION_OBJECT_TYPES:
            raise ValueError(f"Unsupported object type: {object_type}")
        params: dict[str, QueryValue] = {"page": page, "page_size": page_size}
        if object_type != "saved_views":
            params["ordering"] = ordering
        payload = await self.request(
            "GET",
            f"api/{object_type}/",
            params=params,
        )
        payload.pop("all", None)
        return self._enrich_object_results(payload)

    async def get_organization_overview(self, *, sample_size: int) -> JsonObject:
        from paperless_ngx_mcp.organization import summarize_organization

        object_types = sorted(ORGANIZATION_OBJECT_TYPES)
        object_results = await asyncio.gather(
            *(self._fetch_all_objects(object_type) for object_type in object_types)
        )
        count_keys = {
            "total": None,
            "without_correspondent": ("correspondent__isnull", True),
            "without_document_type": ("document_type__isnull", True),
            "without_storage_path": ("storage_path__isnull", True),
            "without_tags": ("is_tagged", False),
            "without_custom_fields": ("has_custom_fields", False),
            "without_archive_serial_number": ("archive_serial_number__isnull", True),
        }
        count_results = await asyncio.gather(
            *(self._count_documents(filter_pair) for filter_pair in count_keys.values())
        )
        return summarize_organization(
            dict(zip(object_types, object_results, strict=True)),
            dict(zip(count_keys, count_results, strict=True)),
            sample_size=sample_size,
        )

    async def find_documents_missing_metadata(
        self,
        missing_field: str,
        *,
        page: int,
        page_size: int,
        ordering: str,
    ) -> JsonObject:
        try:
            filter_name, filter_value = MISSING_METADATA_FILTERS[missing_field]
        except KeyError as exc:
            raise ValueError(f"Unsupported missing metadata field: {missing_field}") from exc

        payload = await self.request(
            "GET",
            "api/documents/",
            params={
                filter_name: filter_value,
                "page": page,
                "page_size": page_size,
                "ordering": ordering,
                "truncate_content": True,
            },
        )
        payload.pop("all", None)
        results = payload.get("results")
        if isinstance(results, list):
            payload["results"] = [
                self._document_summary(item) for item in results if isinstance(item, dict)
            ]
        payload["missing_field"] = missing_field
        return payload

    async def update_document(self, document_id: int, changes: JsonObject) -> JsonObject:
        if self.settings.paperless_read_only:
            raise ReadOnlyError(
                "Write tools are disabled. Set PAPERLESS_READ_ONLY=false to enable updates."
            )
        if not changes:
            raise ValueError("At least one change must be provided")
        return await self.request("PATCH", f"api/documents/{document_id}/", json=changes)

    async def _fetch_all_objects(self, object_type: str) -> list[JsonObject]:
        page = 1
        items: list[JsonObject] = []
        while True:
            payload = await self.list_objects(
                object_type,
                page=page,
                page_size=100,
                ordering="name",
            )
            results = payload.get("results")
            if not isinstance(results, list):
                raise PaperlessApiError(200, f"Invalid paginated response for {object_type}")
            items.extend(item for item in results if isinstance(item, dict))
            if not payload.get("next"):
                return items
            page += 1
            if page > 1_000:
                raise PaperlessApiError(200, f"Pagination limit exceeded for {object_type}")

    async def _count_documents(
        self,
        filter_pair: tuple[str, QueryValue] | None,
    ) -> int:
        params: dict[str, QueryValue] = {"page_size": 1}
        if filter_pair is not None:
            params[filter_pair[0]] = filter_pair[1]
        payload = await self.request("GET", "api/documents/", params=params)
        count = payload.get("count")
        if not isinstance(count, int):
            raise PaperlessApiError(200, "Document count response was invalid")
        return count

    @staticmethod
    def _enrich_object_results(payload: JsonObject) -> JsonObject:
        from paperless_ngx_mcp.organization import enrich_organization_item

        results = payload.get("results")
        if isinstance(results, list):
            payload["results"] = [
                enrich_organization_item(item) for item in results if isinstance(item, dict)
            ]
        return payload

    @staticmethod
    def _document_summary(document: JsonObject) -> JsonObject:
        fields = (
            "id",
            "title",
            "created",
            "added",
            "modified",
            "archive_serial_number",
            "correspondent",
            "document_type",
            "storage_path",
            "tags",
            "original_file_name",
            "__search_hit__",
        )
        return {field: document[field] for field in fields if field in document}

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text[:500] or response.reason_phrase

        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str):
                return detail
        return str(payload)[:500]
