import httpx
import pytest
from fastmcp import Client
from mcp.types import TextContent

from paperless_ngx_mcp.client import PaperlessClient
from paperless_ngx_mcp.config import Settings
from paperless_ngx_mcp.server import _validate_document_changes, create_server


async def test_server_exposes_expected_tools() -> None:
    async with Client(create_server()) as client:
        tools = await client.list_tools()

    assert {tool.name for tool in tools} == {
        "paperless_status",
        "search_documents",
        "get_document",
        "get_document_history",
        "get_task",
        "list_active_tasks",
        "get_task_overview",
        "get_document_suggestions",
        "get_archive_statistics",
        "list_metadata",
        "list_workflows",
        "get_workflow",
        "create_workflow",
        "update_workflow",
        "delete_workflow",
        "configure_default_intake",
        "verify_default_intake",
        "get_organization_overview",
        "find_documents_missing_metadata",
        "find_documents_by_metadata",
        "create_organization_item",
        "update_organization_item",
        "bulk_edit_objects",
        "set_document_metadata_field",
        "modify_document_tags",
        "move_documents_to_trash",
        "reprocess_documents",
        "list_trashed_documents",
        "restore_documents_from_trash",
        "update_document",
        "document_notes",
    }
    assert all(
        "delete_document" not in tool.name and "empty_trash" not in tool.name for tool in tools
    )

    read_tool = next(tool for tool in tools if tool.name == "get_organization_overview")
    assert read_tool.annotations is not None
    assert read_tool.annotations.readOnlyHint is True

    update_tool = next(tool for tool in tools if tool.name == "update_document")
    assert update_tool.annotations is not None
    assert update_tool.annotations.readOnlyHint is False
    assert update_tool.annotations.destructiveHint is False
    assert set(update_tool.inputSchema["properties"]) == {"document_id", "changes"}

    get_document_tool = next(tool for tool in tools if tool.name == "get_document")
    assert "include_file_metadata" in get_document_tool.inputSchema["properties"]

    history_tool = next(tool for tool in tools if tool.name == "get_document_history")
    assert history_tool.annotations is not None
    assert history_tool.annotations.readOnlyHint is True
    assert set(history_tool.inputSchema["properties"]) == {
        "document_id",
        "page",
        "page_size",
        "detail",
    }

    task_tool = next(tool for tool in tools if tool.name == "get_task")
    assert task_tool.annotations is not None
    assert task_tool.annotations.readOnlyHint is True

    notes_tool = next(tool for tool in tools if tool.name == "document_notes")
    assert set(notes_tool.inputSchema["properties"]) == {
        "document_id",
        "operation",
        "note",
        "page",
        "page_size",
    }

    trash_tool = next(tool for tool in tools if tool.name == "move_documents_to_trash")
    assert trash_tool.annotations is not None
    assert trash_tool.annotations.destructiveHint is True

    reprocess_tool = next(tool for tool in tools if tool.name == "reprocess_documents")
    assert reprocess_tool.annotations is not None
    assert reprocess_tool.annotations.readOnlyHint is False
    assert reprocess_tool.annotations.destructiveHint is False

    object_bulk_tool = next(tool for tool in tools if tool.name == "bulk_edit_objects")
    assert object_bulk_tool.annotations is not None
    assert object_bulk_tool.annotations.readOnlyHint is False
    assert object_bulk_tool.annotations.destructiveHint is True

    workflow_delete_tool = next(tool for tool in tools if tool.name == "delete_workflow")
    assert workflow_delete_tool.annotations is not None
    assert workflow_delete_tool.annotations.destructiveHint is True


async def test_server_sends_structured_data_without_duplicate_json_text() -> None:
    settings = Settings.model_validate(
        {
            "PAPERLESS_URL": "http://paperless.test",
            "PAPERLESS_TOKEN": "test-token",
        }
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "count": 1,
                "results": [{"id": 7, "title": "Invoice", "content": "secret OCR"}],
            },
        )
    )
    paperless = PaperlessClient(settings, transport=transport)
    async with paperless, Client(create_server(paperless)) as client:
        result = await client.call_tool("search_documents", {"query": "invoice"})

    assert result.structured_content is not None
    assert result.structured_content["results"] == [{"id": 7, "title": "Invoice"}]
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "Structured Paperless result attached."
    assert "Invoice" not in result.content[0].text


async def test_response_middleware_preserves_tool_errors() -> None:
    async with Client(create_server()) as client:
        result = await client.call_tool(
            "search_documents",
            {"mode": "title", "query": ""},
            raise_on_error=False,
        )

    assert result.is_error is True
    assert result.structured_content is None
    assert isinstance(result.content[0], TextContent)
    assert "query must not be empty" in result.content[0].text


def test_document_patch_validation_supports_explicit_null_and_full_tag_list() -> None:
    _validate_document_changes(
        {
            "correspondent": None,
            "document_type": None,
            "storage_path": None,
            "archive_serial_number": None,
            "tags": [],
        }
    )

    with pytest.raises(ValueError, match="Unsupported document fields"):
        _validate_document_changes({"content": "must remain read-only"})
    with pytest.raises(ValueError, match="title must be"):
        _validate_document_changes({"title": None})
    with pytest.raises(ValueError, match="tags must be"):
        _validate_document_changes({"tags": None})
