import pytest
from fastmcp import Client

from paperless_ngx_mcp.server import _validate_document_changes, create_server


async def test_server_exposes_expected_tools() -> None:
    async with Client(create_server()) as client:
        tools = await client.list_tools()

    assert {tool.name for tool in tools} == {
        "paperless_status",
        "search_documents",
        "get_document",
        "list_metadata",
        "get_organization_overview",
        "find_documents_missing_metadata",
        "find_documents_by_metadata",
        "create_organization_item",
        "update_organization_item",
        "bulk_edit_objects",
        "set_document_metadata_field",
        "modify_document_tags",
        "move_documents_to_trash",
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

    object_bulk_tool = next(tool for tool in tools if tool.name == "bulk_edit_objects")
    assert object_bulk_tool.annotations is not None
    assert object_bulk_tool.annotations.readOnlyHint is False
    assert object_bulk_tool.annotations.destructiveHint is True


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
