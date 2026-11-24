"""Server configuration loaded from environment variables or a .env file.

Every field can be overridden by an environment variable of the same name
(upper- or lower-case). Copy `.env.irs_research` to `.env` for local development.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve paths relative to this file so they work regardless of CWD.
# _HERE is the package directory (irs_research_server/).
# _PROJECT_ROOT is two levels up in a normal src-layout repo; in the
# Orchestrate staged layout the catalog is bundled inside the package
# at _HERE/data/catalog/, so we check there first.
_HERE = Path(__file__).resolve().parent          # irs_research_server/
_PROJECT_ROOT = _HERE.parent.parent              # project root (src-layout)
# Bundled catalog path (used when deployed as a local MCP toolkit where
# the catalog is staged inside the package directory).
_BUNDLED_CATALOG = _HERE / "data" / "catalog" / "irs_catalog.json"
_DEFAULT_CATALOG = (
    _BUNDLED_CATALOG if _BUNDLED_CATALOG.exists() else _PROJECT_ROOT / "data" / "catalog" / "irs_catalog.json"
)


class Settings(BaseSettings):
    """MCP server settings. Override any field via environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Transport ─────────────────────────────────────────────────────────────
    # "stdio"           — local subprocess (Claude Desktop / Claude Code / Zed)
    # "streamable-http" — remote HTTP server (containers, cloud deploys)
    #
    # NOTE: app.py also auto-selects HTTP when a platform injects DATABRICKS_APP_PORT
    # or PORT, so you usually do NOT need to set this on a cloud host.
    mcp_transport: str = "stdio"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    # ── IRS Catalog paths ─────────────────────────────────────────────────────
    # catalog_path: pre-built enriched catalog JSON (output of build_catalog.py)
    # db_path:      SQLite cache for parsed PDFs and About-page responses
    #
    # Set CATALOG_PATH / DB_PATH env vars to override (e.g. on a read-only container
    # host where /tmp is writable but the project directory is not).
    catalog_path: Path = _DEFAULT_CATALOG
    db_path: Path = _PROJECT_ROOT / "data" / "cache" / "irs_history.db"

    # ── Freshness ─────────────────────────────────────────────────────────────────────
    # Cached About pages / current PDFs and the catalog are refreshed monthly; anything
    # fetched before Jan 1 of the current year is stale regardless (see services/freshness.py).
    cache_max_age_days: int = 30

    # ── Remote (HTTP) security ────────────────────────────────────────────────
    # Applies only to the streamable-HTTP transport; stdio runs as a local subprocess.
    # auth_mode: "jwt" validates bearer tokens (JWKS URI or shared HS* secret); "none" disables.
    auth_mode: str = "none"
    jwt_jwks_uri: str | None = None
    jwt_public_key: str | None = None  # PEM public key, or the shared secret for HS256/384/512
    jwt_algorithm: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_required_scopes: str = ""  # comma-separated
    # HTTP refuses to start without auth unless this is set (e.g. a platform already enforces auth).
    allow_unauthenticated_http: bool = False
    # Comma-separated public hostnames; enables Host/Origin (DNS-rebinding) protection when set.
    allowed_hosts: str = ""

    # ── Limits ────────────────────────────────────────────────────────────────
    max_request_bytes: int = 262_144
    max_response_bytes: int = 1_000_000
    rate_limit_per_second: float = 5.0
    rate_limit_burst: int = 20
    max_pdf_bytes: int = 25_000_000
    max_concurrent_fetches: int = 4

    @property
    def jwt_scopes(self) -> list[str]:
        return [s.strip() for s in self.jwt_required_scopes.split(",") if s.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @field_validator("catalog_path", "db_path", mode="before")
    @classmethod
    def _coerce_path(cls, v: object) -> Path:
        return Path(str(v))


settings = Settings()
