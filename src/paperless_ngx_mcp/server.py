"""FastMCP server exposing a local Paperless-ngx instance."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Literal

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from paperless_ngx_mcp import __version__
from paperless_ngx_mcp.client import JsonObject, PaperlessClient
from paperless_ngx_mcp.config import get_settings

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
CREATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
TRASH = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)
OBJECT_BULK_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)
WORKFLOW_DELETE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)


def create_server(client: PaperlessClient | None = None) -> FastMCP:
    """Create a server, optionally using an injected client for tests."""
    server = FastMCP(
        name="Paperless-ngx",
        instructions=(
            "Search and inspect the user's local Paperless-ngx archive and organization. "
            "Use get_organization_overview before assessing tags, correspondents, document "
            "types, storage paths, custom fields, saved views, or workflows. Treat unused "
            "entries as review candidates. Use bulk_edit_objects with dry_run=true before deleting "
            "organization items, show its reference-check preview, and obtain explicit user "
            "approval before repeating with dry_run=false. Read tools are safe; writes are "
            "disabled by default. Permanent document deletion is unavailable: the server never "
            "issues HTTP DELETE for documents and never empties Paperless trash. Workflow "
            "deletion is available only through delete_workflow."
        ),
        version=__version__,
    )

    @asynccontextmanager
    async def use_client() -> AsyncIterator[PaperlessClient]:
        if client is not None:
            yield client
            return
        async with PaperlessClient(get_settings()) as runtime_client:
            yield runtime_client

    @server.tool(annotations=READ_ONLY, tags={"paperless", "status"})
    async def paperless_status() -> JsonObject:
        """Verify authentication and return Paperless/API versions and document count."""
        async with use_client() as paperless:
            return await paperless.check_connection()

    @server.tool(annotations=READ_ONLY, tags={"paperless", "documents", "search"})
    async def search_documents(
        query: str = "",
        mode: Literal["simple", "title", "advanced", "similar"] = "simple",
        page: int = 1,
        page_size: int = 20,
        ordering: str = "-created",
        similar_to_id: int | None = None,
    ) -> JsonObject:
        """Search documents.

        Modes: simple searches title and OCR content, title searches only titles,
        advanced accepts Paperless search syntax, and similar finds documents related
        to similar_to_id.
        """
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if mode != "similar" and not query.strip():
            raise ValueError("query must not be empty")

        async with use_client() as paperless:
            return await paperless.search_documents(
                query=query,
                mode=mode,
                page=page,
                page_size=page_size,
                ordering=ordering,
                similar_to_id=similar_to_id,
            )

    @server.tool(annotations=READ_ONLY, tags={"paperless", "documents"})
    async def get_document(
        document_id: int,
        include_content: bool = True,
        include_file_metadata: bool = False,
        max_content_chars: int = 20_000,
    ) -> JsonObject:
        """Return a document, optionally with OCR text and file checksums.

        include_file_metadata also calls GET /api/documents/{id}/metadata/ and
        includes original_checksum, archive_checksum, sizes, and MIME information.
        """
        if document_id < 1:
            raise ValueError("document_id must be positive")
        if not 1_000 <= max_content_chars <= 200_000:
            raise ValueError("max_content_chars must be between 1,000 and 200,000")

        async with use_client() as paperless:
            return await paperless.get_document(
                document_id,
                include_content=include_content,
                include_file_metadata=include_file_metadata,
                max_content_chars=max_content_chars,
            )

    @server.tool(annotations=READ_ONLY, tags={"paperless", "metadata"})
    async def list_metadata(
        object_type: Literal[
            "tags",
            "correspondents",
            "document_types",
            "storage_paths",
            "custom_fields",
            "saved_views",
            "workflows",
        ],
        page: int = 1,
        page_size: int = 100,
        ordering: str = "name",
    ) -> JsonObject:
        """List organization records with full metadata and human-readable matching modes.

        Supports tags, correspondents, document types, storage paths, custom fields,
        saved views, and workflows. Use pagination to inspect large collections.
        """
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

        async with use_client() as paperless:
            return await paperless.list_objects(
                object_type,
                page=page,
                page_size=page_size,
                ordering=ordering,
            )

    @server.tool(annotations=READ_ONLY, tags={"paperless", "workflows"})
    async def list_workflows(page: int = 1, page_size: int = 100) -> JsonObject:
        """List Paperless workflows through GET /api/workflows/."""
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        async with use_client() as paperless:
            return await paperless.list_workflows(page=page, page_size=page_size)

    @server.tool(annotations=READ_ONLY, tags={"paperless", "workflows"})
    async def get_workflow(workflow_id: int) -> JsonObject:
        """Retrieve one nested Paperless workflow through GET /api/workflows/{id}/."""
        _validate_positive_ids([workflow_id], "workflow_id")
        async with use_client() as paperless:
            return await paperless.get_workflow(workflow_id)

    @server.tool(annotations=CREATE, tags={"paperless", "workflows", "write"})
    async def create_workflow(
        name: str,
        triggers: list[dict[str, Any]],
        actions: list[dict[str, Any]],
        order: int | None = None,
        enabled: bool = True,
    ) -> JsonObject:
        """Create a nested Paperless workflow through POST /api/workflows/."""
        values = _workflow_values(
            name=name,
            triggers=triggers,
            actions=actions,
            order=order,
            enabled=enabled,
        )
        async with use_client() as paperless:
            return await paperless.create_workflow(values)

    @server.tool(annotations=WRITE, tags={"paperless", "workflows", "write"})
    async def update_workflow(workflow_id: int, changes: dict[str, Any]) -> JsonObject:
        """PATCH a workflow using Paperless fields name, order, enabled, triggers, and actions."""
        _validate_positive_ids([workflow_id], "workflow_id")
        _validate_workflow_changes(changes)
        async with use_client() as paperless:
            return await paperless.update_workflow(workflow_id, changes)

    @server.tool(annotations=WORKFLOW_DELETE, tags={"paperless", "workflows", "delete"})
    async def delete_workflow(workflow_id: int, dry_run: bool = True) -> JsonObject:
        """Preview or delete one workflow; dry_run=true is the default.

        This affects only the workflow object, never existing documents. Set dry_run=false
        only after explicit user approval.
        """
        _validate_positive_ids([workflow_id], "workflow_id")
        async with use_client() as paperless:
            return await paperless.delete_workflow(workflow_id, dry_run=dry_run)

    @server.tool(annotations=CREATE, tags={"paperless", "workflows", "intake", "write"})
    async def configure_default_intake(
        storage_path_id: int,
        dry_run: bool = True,
        enabled: bool = True,
    ) -> JsonObject:
        """Idempotently configure the MCP-owned workflow for all newly added documents.

        Uses only the reserved default-intake workflow name. It never changes existing
        documents, other workflows, or workflow ordering. The generated workflow runs after
        other storage-path assignments by choosing an order above them.
        """
        _validate_positive_ids([storage_path_id], "storage_path_id")
        async with use_client() as paperless:
            return await paperless.configure_default_intake(
                storage_path_id,
                dry_run=dry_run,
                enabled=enabled,
            )

    @server.tool(annotations=READ_ONLY, tags={"paperless", "workflows", "intake"})
    async def verify_default_intake() -> JsonObject:
        """Read-only verification of the configured Standard-Eingang workflow."""
        async with use_client() as paperless:
            return await paperless.verify_default_intake()

    @server.tool(annotations=READ_ONLY, tags={"paperless", "organization", "analysis"})
    async def get_organization_overview(sample_size: int = 15) -> JsonObject:
        """Summarize Paperless organization quality across the complete archive.

        Returns usage counts, unused and single-document examples, normalized duplicate
        name groups, matching-rule distribution, tag hierarchy statistics, custom-field
        types, saved-view visibility, and counts of documents missing key assignments.
        """
        if not 1 <= sample_size <= 50:
            raise ValueError("sample_size must be between 1 and 50")

        async with use_client() as paperless:
            return await paperless.get_organization_overview(sample_size=sample_size)

    @server.tool(annotations=READ_ONLY, tags={"paperless", "organization", "documents"})
    async def find_documents_missing_metadata(
        missing_field: Literal[
            "correspondent",
            "document_type",
            "storage_path",
            "tags",
            "custom_fields",
            "archive_serial_number",
        ],
        page: int = 1,
        page_size: int = 20,
        ordering: str = "-created",
    ) -> JsonObject:
        """Find documents that lack one selected organization field.

        Returns compact document metadata without OCR content so an AI can inspect
        organization gaps without loading sensitive document text.
        """
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

        async with use_client() as paperless:
            return await paperless.find_documents_missing_metadata(
                missing_field,
                page=page,
                page_size=page_size,
                ordering=ordering,
            )

    @server.tool(annotations=READ_ONLY, tags={"paperless", "organization", "documents"})
    async def find_documents_by_metadata(
        object_type: Literal[
            "tag",
            "correspondent",
            "document_type",
            "storage_path",
            "custom_field",
        ],
        object_id: int,
        page: int = 1,
        page_size: int = 20,
        ordering: str = "-created",
    ) -> JsonObject:
        """Find compact document records assigned to one organization item."""
        if object_id < 1:
            raise ValueError("object_id must be positive")
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

        async with use_client() as paperless:
            return await paperless.find_documents_by_metadata(
                object_type,
                object_id,
                page=page,
                page_size=page_size,
                ordering=ordering,
            )

    @server.tool(annotations=CREATE, tags={"paperless", "organization", "write"})
    async def create_organization_item(
        object_type: Literal[
            "tags",
            "correspondents",
            "document_types",
            "storage_paths",
            "custom_fields",
        ],
        name: str,
        path: str | None = None,
        color: str | None = None,
        parent_id: int | None = None,
        match: str = "",
        matching_algorithm: int = 0,
        is_insensitive: bool = True,
        is_inbox_tag: bool = False,
        data_type: Literal[
            "string",
            "url",
            "date",
            "boolean",
            "integer",
            "float",
            "monetary",
            "documentlink",
            "select",
            "longtext",
        ]
        | None = None,
        extra_data: dict[str, Any] | None = None,
    ) -> JsonObject:
        """Create a tag, correspondent, document type, storage path, or custom field.

        This tool never deletes or trashes documents. PAPERLESS_READ_ONLY must be false.
        """
        values = _organization_create_values(
            object_type=object_type,
            name=name,
            path=path,
            color=color,
            parent_id=parent_id,
            match=match,
            matching_algorithm=matching_algorithm,
            is_insensitive=is_insensitive,
            is_inbox_tag=is_inbox_tag,
            data_type=data_type,
            extra_data=extra_data,
        )
        async with use_client() as paperless:
            return await paperless.create_organization_item(object_type, values)

    @server.tool(annotations=WRITE, tags={"paperless", "organization", "write"})
    async def update_organization_item(
        object_type: Literal[
            "tags",
            "correspondents",
            "document_types",
            "storage_paths",
            "custom_fields",
        ],
        item_id: int,
        name: str | None = None,
        path: str | None = None,
        color: str | None = None,
        parent_id: int | None = None,
        clear_parent: bool = False,
        match: str | None = None,
        matching_algorithm: int | None = None,
        is_insensitive: bool | None = None,
        is_inbox_tag: bool | None = None,
        data_type: Literal[
            "string",
            "url",
            "date",
            "boolean",
            "integer",
            "float",
            "monetary",
            "documentlink",
            "select",
            "longtext",
        ]
        | None = None,
        extra_data: dict[str, Any] | None = None,
    ) -> JsonObject:
        """Rename or reconfigure one organization item without deleting it."""
        if item_id < 1:
            raise ValueError("item_id must be positive")
        changes = _organization_update_values(
            object_type=object_type,
            name=name,
            path=path,
            color=color,
            parent_id=parent_id,
            clear_parent=clear_parent,
            match=match,
            matching_algorithm=matching_algorithm,
            is_insensitive=is_insensitive,
            is_inbox_tag=is_inbox_tag,
            data_type=data_type,
            extra_data=extra_data,
        )
        async with use_client() as paperless:
            return await paperless.update_organization_item(object_type, item_id, changes)

    @server.tool(
        annotations=OBJECT_BULK_WRITE,
        tags={"paperless", "organization", "delete", "write"},
    )
    async def bulk_edit_objects(
        objects: list[int],
        object_type: Literal[
            "tags",
            "correspondents",
            "document_types",
            "storage_paths",
        ],
        operation: Literal["delete"],
        dry_run: bool = True,
    ) -> JsonObject:
        """Bulk-edit Paperless organization objects through the matching REST endpoint.

        Mirrors POST /api/bulk_edit_objects/ using the REST fields objects,
        object_type, and operation. The currently allowed operation is delete for
        tags, correspondents, document types, and storage paths. With dry_run=true
        (default), it only performs the reference check and returns a request preview.
        After explicit user approval, repeat the same call with dry_run=false.
        Both modes reject the complete request if an object is used, missing, or a
        parent tag. This endpoint cannot target documents and never uses HTTP DELETE.
        """
        _validate_organization_item_ids(objects)
        async with use_client() as paperless:
            return await paperless.bulk_edit_objects(
                object_type,
                objects,
                operation,
                dry_run=dry_run,
            )

    @server.tool(annotations=WRITE, tags={"paperless", "organization", "documents", "write"})
    async def set_document_metadata_field(
        document_ids: list[int],
        field: Literal["correspondent", "document_type", "storage_path"],
        value_id: int | None,
    ) -> JsonObject:
        """Set or clear one metadata field on multiple documents.

        Pass null as value_id to clear the selected field. No document is deleted.
        """
        _validate_document_ids(document_ids)
        if value_id is not None and value_id < 1:
            raise ValueError("value_id must be positive or null")
        async with use_client() as paperless:
            return await paperless.set_document_metadata_field(document_ids, field, value_id)

    @server.tool(annotations=WRITE, tags={"paperless", "tags", "documents", "write"})
    async def modify_document_tags(
        document_ids: list[int],
        add_tag_ids: list[int] | None = None,
        remove_tag_ids: list[int] | None = None,
    ) -> JsonObject:
        """Add and remove tags on multiple documents without replacing unrelated tags."""
        _validate_document_ids(document_ids)
        add_ids = add_tag_ids or []
        remove_ids = remove_tag_ids or []
        _validate_positive_ids(add_ids, "add_tag_ids")
        _validate_positive_ids(remove_ids, "remove_tag_ids")
        overlap = set(add_ids) & set(remove_ids)
        if overlap:
            raise ValueError(f"Tags cannot be added and removed together: {sorted(overlap)}")
        async with use_client() as paperless:
            return await paperless.modify_document_tags(
                document_ids,
                add_tag_ids=add_ids,
                remove_tag_ids=remove_ids,
            )

    @server.tool(annotations=TRASH, tags={"paperless", "trash", "documents", "write"})
    async def move_documents_to_trash(document_ids: list[int]) -> JsonObject:
        """Move documents to Paperless trash, where they remain restorable.

        This is the strongest document-removal action available. Permanent deletion
        and emptying the trash are intentionally not implemented.
        """
        _validate_document_ids(document_ids)
        async with use_client() as paperless:
            return await paperless.move_documents_to_trash(document_ids)

    @server.tool(annotations=READ_ONLY, tags={"paperless", "trash", "documents"})
    async def list_trashed_documents(page: int = 1, page_size: int = 20) -> JsonObject:
        """List compact records of documents currently in Paperless trash."""
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        async with use_client() as paperless:
            return await paperless.list_trashed_documents(page=page, page_size=page_size)

    @server.tool(annotations=WRITE, tags={"paperless", "trash", "documents", "write"})
    async def restore_documents_from_trash(document_ids: list[int]) -> JsonObject:
        """Restore documents from Paperless trash."""
        _validate_document_ids(document_ids)
        async with use_client() as paperless:
            return await paperless.restore_documents_from_trash(document_ids)

    @server.tool(annotations=WRITE, tags={"paperless", "documents", "write"})
    async def update_document(
        document_id: int,
        changes: dict[str, Any],
    ) -> JsonObject:
        """PATCH selected document metadata using Paperless REST field names.

        Allowed fields are title, created, correspondent, document_type,
        storage_path, tags, and archive_serial_number. Null clears nullable
        fields; tags is the complete replacement list. Read-only mode blocks writes.
        """
        if document_id < 1:
            raise ValueError("document_id must be positive")
        _validate_document_changes(changes)

        async with use_client() as paperless:
            return await paperless.update_document(document_id, changes)

    @server.tool(annotations=CREATE, tags={"paperless", "documents", "notes", "write"})
    async def document_notes(
        document_id: int,
        operation: Literal["list", "create"],
        note: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> JsonObject:
        """List or create entries in GET/POST /api/documents/{id}/notes/.

        Creating a note requires PAPERLESS_READ_ONLY=false. Note deletion is not
        exposed because all HTTP DELETE requests remain blocked.
        """
        if document_id < 1:
            raise ValueError("document_id must be positive")
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if operation == "list" and note is not None:
            raise ValueError("note is only valid when operation='create'")
        async with use_client() as paperless:
            return await paperless.document_notes(
                document_id,
                operation,
                note=note,
                page=page,
                page_size=page_size,
            )

    return server


mcp = create_server()


def main() -> None:
    """Run the local MCP server over stdio."""
    mcp.run(transport="stdio", show_banner=False)


def _organization_create_values(
    *,
    object_type: str,
    name: str,
    path: str | None,
    color: str | None,
    parent_id: int | None,
    match: str,
    matching_algorithm: int,
    is_insensitive: bool,
    is_inbox_tag: bool,
    data_type: str | None,
    extra_data: dict[str, Any] | None,
) -> JsonObject:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("name must not be empty")
    if len(clean_name) > 128:
        raise ValueError("name must not exceed 128 characters")

    values: JsonObject = {"name": clean_name}
    if object_type in {"tags", "correspondents", "document_types", "storage_paths"}:
        _validate_matching_algorithm(matching_algorithm)
        values.update(
            {
                "match": match,
                "matching_algorithm": matching_algorithm,
                "is_insensitive": is_insensitive,
            }
        )
    if object_type == "tags":
        values["is_inbox_tag"] = is_inbox_tag
        if color is not None:
            _validate_color(color)
            values["color"] = color
        if parent_id is not None:
            _validate_positive_ids([parent_id], "parent_id")
            values["parent"] = parent_id
    elif object_type == "storage_paths":
        if path is None or not path.strip():
            raise ValueError("path is required for storage_paths")
        values["path"] = path.strip()
    elif object_type == "custom_fields":
        if data_type is None:
            raise ValueError("data_type is required for custom_fields")
        values["data_type"] = data_type
        if extra_data is not None:
            values["extra_data"] = extra_data
    return values


def _organization_update_values(
    *,
    object_type: str,
    name: str | None,
    path: str | None,
    color: str | None,
    parent_id: int | None,
    clear_parent: bool,
    match: str | None,
    matching_algorithm: int | None,
    is_insensitive: bool | None,
    is_inbox_tag: bool | None,
    data_type: str | None,
    extra_data: dict[str, Any] | None,
) -> JsonObject:
    changes: JsonObject = {}
    if name is not None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("name must not be empty")
        if len(clean_name) > 128:
            raise ValueError("name must not exceed 128 characters")
        changes["name"] = clean_name

    if object_type in {"tags", "correspondents", "document_types", "storage_paths"}:
        if match is not None:
            changes["match"] = match
        if matching_algorithm is not None:
            _validate_matching_algorithm(matching_algorithm)
            changes["matching_algorithm"] = matching_algorithm
        if is_insensitive is not None:
            changes["is_insensitive"] = is_insensitive
    if object_type == "tags":
        if color is not None:
            _validate_color(color)
            changes["color"] = color
        if parent_id is not None and clear_parent:
            raise ValueError("parent_id and clear_parent cannot be used together")
        if parent_id is not None:
            _validate_positive_ids([parent_id], "parent_id")
            changes["parent"] = parent_id
        elif clear_parent:
            changes["parent"] = None
        if is_inbox_tag is not None:
            changes["is_inbox_tag"] = is_inbox_tag
    elif object_type == "storage_paths" and path is not None:
        if not path.strip():
            raise ValueError("path must not be empty")
        changes["path"] = path.strip()
    elif object_type == "custom_fields":
        if data_type is not None:
            changes["data_type"] = data_type
        if extra_data is not None:
            changes["extra_data"] = extra_data
    return changes


def _validate_document_ids(document_ids: list[int]) -> None:
    if not document_ids:
        raise ValueError("document_ids must not be empty")
    if len(document_ids) > 500:
        raise ValueError("document_ids must contain at most 500 IDs")
    _validate_positive_ids(document_ids, "document_ids")
    if len(set(document_ids)) != len(document_ids):
        raise ValueError("document_ids must not contain duplicates")


def _validate_organization_item_ids(item_ids: list[int]) -> None:
    if not item_ids:
        raise ValueError("item_ids must not be empty")
    if len(item_ids) > 100:
        raise ValueError("item_ids must contain at most 100 IDs")
    _validate_positive_ids(item_ids, "item_ids")
    if len(set(item_ids)) != len(item_ids):
        raise ValueError("item_ids must not contain duplicates")


def _validate_document_changes(changes: dict[str, Any]) -> None:
    allowed_fields = {
        "title",
        "created",
        "correspondent",
        "document_type",
        "storage_path",
        "tags",
        "archive_serial_number",
    }
    if not changes:
        raise ValueError("changes must not be empty")
    unsupported = sorted(set(changes) - allowed_fields)
    if unsupported:
        raise ValueError(f"Unsupported document fields: {unsupported}")

    if "title" in changes:
        title = changes["title"]
        if not isinstance(title, str) or not title.strip() or len(title) > 128:
            raise ValueError("title must be a non-empty string with at most 128 characters")
    if "created" in changes:
        created = changes["created"]
        if not isinstance(created, str):
            raise ValueError("created must be an ISO date string")
        try:
            date.fromisoformat(created)
        except ValueError as exc:
            raise ValueError("created must be an ISO date string") from exc
    for field in ("correspondent", "document_type", "storage_path"):
        value = changes.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 1
        ):
            raise ValueError(f"{field} must be a positive integer or null")
    if "tags" in changes:
        tags = changes["tags"]
        if not isinstance(tags, list):
            raise ValueError("tags must be a list of positive IDs")
        _validate_positive_ids(tags, "tags")
        if len(set(tags)) != len(tags):
            raise ValueError("tags must not contain duplicates")
    archive_serial_number = changes.get("archive_serial_number")
    if archive_serial_number is not None and (
        not isinstance(archive_serial_number, int)
        or isinstance(archive_serial_number, bool)
        or not 0 <= archive_serial_number <= 4_294_967_295
    ):
        raise ValueError("archive_serial_number must be between 0 and 4294967295 or null")


def _workflow_values(
    *,
    name: str,
    triggers: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    order: int | None,
    enabled: bool,
) -> JsonObject:
    _validate_workflow_name(name)
    _validate_workflow_entries(triggers, "triggers")
    _validate_workflow_entries(actions, "actions")
    if order is not None and (not isinstance(order, int) or isinstance(order, bool)):
        raise ValueError("order must be an integer or null")
    return {
        "name": name.strip(),
        "triggers": triggers,
        "actions": actions,
        "enabled": enabled,
        **({"order": order} if order is not None else {}),
    }


def _validate_workflow_changes(changes: dict[str, Any]) -> None:
    allowed_fields = {"name", "order", "enabled", "triggers", "actions"}
    if not changes:
        raise ValueError("changes must not be empty")
    unsupported = sorted(set(changes) - allowed_fields)
    if unsupported:
        raise ValueError(f"Unsupported workflow fields: {unsupported}")
    if "name" in changes:
        _validate_workflow_name(changes["name"])
    if "order" in changes and (
        not isinstance(changes["order"], int) or isinstance(changes["order"], bool)
    ):
        raise ValueError("order must be an integer")
    if "enabled" in changes and not isinstance(changes["enabled"], bool):
        raise ValueError("enabled must be a boolean")
    for field in ("triggers", "actions"):
        if field in changes:
            _validate_workflow_entries(changes[field], field)


def _validate_workflow_name(value: Any) -> None:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
        raise ValueError("name must be a non-empty string with at most 256 characters")


def _validate_workflow_entries(value: Any, field_name: str) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} must be a non-empty list")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{field_name} must contain only objects")
    for item in value:
        workflow_type = item.get("type")
        if not isinstance(workflow_type, int) or isinstance(workflow_type, bool):
            raise ValueError(f"each {field_name} entry must have an integer type")


def _validate_positive_ids(values: list[int], field_name: str) -> None:
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in values):
        raise ValueError(f"{field_name} must contain only positive IDs")


def _validate_matching_algorithm(value: int) -> None:
    if value not in range(7):
        raise ValueError("matching_algorithm must be between 0 and 6")


def _validate_color(value: str) -> None:
    if len(value) != 7 or not value.startswith("#"):
        raise ValueError("color must be a hex value like #a6cee3")
    try:
        int(value[1:], 16)
    except ValueError as exc:
        raise ValueError("color must be a hex value like #a6cee3") from exc


if __name__ == "__main__":
    main()
