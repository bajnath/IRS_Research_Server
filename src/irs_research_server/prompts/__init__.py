"""Prompts submodule — research conversation starters.

Each prompt module exposes a `register(mcp)` function; this file aggregates
them into `register_prompts(mcp)`.
"""

from __future__ import annotations

from irs_research_server.prompts import irs_prompts


def register_prompts(mcp) -> None:
    """Register all IRS research prompts with the MCP server."""
    irs_prompts.register(mcp)


__all__ = ["register_prompts"]
