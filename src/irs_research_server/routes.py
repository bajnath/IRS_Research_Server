"""Custom HTTP routes for the MCP server.

These are only active when the server runs over HTTP (streamable-http). They
sit alongside the `/mcp` endpoint on the same app and are intentionally
unauthenticated (FastMCP never applies auth to custom routes), so they expose
only operational status - no catalog content.

  /health  liveness for platform probes: process up AND catalog loaded (503 otherwise)
  /ready   readiness: catalog, tag registry, freshness, SQLite cache writable, PDF
           parser importable; add ?check_irs=true to also probe IRS.gov reachability
"""

from __future__ import annotations

import time

import httpx
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from irs_research_server import __version__


def _check_catalog() -> dict:
    from irs_research_server.services.irs_history import catalog_status, load_catalog

    cat = load_catalog()
    return {
        "ok": cat["total_unique_products"] > 0 and "tag_registry" in cat,
        "products": cat["total_unique_products"],
        "freshness": catalog_status(),
    }


def _check_cache_db() -> dict:
    from irs_research_server.services.irs_history import _get_db

    with _get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS health_probe (ts REAL)")
        conn.execute("INSERT INTO health_probe (ts) VALUES (?)", (time.time(),))
        conn.execute("DELETE FROM health_probe")
    return {"ok": True}


def _check_pdf_parser() -> dict:
    import pymupdf  # noqa: F401

    return {"ok": True}


async def _check_irs() -> dict:
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
        resp = await client.head("https://www.irs.gov/")
    return {"ok": resp.status_code < 500, "status_code": resp.status_code}


def _run(check) -> dict:
    try:
        return check()
    except Exception as exc:  # report, never raise, from a probe
        return {"ok": False, "error": type(exc).__name__}


def register_routes(mcp: FastMCP) -> None:
    """Register custom HTTP routes with the server."""

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        catalog = _run(_check_catalog)
        ok = catalog["ok"]
        return JSONResponse(
            {"status": "healthy" if ok else "unhealthy", "service": "irs-research-mcp", "catalog_loaded": ok},
            status_code=200 if ok else 503,
        )

    @mcp.custom_route("/ready", methods=["GET"])
    async def readiness(request: Request) -> JSONResponse:
        checks = {
            "catalog": _run(_check_catalog),
            "cache_db": _run(_check_cache_db),
            "pdf_parser": _run(_check_pdf_parser),
        }
        if request.query_params.get("check_irs") == "true":
            try:
                checks["irs_gov"] = await _check_irs()
            except Exception as exc:
                checks["irs_gov"] = {"ok": False, "error": type(exc).__name__}

        critical_ok = checks["catalog"]["ok"] and checks["cache_db"]["ok"]
        freshness = checks["catalog"].get("freshness", {}).get("status")
        degraded = not all(c["ok"] for c in checks.values()) or freshness not in ("fresh", None)
        status = "not_ready" if not critical_ok else "degraded" if degraded else "ready"
        return JSONResponse(
            {"status": status, "version": __version__, "checks": checks},
            status_code=200 if critical_ok else 503,
        )

    @mcp.custom_route("/version", methods=["GET"])
    async def version(request: Request) -> JSONResponse:
        return JSONResponse({"version": __version__})
