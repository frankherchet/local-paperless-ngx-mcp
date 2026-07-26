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
    "workflows",
}

MISSING_METADATA_FILTERS: dict[str, tuple[str, QueryValue]] = {
    "correspondent": ("correspondent__isnull", True),
    "document_type": ("document_type__isnull", True),
    "storage_path": ("storage_path__isnull", True),
    "tags": ("is_tagged", False),
    "custom_fields": ("has_custom_fields", False),
    "archive_serial_number": ("archive_serial_number__isnull", True),
}

WRITABLE_ORGANIZATION_OBJECT_TYPES = {
    "tags",
    "correspondents",
    "document_types",
    "storage_paths",
    "custom_fields",
}

DELETABLE_ORGANIZATION_OBJECT_TYPES = {
    "tags",
    "correspondents",
    "document_types",
    "storage_paths",
}

SAFE_BULK_DOCUMENT_METHODS = {
    "set_correspondent",
    "set_document_type",
    "set_storage_path",
    "add_tag",
    "remove_tag",
    "modify_tags",
    "modify_custom_fields",
    "delete",  # Paperless moves documents to its reversible trash.
}

DOCUMENT_METADATA_FILTERS = {
    "tag": "tags__id",
    "correspondent": "correspondent__id",
    "document_type": "document_type__id",
    "storage_path": "storage_path__id",
    "custom_field": "custom_fields__id__in",
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


class PermanentDeletionDisabled(PaperlessError):
    """The requested operation could permanently delete Paperless data."""


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
        self._enforce_deletion_safety(method, path, json)
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
            "safety_policy": {
                "permanent_document_deletion": False,
                "empty_trash": False,
                "http_delete_requests": False,
                "move_to_trash": not self.settings.paperless_read_only,
                "bulk_edit_objects": {
                    "enabled": not self.settings.paperless_read_only,
                    "allowed_types": sorted(DELETABLE_ORGANIZATION_OBJECT_TYPES),
                    "allowed_operations": ["delete"],
                    "delete_reference_check_required": True,
                    "workflow_reference_check_required": True,
                },
            },
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
        include_file_metadata: bool,
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
        if include_file_metadata:
            document["file_metadata"] = await self.request(
                "GET",
                f"api/documents/{document_id}/metadata/",
            )
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
        if object_type not in {"saved_views", "workflows"}:
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

    async def find_documents_by_metadata(
        self,
        object_type: str,
        object_id: int,
        *,
        page: int,
        page_size: int,
        ordering: str,
    ) -> JsonObject:
        try:
            filter_name = DOCUMENT_METADATA_FILTERS[object_type]
        except KeyError as exc:
            raise ValueError(f"Unsupported metadata object type: {object_type}") from exc

        payload = await self.request(
            "GET",
            "api/documents/",
            params={
                filter_name: object_id,
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
        payload["metadata"] = {"object_type": object_type, "object_id": object_id}
        return payload

    async def create_organization_item(
        self,
        object_type: str,
        values: JsonObject,
    ) -> JsonObject:
        self._ensure_write_enabled()
        self._validate_writable_object_type(object_type)
        return await self.request("POST", f"api/{object_type}/", json=values)

    async def update_organization_item(
        self,
        object_type: str,
        item_id: int,
        changes: JsonObject,
    ) -> JsonObject:
        self._ensure_write_enabled()
        self._validate_writable_object_type(object_type)
        if not changes:
            raise ValueError("At least one organization change must be provided")
        return await self.request("PATCH", f"api/{object_type}/{item_id}/", json=changes)

    async def _preview_organization_object_deletion(
        self,
        object_type: str,
        item_ids: list[int] | None,
    ) -> JsonObject:
        """Check document and tag-hierarchy references before metadata deletion."""
        self._validate_deletable_object_type(object_type)
        items = await self._fetch_all_objects(object_type)
        workflows = await self._fetch_all_objects("workflows")
        selected_ids = set(item_ids) if item_ids is not None else None
        items_by_id = {
            item["id"]: item
            for item in items
            if isinstance(item.get("id"), int)
            and (selected_ids is None or item["id"] in selected_ids)
        }

        child_tag_ids: dict[int, list[int]] = {}
        if object_type == "tags":
            for item in items:
                item_id = item.get("id")
                parent_id = item.get("parent")
                if isinstance(item_id, int) and isinstance(parent_id, int):
                    child_tag_ids.setdefault(parent_id, []).append(item_id)

        candidates: list[JsonObject] = []
        blocked: list[JsonObject] = []
        referenced_items_omitted = 0
        for item_id in sorted(items_by_id):
            item = items_by_id[item_id]
            document_count = item.get("document_count")
            children = sorted(child_tag_ids.get(item_id, []))
            if selected_ids is None and isinstance(document_count, int) and document_count > 0:
                referenced_items_omitted += 1
                continue
            reasons: list[str] = []
            if not isinstance(document_count, int):
                reasons.append("document_count_unavailable")
            elif document_count != 0:
                reasons.append("referenced_by_documents")
            if children:
                reasons.append("parent_of_tags")
            workflow_references = self._find_workflow_references(
                workflows,
                object_type,
                item_id,
            )
            if workflow_references:
                reasons.append("referenced_by_workflows")

            assessment: JsonObject = {
                "id": item_id,
                "name": item.get("name"),
                "document_count": document_count,
                "child_tag_ids": children,
                "deletable": not reasons,
                "blocking_reasons": reasons,
                "workflow_references": workflow_references,
            }
            if object_type == "storage_paths":
                assessment["path"] = item.get("path")
            (candidates if not reasons else blocked).append(assessment)

        missing_ids = sorted(selected_ids - set(items_by_id)) if selected_ids is not None else []
        return {
            "object_type": object_type,
            "requested_ids": sorted(selected_ids) if selected_ids is not None else None,
            "candidates": candidates,
            "blocked": blocked,
            "missing_ids": missing_ids,
            "candidate_count": len(candidates),
            "blocked_count": len(blocked),
            "scanned_count": len(items),
            "referenced_items_omitted": referenced_items_omitted,
            "reference_checks": ["document_count", "workflow_triggers", "workflow_actions"]
            + (["child_tag_relationships"] if object_type == "tags" else []),
            "requires_explicit_user_approval": True,
        }

    async def bulk_edit_objects(
        self,
        object_type: str,
        objects: list[int],
        operation: str,
        *,
        dry_run: bool,
    ) -> JsonObject:
        """Mirror Paperless object bulk editing with centralized safety checks."""
        self._validate_deletable_object_type(object_type)
        if operation != "delete":
            raise ValueError(f"Unsupported object bulk operation: {operation}")
        if not dry_run:
            self._ensure_write_enabled()

        preview = await self._preview_organization_object_deletion(object_type, objects)
        blocked = preview["blocked"]
        missing_ids = preview["missing_ids"]
        if not dry_run and (blocked or missing_ids):
            raise ValueError(
                "Organization deletion blocked by reference check: "
                f"blocked={blocked}, missing_ids={missing_ids}"
            )

        result: JsonObject = {
            "dry_run": dry_run,
            "operation": operation,
            "object_type": object_type,
            "objects": objects,
            "preflight": preview,
            "deletion_submitted": False,
        }
        if dry_run:
            return result

        response = await self.request(
            "POST",
            "api/bulk_edit_objects/",
            json={
                "objects": objects,
                "object_type": object_type,
                "operation": operation,
            },
        )
        result["deletion_submitted"] = True
        result["paperless_response"] = response
        return result

    async def set_document_metadata_field(
        self,
        document_ids: list[int],
        field: str,
        value_id: int | None,
    ) -> JsonObject:
        methods = {
            "correspondent": ("set_correspondent", "correspondent"),
            "document_type": ("set_document_type", "document_type"),
            "storage_path": ("set_storage_path", "storage_path"),
        }
        try:
            method, parameter_name = methods[field]
        except KeyError as exc:
            raise ValueError(f"Unsupported document metadata field: {field}") from exc
        return await self._bulk_edit_documents(
            document_ids,
            method,
            {parameter_name: value_id},
        )

    async def modify_document_tags(
        self,
        document_ids: list[int],
        *,
        add_tag_ids: list[int],
        remove_tag_ids: list[int],
    ) -> JsonObject:
        if not add_tag_ids and not remove_tag_ids:
            raise ValueError("At least one tag must be added or removed")
        return await self._bulk_edit_documents(
            document_ids,
            "modify_tags",
            {
                "add_tags": add_tag_ids,
                "remove_tags": remove_tag_ids,
            },
        )

    async def move_documents_to_trash(self, document_ids: list[int]) -> JsonObject:
        """Move documents to Paperless trash; this does not permanently delete them."""
        return await self._bulk_edit_documents(document_ids, "delete", {})

    async def list_trashed_documents(
        self,
        *,
        page: int,
        page_size: int,
    ) -> JsonObject:
        payload = await self.request(
            "GET",
            "api/trash/",
            params={"page": page, "page_size": page_size},
        )
        payload.pop("all", None)
        results = payload.get("results")
        if isinstance(results, list):
            payload["results"] = [
                self._document_summary(item) for item in results if isinstance(item, dict)
            ]
        return payload

    async def restore_documents_from_trash(self, document_ids: list[int]) -> JsonObject:
        self._ensure_write_enabled()
        return await self.request(
            "POST",
            "api/trash/",
            json={"documents": document_ids, "action": "restore"},
        )

    async def update_document(self, document_id: int, changes: JsonObject) -> JsonObject:
        self._ensure_write_enabled()
        if not changes:
            raise ValueError("At least one change must be provided")
        return await self.request("PATCH", f"api/documents/{document_id}/", json=changes)

    async def document_notes(
        self,
        document_id: int,
        operation: str,
        *,
        note: str | None,
        page: int,
        page_size: int,
    ) -> JsonObject:
        path = f"api/documents/{document_id}/notes/"
        if operation == "list":
            return await self.request(
                "GET",
                path,
                params={"page": page, "page_size": page_size},
            )
        if operation == "create":
            self._ensure_write_enabled()
            if note is None or not note.strip():
                raise ValueError("note is required when operation='create'")
            return await self.request("POST", path, json={"note": note.strip()})
        raise ValueError(f"Unsupported document notes operation: {operation}")

    async def _bulk_edit_documents(
        self,
        document_ids: list[int],
        method: str,
        parameters: JsonObject,
    ) -> JsonObject:
        self._ensure_write_enabled()
        if method not in SAFE_BULK_DOCUMENT_METHODS:
            raise PermanentDeletionDisabled(f"Bulk document method is not allowed: {method}")
        return await self.request(
            "POST",
            "api/documents/bulk_edit/",
            json={
                "documents": document_ids,
                "method": method,
                "parameters": parameters,
            },
        )

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

    def _ensure_write_enabled(self) -> None:
        if self.settings.paperless_read_only:
            raise ReadOnlyError(
                "Write tools are disabled. Set PAPERLESS_READ_ONLY=false to enable updates."
            )

    @staticmethod
    def _validate_writable_object_type(object_type: str) -> None:
        if object_type not in WRITABLE_ORGANIZATION_OBJECT_TYPES:
            raise ValueError(f"Unsupported writable organization object type: {object_type}")

    @staticmethod
    def _validate_deletable_object_type(object_type: str) -> None:
        if object_type not in DELETABLE_ORGANIZATION_OBJECT_TYPES:
            raise ValueError(f"Unsupported deletable organization object type: {object_type}")

    @staticmethod
    def _enforce_deletion_safety(
        method: str,
        path: str,
        json: JsonObject | None,
    ) -> None:
        normalized_path = path.lstrip("/")
        if method.upper() == "DELETE":
            raise PermanentDeletionDisabled(
                "HTTP DELETE is disabled in this MCP server. "
                "Documents can only be moved to Paperless trash."
            )
        if method.upper() == "POST" and normalized_path == "api/bulk_edit_objects/":
            object_type = json.get("object_type") if json is not None else None
            operation = json.get("operation") if json is not None else None
            object_ids = json.get("objects") if json is not None else None
            valid_ids = (
                isinstance(object_ids, list)
                and bool(object_ids)
                and all(
                    isinstance(item_id, int) and not isinstance(item_id, bool) and item_id > 0
                    for item_id in object_ids
                )
            )
            if (
                object_type in DELETABLE_ORGANIZATION_OBJECT_TYPES
                and operation == "delete"
                and valid_ids
            ):
                return
            raise PermanentDeletionDisabled(
                "Object bulk editing is restricted to deleting explicitly selected tags, "
                "correspondents, document types, or storage paths."
            )
        if method.upper() == "POST" and normalized_path == "api/trash/":
            action = json.get("action") if json is not None else None
            if action == "restore":
                return
            raise PermanentDeletionDisabled(
                "Only restoring documents is allowed on the Paperless trash endpoint. "
                "Emptying trash is permanently disabled."
            )
        if (
            method.upper() == "POST"
            and normalized_path == "api/documents/bulk_edit/"
            and json is not None
        ):
            bulk_method = json.get("method")
            if bulk_method not in SAFE_BULK_DOCUMENT_METHODS:
                raise PermanentDeletionDisabled(
                    f"Bulk document method is not allowed: {bulk_method}"
                )

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
    def _find_workflow_references(
        workflows: list[JsonObject],
        object_type: str,
        item_id: int,
    ) -> list[JsonObject]:
        fields: dict[str, tuple[tuple[str, bool], ...]] = {
            "tags": (
                ("filter_has_tags", True),
                ("filter_has_all_tags", True),
                ("filter_has_not_tags", True),
                ("assign_tags", True),
                ("remove_tags", True),
            ),
            "correspondents": (
                ("filter_has_correspondent", False),
                ("filter_has_not_correspondents", True),
                ("assign_correspondent", False),
                ("remove_correspondents", True),
            ),
            "document_types": (
                ("filter_has_document_type", False),
                ("filter_has_not_document_types", True),
                ("assign_document_type", False),
                ("remove_document_types", True),
            ),
            "storage_paths": (
                ("filter_has_storage_path", False),
                ("filter_has_not_storage_paths", True),
                ("assign_storage_path", False),
                ("remove_storage_paths", True),
            ),
        }
        references: list[JsonObject] = []
        configured_fields = fields[object_type]
        trigger_fields = {
            field for field, _many in configured_fields if field.startswith("filter_")
        }

        for workflow in workflows:
            for section_name in ("triggers", "actions"):
                entries = workflow.get(section_name)
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    entry_id = entry.get("id")
                    for field, many in configured_fields:
                        if (field in trigger_fields) != (section_name == "triggers"):
                            continue
                        value = entry.get(field)
                        is_reference = (
                            isinstance(value, list) and item_id in value
                            if many
                            else value == item_id
                        )
                        if is_reference:
                            references.append(
                                {
                                    "workflow_id": workflow.get("id"),
                                    "workflow_name": workflow.get("name"),
                                    "workflow_enabled": workflow.get("enabled"),
                                    "section": section_name,
                                    "entry_id": entry_id,
                                    "field": field,
                                }
                            )
        return references

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
