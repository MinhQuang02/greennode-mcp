"""GreenNode vServer MCP Server entry point — follows the VKS handler pattern."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from greennode.mcp_core.config import resolve_config_dir
from greennode.mcp_core.http import user_token_var
from greennode.vserver_mcp_server import __version__
from greennode.vserver_mcp_server.auth import TokenManager
from greennode.vserver_mcp_server.auth_debug import summarize_request
from greennode.vserver_mcp_server.auth_handler import AuthHandler
from greennode.vserver_mcp_server.client import VbackupClient, VserverClient
from greennode.vserver_mcp_server.config import REGIONS, VserverConfig, load_config
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.flavor_handler import FlavorHandler
from greennode.vserver_mcp_server.image_handler import ImageHandler
from greennode.vserver_mcp_server.interconnect_handler import InterconnectHandler
from greennode.vserver_mcp_server.networkacl_handler import NetworkAclHandler
from greennode.vserver_mcp_server.networkinterface_handler import NetworkInterfaceHandler
from greennode.vserver_mcp_server.peering_handler import PeeringHandler
from greennode.vserver_mcp_server.placementgroup_handler import PlacementGroupHandler
from greennode.vserver_mcp_server.prompts_handler import PromptsHandler
from greennode.vserver_mcp_server.routetable_handler import RouteTableHandler
from greennode.vserver_mcp_server.secgroup_handler import SecurityGroupHandler
from greennode.vserver_mcp_server.server_handler import ServerHandler
from greennode.vserver_mcp_server.snapshot_handler import SnapshotHandler
from greennode.vserver_mcp_server.sshkey_handler import SshKeyHandler
from greennode.vserver_mcp_server.subnet_handler import SubnetHandler
from greennode.vserver_mcp_server.userimage_handler import UserImageHandler
from greennode.vserver_mcp_server.virtualip_handler import VirtualIpHandler
from greennode.vserver_mcp_server.volume_handler import VolumeHandler
from greennode.vserver_mcp_server.volumetype_handler import VolumeTypeHandler
from greennode.vserver_mcp_server.vpc_handler import VpcHandler
from greennode.vserver_mcp_server.zone_handler import ZoneHandler
from mcp.server.mcpserver import MCPServer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


# Prefer ~/.greennode; fall back to the legacy ~/.greenode when only it exists
CONFIG_PATH = resolve_config_dir()

SERVER_INSTRUCTIONS = """
# GreenNode vServer MCP Server

MCP Server for GreenNode vServer (compute / IaaS: instances, volumes, networking).

## IMPORTANT: Operating mode

By default the server runs in **read-only** mode. Use the `--allow-write` flag to enable write operations (create, update, delete, power actions).

## Regions and projects

Every resource is region-scoped: `HCM-3` (default) or `HAN`. Each region's gateway exposes its **own project**, resolved automatically — never ask the user for a project id. If a resource the user mentions isn't found, retry in the other region.

## Presenting results

When rendering any resource list or detail to the user (tables, bullet lists),
ALWAYS include each item's `id` and `name` as the FIRST two columns (id, name,
then everything else) — follow-up commands and confirmations need them. Never
drop the id column to save space and never truncate id values.

Never silently accept a default for a parameter that encodes a user decision
(sizes, disk types, images, security toggles, resource choices) — ask.
"""

mcp = None


class UpstreamIdentityMiddleware(BaseHTTPMiddleware):
    """Per-request upstream identity for the HTTP transport.

    The AgentBase Gateway forwards the caller's IAM bearer token in the
    Authorization header. Resolution order:

    1. Bearer token on the request -> every vServer call runs as the CALLER
       (token scoped to this request via a contextvar; a rejected user token
       never falls back to the service account).
    2. No token, and the operator opted in (--allow-service-account-fallback
       with service-account credentials configured) -> the shared service
       account.
    3. Otherwise -> 401. Without the opt-in, anyone who can reach the port
       would act as the service account.
    """

    def __init__(self, app, allow_service_account_fallback: bool) -> None:
        super().__init__(app)
        self._allow_service_account_fallback = allow_service_account_fallback

    async def dispatch(self, request: Request, call_next):
        """Resolve this request's upstream identity, then forward it."""
        if request.url.path == "/health":
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:].strip():
            ctx_token = user_token_var.set(auth[7:].strip())
            try:
                return await call_next(request)
            finally:
                user_token_var.reset(ctx_token)
        if self._allow_service_account_fallback:
            return await call_next(request)
        return Response(
            "Unauthorized: provide the caller's IAM bearer token in the Authorization header.",
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )


class AuthDebugMiddleware(BaseHTTPMiddleware):
    """DIAGNOSTIC: log a redacted summary of every inbound request, then pass it through unchanged.

    Never blocks a request; never logs the full bearer token.
    """

    async def dispatch(self, request: Request, call_next):
        """Log the request's redacted auth summary, then forward it untouched."""
        summary = summarize_request(request.method, request.url.path, request.headers)
        print(f"AUTH-DEBUG {json.dumps(summary, default=str)}", flush=True)
        return await call_next(request)


def _env_truthy(val: str | None) -> bool:
    """True for common truthy env-var spellings (1/true/yes/on)."""
    return (val or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_service_account_fallback(requested: bool, has_service_credentials: bool) -> bool:
    """Decide whether HTTP requests without a bearer token run as the service account.

    Off unless the operator asks for it: the fallback turns network reach into
    service-account access, so it belongs only behind a trusted authenticating
    proxy. Prints why the decision was made so a 401 after an upgrade is easy
    to trace.
    """
    if requested and has_service_credentials:
        print(
            "Warning: --allow-service-account-fallback is ON — HTTP requests without a "
            "bearer token run as the service account. Expose this port only behind an "
            "authenticating proxy.",
            file=sys.stderr,
        )
        return True
    if requested:
        print(
            "Note: --allow-service-account-fallback has no effect: no service-account "
            "credentials are configured.",
            file=sys.stderr,
        )
    elif has_service_credentials:
        print(
            "Note: HTTP requests without a bearer token are rejected (401); the configured "
            "service-account credentials serve them only with "
            "--allow-service-account-fallback.",
            file=sys.stderr,
        )
    return False


def _mode_addendum(allow_write: bool) -> str:
    """Runtime-mode addendum for SERVER_INSTRUCTIONS.

    The server knows this session's mode at startup — telling the agent up
    front turns "the create fails after the whole guided conversation" into
    "the agent refuses the flow in its first reply".
    """
    if allow_write:
        write = (
            "- Write: ENABLED — create/update/delete/power tools are available. "
            "Every write still goes through the plan review + explicit user "
            "confirmation gate."
        )
    else:
        write = (
            "- Write: OFF — this session is read-only; create/update/delete/power "
            "tools are NOT registered. If the user asks for one, do NOT start the "
            "creation flow or ask any configuration question: tell them immediately "
            "to restart the server with --allow-write."
        )
    upstream = (
        "- vServer identity: per-request — when the request carries an IAM bearer "
        "token in Authorization, every vServer call runs as THAT caller "
        "(per-user projects and permissions); otherwise the shared service "
        "account is used."
    )
    return f"\n## This session (runtime mode)\n\n{write}\n{upstream}\n"


def create_server(auth_debug: bool = False, allow_write: bool = False) -> MCPServer:
    """Create and return a MCPServer server instance."""
    instructions = SERVER_INSTRUCTIONS + _mode_addendum(allow_write)
    server = MCPServer("vserver-mcp-server", instructions=instructions, version=__version__)

    PromptsHandler(server)

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        """Liveness/readiness probe endpoint (no authentication required)."""
        return JSONResponse({"status": "ok"})

    if auth_debug:

        @server.custom_route("/whoami", methods=["GET"])
        async def whoami(request: Request) -> Response:
            """DIAGNOSTIC: echo the request's redacted auth summary (no auth, no verify)."""
            return JSONResponse(
                summarize_request(request.method, request.url.path, request.headers)
            )

    return server


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="GreenNode MCP Server -- manage GreenNode vServer via MCP"
    )
    parser.add_argument(
        "--allow-write",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable write mode (allow create, update, delete, power actions)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport mode: stdio (default) or streamable-http",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host for HTTP transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port for HTTP transport (default: 8000)",
    )
    parser.add_argument(
        "--auth-debug",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="DIAGNOSTIC: log redacted inbound auth summary and expose /whoami "
        "(HTTP only, off by default; env: GRN_MCP_AUTH_DEBUG). Do NOT use in production.",
    )
    parser.add_argument(
        "--allow-service-account-fallback",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="HTTP only: serve requests without a bearer token as the configured service "
        "account instead of rejecting them with 401. Only for a port reachable solely "
        "through a trusted authenticating proxy (env: GRN_MCP_ALLOW_SERVICE_ACCOUNT_FALLBACK).",
    )
    return parser


def main() -> None:
    """Load config, create handlers, and run the MCP server."""
    global mcp

    args = _build_parser().parse_args()
    auth_debug = args.auth_debug or _env_truthy(os.environ.get("GRN_MCP_AUTH_DEBUG"))

    try:
        config = load_config(CONFIG_PATH)
    except (FileNotFoundError, ValueError):
        if args.transport != "streamable-http":
            raise SystemExit(
                "No credentials found (~/.greennode or GRN_CLIENT_ID/"
                "GRN_CLIENT_SECRET). stdio transport requires service-account "
                "credentials; the HTTP transport can run token-passthrough-only."
            ) from None
        print(
            "Note: no service-account credentials configured — every HTTP request "
            "must carry the caller's IAM bearer token (passthrough-only).",
            file=sys.stderr,
        )
        config = VserverConfig(
            client_id="",
            client_secret="",
            default_region=os.environ.get("GRN_DEFAULT_REGION", "HCM-3"),
            regions=REGIONS,
            project_id=os.environ.get("GRN_PROJECT_ID"),
        )

    token_manager = TokenManager(config)
    client = VserverClient(config, token_manager)
    backup_client = VbackupClient(config, token_manager)

    mcp = create_server(auth_debug=auth_debug, allow_write=args.allow_write)

    cache = DiscoveryCache()
    AuthHandler(mcp, config, token_manager)
    ZoneHandler(mcp, config, client, cache)
    FlavorHandler(mcp, config, client, cache)
    ImageHandler(mcp, config, client, cache)
    VolumeTypeHandler(mcp, config, client, cache)
    VpcHandler(mcp, config, client, cache, allow_write=args.allow_write)
    SubnetHandler(mcp, config, client, cache, allow_write=args.allow_write)
    SecurityGroupHandler(mcp, config, client, cache, allow_write=args.allow_write)
    ServerHandler(mcp, config, client, cache, allow_write=args.allow_write)
    VolumeHandler(mcp, config, client, cache, allow_write=args.allow_write)
    UserImageHandler(mcp, config, client, cache, allow_write=args.allow_write)
    SshKeyHandler(mcp, config, client, cache, allow_write=args.allow_write)
    PlacementGroupHandler(mcp, config, client, cache, allow_write=args.allow_write)
    NetworkInterfaceHandler(mcp, config, client, cache, allow_write=args.allow_write)
    SnapshotHandler(mcp, config, client, cache, backup_client, allow_write=args.allow_write)
    RouteTableHandler(mcp, config, client, cache, allow_write=args.allow_write)
    NetworkAclHandler(mcp, config, client, cache, allow_write=args.allow_write)
    PeeringHandler(mcp, config, client, cache, allow_write=args.allow_write)
    VirtualIpHandler(mcp, config, client, cache, allow_write=args.allow_write)
    InterconnectHandler(mcp, config, client, cache, allow_write=args.allow_write)

    if args.transport == "stdio":
        if auth_debug:
            print(
                "Note: --auth-debug has no effect with stdio transport (HTTP only); ignoring.",
                file=sys.stderr,
            )
        mcp.run()
    else:
        import uvicorn

        transport_security = None
        loopback = {"127.0.0.1", "localhost", "::1"}
        if args.host not in loopback:
            from mcp.server.transport_security import TransportSecuritySettings

            transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=False,
            )

        starlette_app = mcp.streamable_http_app(
            transport_security=transport_security,
            host=args.host,
        )
        # Per-request upstream identity: caller's IAM token when present,
        # else the service account only when explicitly allowed, else 401.
        sa_fallback = _resolve_service_account_fallback(
            args.allow_service_account_fallback
            or _env_truthy(os.environ.get("GRN_MCP_ALLOW_SERVICE_ACCOUNT_FALLBACK")),
            bool(config.client_id and config.client_secret),
        )
        starlette_app.add_middleware(
            UpstreamIdentityMiddleware,
            allow_service_account_fallback=sa_fallback,
        )

        if auth_debug:
            print(
                "Warning: --auth-debug is ON. Redacted request auth metadata is logged "
                "and /whoami is exposed. Diagnostic only -- do NOT enable in production.",
                file=sys.stderr,
            )
            starlette_app.add_middleware(AuthDebugMiddleware)

        uv_config = uvicorn.Config(
            starlette_app,
            host=args.host,
            port=args.port,
            log_level="info",
        )
        server = uvicorn.Server(uv_config)
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
