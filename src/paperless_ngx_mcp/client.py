"""Asynchronous client for the Paperless-ngx REST API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx

from paperless_ngx_mcp import __version__
from paperless_ngx_mcp.config import Settings
from paperless_ngx_mcp.default_intake import (
    DEFAULT_INTAKE_STORAGE_PATH_ID,
    DEFAULT_INTAKE_WORKFLOW_NAME,
    plan_default_intake,
    verify_default_intake,
)
from paperless_ngx_mcp.metadata_cleanup import preview_metadata_deletion
from paperless_ngx_mcp.mutation_policy import (
    DELETABLE_ORGANIZATION_OBJECT_TYPES,
    PaperlessError,
    PermanentDeletionDisabled,
    ReadOnlyError,
    enforce_transport_mutation_policy,
    ensure_deletable_organization_type,
    ensure_metadata_deletion_operation,
    ensure_reference_review_before_deletion,
    ensure_safe_document_bulk_method,
    ensure_writable_organization_type,
    ensure_write_enabled,
)
from paperless_ngx_mcp.organization import (
    DOCUMENT_COUNT_FILTERS,
    ORGANIZATION_OBJECT_TYPES,
    compact_organization_item,
    enrich_organization_item,
    summarize_organization,
)

JsonObject = dict[str, Any]
QueryValue = str | int | bool

__all__ = [
    "DEFAULT_INTAKE_STORAGE_PATH_ID",
    "DEFAULT_INTAKE_WORKFLOW_NAME",
    "JsonObject",
    "PaperlessApiError",
    "PaperlessClient",
    "PaperlessError",
    "PermanentDeletionDisabled",
    "ReadOnlyError",
]

MISSING_METADATA_FILTERS: dict[str, tuple[str, QueryValue]] = {
    "correspondent": ("correspondent__isnull", True),
    "document_type": ("document_type__isnull", True),
    "storage_path": ("storage_path__isnull", True),
    "tags": ("is_tagged", False),
    "custom_fields": ("has_custom_fields", False),
    "archive_serial_number": ("archive_serial_number__isnull", True),
}

DOCUMENT_METADATA_FILTERS = {
    "tag": "tags__id",
    "correspondent": "correspondent__id",
    "document_type": "document_type__id",
    "storage_path": "storage_path__id",
    "custom_field": "custom_fields__id__in",
}

NAME_FILTER_OBJECT_TYPES = frozenset(
    {"tags", "correspondents", "document_types", "storage_paths", "custom_fields"}
)

TASK_FIELDS = (
    "id",
    "task_id",
    "task_type",
    "status",
    "date_created",
    "date_started",
    "date_done",
    "duration_seconds",
    "wait_time_seconds",
    "related_document_ids",
)


class PaperlessApiError(PaperlessError):
    """An HTTP request to Paperless failed."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Paperless API returned HTTP {status_code}: {message}")


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
        _allow_workflow_deletion: bool = False,
        _allow_metadata_deletion: bool = False,
    ) -> JsonObject:
        is_mutation = enforce_transport_mutation_policy(
            method,
            path,
            json,
            allow_workflow_deletion=_allow_workflow_deletion,
            allow_metadata_deletion=_allow_metadata_deletion,
        )
        if is_mutation:
            ensure_write_enabled(self.settings.paperless_read_only)
        response = await self._client.request(method, path.lstrip("/"), params=params, json=json)
        if response.is_error:
            raise PaperlessApiError(response.status_code, self._error_message(response))
        if response.status_code == 204:
            return {"deleted": True}

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
                "http_delete_requests": {
                    "documents": False,
                    "workflow_objects": "delete_workflow only",
                },
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
            if query:
                params["text"] = query
        elif mode == "title":
            params["title_search"] = query
        elif mode == "advanced":
            params["query"] = query
        else:
            raise ValueError(f"Unsupported search mode: {mode}")

        payload = await self.request("GET", "api/documents/", params=params)
        payload.pop("all", None)
        corrected_query = payload.get("corrected_query")
        if not corrected_query:
            payload.pop("corrected_query", None)
        results = payload.get("results")
        if isinstance(results, list):
            payload["results"] = [
                self._document_summary(item) for item in results if isinstance(item, dict)
            ]
        result = self._compact_page(payload, page=page, page_size=page_size)
        if corrected_query:
            result["corrected_query"] = corrected_query
        return result

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

    async def get_document_history(
        self,
        document_id: int,
        *,
        page: int = 1,
        page_size: int = 10,
        detail: str = "compact",
    ) -> JsonObject:
        """Return compact or full Paperless audit entries for one document."""
        payload = await self.request(
            "GET",
            f"api/documents/{document_id}/history/",
            params={"page": page, "page_size": page_size},
        )
        paginated_entries = payload.get("results")
        legacy_entries = payload.get("result")
        if isinstance(paginated_entries, list):
            entries = paginated_entries
            count = payload.get("count", len(entries))
            has_next = bool(payload.get("next"))
            has_previous = bool(payload.get("previous"))
        elif isinstance(legacy_entries, list):
            count = len(legacy_entries)
            start = (page - 1) * page_size
            entries = legacy_entries[start : start + page_size]
            has_next = start + page_size < count
            has_previous = page > 1
        else:
            raise PaperlessApiError(200, "Document history response was invalid")
        if detail == "compact":
            entries = [
                self._compact_history_entry(entry) for entry in entries if isinstance(entry, dict)
            ]
        return {
            "document_id": document_id,
            "count": count,
            "page": page,
            "page_size": page_size,
            "has_next": has_next,
            "has_previous": has_previous,
            "entries": entries,
        }

    async def get_task(self, task_id: str) -> JsonObject:
        """Return one Paperless background task, when it still exists."""
        payload = await self.request(
            "GET",
            "api/tasks/",
            params={"task_id": task_id, "page_size": 1},
        )
        tasks = payload.get("results", payload.get("result"))
        if not isinstance(tasks, list):
            raise PaperlessApiError(200, "Task response was invalid")
        return {
            "task_id": task_id,
            "found": bool(tasks),
            "task": self._compact_task(tasks[0]) if tasks and isinstance(tasks[0], dict) else None,
        }

    async def list_active_tasks(self) -> JsonObject:
        """Return pending and running Paperless background tasks."""
        payload = await self.request("GET", "api/tasks/active/")
        tasks = payload.get("result")
        if not isinstance(tasks, list):
            raise PaperlessApiError(200, "Active task response was invalid")
        return {
            "count": len(tasks),
            "tasks": [self._compact_task(task) for task in tasks if isinstance(task, dict)],
        }

    async def get_task_overview(self, *, days: int, include_active: bool) -> JsonObject:
        """Return native task counts, recent aggregates, and compact active tasks."""
        requests = [
            self.request("GET", "api/tasks/status_counts/"),
            self.request("GET", "api/tasks/summary/", params={"days": days}),
        ]
        if include_active:
            requests.append(self.request("GET", "api/tasks/active/"))
        counts, summary, *active_payload = await asyncio.gather(*requests)
        by_type = summary.get("result")
        if not isinstance(by_type, list):
            raise PaperlessApiError(200, "Task summary response was invalid")
        active = active_payload[0].get("result") if active_payload else []
        if not isinstance(active, list):
            raise PaperlessApiError(200, "Active task response was invalid")
        return {
            "days": days,
            "status_counts": counts,
            "by_type": by_type,
            "active": [self._compact_task(task) for task in active if isinstance(task, dict)],
        }

    async def get_document_suggestions(self, document_id: int, *, source: str) -> JsonObject:
        """Return native classifier and/or AI suggestions without applying them."""
        sources = ("paperless", "ai") if source == "both" else (source,)
        paths = {
            "paperless": f"api/documents/{document_id}/suggestions/",
            "ai": f"api/documents/{document_id}/ai_suggestions/",
        }
        responses = await asyncio.gather(
            *(self.request("GET", paths[item]) for item in sources),
            return_exceptions=True,
        )
        result: JsonObject = {"document_id": document_id}
        unavailable: list[str] = []
        for item, response in zip(sources, responses, strict=True):
            if (
                isinstance(response, PaperlessApiError)
                and response.status_code in {400, 404, 501}
                and source == "both"
                and item == "ai"
            ):
                unavailable.append(item)
            elif isinstance(response, BaseException):
                raise response
            else:
                result[item] = response
        if unavailable:
            result["unavailable"] = unavailable
        return result

    async def get_archive_statistics(self) -> JsonObject:
        """Return native Paperless archive statistics with empty values removed."""
        result = self._prune_empty(await self.request("GET", "api/statistics/"))
        if not isinstance(result, dict):
            raise PaperlessApiError(200, "Statistics response was invalid")
        return result

    async def list_objects(
        self,
        object_type: str,
        *,
        page: int,
        page_size: int,
        ordering: str,
        detail: str = "full",
        item_id: int | None = None,
        name_contains: str | None = None,
    ) -> JsonObject:
        if object_type not in ORGANIZATION_OBJECT_TYPES:
            raise ValueError(f"Unsupported object type: {object_type}")
        if item_id is not None:
            item = await self.request("GET", f"api/{object_type}/{item_id}/")
            enriched = enrich_organization_item(item)
            return {
                "count": 1,
                "page": 1,
                "page_size": 1,
                "has_next": False,
                "has_previous": False,
                "results": [
                    compact_organization_item(object_type, enriched)
                    if detail == "compact"
                    else enriched
                ],
            }
        params: dict[str, QueryValue] = {"page": page, "page_size": page_size}
        if object_type not in {"mail_rules", "saved_views", "workflows"}:
            params["ordering"] = ordering
        if name_contains is not None:
            if object_type not in NAME_FILTER_OBJECT_TYPES:
                raise ValueError(f"Name filtering is not supported for {object_type}")
            params["name__icontains"] = name_contains
        payload = await self.request(
            "GET",
            f"api/{object_type}/",
            params=params,
        )
        payload.pop("all", None)
        payload = self._enrich_object_results(payload)
        results = payload.get("results")
        if detail == "compact" and isinstance(results, list):
            payload["results"] = [
                compact_organization_item(object_type, item)
                for item in results
                if isinstance(item, dict)
            ]
        return self._compact_page(payload, page=page, page_size=page_size)

    async def get_organization_overview(self, *, sample_size: int) -> JsonObject:
        object_types = sorted(ORGANIZATION_OBJECT_TYPES - {"mail_rules"})
        object_results = await asyncio.gather(
            *(self._fetch_all_objects(object_type) for object_type in object_types)
        )
        count_results = await asyncio.gather(
            *(self._count_documents(filter_pair) for filter_pair in DOCUMENT_COUNT_FILTERS.values())
        )
        objects = dict(zip(object_types, object_results, strict=True))
        unavailable: list[str] = []
        try:
            objects["mail_rules"] = await self._fetch_all_objects("mail_rules")
        except PaperlessApiError as exc:
            if exc.status_code not in {403, 404}:
                raise
            unavailable.append("mail_rules")
        result = summarize_organization(
            objects,
            dict(zip(DOCUMENT_COUNT_FILTERS, count_results, strict=True)),
            sample_size=sample_size,
        )
        if unavailable:
            result["unavailable"] = unavailable
        return result

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
        result = self._compact_page(payload, page=page, page_size=page_size)
        result["missing_field"] = missing_field
        return result

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
        result = self._compact_page(payload, page=page, page_size=page_size)
        result["metadata"] = {"object_type": object_type, "object_id": object_id}
        return result

    async def create_organization_item(
        self,
        object_type: str,
        values: JsonObject,
    ) -> JsonObject:
        ensure_writable_organization_type(object_type)
        return await self.request("POST", f"api/{object_type}/", json=values)

    async def update_organization_item(
        self,
        object_type: str,
        item_id: int,
        changes: JsonObject,
    ) -> JsonObject:
        ensure_writable_organization_type(object_type)
        if not changes:
            raise ValueError("At least one organization change must be provided")
        return await self.request("PATCH", f"api/{object_type}/{item_id}/", json=changes)

    async def list_workflows(self, *, page: int, page_size: int) -> JsonObject:
        """List Paperless workflows through GET /api/workflows/."""
        return await self.list_objects(
            "workflows",
            page=page,
            page_size=page_size,
            ordering="name",
            detail="compact",
        )

    async def get_workflow(self, workflow_id: int) -> JsonObject:
        """Retrieve one workflow through GET /api/workflows/{id}/."""
        return await self.request("GET", f"api/workflows/{workflow_id}/")

    async def create_workflow(self, values: JsonObject) -> JsonObject:
        """Create a nested Paperless workflow through POST /api/workflows/."""
        return await self.request("POST", "api/workflows/", json=values)

    async def update_workflow(self, workflow_id: int, changes: JsonObject) -> JsonObject:
        """PATCH one nested Paperless workflow."""
        if not changes:
            raise ValueError("At least one workflow change must be provided")
        return await self.request("PATCH", f"api/workflows/{workflow_id}/", json=changes)

    async def delete_workflow(self, workflow_id: int, *, dry_run: bool) -> JsonObject:
        """Delete one workflow only after an explicit non-dry-run request."""
        workflow = await self.get_workflow(workflow_id)
        result: JsonObject = {
            "dry_run": dry_run,
            "workflow_id": workflow_id,
            "workflow": workflow,
            "deleted": False,
        }
        if dry_run:
            return result
        deletion = await self.request(
            "DELETE",
            f"api/workflows/{workflow_id}/",
            _allow_workflow_deletion=True,
        )
        result["deleted"] = True
        result["paperless_response"] = deletion
        return result

    async def configure_default_intake(
        self,
        storage_path_id: int,
        *,
        dry_run: bool,
        enabled: bool,
    ) -> JsonObject:
        """Create or update only the MCP-owned default intake workflow."""
        storage_paths, workflows = await asyncio.gather(
            self._fetch_all_objects("storage_paths"),
            self._fetch_all_objects("workflows"),
        )
        storage_path = self._find_object_by_id(storage_paths, storage_path_id, "storage path")
        plan = plan_default_intake(workflows, storage_path_id, enabled=enabled)
        result = plan.result(storage_path)
        result["dry_run"] = dry_run
        result["planned_operation"] = plan.operation
        result["planned_definition"] = plan.definition
        if dry_run or not plan.changed:
            return result

        if plan.operation == "create":
            saved = await self.create_workflow(plan.definition)
        else:
            if plan.workflow_id is None:
                raise PaperlessApiError(200, "Existing default intake workflow has no valid ID")
            saved = await self.update_workflow(plan.workflow_id, plan.definition)
        saved_id = saved.get("id")
        if not isinstance(saved_id, int):
            raise PaperlessApiError(200, "Workflow create/update response has no valid ID")
        result.update(plan.result(storage_path))
        result["workflow_id"] = saved_id
        result["paperless_response"] = saved
        return result

    async def verify_default_intake(self) -> JsonObject:
        """Read-only verification of the reserved default intake workflow."""
        storage_paths, workflows = await asyncio.gather(
            self._fetch_all_objects("storage_paths"),
            self._fetch_all_objects("workflows"),
        )
        storage_path = self._find_object_by_id_or_none(
            storage_paths,
            DEFAULT_INTAKE_STORAGE_PATH_ID,
        )
        return verify_default_intake(workflows, storage_path)

    async def _preview_organization_object_deletion(
        self,
        object_type: str,
        item_ids: list[int] | None,
    ) -> JsonObject:
        """Check document and tag-hierarchy references before metadata deletion."""
        ensure_deletable_organization_type(object_type)
        items, workflows, mail_rules, saved_views = await asyncio.gather(
            self._fetch_all_objects(object_type),
            self._fetch_all_rule_records("workflows"),
            self._fetch_all_rule_records("mail_rules"),
            self._fetch_all_rule_records("saved_views"),
        )
        try:
            return preview_metadata_deletion(
                object_type,
                items,
                workflows,
                mail_rules,
                saved_views,
                item_ids,
            )
        except ValueError as exc:
            raise PaperlessApiError(200, str(exc)) from exc

    async def bulk_edit_objects(
        self,
        object_type: str,
        objects: list[int],
        operation: str,
        *,
        dry_run: bool,
    ) -> JsonObject:
        """Mirror Paperless object bulk editing with centralized safety checks."""
        ensure_metadata_deletion_operation(object_type, operation)
        preview = await self._preview_organization_object_deletion(object_type, objects)
        blocked = preview["blocked"]
        missing_ids = preview["missing_ids"]
        ensure_reference_review_before_deletion(
            dry_run=dry_run,
            blocked=blocked,
            missing_ids=missing_ids,
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
            _allow_metadata_deletion=True,
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

    async def reprocess_documents(self, document_ids: list[int]) -> JsonObject:
        """Queue Paperless reprocessing using its configured OCR mode."""
        return await self._bulk_edit_documents(document_ids, "reprocess", {})

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
        return self._compact_page(payload, page=page, page_size=page_size)

    async def restore_documents_from_trash(self, document_ids: list[int]) -> JsonObject:
        return await self.request(
            "POST",
            "api/trash/",
            json={"documents": document_ids, "action": "restore"},
        )

    async def update_document(self, document_id: int, changes: JsonObject) -> JsonObject:
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
        ensure_safe_document_bulk_method(method)
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
                detail="full",
            )
            results = payload.get("results")
            if not isinstance(results, list):
                raise PaperlessApiError(200, f"Invalid paginated response for {object_type}")
            items.extend(item for item in results if isinstance(item, dict))
            if not payload.get("has_next"):
                return items
            page += 1
            if page > 1_000:
                raise PaperlessApiError(200, f"Pagination limit exceeded for {object_type}")

    async def _fetch_all_rule_records(self, object_type: str) -> list[JsonObject]:
        """Read every rule source used by metadata-deletion reference checks.

        Invalid pagination data is a safety failure: callers must not infer that a
        record is unused when a reference source cannot be interpreted.
        """
        page = 1
        records: list[JsonObject] = []
        while True:
            payload = await self.request(
                "GET",
                f"api/{object_type}/",
                params={"page": page, "page_size": 100},
            )
            results = payload.get("results")
            if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
                raise PaperlessApiError(200, f"Invalid paginated response for {object_type}")
            records.extend(results)
            if not payload.get("next"):
                return records
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
    def _find_object_by_id(
        objects: list[JsonObject],
        object_id: int,
        object_label: str,
    ) -> JsonObject:
        result = PaperlessClient._find_object_by_id_or_none(objects, object_id)
        if result is None:
            raise ValueError(
                f"{object_label.capitalize()} {object_id} does not exist or is not visible"
            )
        return result

    @staticmethod
    def _find_object_by_id_or_none(
        objects: list[JsonObject],
        object_id: int,
    ) -> JsonObject | None:
        return next(
            (item for item in objects if item.get("id") == object_id),
            None,
        )

    @staticmethod
    def _enrich_object_results(payload: JsonObject) -> JsonObject:
        results = payload.get("results")
        if isinstance(results, list):
            payload["results"] = [
                enrich_organization_item(item) for item in results if isinstance(item, dict)
            ]
        return payload

    @staticmethod
    def _compact_page(payload: JsonObject, *, page: int, page_size: int) -> JsonObject:
        return {
            "count": payload.get("count", 0),
            "page": page,
            "page_size": page_size,
            "has_next": bool(payload.get("next")),
            "has_previous": bool(payload.get("previous")),
            "results": payload.get("results", []),
        }

    @staticmethod
    def _compact_history_entry(entry: JsonObject) -> JsonObject:
        compact = {
            field: entry[field]
            for field in ("id", "timestamp", "action", "actor")
            if field in entry
        }
        changes = entry.get("changes")
        if isinstance(changes, dict):
            compact["changes"] = {
                field: PaperlessClient._content_change_summary(value)
                if field == "content"
                else value
                for field, value in changes.items()
            }
        return compact

    @staticmethod
    def _content_change_summary(value: Any) -> JsonObject:
        summary: JsonObject = {"changed": True}
        if isinstance(value, list) and len(value) == 2:
            before, after = value
            if isinstance(before, str):
                summary["before_chars"] = len(before)
            if isinstance(after, str):
                summary["after_chars"] = len(after)
        elif isinstance(value, dict):
            for key, label in (
                ("old", "before_chars"),
                ("old_value", "before_chars"),
                ("new", "after_chars"),
                ("new_value", "after_chars"),
            ):
                text = value.get(key)
                if isinstance(text, str):
                    summary[label] = len(text)
        elif isinstance(value, str):
            summary["chars"] = len(value)
        return summary

    @staticmethod
    def _compact_task(task: JsonObject) -> JsonObject:
        compact = {field: task[field] for field in TASK_FIELDS if field in task}
        if "task_type" not in compact:
            task_type = task.get("task_name", task.get("type"))
            if task_type is not None:
                compact["task_type"] = task_type
        if "related_document_ids" not in compact:
            related = task.get("related_document")
            if isinstance(related, int):
                compact["related_document_ids"] = [related]
            elif isinstance(related, list):
                compact["related_document_ids"] = related
        result_data = task.get("result_data", task.get("result"))
        if isinstance(result_data, dict):
            important = {
                key: result_data[key]
                for key in ("error", "message", "document_id")
                if key in result_data
            }
            if important:
                compact["result"] = important
        elif isinstance(result_data, str) and result_data:
            compact["result"] = result_data[:500]
            if len(result_data) > 500:
                compact["result_truncated"] = True
        return compact

    @staticmethod
    def _prune_empty(value: Any) -> Any:
        if isinstance(value, dict):
            result: JsonObject = {}
            for key, item in value.items():
                compact = PaperlessClient._prune_empty(item)
                if compact not in (None, "", [], {}):
                    result[key] = compact
            return result
        if isinstance(value, list):
            return [PaperlessClient._prune_empty(item) for item in value]
        return value

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
