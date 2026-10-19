"""GreenNode vBackup MCP Server entry point — follows the vServer handler pattern."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from greennode.mcp_core.config import resolve_config_dir
from greennode.mcp_core.http import user_token_var
from greennode.vbackup_mcp_server import __version__
from greennode.vbackup_mcp_server.auth import TokenManager
from greennode.vbackup_mcp_server.auth_debug import summarize_request
from greennode.vbackup_mcp_server.auth_handler import AuthHandler
from greennode.vbackup_mcp_server.backup_server_handler import BackupServerHandler
from greennode.vbackup_mcp_server.catalogue_handler import CatalogueHandler
from greennode.vbackup_mcp_server.client import VbackupClient
from greennode.vbackup_mcp_server.config import REGIONS, VbackupConfig, load_config
from greennode.vbackup_mcp_server.database_handler import DatabaseHandler
from greennode.vbackup_mcp_server.destination_handler import DestinationHandler
from greennode.vbackup_mcp_server.discovery_cache import DiscoveryCache
from greennode.vbackup_mcp_server.history_handler import HistoryHandler
from greennode.vbackup_mcp_server.metrics_handler import MetricsHandler
from greennode.vbackup_mcp_server.policy_handler import PolicyHandler
from greennode.vbackup_mcp_server.prompts_handler import PromptsHandler
from greennode.vbackup_mcp_server.vserver_handler import VserverHandler
from mcp.server.mcpserver import MCPServer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


# Prefer ~/.greennode; fall back to the legacy ~/.greenode when only it exists
CONFIG_PATH = resolve_config_dir()

SERVER_INSTRUCTIONS = """
# GreenNode vBackup MCP Server

MCP Server for GreenNode vBackup — scheduled, policy-driven backups of vServer
instances and their volumes and of vDB databases, the restore points they
produce, and the history of backup and restore runs.

## Two products, two parallel tool families

vBackup protects **vServer** instances (`backup_server` tools, ids `bk-ins-`)
and **vDB** databases (`backup_database` tools, ids `bk-db-`). They share
policies, destinations and the history service but nothing else: a database never
appears in a backup-server listing and vice versa, and each call answers `200`
with an empty list rather than an error when asked about the wrong family. When
a user says "my backups", establish which product they mean before searching —
"not found" in one family is not evidence of absence in the other.

## IMPORTANT: Operating mode

By default the server runs in **read-only** mode. Use the `--allow-write` flag to enable write operations (create, update, delete).

## Regions and backends

Every resource is region-scoped: `HCM-3` (default) or `HAN`, selected per call via the `region` parameter. Within a region a resource also belongs to a **backend** (`backendId`) and a project (`projectId`) — both are filters, never path segments, and are resolved from the caller's token. The two region gateways do NOT return the same set of backends, so never infer a region from a `backendId`. If a resource the user mentions isn't found, retry in the other region before reporting it missing.

## vBackup is not vServer snapshots

vBackup takes **file-level** backups into a destination vault and they survive the source server's deletion. vServer **snapshots** are block-level and live in the vServer product (a separate MCP server). When a user says "backup", confirm which one they mean instead of guessing.

## Presenting results

When rendering any resource list or detail to the user (tables, bullet lists),
ALWAYS include each item's `id` and `name` as the FIRST two columns (id, name,
then everything else) — follow-up commands and confirmations need them. Never
drop the id column to save space and never truncate id values.

Never silently accept a default for a parameter that encodes a user decision
(which policy, which destination, which volumes are included) — ask.

A backup server whose source server is gone (`server_deleted`) still holds
restore points and is still billed. Surface that state rather than hiding it.
"""

mcp = None


class UpstreamIdentityMiddleware(BaseHTTPMiddleware):
    """Per-request upstream identity for the HTTP transport.

    The AgentBase Gateway forwards the caller's IAM bearer token in the
    Authorization header. Resolution order:

    1. Bearer token on the request -> every vBackup call runs as the CALLER
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
            "- Write: ENABLED — create/update/delete tools are available. "
            "Every write still goes through the plan review + explicit user "
            "confirmation gate."
        )
    else:
        write = (
            "- Write: OFF — this session is read-only; create/update/delete "
            "tools are NOT registered. If the user asks for one, do NOT start "
            "the flow or ask any configuration question: tell them immediately "
            "to restart the server with --allow-write."
        )
    upstream = (
        "- vBackup identity: per-request — when the request carries an IAM bearer "
        "token in Authorization, every vBackup call runs as THAT caller "
        "(per-user projects and permissions); otherwise the shared service "
        "account is used."
    )
    return f"\n## This session (runtime mode)\n\n{write}\n{upstream}\n"


def create_server(auth_debug: bool = False, allow_write: bool = False) -> MCPServer:
    """Create and return a MCPServer server instance."""
    instructions = SERVER_INSTRUCTIONS + _mode_addendum(allow_write)
    server = MCPServer("vbackup-mcp-server", instructions=instructions, version=__version__)

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
        description="GreenNode MCP Server -- manage GreenNode vBackup via MCP"
    )
    parser.add_argument(
        "--allow-write",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable write mode (allow create, update, delete)",
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
        config = VbackupConfig(
            client_id="",
            client_secret="",
            default_region=os.environ.get("GRN_DEFAULT_REGION", "HCM-3"),
            regions=REGIONS,
            project_id=os.environ.get("GRN_PROJECT_ID"),
        )

    token_manager = TokenManager(config)
    client = VbackupClient(config, token_manager)

    mcp = create_server(auth_debug=auth_debug, allow_write=args.allow_write)

    cache = DiscoveryCache()
    AuthHandler(mcp, config, token_manager)
    CatalogueHandler(mcp, config, client, cache, allow_write=args.allow_write)
    DestinationHandler(mcp, config, client, cache, allow_write=args.allow_write)
    PolicyHandler(mcp, config, client, cache, allow_write=args.allow_write)
    BackupServerHandler(mcp, config, client, cache, allow_write=args.allow_write)
    DatabaseHandler(mcp, config, client, cache, allow_write=args.allow_write)
    HistoryHandler(mcp, config, client, allow_write=args.allow_write)
    MetricsHandler(mcp, config, client, allow_write=args.allow_write)
    VserverHandler(mcp, config, client, cache, allow_write=args.allow_write)

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
