"""Smoke tests for the IRS Research MCP server.

Verifies that the server imports cleanly, every submodule registers without
error, all five IRS tools are discoverable, and the catalog loads correctly.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from irs_research_server.app import mcp
from irs_research_server.config import settings


def test_server_is_importable():
    assert mcp is not None


def test_server_has_correct_name():
    assert mcp.name == "IRS Research MCP Server"


def test_default_transport_is_stdio():
    """Guard against accidentally shipping with a non-stdio default."""
    assert settings.mcp_transport == "stdio"


def test_tools_register_without_error():
    from irs_research_server.tools import register_tools

    register_tools(FastMCP("test"))  # must not raise


def test_prompts_register_without_error():
    from irs_research_server.prompts import register_prompts

    register_prompts(FastMCP("test"))  # must not raise


def test_resources_register_without_error():
    from irs_research_server.resources import register_resources

    register_resources(FastMCP("test"))  # must not raise


def test_routes_register_without_error():
    from irs_research_server.routes import register_routes

    register_routes(FastMCP("test"))  # must not raise


@pytest.mark.asyncio
async def test_all_five_irs_tools_are_discoverable():
    """All five IRS research tools must be registered and discoverable."""
    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}
    expected = {
        "lookup_form",
        "browse_forms_by_topic",
        "get_form_history",
        "trace_form_lineage",
        "compare_form_revisions",
    }
    assert expected == tool_names, f"Missing tools: {expected - tool_names}"
