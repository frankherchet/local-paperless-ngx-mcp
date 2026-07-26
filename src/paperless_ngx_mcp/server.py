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


def create_server(client: PaperlessClient | None = None) -> FastMCP:
    """Create a server, optionally using an injected client for tests."""
    server = FastMCP(
        name="Paperless-ngx",
        instructions=(
            "Search and inspect the user's local Paperless-ngx archive and organization. "
            "Use get_organization_overview before assessing tags, correspondents, document "
            "types, storage paths, custom fields, or saved views. Treat unused entries as "
            "review candidates, not deletion recommendations. Read tools are safe; document "
            "updates are disabled by default. Permanent deletion is unavailable: the server "
            "never issues HTTP DELETE and never empties Paperless trash."
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
        max_content_chars: int = 20_000,
    ) -> JsonObject:
        """Return metadata and optionally OCR text for one document."""
        if document_id < 1:
            raise ValueError("document_id must be positive")
        if not 1_000 <= max_content_chars <= 200_000:
            raise ValueError("max_content_chars must be between 1,000 and 200,000")

        async with use_client() as paperless:
            return await paperless.get_document(
                document_id,
                include_content=include_content,
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
        ],
        page: int = 1,
        page_size: int = 100,
        ordering: str = "name",
    ) -> JsonObject:
        """List organization records with full metadata and human-readable matching modes.

        Supports tags, correspondents, document types, storage paths, custom fields,
        and saved views. Use pagination to inspect large collections.
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
        title: str | None = None,
        created: date | None = None,
        correspondent_id: int | None = None,
        document_type_id: int | None = None,
        storage_path_id: int | None = None,
        tag_ids: list[int] | None = None,
        archive_serial_number: int | None = None,
    ) -> JsonObject:
        """Update selected metadata on a document.

        The tool is blocked while PAPERLESS_READ_ONLY=true (the default).
        Only supplied fields are changed.
        """
        if document_id < 1:
            raise ValueError("document_id must be positive")

        values: dict[str, Any] = {
            "title": title,
            "created": created.isoformat() if created else None,
            "correspondent": correspondent_id,
            "document_type": document_type_id,
            "storage_path": storage_path_id,
            "tags": tag_ids,
            "archive_serial_number": archive_serial_number,
        }
        changes = {key: value for key, value in values.items() if value is not None}

        async with use_client() as paperless:
            return await paperless.update_document(document_id, changes)

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


def _validate_positive_ids(values: list[int], field_name: str) -> None:
    if any(value < 1 for value in values):
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
