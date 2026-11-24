"""Remote-deployment security: bearer-token auth, request-size limits, and audit records.

The request-size limiter is plain ASGI (no FastMCP dependency) so it can be tested
in isolation. FastMCP is imported lazily where needed.
"""

from __future__ import annotations

import json
import logging

audit_log = logging.getLogger("irs_research_server.audit")


class AuthConfigError(RuntimeError):
    """Raised when auth settings are missing or inconsistent."""


def build_auth(settings):
    """Return a FastMCP token verifier for ``auth_mode=jwt``, or None for ``auth_mode=none``."""
    mode = settings.auth_mode.strip().lower()
    if mode == "none":
        return None
    if mode != "jwt":
        raise AuthConfigError(f"Unsupported AUTH_MODE {settings.auth_mode!r}; use 'jwt' or 'none'.")
    if bool(settings.jwt_jwks_uri) == bool(settings.jwt_public_key):
        raise AuthConfigError("AUTH_MODE=jwt needs exactly one of JWT_JWKS_URI or JWT_PUBLIC_KEY.")
    if not settings.jwt_issuer or not settings.jwt_audience:
        raise AuthConfigError("AUTH_MODE=jwt needs JWT_ISSUER and JWT_AUDIENCE so foreign tokens are rejected.")

    from fastmcp.server.auth.providers.jwt import JWTVerifier

    kwargs = {
        "issuer": settings.jwt_issuer,
        "audience": settings.jwt_audience,
        "required_scopes": settings.jwt_scopes or None,
    }
    if settings.jwt_jwks_uri:
        kwargs["jwks_uri"] = settings.jwt_jwks_uri
    else:
        kwargs["public_key"] = settings.jwt_public_key
    if settings.jwt_algorithm:
        kwargs["algorithm"] = settings.jwt_algorithm
    return JWTVerifier(**kwargs)


def require_http_auth(auth, settings) -> None:
    """Fail closed: refuse to serve HTTP without auth unless explicitly allowed."""
    if auth is None and not settings.allow_unauthenticated_http:
        raise SystemExit(
            "Refusing to start the HTTP transport without authentication. Set AUTH_MODE=jwt "
            "(with JWT_* settings), or set ALLOW_UNAUTHENTICATED_HTTP=true only when a platform "
            "in front of this server already enforces authentication."
        )
    if auth is None:
        logging.getLogger(__name__).warning(
            "HTTP transport running WITHOUT authentication (ALLOW_UNAUTHENTICATED_HTTP=true)."
        )


class RequestSizeLimitMiddleware:
    """Reject HTTP request bodies larger than ``max_bytes`` with 413, including chunked uploads."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_bytes):
            await self._reject(send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        started = False

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(send)

    async def _reject(self, send):
        body = json.dumps({"error": f"Request body exceeds {self.max_bytes} bytes."}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    pass


def audit_record(tool: str, arguments: dict | None, status: str, duration_ms: float, error: str | None = None) -> dict:
    """One structured audit line per tool call; argument values are truncated, never logged in full."""
    record = {
        "event": "tool_call",
        "tool": tool,
        "arguments": {k: str(v)[:200] for k, v in (arguments or {}).items()},
        "status": status,
        "duration_ms": round(duration_ms, 1),
    }
    if error:
        record["error"] = error
    return record
