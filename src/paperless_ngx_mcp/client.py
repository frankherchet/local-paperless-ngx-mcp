"""Asynchronous client for the Paperless-ngx REST API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from paperless_ngx_mcp.config import Settings

JsonObject = dict[str, Any]
QueryValue = str | int | bool


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
                "User-Agent": "local-paperless-ngx-mcp/0.1.0",
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
        allowed = {"tags", "correspondents", "document_types", "storage_paths"}
        if object_type not in allowed:
            raise ValueError(f"Unsupported object type: {object_type}")
        return await self.request(
            "GET",
            f"api/{object_type}/",
            params={"page": page, "page_size": page_size, "ordering": ordering},
        )

    async def update_document(self, document_id: int, changes: JsonObject) -> JsonObject:
        if self.settings.paperless_read_only:
            raise ReadOnlyError(
                "Write tools are disabled. Set PAPERLESS_READ_ONLY=false to enable updates."
            )
        if not changes:
            raise ValueError("At least one change must be provided")
        return await self.request("PATCH", f"api/documents/{document_id}/", json=changes)

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
