"""MCP-style tool modules.

The functions in this package intentionally expose structured inputs and outputs.
They can be wrapped by FastMCP later without changing engine code.
"""
from mcp_servers import decision_server

__all__ = ["decision_server"]
