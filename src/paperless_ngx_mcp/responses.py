"""Keep MCP text content short when structured content is available."""

from __future__ import annotations

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult


class CompactToolResultMiddleware(Middleware):
    """Avoid repeating structured JSON verbatim in a text content block."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        result = await call_next(context)
        if result.structured_content is not None and not result.is_error:
            result.content = [
                mt.TextContent(type="text", text="Structured Paperless result attached.")
            ]
        return result
