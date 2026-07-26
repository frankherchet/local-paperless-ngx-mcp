from fastmcp import Client

from paperless_ngx_mcp.server import create_server


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
        "set_document_metadata_field",
        "modify_document_tags",
        "move_documents_to_trash",
        "list_trashed_documents",
        "restore_documents_from_trash",
        "update_document",
    }
    assert all("delete" not in tool.name and "empty_trash" not in tool.name for tool in tools)

    read_tool = next(tool for tool in tools if tool.name == "get_organization_overview")
    assert read_tool.annotations is not None
    assert read_tool.annotations.readOnlyHint is True

    update_tool = next(tool for tool in tools if tool.name == "update_document")
    assert update_tool.annotations is not None
    assert update_tool.annotations.readOnlyHint is False
    assert update_tool.annotations.destructiveHint is False

    trash_tool = next(tool for tool in tools if tool.name == "move_documents_to_trash")
    assert trash_tool.annotations is not None
    assert trash_tool.annotations.destructiveHint is True
