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


def create_server(client: PaperlessClient | None = None) -> FastMCP:
    """Create a server, optionally using an injected client for tests."""
    server = FastMCP(
        name="Paperless-ngx",
        instructions=(
            "Search and inspect the user's local Paperless-ngx archive. "
            "Read tools are safe; document updates are disabled by default."
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
        object_type: Literal["tags", "correspondents", "document_types", "storage_paths"],
        page: int = 1,
        page_size: int = 100,
        ordering: str = "name",
    ) -> JsonObject:
        """List Paperless tags, correspondents, document types, or storage paths."""
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


if __name__ == "__main__":
    main()
