"""Tests for server CLI arg parsing, auth middleware, and the --auth-debug diagnostic."""

from __future__ import annotations

import argparse
import pytest
from greennode.vks_mcp_server.server import _build_parser
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def _parse_args(argv: list[str]) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def test_default_transport_is_stdio():
    args = _parse_args([])
    assert args.transport == "stdio"


def test_transport_http_flag():
    args = _parse_args(["--transport", "streamable-http"])
    assert args.transport == "streamable-http"


def test_default_host_and_port():
    args = _parse_args(["--transport", "streamable-http"])
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_custom_host_and_port():
    args = _parse_args(["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9000"])
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_invalid_transport_raises():
    with pytest.raises(SystemExit):
        _parse_args(["--transport", "sse"])


async def _homepage(request):
    return PlainTextResponse("ok")


_inner_app = Starlette(routes=[Route("/", _homepage)])


# ---------------------------------------------------------------------------
# Upstream identity: caller's IAM token when present, else the service account
# only when explicitly allowed, else 401
# ---------------------------------------------------------------------------


def _identity_app(allow_service_account_fallback: bool):
    from greennode.mcp_core.http import user_token_var
    from greennode.vks_mcp_server.server import UpstreamIdentityMiddleware

    seen: dict = {}

    async def echo_identity(request):
        seen["token"] = user_token_var.get()
        return PlainTextResponse("ok")

    app = UpstreamIdentityMiddleware(
        Starlette(routes=[Route("/", echo_identity), Route("/health", echo_identity)]),
        allow_service_account_fallback=allow_service_account_fallback,
    )
    return app, seen


def test_identity_middleware_uses_bearer_token_when_present():
    """A request carrying an IAM token runs as THAT caller."""
    app, seen = _identity_app(allow_service_account_fallback=False)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/", headers={"Authorization": "Bearer user-iam-tok"})
    assert r.status_code == 200
    assert seen["token"] == "user-iam-tok"


def test_identity_middleware_falls_back_to_service_account_when_allowed():
    """No token + explicit opt-in -> the shared service account."""
    app, seen = _identity_app(allow_service_account_fallback=True)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/")
    assert r.status_code == 200
    assert seen["token"] is None  # BaseClient will use the TokenManager


def test_identity_middleware_401_without_token_by_default():
    """No token and no opt-in -> 401, even when service credentials exist."""
    app, seen = _identity_app(allow_service_account_fallback=False)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/")
    assert r.status_code == 401
    assert "WWW-Authenticate" in r.headers
    assert "token" not in seen  # the request never reached the app


def test_identity_middleware_health_stays_open():
    app, _ = _identity_app(allow_service_account_fallback=False)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/health").status_code == 200


def test_service_account_fallback_flag_defaults_off():
    assert _parse_args(["--transport", "streamable-http"]).allow_service_account_fallback is False
    args = _parse_args(["--allow-service-account-fallback"])
    assert args.allow_service_account_fallback is True


@pytest.mark.parametrize(
    "requested,has_credentials,expected",
    [(False, True, False), (False, False, False), (True, False, False), (True, True, True)],
)
def test_service_account_fallback_needs_opt_in_and_credentials(
    requested, has_credentials, expected
):
    from greennode.vks_mcp_server.server import _resolve_service_account_fallback

    assert _resolve_service_account_fallback(requested, has_credentials) is expected


def test_identity_middleware_resets_token_after_request():
    from greennode.mcp_core.http import user_token_var

    app, _ = _identity_app(allow_service_account_fallback=True)
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/", headers={"Authorization": "Bearer leak-check"})
    assert user_token_var.get() is None  # no bleed into the next context
