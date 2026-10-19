"""Tests for the vMonitor MCP server wiring."""

from __future__ import annotations

import pytest
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.client import VmonitorClient
from greennode.vmonitor_mcp_server.config import load_config
from greennode.vmonitor_mcp_server.dashboard_handler import DashboardHandler
from greennode.vmonitor_mcp_server.server import create_server
from mcp.server.mcpserver import MCPServer


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


@pytest.fixture
def client(config):
    return VmonitorClient(config, TokenManager(config))


@pytest.fixture
def handler(config, client):
    return DashboardHandler(MCPServer("test"), config, client)


def test_create_server():
    server = create_server()
    assert server.name == "vmonitor-mcp-server"


def test_config_base_url_ignores_region(config):
    assert config.get_base_url(None, "vmonitor") == "https://vmonitorapis.vngcloud.vn/vmonitor-api"
    assert config.get_base_url("anything", "vmonitor") == config.base_url


@pytest.mark.asyncio
async def test_list_dashboards_registered(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    assert "list_dashboards" in tools


# ── HTTP upstream identity ────────────────────────────────────────────────────


def _identity_client(allow_service_account_fallback: bool):
    from greennode.mcp_core.http import user_token_var
    from greennode.vmonitor_mcp_server.server import UpstreamIdentityMiddleware
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    seen: dict = {}

    async def echo_identity(request):
        seen["token"] = user_token_var.get()
        return PlainTextResponse("ok")

    app = UpstreamIdentityMiddleware(
        Starlette(routes=[Route("/", echo_identity), Route("/health", echo_identity)]),
        allow_service_account_fallback=allow_service_account_fallback,
    )
    return TestClient(app, raise_server_exceptions=False), seen


def test_identity_middleware_401_without_token_by_default():
    client, seen = _identity_client(allow_service_account_fallback=False)
    r = client.get("/")
    assert r.status_code == 401
    assert "token" not in seen
    assert client.get("/health").status_code == 200


def test_identity_middleware_bearer_runs_as_caller():
    client, seen = _identity_client(allow_service_account_fallback=False)
    assert client.get("/", headers={"Authorization": "Bearer user-tok"}).status_code == 200
    assert seen["token"] == "user-tok"


def test_identity_middleware_falls_back_only_when_allowed():
    client, seen = _identity_client(allow_service_account_fallback=True)
    assert client.get("/").status_code == 200
    assert seen["token"] is None


@pytest.mark.parametrize(
    "requested,has_credentials,expected",
    [(False, True, False), (False, False, False), (True, False, False), (True, True, True)],
)
def test_service_account_fallback_needs_opt_in_and_credentials(
    requested, has_credentials, expected
):
    from greennode.vmonitor_mcp_server.server import (
        _build_parser,
        _resolve_service_account_fallback,
    )

    assert _build_parser().parse_args([]).allow_service_account_fallback is False
    assert _resolve_service_account_fallback(requested, has_credentials) is expected
