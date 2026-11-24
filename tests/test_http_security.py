"""Remote-deployment security: request-size limits, fail-closed auth, audit records, probes."""

import asyncio
from types import SimpleNamespace

import pytest

from irs_research_server.security import (
    AuthConfigError,
    RequestSizeLimitMiddleware,
    audit_record,
    build_auth,
    require_http_auth,
)


def _settings(**overrides):
    base = dict(
        auth_mode="none",
        jwt_jwks_uri=None,
        jwt_public_key=None,
        jwt_algorithm=None,
        jwt_issuer=None,
        jwt_audience=None,
        jwt_scopes=[],
        allow_unauthenticated_http=False,
    )
    return SimpleNamespace(**{**base, **overrides})


async def _echo_app(scope, receive, send):
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": body})


def _call(app, headers, chunks):
    sent = []
    pending = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]

    async def receive():
        return pending.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "headers": headers}
    asyncio.run(app(scope, receive, send))
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def test_small_request_passes():
    app = RequestSizeLimitMiddleware(_echo_app, max_bytes=10)
    assert _call(app, [(b"content-length", b"5")], [b"hello"]) == 200


def test_declared_oversize_request_is_rejected():
    app = RequestSizeLimitMiddleware(_echo_app, max_bytes=10)
    assert _call(app, [(b"content-length", b"999")], [b"x"]) == 413


def test_chunked_oversize_request_is_rejected():
    app = RequestSizeLimitMiddleware(_echo_app, max_bytes=10)
    assert _call(app, [], [b"123456", b"789012"]) == 413


def test_http_refuses_to_start_without_auth():
    with pytest.raises(SystemExit):
        require_http_auth(None, _settings())


def test_explicit_opt_out_allows_unauthenticated_http():
    require_http_auth(None, _settings(allow_unauthenticated_http=True))


def test_auth_none_returns_no_verifier():
    assert build_auth(_settings()) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"auth_mode": "basic"},
        {"auth_mode": "jwt", "jwt_issuer": "i", "jwt_audience": "a"},  # no key source
        {
            "auth_mode": "jwt",
            "jwt_jwks_uri": "https://idp/jwks",
            "jwt_public_key": "k",
            "jwt_issuer": "i",
            "jwt_audience": "a",
        },
        {"auth_mode": "jwt", "jwt_jwks_uri": "https://idp/jwks"},  # no issuer/audience
    ],
)
def test_invalid_auth_config_is_rejected(overrides):
    with pytest.raises(AuthConfigError):
        build_auth(_settings(**overrides))


def test_audit_record_truncates_argument_values():
    record = audit_record("lookup_form", {"query": "x" * 500}, "completed", 12.34)
    assert len(record["arguments"]["query"]) == 200
    assert record["status"] == "completed" and record["duration_ms"] == 12.3


def test_health_and_ready_probes():
    pytest.importorskip("fastmcp")
    from starlette.testclient import TestClient

    from irs_research_server.app import mcp

    with TestClient(mcp.http_app()) as client:
        health = client.get("/health")
        assert health.status_code == 200 and health.json()["catalog_loaded"] is True
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json()["checks"]["catalog"]["products"] > 2800
        assert ready.json()["checks"]["cache_db"]["ok"] is True
