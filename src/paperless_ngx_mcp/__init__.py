"""A local FastMCP server for Paperless-ngx."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("local-paperless-ngx-mcp")
except PackageNotFoundError:
    __version__ = "0.6.0"

__all__ = ["__version__"]
