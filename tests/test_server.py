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
        "update_document",
    }

    update_tool = next(tool for tool in tools if tool.name == "update_document")
    assert update_tool.annotations is not None
    assert update_tool.annotations.readOnlyHint is False
    assert update_tool.annotations.destructiveHint is False
