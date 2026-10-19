"""GreenNode MCP Server entry point — follows EKS MCP Server handler pattern."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from greennode.mcp_core.config import resolve_config_dir
from greennode.mcp_core.http import user_token_var
from greennode.vks_mcp_server import __version__
from greennode.vks_mcp_server.auth import TokenManager
from greennode.vks_mcp_server.auth_debug import summarize_request
from greennode.vks_mcp_server.auth_handler import AuthHandler
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.cluster_handler import ClusterHandler
from greennode.vks_mcp_server.config import REGIONS, VksConfig, load_config
from greennode.vks_mcp_server.discovery_cache import DiscoveryCache
from greennode.vks_mcp_server.discovery_handler import DiscoveryHandler
from greennode.vks_mcp_server.k8s_handler import K8sHandler
from greennode.vks_mcp_server.nodegroup_handler import NodeGroupHandler
from greennode.vks_mcp_server.prompts_handler import PromptsHandler
from greennode.vks_mcp_server.version_handler import VersionHandler
from mcp.server.mcpserver import MCPServer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


# Prefer ~/.greennode; fall back to the legacy ~/.greenode when only it exists
# (mirrors greennode-cli after the rename).
CONFIG_PATH = resolve_config_dir()

SERVER_INSTRUCTIONS = """
# GreenNode MCP Server

MCP Server for GreenNode Kubernetes Service (VKS).

## IMPORTANT: Operating mode

By default the server runs in **read-only** mode. Use the `--allow-write` flag to enable write operations (create, update, delete cluster/node group).

## Regions

Every resource is region-scoped: `HCM-3` (default) or `HAN`. Clusters, SSH keys, security groups, and quotas differ per region — if a resource the user mentions isn't found, retry in the other region. Discovery outputs echo the region they were fetched from.

## Creation flows (resolve every id via discovery — never invent one)

Before starting either flow, call get_creation_guide(resource="cluster" | "nodegroup") and conduct the conversation exactly as it says (question order, one setting per question, confirm gate).

Create a cluster: get_quota -> list_vpcs (vpcId) -> list_cluster_versions -> validate_cluster_create -> create_cluster -> poll get_cluster until ACTIVE. The cluster is control-plane only; add workers next.

Add a node group: get_cluster (vpcId + region) -> get_quota -> list_subnets (user picks a subnetId; its zone scopes the next two) -> list_flavors(cluster_id, subnet_id) (flavorId) -> list_volume_types(cluster_id, subnet_id) (IOPS tier id = diskType) -> list_ssh_keys (sshKeyId) -> validate_nodegroup_create -> create_nodegroup -> poll get_nodegroup.

Present the discovered options to the user and wait for confirmation before any write call. Never silently accept a default for a parameter that encodes a user decision (credential lifetimes, sizes, security toggles, resource choices) — ask.

## Presenting results

When rendering any resource list or detail to the user (tables, bullet
lists), ALWAYS include each item's `id` and `name` as the FIRST two columns
(id, name, then everything else) — follow-up commands and confirmations need
them. Never drop the id column to save space and never truncate id values.

## Guided prompts

For full step-by-step flows (safe defaults, plan review, confirm gate) load the prompts: `vks_getting_started`, `vks_create_cluster`, `vks_create_nodegroup`.

## Available tools

### Read-only (always available):
- get_creation_guide: Step-by-step guide for the create flows — call it FIRST
- get_access_token: Check which identity API calls run as and that it authenticates (never returns the token)
- list_clusters, get_cluster: View clusters (get_cluster is step 1 of the node-group flow)
- get_cluster_kubeconfig: Get kubeconfig YAML — cluster-admin credentials, requires --allow-sensitive-data-access (a new cluster needs generate_kubeconfig first)
- get_cluster_events: View cluster events
- delete_cluster_dryrun: Preview information before deleting a cluster
- validate_cluster_create: Validate body before creating a cluster
- list_cluster_versions: List available Kubernetes versions
- list_nodegroups, get_nodegroup: View node groups
- list_nodes: List nodes in a node group
- delete_nodegroup_dryrun: Preview information before deleting a node group
- validate_nodegroup_create: Validate a create_nodegroup body before creating (free, non-mutating)
- list_vpcs, list_subnets, list_ssh_keys, list_security_groups, list_placement_groups: Discover vServer resources to fill cluster/node-group creation
- list_flavors, list_volume_types: Zone-scoped discovery (pass cluster_id + the chosen subnet_id; region and zone are derived server-side)
- get_quota: Check per-region limits before creating anything

### Write (requires --allow-write):
- create_cluster, update_cluster, delete_cluster: Create, update, delete cluster (delete requires deleting every node group FIRST — the API cascades nothing; delete_cluster_dryrun lists them and the order)
- configure_auto_upgrade, delete_auto_upgrade: Configure auto-upgrade
- configure_auto_healing: Configure cluster auto-healing
- generate_kubeconfig: Mint a kubeconfig for a cluster (async; required once for a new cluster)
- create_nodegroup, update_nodegroup, delete_nodegroup: Create, update, delete node group
- update_nodegroup_metadata: Update a node group's labels/tags/taints
- upgrade_nodegroup_version: Upgrade a node group's Kubernetes version (irreversible)

### Kubernetes Resource Management (requires kubeconfig from VKS API):
Prefer these tools over raw `kubectl` — they fetch and cache the cluster's kubeconfig from the VKS API automatically, no manual setup.
- list_k8s_resources: List K8s resources (Pods, Services, Deployments...)
- get_pod_logs: View pod logs
- get_k8s_events: View resource events
- list_api_versions: List API versions
- generate_app_manifest: Generate a deployment manifest for an app
- manage_k8s_resource: CRUD single K8s resource (requires --allow-write for write ops, --allow-sensitive-data-access for Secrets)
- apply_yaml: Apply YAML manifest content (pass the YAML itself, not a file path; requires --allow-write)
"""

mcp = None


class UpstreamIdentityMiddleware(BaseHTTPMiddleware):
    """Per-request upstream identity for the HTTP transport.

    The AgentBase Gateway forwards the caller's IAM bearer token in the
    Authorization header. Resolution order:

    1. Bearer token on the request -> every VKS/vServer call runs as the
       CALLER (token scoped to this request via a contextvar; a rejected
       user token never falls back to the service account).
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
        # Emit a single plain line to stdout rather than via the logging framework:
        # the uvicorn/rich log handler wraps long messages across several lines,
        # which splits the JSON and makes it ungreppable in collected runtime logs.
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


def _mode_addendum(allow_write: bool, allow_sensitive_data_access: bool) -> str:
    """Runtime-mode addendum for SERVER_INSTRUCTIONS (EKS pattern).

    The server knows this session's mode at startup — telling the agent up
    front turns "the create fails after the whole guided conversation" into
    "the agent refuses the flow in its first reply".
    """
    if allow_write:
        write = (
            "- Write: ENABLED — create/update/delete/scale tools are available. "
            "Every write still goes through the plan review + explicit user "
            "confirmation gate."
        )
    else:
        write = (
            "- Write: OFF — this session is read-only; create/update/delete/scale "
            "tools are NOT registered. If the user asks for one, do NOT start the "
            "creation flow or ask any configuration question: tell them immediately "
            "to restart the server with --allow-write."
        )
    if allow_sensitive_data_access:
        sensitive = (
            "- Sensitive data: ENABLED — Kubernetes Secrets and the cluster "
            "kubeconfig can be read."
        )
    else:
        sensitive = (
            "- Sensitive data: OFF — reading Kubernetes Secrets or the cluster "
            "kubeconfig requires restarting with --allow-sensitive-data-access."
        )
    upstream = (
        "- VKS identity: per-request — when the request carries an IAM bearer "
        "token in Authorization, every VKS/vServer call runs as THAT caller "
        "(per-user projects and permissions); otherwise the shared service "
        "account is used."
    )
    return f"\n## This session (runtime mode)\n\n{write}\n{sensitive}\n{upstream}\n"


def create_server(
    auth_debug: bool = False,
    allow_write: bool = False,
    allow_sensitive_data_access: bool = False,
) -> MCPServer:
    """Create and return an MCPServer instance."""
    instructions = SERVER_INSTRUCTIONS + _mode_addendum(allow_write, allow_sensitive_data_access)
    server = MCPServer("vks-mcp-server", instructions=instructions, version=__version__)

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        """Liveness/readiness probe endpoint (no authentication required)."""
        return JSONResponse({"status": "ok"})

    if auth_debug:
        # Intentionally unauthenticated: it must observe the raw inbound request.

        @server.custom_route("/whoami", methods=["GET"])
        async def whoami(request: Request) -> Response:
            """DIAGNOSTIC: echo the request's redacted auth summary (no auth, no verify)."""
            return JSONResponse(
                summarize_request(request.method, request.url.path, request.headers)
            )

    # Prompts are always available (read-only guidance, no --allow-write needed).
    PromptsHandler(server)

    return server


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="GreenNode MCP Server -- manage GreenNode Kubernetes Service via MCP"
    )
    parser.add_argument(
        "--allow-write",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable write mode (allow create, update, delete cluster/node group)",
    )
    parser.add_argument(
        "--allow-sensitive-data-access",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable access to sensitive data (required for reading Kubernetes Secrets)",
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
        # Passthrough-only deployment: no service-account credentials at all.
        # HTTP requests must then carry the caller's IAM token (401 otherwise);
        # stdio has no headers, so it cannot run without credentials.
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
        config = VksConfig(
            client_id="",
            client_secret="",
            default_region=os.environ.get("GRN_DEFAULT_REGION", "HCM-3"),
            regions=REGIONS,
            project_id=os.environ.get("GRN_PROJECT_ID"),
        )
    token_manager = TokenManager(config)
    client = VksClient(config, token_manager)

    mcp = create_server(
        auth_debug=auth_debug,
        allow_write=args.allow_write,
        allow_sensitive_data_access=args.allow_sensitive_data_access,
    )

    AuthHandler(mcp, config, token_manager)
    discovery_cache = DiscoveryCache()
    ClusterHandler(
        mcp,
        config,
        client,
        allow_write=args.allow_write,
        allow_sensitive_data_access=args.allow_sensitive_data_access,
        cache=discovery_cache,
    )
    NodeGroupHandler(mcp, config, client, allow_write=args.allow_write, cache=discovery_cache)
    DiscoveryHandler(mcp, config, client, discovery_cache)
    VersionHandler(mcp, config, client, discovery_cache)
    K8sHandler(
        mcp,
        config,
        client,
        allow_write=args.allow_write,
        allow_sensitive_data_access=args.allow_sensitive_data_access,
    )

    if args.transport == "stdio":
        if auth_debug:
            print(
                "Note: --auth-debug has no effect with stdio transport (HTTP only); ignoring.",
                file=sys.stderr,
            )
        mcp.run()
    else:
        # streamable-http mode
        import uvicorn  # optional dep; only required for streamable-http mode

        # host/port go straight to uvicorn below; the app only needs to know the
        # host it is bound to for DNS-rebinding protection.
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
