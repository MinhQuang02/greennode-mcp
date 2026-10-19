"""Tests for the vServer MCP server wiring (config, client, auth, zones)."""

from __future__ import annotations

import httpx
import pytest
import respx
from greennode.vserver_mcp_server.auth import TokenManager
from greennode.vserver_mcp_server.auth_handler import AuthHandler
from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import REGIONS, load_config
from greennode.vserver_mcp_server.discovery_cache import TTL_CONFIG, DiscoveryCache
from greennode.vserver_mcp_server.paging import as_list, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.server import _mode_addendum, create_server
from greennode.vserver_mcp_server.zone_handler import ZoneHandler
from mcp.server.mcpserver import MCPServer


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
HCM3 = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
HAN = "https://han-1.api.vngcloud.vn/vserver/vserver-gateway"
PROJECT = "pro-test-0001"


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


@pytest.fixture
def client(config):
    return VserverClient(config, TokenManager(config))


@pytest.fixture
def cache():
    return DiscoveryCache()


@pytest.fixture
def handler(config, client, cache):
    return ZoneHandler(MCPServer("test"), config, client, cache)


# ── server ────────────────────────────────────────────────────────────────────


def test_create_server():
    server = create_server()
    assert server.name == "vserver-mcp-server"


def test_mode_addendum_reflects_write_flag():
    assert "Write: OFF" in _mode_addendum(allow_write=False)
    assert "Write: ENABLED" in _mode_addendum(allow_write=True)


# ── config ────────────────────────────────────────────────────────────────────


def test_regions_cover_both_gateways():
    assert REGIONS["HCM-3"].vserver == HCM3
    assert REGIONS["HAN"].vserver == HAN


def test_get_base_url_defaults_to_configured_region(config):
    assert config.get_base_url(None, "vserver") == HCM3
    assert config.get_base_url("HAN", "vserver") == HAN


def test_get_base_url_rejects_unknown_region(config):
    with pytest.raises(ValueError, match="does not exist"):
        config.get_base_url("NOPE", "vserver")


# ── paging helpers ────────────────────────────────────────────────────────────


def test_as_list_handles_every_envelope():
    assert as_list([1, 2]) == [1, 2]
    assert as_list({"listData": [1]}) == [1]
    assert as_list({"data": [2]}) == [2]
    assert as_list({"volumeTypes": [3]}, "volumeTypes") == [3]
    assert as_list({"nothing": 1}) == []


def test_as_list_unwraps_the_v1_success_envelope():
    images = {"success": True, "errorCode": None, "errorMsg": None, "extra": {}, "images": [1, 2]}
    assert as_list(images) == [1, 2]
    zones = {"success": True, "errorCode": None, "volumeTypeZones": [{"id": "vt-1"}]}
    assert as_list(zones) == [{"id": "vt-1"}]


def test_as_list_is_ambiguity_safe():
    assert as_list({"success": True, "a": [1], "b": [2]}) == []
    assert as_list({"images": [1]}, "volumeTypes") == []


def test_unwrap_returns_inner_data_object():
    assert unwrap({"data": {"id": "x"}}) == {"id": "x"}
    assert unwrap({"id": "x"}) == {"id": "x"}


# ── project resolution ────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_require_project_id_uses_configured_value_for_default_region(config, client):
    assert await require_project_id(config, client) == PROJECT


@respx.mock
@pytest.mark.asyncio
async def test_require_project_id_discovers_and_caches_other_region(config, client):
    _mock_iam(respx.mock)
    route = respx.get(f"{HAN}/v1/projects").mock(
        return_value=httpx.Response(200, json={"data": [{"projectId": "pro-han-9"}]})
    )
    assert await require_project_id(config, client, "HAN") == "pro-han-9"
    assert await require_project_id(config, client, "HAN") == "pro-han-9"
    assert route.call_count == 1


@respx.mock
@pytest.mark.asyncio
async def test_require_project_id_errors_when_no_project(config, client):
    _mock_iam(respx.mock)
    respx.get(f"{HAN}/v1/projects").mock(return_value=httpx.Response(200, json={"data": []}))
    with pytest.raises(ValueError, match="Could not determine project_id"):
        await require_project_id(config, client, "HAN")


# ── auth handler ──────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_get_access_token_reports_endpoint_without_the_token(config):
    _mock_iam(respx.mock)
    token_manager = TokenManager(config)
    handler = AuthHandler(MCPServer("test"), config, token_manager)
    out = await handler.get_access_token()
    assert "access_token: tok" not in out
    assert "auth_mode: service account" in out
    assert HCM3 in out


@respx.mock
@pytest.mark.asyncio
async def test_get_access_token_with_caller_token_skips_service_account(config):
    from greennode.mcp_core.http import user_token_var

    iam = respx.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )
    handler = AuthHandler(MCPServer("test"), config, TokenManager(config))
    t = user_token_var.set("caller-token-xyz")
    try:
        out = await handler.get_access_token()
    finally:
        user_token_var.reset(t)
    assert not iam.called
    assert "caller-token-xyz" not in out
    assert "auth_mode: caller IAM token" in out


# ── HTTP upstream identity ────────────────────────────────────────────────────


def _identity_client(allow_service_account_fallback: bool):
    from greennode.mcp_core.http import user_token_var
    from greennode.vserver_mcp_server.server import UpstreamIdentityMiddleware
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
    from greennode.vserver_mcp_server.server import (
        _build_parser,
        _resolve_service_account_fallback,
    )

    assert _build_parser().parse_args([]).allow_service_account_fallback is False
    assert _resolve_service_account_fallback(requested, has_credentials) is expected


# ── zone handler ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_zones_registered(handler):
    tools = {t.name for t in await handler.mcp.list_tools()}
    assert "list_zones" in tools


@respx.mock
@pytest.mark.asyncio
async def test_list_zones_returns_structured_and_filters_disabled(handler):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v1/{PROJECT}/zones").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "uuid": "z-1",
                        "name": "HCM03-1A",
                        "description": "Zone A",
                        "isEnabled": True,
                    },
                    {"uuid": "z-2", "name": "HCM03-1B", "isEnabled": False},
                ]
            },
        )
    )
    result = await handler.list_zones(region="HCM-3", refresh=False)
    assert result.region == "HCM-3"
    assert [z.id for z in result.zones] == ["z-1"]
    assert result.zones[0].name == "HCM03-1A"


@respx.mock
@pytest.mark.asyncio
async def test_list_zones_reports_type_and_default_like_the_console(handler):
    """The console groups zones by zoneType and marks the default; the id is not the label."""
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v1/{PROJECT}/zones").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "uuid": "HCM03-1C",
                        "name": "HCM-1C",
                        "zoneType": "AVAILABILITY",
                        "isDefault": True,
                        "isEnabled": True,
                    },
                    {
                        "uuid": "HCM03-BKK-01",
                        "name": "HCM-BKK-1A",
                        "zoneType": "LOCAL",
                        "isDefault": False,
                        "isEnabled": True,
                    },
                ]
            },
        )
    )
    result = await handler.list_zones(region="HCM-3", refresh=False)
    assert [(z.id, z.name, z.zone_type, z.is_default) for z in result.zones] == [
        ("HCM03-1C", "HCM-1C", "AVAILABILITY", True),
        ("HCM03-BKK-01", "HCM-BKK-1A", "LOCAL", False),
    ]


@respx.mock
@pytest.mark.asyncio
async def test_list_zones_is_cached_until_refresh(handler):
    _mock_iam(respx.mock)
    route = respx.get(f"{HCM3}/v1/{PROJECT}/zones").mock(
        return_value=httpx.Response(200, json={"data": [{"uuid": "z-1", "name": "A"}]})
    )
    await handler.list_zones(region="HCM-3", refresh=False)
    await handler.list_zones(region="HCM-3", refresh=False)
    assert route.call_count == 1

    await handler.list_zones(region="HCM-3", refresh=True)
    assert route.call_count == 2


def test_discovery_cache_ttls_are_tiered():
    assert TTL_CONFIG["list_zones"] > TTL_CONFIG["list_vpcs"] > TTL_CONFIG["list_ssh_keys"]
