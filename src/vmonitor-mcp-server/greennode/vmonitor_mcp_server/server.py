"""GreenNode vMonitor MCP Server entry point.

Wires the configuration, IAM token manager, HTTP clients and handlers together
and runs the MCPServer server over stdio or streamable-http. Each handler owns one
resource area and registers its tools on the shared MCPServer instance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from greennode.mcp_core.config import resolve_config_dir
from greennode.mcp_core.http import user_token_var
from greennode.vmonitor_mcp_server import __version__
from greennode.vmonitor_mcp_server.alarm_handler import AlarmHandler
from greennode.vmonitor_mcp_server.apikey_handler import ApiKeyHandler
from greennode.vmonitor_mcp_server.auth import TokenManager
from greennode.vmonitor_mcp_server.auth_debug import summarize_request
from greennode.vmonitor_mcp_server.certificate_handler import CertificateHandler
from greennode.vmonitor_mcp_server.change_alarm_handler import ChangeAlarmHandler
from greennode.vmonitor_mcp_server.client import (
    VmonitorBillingClient,
    VmonitorClient,
    VmonitorLogClient,
    VmonitorNotificationClient,
    VmonitorUptimeClient,
)
from greennode.vmonitor_mcp_server.config import VmonitorConfig, load_config
from greennode.vmonitor_mcp_server.dashboard_handler import DashboardHandler
from greennode.vmonitor_mcp_server.infrastructure_handler import InfrastructureHandler
from greennode.vmonitor_mcp_server.integration_handler import IntegrationHandler
from greennode.vmonitor_mcp_server.log_archive_handler import LogArchiveHandler
from greennode.vmonitor_mcp_server.log_mapping_handler import LogMappingHandler
from greennode.vmonitor_mcp_server.log_pipeline_handler import LogPipelineHandler
from greennode.vmonitor_mcp_server.log_processor_handler import LogProcessorHandler
from greennode.vmonitor_mcp_server.log_project_handler import LogProjectHandler
from greennode.vmonitor_mcp_server.log_refill_handler import LogRefillHandler
from greennode.vmonitor_mcp_server.log_search_handler import LogSearchHandler
from greennode.vmonitor_mcp_server.metric_catalogue_handler import MetricCatalogueHandler
from greennode.vmonitor_mcp_server.metric_unit_handler import MetricUnitHandler
from greennode.vmonitor_mcp_server.notification_handler import NotificationHandler
from greennode.vmonitor_mcp_server.prompts_handler import PromptsHandler
from greennode.vmonitor_mcp_server.quota_catalog_handler import QuotaCatalogHandler
from greennode.vmonitor_mcp_server.quota_order_handler import QuotaOrderHandler
from greennode.vmonitor_mcp_server.quota_price_handler import QuotaPriceHandler
from greennode.vmonitor_mcp_server.quota_usage_handler import QuotaUsageHandler
from greennode.vmonitor_mcp_server.statistic_handler import StatisticHandler
from greennode.vmonitor_mcp_server.synthetic_location_handler import SyntheticLocationHandler
from greennode.vmonitor_mcp_server.synthetic_uptime_handler import SyntheticUptimeHandler
from greennode.vmonitor_mcp_server.variable_handler import VariableHandler
from greennode.vmonitor_mcp_server.view_handler import ViewHandler
from greennode.vmonitor_mcp_server.widget_handler import WidgetHandler
from mcp.server.mcpserver import MCPServer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


# Prefer ~/.greennode; fall back to the legacy ~/.greenode when only it exists
CONFIG_PATH = resolve_config_dir()

SERVER_INSTRUCTIONS = """
# GreenNode vMonitor MCP Server

MCP Server for GreenNode vMonitor (observability: dashboards, metrics, alarms).

## IMPORTANT: Operating mode

By default the server runs in **read-only** mode. Use the `--allow-write` flag
to enable write operations.

## Scope

vMonitor is a global service — resources are NOT region-scoped.

## Guidance

Some features are composite — built by combining several tools/endpoints. Each
has a guide. For any multi-step flow, call `get_feature_guide feature=<name>`
FIRST and follow it — it carries the tool order, guardrails, and confirm gate.
Features: build_dashboard, query_metrics, create_metric_alarm,
monitor_infrastructure, edit_metric_unit, manage_log_projects,
manage_integrations, create_notification_channel, view_quota_usage,
create_uptime_monitor. The same guides are also MCP prompts
(`vmonitor_getting_started` for onboarding + one `vmonitor_<feature>` each). Tool
docstrings carry only the per-tool contract; the flow lives in the guide.

## Presenting results

When rendering any resource list or detail to the user (tables, bullet lists),
ALWAYS include each item's `id` and `name` as the FIRST two columns (id, name,
then everything else) — follow-up commands and confirmations need them. Never
drop the id column to save space and never truncate id values.

## Available tools

### Read-only (always available):
- list_dashboards: List vMonitor dashboards (optionally search by name; paginate with page/size)
- get_dashboard: Get a single dashboard by ID (includes widget count)
- get_dashboard_by_name: Get a single dashboard by its exact name
- list_widgets: List a dashboard's widgets AND the metric query behind each — the
  fastest way to see what a resource is being measured on, and to run those same
  queries, without enabling detailed monitoring

### Infrastructure hosts (read-only) — each host owns an auto-generated default dashboard:
- list_hosts: Agent-based hosts (servers running the Metric Agent)
- list_vserver_hosts, list_vstorage_hosts, list_vdb_hosts, list_vdb_kafka_hosts, list_vlb_hosts, list_vbackup_hosts, list_vbandwidth_hosts, list_vas_hosts: Product resources monitored as hosts
- get_host: Get one agent-based host by ID
- get_host_metrics: Get an agent-based host's current metric snapshot (status + CPU/load/memory)
- get_<type>_host_metrics (vserver/vstorage/vdb/vlb/vbackup/vbandwidth/vas): Current metric snapshot for a product host

### Metric information + catalogue (read-only):
- get_metric_names: List the metric catalogue (every metric collected) — start here to pick a metric
- list_metric_dimension_names: List every dimension key known across metrics
- list_metric_dimension_values: List the observed values of one dimension (e.g. hosts for `host`)
- get_metric_dimensions: List a metric's dimensions and their observed values (scope/group a metric)
- list_metric_units: List the units that can be assigned to a metric
- list_metric_unit_mappings: List the current metric-to-unit mappings shown in metric info panels

### Metric statistics (read-only) — the data points behind a chart:
- get_statistics: Query a metric's time-series data (filter by dimensions, group_by, window)
- get_statistics_synthetic: Query a metric's single aggregated value (number/single-stat charts)
- get_statistics_v2: Run a typed statistic query (type + data body)

### Dashboard composition (read-only):
- list_dashboard_variables / get_dashboard_variable: A dashboard's shared query variables
- list_dashboard_views / get_dashboard_view: A dashboard's saved query/filter/time-range presets
- list_widgets: A dashboard's widgets + the replayable metric query behind each
- get_widget: Get one dashboard widget (its chart config + graph specs)

### Alarms (read-only):
- list_alarms: List alarms (filter by name/severity/status/type)
- get_alarm: Get one alarm by id
- get_metric_alarm_definition / get_synthetic_alarm_definition: Upstream evaluator definition
- list_metric_alarm_histories / list_synthetic_alarm_histories: Metric alarm state-transition history
- list_log_alarm_histories / get_log_alarm_status: Log alarm history and current status
- get_change_alarm / list_change_alarm_histories: Change-detection alarm definition and history

### Integrations, API keys & certificates (read-only):
- list_integrations / get_integration: Installable metric-source apps
- list_metric_api_keys: Keys for pushing custom metrics into vMonitor
- get_project_certificate_download: Download a log project's client certificate

### Logs (read-only) — the Log API (separate host, same auth):
- list_projects / get_project / get_project_mappings: Log projects
- get_project_log_data_exists: Whether a project has ingested data
- search_logs / search_logs_default: Query a project's logs (structured body)
- get_log_export: Track a prepared export
- list_archives / get_archive, list_refills / get_refill: Archive (export) and refill (re-ingest) jobs
- list_pipelines / get_pipeline, get_processor_group, list_processor_group_libraries, list_date_formats: Log processing
- list_<vcdn|vdb|vlb|vstorage|vstorage_bucket>_log_mappings (+ vcdn types / vstorage regions): Resource→project log routing

### Synthetic / Uptime (read-only) — the uptime manager (separate host, same auth):
- list_uptimes / get_uptime: Synthetic uptime monitors (API tests) and their status
- get_uptime_config: Private-location worker install instructions
- validate_uptime: Preview a probe once without saving (read-only)
- list_locations / get_location: Probing locations (PUBLIC platform + PRIVATE self-run)

### Quota & billing (read-only) — the billing API (separate host, same auth):
- get_composite_usage / get_quota_usage / get_log_usage: How much of each quota is consumed
- get_current_quota / list_log_quotas / get_log_quota / get_quota_detail: The quota the account owns (resource id, packageId, class, current size)
- list_quota_classes / list_quota_class_packages: What can be bought — each class's `config.retentions[]` carries the packageId AND the min/max/step the order's quantity must respect
- list_packages / list_tiers (+ their *_description tools): The older flat catalogue (sms/email live only here)
- get_creation_price / get_resize_price / get_renewal_price / get_recovery_price: Price a change WITHOUT ordering. Pass `quantity` to get the v2 quota-class quote, which sends the same body the matching order tool would — always the pre-flight before a paid tool
- get_billing_settings / list_trash_quotas / get_convert_result: Payment method + allowed periods, deleted quotas, billing conversion

### Guidance (read-only, always available):
- get_feature_guide: Step-by-step flow for a composite vMonitor capability (build_dashboard, query_metrics, create_metric_alarm, monitor_infrastructure, edit_metric_unit, manage_log_projects, manage_integrations, create_notification_channel, view_quota_usage, create_uptime_monitor)

### Write (requires --allow-write):
- create_dashboard: Create a new empty dashboard
- create_dashboard_clone: Clone an existing dashboard into a new one
- update_dashboard: Update a dashboard's general settings (dark mode, refresh, time range, selected view)
- update_dashboard_name: Rename a dashboard
- update_dashboard_favorite: Mark/unmark a dashboard as favorite
- delete_dashboard: Delete a dashboard (IRREVERSIBLE)
- update_host_enabled / update_host_disabled: Resume / pause monitoring of an agent-based host
- delete_host: Remove an agent-based host from the Infrastructure list (IRREVERSIBLE)
- update_<type>_host (vserver/vstorage/vdb/vlb/vbackup/vbandwidth/vas): Enable/disable monitoring for a product host
- delete_<type>_host (same types): Remove a product host from the Infrastructure list (IRREVERSIBLE)
- create_metric_unit_mapping: Override a metric's display unit for the current user
- delete_metric_unit_mapping: Reset a metric's display unit (remove the user override)
- update_dashboard_variables: Replace a dashboard's variable list (whole-list replace)
- create_dashboard_view / update_dashboard_view: Save or edit a dashboard view preset
- delete_dashboard_view: Delete a saved dashboard view (IRREVERSIBLE)
- create_widget: Add a widget (chart) to a dashboard (graphs map)
- update_widget / update_widget_v2: Edit a widget's chart content (v1 arrays / v2 graphs map)
- update_widget_layout: Move/resize a widget and adjust its time window
- delete_widget: Delete a widget from a dashboard (IRREVERSIBLE)
- create_metric_alarm / update_metric_alarm / delete_metric_alarm: Metric alarms
- delete_metric_sub_alarm: Delete a composite metric alarm's sub-alarm (IRREVERSIBLE)
- create_log_alarm / update_log_alarm / delete_log_alarm: Log alarms
- create_change_alarm / update_change_alarm / delete_change_alarm: Change-detection alarms
- delete_change_alarm_history: Clear a change alarm's history (IRREVERSIBLE)
- update_integration_installed / update_integration_uninstalled: Install/uninstall an integration
- delete_integration: Delete an integration (IRREVERSIBLE)
- create_metric_api_key / delete_metric_api_key: Issue / revoke a metric API key
- create_project_certificate / delete_project_certificate: Issue / revoke a log project client certificate
- Log projects: update_project, update_project_mappings
- Log archives: create/update/delete_archive, validate_archive_connection
- Log refills: create_refill, create_refill_from_archive, delete_refill, validate_refill_connection
- Log pipelines: create/update/delete_pipeline
- Log processors: create/update/delete_processor(_group), update_processor_order, create_processor_group_library, validate_grok_parser
- Log exports: create_log_export
- Resource log mappings: update_<type>_log_mapping[_enabled|_disabled], update_vstorage_bucket_log_mapping
- Synthetic monitors: create_uptime, update_uptime, update_uptime_status, delete_uptime (IRREVERSIBLE)
- Synthetic locations: create_location, update_location, delete_location (IRREVERSIBLE)

### Quota orders (requires --allow-write) — THESE SPEND REAL MONEY:
- create_log_project: Buy a new log project (the log quota order IS what creates the project)
- resize_log_project: Grow a log project's quota / upgrade Basic -> Pro (IRREVERSIBLE)
- delete_log_project: Delete a log project, its quota AND its stored logs (IRREVERSIBLE)
- resize_metric_quota: Resize the account's single metric quota (IRREVERSIBLE)
- resize_sms_quota / resize_email_quota: Swap the notification quota to another package (IRREVERSIBLE)
Never call one of these before quoting it with get_creation_price / get_resize_price
(passing the same `quantity`) and showing the user the amount and the exact resource
name — then wait for their confirmation.
"""

mcp = None


class UpstreamIdentityMiddleware(BaseHTTPMiddleware):
    """Per-request upstream identity for the HTTP transport.

    Resolution order:

    1. Bearer token on the request -> every vMonitor call runs as the CALLER
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
    """Runtime-mode addendum for SERVER_INSTRUCTIONS."""
    if allow_write:
        write = (
            "- Write: ENABLED — create/update/delete tools are available. Every "
            "write still goes through the plan review + explicit user "
            "confirmation gate."
        )
    else:
        write = (
            "- Write: OFF — this session is read-only; create/update/delete tools "
            "are NOT registered. If the user asks for one, tell them to restart "
            "the server with --allow-write."
        )
    return f"\n## This session (runtime mode)\n\n{write}\n"


def create_server(auth_debug: bool = False, allow_write: bool = False) -> MCPServer:
    """Create and return a MCPServer server instance."""
    instructions = SERVER_INSTRUCTIONS + _mode_addendum(allow_write)
    server = MCPServer("vmonitor-mcp-server", instructions=instructions, version=__version__)

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
        description="GreenNode MCP Server -- manage GreenNode vMonitor via MCP"
    )
    parser.add_argument(
        "--allow-write",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable write mode (allow create, update, delete operations)",
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
        "(HTTP transport only). Never enable in production.",
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
        config = VmonitorConfig(
            client_id="",
            client_secret="",
            project_id=os.environ.get("GRN_PROJECT_ID"),
        )

    token_manager = TokenManager(config)
    client = VmonitorClient(config, token_manager)
    log_client = VmonitorLogClient(config, token_manager)
    notification_client = VmonitorNotificationClient(config, token_manager)
    billing_client = VmonitorBillingClient(config, token_manager)
    uptime_client = VmonitorUptimeClient(config, token_manager)

    mcp = create_server(auth_debug=auth_debug, allow_write=args.allow_write)
    DashboardHandler(mcp, config, client, allow_write=args.allow_write)
    InfrastructureHandler(mcp, config, client, allow_write=args.allow_write)
    MetricCatalogueHandler(mcp, config, client, allow_write=args.allow_write)
    MetricUnitHandler(mcp, config, client, allow_write=args.allow_write)
    StatisticHandler(mcp, config, client, allow_write=args.allow_write)
    VariableHandler(mcp, config, client, allow_write=args.allow_write)
    ViewHandler(mcp, config, client, allow_write=args.allow_write)
    WidgetHandler(mcp, config, client, allow_write=args.allow_write)
    AlarmHandler(mcp, config, client, allow_write=args.allow_write)
    ChangeAlarmHandler(mcp, config, client, allow_write=args.allow_write)
    IntegrationHandler(mcp, config, client, allow_write=args.allow_write)
    ApiKeyHandler(mcp, config, client, allow_write=args.allow_write)
    LogProjectHandler(mcp, config, log_client, allow_write=args.allow_write)
    LogSearchHandler(mcp, config, log_client, allow_write=args.allow_write)
    LogArchiveHandler(mcp, config, log_client, allow_write=args.allow_write)
    LogRefillHandler(mcp, config, log_client, allow_write=args.allow_write)
    LogPipelineHandler(mcp, config, log_client, allow_write=args.allow_write)
    LogProcessorHandler(mcp, config, log_client, allow_write=args.allow_write)
    LogMappingHandler(mcp, config, log_client, allow_write=args.allow_write)
    CertificateHandler(mcp, config, log_client, allow_write=args.allow_write)
    NotificationHandler(mcp, config, notification_client, allow_write=args.allow_write)
    QuotaUsageHandler(mcp, config, billing_client, allow_write=args.allow_write)
    QuotaCatalogHandler(mcp, config, billing_client, allow_write=args.allow_write)
    QuotaPriceHandler(mcp, config, billing_client, allow_write=args.allow_write)
    QuotaOrderHandler(mcp, config, billing_client, allow_write=args.allow_write)
    SyntheticUptimeHandler(mcp, config, uptime_client, allow_write=args.allow_write)
    SyntheticLocationHandler(mcp, config, uptime_client, allow_write=args.allow_write)
    PromptsHandler(mcp)

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
