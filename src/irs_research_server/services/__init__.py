"""Services submodule — data layer for the IRS Research MCP server.

Contains the catalog loader, SQLite-backed PDF cache, IRS URL validator,
and all PDF-parsing and diff logic. Import from here rather than directly
from the submodules to keep tool code clean.
"""
