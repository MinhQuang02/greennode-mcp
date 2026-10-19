"""Server construction, config, auth, prompts and the whole tool surface."""

from __future__ import annotations

import pytest
import respx
from .helpers import API_BASE, HAN_BASE, mock_iam
from greennode.vbackup_mcp_server.config import VBACKUP_SERVICE
from greennode.vbackup_mcp_server.discovery_cache import TTL_CONFIG, UNCACHED_TOOLS
from greennode.vbackup_mcp_server.guards import require_write
from greennode.vbackup_mcp_server.prompts_handler import _FEATURE_GUIDES, PromptsHandler
from greennode.vbackup_mcp_server.server import _mode_addendum, create_server
from greennode.vbackup_mcp_server.useragent import USER_AGENT
from mcp.server.mcpserver import MCPServer


READ_TOOLS = {
    "get_access_token",
    "get_configuration",
    "get_backup_destination",
    "get_backup_destination_metrics",
    "get_backup_server_point_download_urls",
    "get_backup_metrics",
    "get_backup_statistics",
    "get_backup_policy",
    "get_backup_server",
    "get_feature_guide",
    "get_vserver_backup_server",
    "get_vserver_instance",
    "get_vserver_backup_server_point",
    "get_vserver_backup_volume_point",
    "list_backends",
    "list_backup_destination_databases",
    "list_backup_destination_history",
    "list_backup_destination_servers",
    "list_backup_destination_tags",
    "list_backup_destinations",
    "list_backup_history",
    "list_backup_policies",
    "list_backup_products",
    "list_backup_regions",
    "get_backup_database",
    "list_backup_database_points",
    "list_backup_databases",
    "list_backup_server_points",
    "list_backup_server_volumes",
    "list_backup_servers",
    "list_database_backup_history",
    "list_database_restore_history",
    "list_databases",
    "list_protected_databases",
    "list_protected_servers",
    "list_restore_history",
    "list_server_migration_history",
    "list_volume_usage",
    "list_vserver_backup_server_points",
    "list_vserver_backup_servers",
    "list_vserver_backup_volume_points",
}

WRITE_TOOLS = {
    "create_backup_database",
    "create_backup_destination",
    "create_backup_policy",
    "create_backup_server",
    "create_vserver_backup_servers",
    "delete_backup_database",
    "delete_backup_database_point",
    "delete_backup_destination",
    "delete_backup_policy",
    "delete_backup_server",
    "disable_backup_database",
    "disable_backup_server",
    "delete_backup_server_point",
    "enable_backup_database",
    "enable_backup_server",
    "start_backup",
    "start_database_backup",
    "update_backup_server_destination",
    "update_default_backup_policy",
    "update_backup_destination_max_quota",
    "update_backup_destination_name",
    "update_backup_destination_soft_delete",
    "update_backup_destination_vault_lock",
    "update_backup_policy",
    "update_backup_database_policy",
    "update_backup_server_policy",
    "update_backup_server_volumes",
}


def _build(allow_write: bool):
    """Build a server with every handler registered, as main() does."""
    from greennode.mcp_core.auth import TokenManager
    from greennode.vbackup_mcp_server.auth_handler import AuthHandler
    from greennode.vbackup_mcp_server.backup_server_handler import BackupServerHandler
    from greennode.vbackup_mcp_server.catalogue_handler import CatalogueHandler
    from greennode.vbackup_mcp_server.client import VbackupClient
    from greennode.vbackup_mcp_server.config import REGIONS, VbackupConfig
    from greennode.vbackup_mcp_server.database_handler import DatabaseHandler
    from greennode.vbackup_mcp_server.destination_handler import DestinationHandler
    from greennode.vbackup_mcp_server.discovery_cache import DiscoveryCache
    from greennode.vbackup_mcp_server.history_handler import HistoryHandler
    from greennode.vbackup_mcp_server.metrics_handler import MetricsHandler
    from greennode.vbackup_mcp_server.policy_handler import PolicyHandler
    from greennode.vbackup_mcp_server.vserver_handler import VserverHandler

    config = VbackupConfig(
        client_id="id", client_secret="secret", default_region="HCM-3", regions=REGIONS
    )
    token_manager = TokenManager(config)
    client = VbackupClient(config, token_manager)
    server = create_server(allow_write=allow_write)
    cache = DiscoveryCache()

    AuthHandler(server, config, token_manager)
    CatalogueHandler(server, config, client, cache, allow_write=allow_write)
    DestinationHandler(server, config, client, cache, allow_write=allow_write)
    PolicyHandler(server, config, client, cache, allow_write=allow_write)
    BackupServerHandler(server, config, client, cache, allow_write=allow_write)
    DatabaseHandler(server, config, client, cache, allow_write=allow_write)
    HistoryHandler(server, config, client, allow_write=allow_write)
    MetricsHandler(server, config, client, allow_write=allow_write)
    VserverHandler(server, config, client, cache, allow_write=allow_write)
    return server


def test_create_server():
    assert create_server().name == "vbackup-mcp-server"


def test_regions_resolve_per_region(config):
    assert config.get_base_url("HCM-3", VBACKUP_SERVICE) == API_BASE
    assert config.get_base_url("HAN", VBACKUP_SERVICE) == HAN_BASE
    with pytest.raises(ValueError, match="does not exist"):
        config.get_base_url("NOPE", VBACKUP_SERVICE)


def test_default_region_used_when_none(config):
    assert config.get_base_url(None, VBACKUP_SERVICE) == API_BASE


def test_user_agent_identifies_this_server():
    assert USER_AGENT.startswith("vbackup-mcp-server/")


def test_write_guard_blocks_in_read_only_mode():
    with pytest.raises(ValueError, match="--allow-write"):
        require_write(False)
    require_write(True)


def test_mode_addendum_states_the_runtime_mode():
    assert "Write: OFF" in _mode_addendum(False)
    assert "Write: ENABLED" in _mode_addendum(True)


def test_read_only_server_does_not_announce_write_tools():
    """A read-only session must tell the agent up front, not fail mid-flow."""
    instructions = create_server(allow_write=False).instructions or ""
    assert "Write: OFF" in instructions
    assert "--allow-write" in instructions


@pytest.mark.asyncio
async def test_read_only_mode_registers_exactly_the_read_tools():
    """Locks the surface: a tool lost in a refactor fails here, not in production."""
    tools = {t.name for t in await _build(allow_write=False).list_tools()}
    assert tools == READ_TOOLS


@pytest.mark.asyncio
async def test_write_mode_registers_every_tool():
    tools = {t.name for t in await _build(allow_write=True).list_tools()}
    assert tools == READ_TOOLS | WRITE_TOOLS


@pytest.mark.asyncio
async def test_every_tool_is_annotated():
    """An unannotated tool cannot be auto-approved or warned about by a client."""
    for tool in await _build(allow_write=True).list_tools():
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is not None, tool.name


@pytest.mark.asyncio
async def test_read_tools_are_marked_read_only():
    tools = {t.name: t for t in await _build(allow_write=True).list_tools()}
    for name in READ_TOOLS:
        assert tools[name].annotations.read_only_hint is True, name
    for name in WRITE_TOOLS:
        assert tools[name].annotations.read_only_hint is False, name


@pytest.mark.asyncio
async def test_every_tool_has_a_description():
    """The docstring is the tool description an agent reads before calling."""
    for tool in await _build(allow_write=True).list_tools():
        assert tool.description and len(tool.description) > 40, tool.name


def test_cached_and_uncached_tools_do_not_overlap():
    """A tool in both lists would make the 'never cached' promise a lie."""
    assert not set(TTL_CONFIG) & set(UNCACHED_TOOLS)


@pytest.mark.asyncio
async def test_prompts_and_guide_tool_registered():
    server = MCPServer("test")
    PromptsHandler(server)
    tools = {t.name for t in await server.list_tools()}
    prompts = {p.name for p in await server.list_prompts()}
    assert "get_feature_guide" in tools
    assert prompts == {f"vbackup_{name}" for name in _FEATURE_GUIDES}


@pytest.mark.asyncio
async def test_every_feature_guide_is_reachable():
    """A Feature literal with no guide behind it fails the call at runtime."""
    handler = PromptsHandler(MCPServer("test"))
    for feature in _FEATURE_GUIDES:
        guide = await handler.get_feature_guide(feature=feature)
        assert len(guide) > 200, feature


@pytest.mark.asyncio
async def test_getting_started_guide_covers_the_snapshot_distinction():
    """The guide must stop an agent conflating vBackup with vServer snapshots."""
    handler = PromptsHandler(MCPServer("test"))
    guide = await handler.get_feature_guide(feature="getting_started")
    assert "snapshot" in guide.lower()
    assert "bk-ins-" in guide


@pytest.mark.asyncio
async def test_restore_guide_states_this_server_cannot_restore():
    """The gateway exposes restore history but no trigger — say so, don't hunt."""
    handler = PromptsHandler(MCPServer("test"))
    guide = await handler.get_feature_guide(feature="inspect_restore_point")
    assert "KHÔNG khôi phục được" in guide
    assert "list_restore_history" in guide


@respx.mock
@pytest.mark.asyncio
async def test_get_access_token_reports_region_and_endpoint(config):
    from greennode.mcp_core.auth import TokenManager
    from greennode.vbackup_mcp_server.auth_handler import AuthHandler

    mock_iam(respx.mock)
    handler = AuthHandler(MCPServer("test"), config, TokenManager(config))
    result = await handler.get_access_token()
    assert "region: HCM-3" in result
    assert API_BASE in result
    assert "access_token: tok" not in result
    assert "auth_mode: service account" in result


@respx.mock
@pytest.mark.asyncio
async def test_get_access_token_with_caller_token_skips_service_account(config):
    from greennode.mcp_core.auth import IAM_TOKEN_URL, TokenManager
    from greennode.mcp_core.http import user_token_var
    from greennode.vbackup_mcp_server.auth_handler import AuthHandler

    iam = respx.post(IAM_TOKEN_URL)
    handler = AuthHandler(MCPServer("test"), config, TokenManager(config))
    t = user_token_var.set("caller-token-xyz")
    try:
        result = await handler.get_access_token()
    finally:
        user_token_var.reset(t)
    assert not iam.called
    assert "caller-token-xyz" not in result
    assert "auth_mode: caller IAM token" in result


def _identity_client(allow_service_account_fallback: bool):
    from greennode.mcp_core.http import user_token_var
    from greennode.vbackup_mcp_server.server import UpstreamIdentityMiddleware
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
    from greennode.vbackup_mcp_server.server import (
        _build_parser,
        _resolve_service_account_fallback,
    )

    assert _build_parser().parse_args([]).allow_service_account_fallback is False
    assert _resolve_service_account_fallback(requested, has_credentials) is expected
