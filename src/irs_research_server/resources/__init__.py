"""Resources submodule — static catalog data and policy docs.

Each resource module exposes a `register(mcp)` function; this file aggregates
them into `register_resources(mcp)`.
"""

from __future__ import annotations

from irs_research_server.resources import irs_resources


def register_resources(mcp) -> None:
    """Register all IRS research resources with the MCP server."""
    irs_resources.register(mcp)


__all__ = ["register_resources"]
