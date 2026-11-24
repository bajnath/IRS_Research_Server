"""Tools submodule — one file per tool group.

Each tool module exposes a `register(mcp)` function that attaches its
`@mcp.tool`-decorated functions to the server.

To add a tool:
  1. Create `tools/<your_tool>.py` with a `register(mcp)` function.
  2. Import it below and call `<your_tool>.register(mcp)` inside register_tools.
"""

from __future__ import annotations

from irs_research_server.tools import irs_tools


def register_tools(mcp) -> None:
    """Register all IRS research tools with the MCP server."""
    irs_tools.register(mcp)


__all__ = ["register_tools"]
