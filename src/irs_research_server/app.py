"""MCP server entry point.

Keep this file THIN. It only:
  1. creates the FastMCP instance,
  2. registers tools / prompts / resources / routes from the submodules, and
  3. decides which transport to run (stdio locally vs. HTTP when deployed).

All real logic lives in the submodules:
  - tools/      one file per tool group (each exposes `register(mcp)`)
  - prompts/    reusable research conversation starters
  - resources/  static catalog data and policy docs the client can read directly
  - routes.py   HTTP-only infrastructure (health check, etc.)
  - config.py   settings loaded from env vars / .env
  - services/   IRS data layer (catalog loader, PDF parser, diff engine)
"""

from __future__ import annotations

import json
import logging
import os
import time

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware

from irs_research_server.config import settings
from irs_research_server.prompts import register_prompts
from irs_research_server.resources import register_resources
from irs_research_server.routes import register_routes
from irs_research_server.security import (
    RequestSizeLimitMiddleware,
    audit_log,
    audit_record,
    build_auth,
    require_http_auth,
)
from irs_research_server.tools import register_tools


class AuditMiddleware(Middleware):
    """Emit one structured audit line per tool call (truncated arguments, outcome, duration)."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        started = time.perf_counter()
        name, args = context.message.name, context.message.arguments
        try:
            result = await call_next(context)
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            audit_log.info(json.dumps(audit_record(name, args, "failed", elapsed, type(exc).__name__)))
            raise
        status = "error" if getattr(result, "is_error", False) else "completed"
        audit_log.info(json.dumps(audit_record(name, args, status, (time.perf_counter() - started) * 1000)))
        return result


auth = build_auth(settings)

mcp = FastMCP(
    name="IRS Research MCP Server",
    auth=auth,
    instructions=(
        "This server is a research tool for understanding the structure, history, "
        "and taxonomy of IRS tax forms. It provides factual information sourced "
        "directly from IRS publications and form documents — including form "
        "classifications, subject-matter tags, revision histories, data-flow "
        "relationships between forms, and verbatim line-level diffs across "
        "historical revisions.\n\n"
        "SCOPE: This server is intended for professional research use by academics, "
        "journalists, law practitioners, and policy researchers who need to understand "
        "the IRS form landscape. It is NOT a tax preparation tool, and it does NOT "
        "help individuals or businesses prepare, complete, or file tax returns.\n\n"
        "GUARDRAILS:\n"
        "- Do not give tax advice or recommend any course of action to a taxpayer.\n"
        "- Do not interpret tax law, eligibility criteria, or legal obligations.\n"
        "- Do not answer questions like 'Do I need to file this form?' or "
        "'What counts as a qualifying expense?'\n"
        "- When asked to do any of the above, politely decline and direct the user "
        "to a licensed tax professional or irs.gov.\n"
        "- All quoted text is from IRS source documents and is presented as-is, "
        "without interpretation of legal applicability.\n"
        "- Every relationship and tag carries a provenance basis (irs_naming, irs_text, "
        "curated, inferred). Label each claim as IRS-published fact, project inference, "
        "curated relationship, or your own interpretation (see irs://meta/usage-policy)."
    ),
)

# Wire up everything the server exposes.
register_tools(mcp)
register_prompts(mcp)
register_resources(mcp)
register_routes(mcp)

# Rate limiting is a per-process token bucket shared by all clients; it caps resource use,
# it is not per-user fairness. Put a gateway in front for per-client quotas.
mcp.add_middleware(
    RateLimitingMiddleware(
        max_requests_per_second=settings.rate_limit_per_second,
        burst_capacity=settings.rate_limit_burst,
    )
)
mcp.add_middleware(ResponseLimitingMiddleware(max_size=settings.max_response_bytes))
mcp.add_middleware(AuditMiddleware())


def _serve_http(host: str, port: int) -> None:
    """Serve streamable HTTP with auth enforced, request-size limits, and optional Host checks."""
    import uvicorn
    from starlette.middleware import Middleware as AsgiMiddleware

    from irs_research_server.services.irs_history import load_catalog

    require_http_auth(auth, settings)
    load_catalog()  # fail at startup, not on the first tool call, if the catalog is missing

    app_kwargs: dict = {
        "middleware": [AsgiMiddleware(RequestSizeLimitMiddleware, max_bytes=settings.max_request_bytes)],
    }
    if settings.allowed_host_list:
        app_kwargs.update(host_origin_protection=True, allowed_hosts=settings.allowed_host_list)
    uvicorn.run(mcp.http_app(**app_kwargs), host=host, port=port, proxy_headers=True)


def main() -> None:
    """Start the server, choosing a transport based on the environment.

    Precedence:
      1. A platform-injected port (Databricks `DATABRICKS_APP_PORT` or a generic
         `PORT`) → run HTTP on that port. Cloud hosts set this for you.
      2. `MCP_TRANSPORT=streamable-http` in the environment → run HTTP on
         `MCP_HOST`:`MCP_PORT` (see config.py / .env).
      3. Otherwise → stdio, which is how local clients (Claude Desktop, Claude
         Code, Zed) launch the server as a subprocess.
    """
    platform_port = os.getenv("DATABRICKS_APP_PORT") or os.getenv("PORT")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if platform_port:
        _serve_http("0.0.0.0", int(platform_port))
    elif settings.mcp_transport == "streamable-http":
        _serve_http(settings.mcp_host, settings.mcp_port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
