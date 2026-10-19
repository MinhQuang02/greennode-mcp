# GreenNode vMonitor MCP Server

An MCP (Model Context Protocol) server that gives AI assistants (Claude, Cursor,
Gemini, etc.) tools to manage **vMonitor** — the GreenNode / VNG Cloud
observability platform (dashboards, metrics, alarms, logs, uptime).

- **213 tools** (111 read / 69 write / 33 destructive) + **11 prompts**, organized
  by feature area — one handler per area
- Covers **five vMonitor APIs**, each on its own host but sharing one IAM auth:
  the metric/dashboard API, the Log API, the notification gateway, the
  billing / quota-usage API, and the synthetic / uptime manager — routed
  automatically
- Fully **async** (httpx) on the **MCPServer** framework
- **Read-only by default** (110 tools); create/update/delete are opt-in via
  `--allow-write` (all 213), and the server instructions tell the agent which
  mode **this** session runs in
- Every tool declares MCP **ToolAnnotations** (`readOnlyHint` / `destructiveHint`),
  so clients can auto-approve reads and warn before destructive calls
- **Structured (JSON) output** for data tools; MCPServer emits `outputSchema` +
  `structuredContent`
- Import package: `greennode.vmonitor_mcp_server` — CLI entry point
  `vmonitor-mcp-server`
- vMonitor is a **global service** — its resources are **not region-scoped**
  (`GRN_DEFAULT_REGION` is ignored)

## Installation

Requires Python ≥ 3.11. From the repository root (uv workspace):

```bash
uv sync
```

Or from this project directory:

```bash
cd src/vmonitor-mcp-server
uv sync
```

## Configuration

Credentials are read from `~/.greennode/credentials` and `~/.greennode/config`
(INI format, shared with greennode-cli). A service account's `client_id` /
`client_secret` from the GreenNode IAM Portal is all that is required:

```ini
# ~/.greennode/credentials
[default]
client_id = <your-client-id>
client_secret = <your-client-secret>
```

Environment variables override the config files (highest priority):

| Variable | Purpose |
|----------|---------|
| `GRN_CLIENT_ID` | Override client_id |
| `GRN_CLIENT_SECRET` | Override client_secret |
| `GRN_PROFILE` | Select profile (default: `default`) |
| `GRN_PROJECT_ID` | Override project_id |

There is no region setting — vMonitor is global (a single base host).

## Running

```bash
# Read-only mode (default) — 110 tools
uv run vmonitor-mcp-server

# Enable create/update/delete operations — all 213 tools
uv run vmonitor-mcp-server --allow-write
```

The server speaks MCP over **stdio** by default. Example Claude Desktop /
Cursor entry:

```json
{
  "mcpServers": {
    "vmonitor": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/greennode-mcp", "vmonitor-mcp-server", "--allow-write"]
    }
  }
}
```

### HTTP transport

```bash
# default bind is 127.0.0.1:8000
uv run vmonitor-mcp-server --transport streamable-http --host 0.0.0.0 --port 8080
```

`GET /health` is always unauthenticated (liveness/readiness). The Docker image
serves streamable-http on port 8080.

### Authentication (HTTP transport)

The upstream identity is resolved per request:

1. The request carries an IAM bearer token in `Authorization` (the AgentBase
   Gateway forwards the caller's token) → **every vMonitor call runs as that
   caller**. A rejected user token is surfaced as an error, never silently
   retried as the service account.
2. No token → **401** + `WWW-Authenticate: Bearer`, even when service-account
   credentials are configured. Serving such requests as the shared service
   account is opt-in: `--allow-service-account-fallback` (env
   `GRN_MCP_ALLOW_SERVICE_ACCOUNT_FALLBACK=1`) — only for a port reachable
   solely through a trusted authenticating proxy.

The HTTP transport can boot with **no credentials at all**
(passthrough-only deployments behind the Gateway) — every request then requires
a token. The **stdio** transport always requires service-account credentials.
`GET /health` is always open. The server does not verify tokens itself; the
vMonitor APIs are the verifier (a stale token surfaces as `500
IAM_VALIDATION_ERROR`, which the shared HTTP client treats as an auth failure
and refreshes once).

`--auth-debug` (env `GRN_MCP_AUTH_DEBUG=1`) is an opt-in, redacted, HTTP-only
diagnostic: it logs a summary of inbound request auth and exposes `GET /whoami`.
It never verifies signatures and never logs the full token. Not for production.

### Docker

```bash
# Build (from the repo root)
docker build -f src/vmonitor-mcp-server/Dockerfile -t vmonitor-mcp-server .

# Run (streamable-http on :8080); pass credentials via env or a mounted ~/.greennode
docker run --rm -p 8080:8080 \
  -e GRN_CLIENT_ID=<id> -e GRN_CLIENT_SECRET=<secret> \
  vmonitor-mcp-server
```

## Tools

| Tool | Access | Description |
|------|--------|-------------|
| `list_dashboards` | read | List vMonitor dashboards; optional `searching_text`/`searching_field` filter and 1-based `page`/`size` paging (omit `page` to return all) |
| `get_dashboard` | read | Get a single dashboard by ID (includes widget count) |
| `get_dashboard_by_name` | read | Get a single dashboard by its exact name |
| `create_dashboard` | write | Create a new empty dashboard (only `name` is required) |
| `create_dashboard_clone` | write | Clone an existing dashboard into a new one |
| `update_dashboard` | write | Update a dashboard's general settings (dark mode, refresh, time range, selected view) |
| `update_dashboard_name` | write | Rename a dashboard |
| `update_dashboard_favorite` | write | Mark/unmark a dashboard as favorite |
| `delete_dashboard` | write (destructive) | Delete a dashboard (irreversible) |
| `list_hosts` | read | List agent-based infrastructure hosts (Metric Agent servers) |
| `list_vserver_hosts` | read | List vServer instances monitored as hosts |
| `list_vstorage_hosts` | read | List vStorage resources monitored as hosts |
| `list_vdb_hosts` | read | List vDB instances monitored as hosts |
| `list_vdb_kafka_hosts` | read | List vDB Kafka brokers monitored as hosts |
| `list_vlb_hosts` | read | List vLB (load balancer) resources monitored as hosts |
| `list_vbackup_hosts` | read | List vBackup resources monitored as hosts |
| `list_vbandwidth_hosts` | read | List vBandwidth resources monitored as hosts |
| `list_vas_hosts` | read | List VAS resources monitored as hosts |
| `get_host` | read | Get one agent-based host by ID |
| `get_host_metrics` | read | Get an agent-based host's current metric snapshot (status + CPU/load/memory) |
| `update_host_enabled` | write | Resume monitoring of an agent-based host |
| `update_host_disabled` | write | Pause monitoring of an agent-based host (agent stays installed) |
| `delete_host` | write (destructive) | Remove an agent-based host from the Infrastructure list (irreversible) |
| `get_<type>_host_metrics` | read | Current metric snapshot for a product host (`<type>` = vserver/vstorage/vdb/vlb/vbackup/vbandwidth/vas) |
| `update_<type>_host` | write | Enable/disable monitoring for a product host (body `{enabled}`) |
| `delete_<type>_host` | write (destructive) | Remove a product host from the Infrastructure list (irreversible) |
| `get_metric_names` | read | List the metric catalogue (every metric collected) — start here to pick a metric |
| `list_metric_dimension_names` | read | List every dimension key known across metrics |
| `list_metric_dimension_values` | read | List the observed values of one dimension (e.g. hosts for `host`) |
| `get_metric_dimensions` | read | List a metric's dimensions and each dimension's observed values (scope/group a metric) |
| `list_metric_units` | read | List the units that can be assigned to a metric (for overriding a metric's display unit) |
| `list_metric_unit_mappings` | read | List the metric-to-unit mappings shown in metric information panels |
| `create_metric_unit_mapping` | write | Override a metric's display unit for the current user |
| `delete_metric_unit_mapping` | write (destructive) | Reset a metric's display unit (remove the user override) |
| `get_statistics` | read | Query a metric's time-series data (filter by dimensions, group_by, window) — the data behind a chart |
| `get_statistics_synthetic` | read | Query a metric's single aggregated value (number/single-stat charts) |
| `get_statistics_v2` | read | Run a typed statistic query (`type` = `SIMPLE`/`CUSTOM` + `data` body) |
| `list_dashboard_variables` | read | List a dashboard's shared query variables |
| `get_dashboard_variable` | read | Get one dashboard variable by ID |
| `update_dashboard_variables` | write | Replace a dashboard's variable list (whole-list replace) |
| `list_dashboard_views` | read | List a dashboard's saved query/filter/time-range presets |
| `get_dashboard_view` | read | Get one saved dashboard view by ID |
| `create_dashboard_view` | write | Save the current dashboard state as a named view |
| `update_dashboard_view` | write | Update a saved view's stored state |
| `delete_dashboard_view` | write (destructive) | Delete a saved dashboard view (irreversible) |
| `list_widgets` | read | List a dashboard's widgets **and the metric query behind each** — replay them via `get_statistics_v2` with no dimension discovery and no detailed monitoring |
| `get_widget` | read | Get one dashboard widget (its chart config + graph specs) |
| `create_widget` | write | Add a widget (chart) to a dashboard (v2 `graphs` map; omit `layout` to auto-place it cleanly on the grid) |
| `update_widget` | write | Edit a widget's content (v1 metricGraphs/logGraphs arrays) |
| `update_widget_v2` | write | Edit a widget's content (v2 `graphs` map) |
| `update_widget_layout` | write | Move/resize a widget and adjust its time window |
| `delete_widget` | write (destructive) | Delete a widget from a dashboard (irreversible) |
| `list_alarms` | read | List alarms (filter by name/severity/status/type) |
| `get_alarm` | read | Get one alarm by ID (with its type-specific config) |
| `get_metric_alarm_definition` | read | Get a metric alarm's upstream evaluator definition |
| `list_metric_alarm_histories` | read | List a metric alarm's history (id = an evaluator sub-alarm `alarms[].id` from `get_metric_alarm_definition`; window auto-defaults) |
| `get_synthetic_alarm_definition` | read | Get a synthetic metric alarm's definition |
| `list_synthetic_alarm_histories` | read | List a synthetic metric alarm's history (id = a sub-alarm `alarms[].id` from `get_synthetic_alarm_definition`; window auto-defaults) |
| `list_log_alarm_histories` | read | List a log alarm's history |
| `get_log_alarm_status` | read | Get a log alarm's current status |
| `create_metric_alarm` / `update_metric_alarm` | write | Create / edit a metric alarm (severity/condition accepted case-insensitively) |
| `delete_metric_alarm` | write (destructive) | Delete a metric alarm (irreversible) |
| `delete_metric_sub_alarm` | write (destructive) | Delete a composite metric alarm's sub-alarm (irreversible) |
| `create_log_alarm` / `update_log_alarm` | write | Create / edit a log alarm |
| `delete_log_alarm` | write (destructive) | Delete a log alarm (irreversible) |
| `get_change_alarm` | read | Get a change-detection alarm's definition (requires a window) |
| `list_change_alarm_histories` | read | List a change alarm's history (requires a window) |
| `create_change_alarm` / `update_change_alarm` | write | Create / edit a change-detection alarm |
| `delete_change_alarm` | write (destructive) | Delete a change alarm (irreversible) |
| `delete_change_alarm_history` | write (destructive) | Clear a change alarm's history (irreversible) |
| `list_integrations` / `get_integration` | read | List / get installable metric-source apps |
| `update_integration_installed` / `update_integration_uninstalled` | write | Install / uninstall an integration |
| `delete_integration` | write (destructive) | Delete an integration (irreversible) |
| `list_metric_api_keys` | read | List metric API keys |
| `create_metric_api_key` | write | Issue a new metric API key |
| `delete_metric_api_key` | write (destructive) | Revoke a metric API key (irreversible) |
| `list_projects` / `get_project` / `get_project_mappings` | read | Log projects (Log API) |
| `get_project_log_data_exists` | read | Whether a log project has ingested data |
| `search_logs` / `search_logs_default` | read | Query a log project's data (structured `{type,value}` DSL — match/range/exists/bool; ES shorthands translated) |
| `get_log_export` / `get_project_certificate_download` | read | Track an export / download a project cert (base64 ZIP; `cert_id` comes from `list_projects` → `certInfos[]`) |
| `update_project` / `update_project_mappings` | write | Edit a log project's settings / field mappings |
| `create_project_certificate` | write | Issue a project client certificate |
| `delete_project_certificate` | write (destructive) | Revoke a project client certificate |
| `create_log_export` | write | Prepare an asynchronous log export |
| `list_archives` / `get_archive` | read | Log archives (export destinations) |
| `validate_archive_connection` / `validate_refill_connection` | read | Test storage connectivity |
| `create_archive` / `update_archive` | write | Create / edit a log archive |
| `delete_archive` | write (destructive) | Delete a log archive |
| `list_refills` / `get_refill` | read | Log refill (re-ingest) jobs |
| `create_refill` / `create_refill_from_archive` | write | Create a refill job |
| `delete_refill` | write (destructive) | Delete a refill job |
| `list_pipelines` / `get_pipeline` | read | Log processing pipelines |
| `create_pipeline` / `update_pipeline` | write | Create / edit a pipeline |
| `delete_pipeline` | write (destructive) | Delete a pipeline |
| `get_processor_group` / `list_processor_group_libraries` / `list_date_formats` / `validate_grok_parser` | read | Processor group / library / helpers |
| `create_processor_group` / `update_processor_group` / `update_processor_order` / `create_processor_group_library` | write | Manage processor groups |
| `delete_processor_group` | write (destructive) | Delete a processor group |
| `create_processor` / `update_processor` | write | Manage processors |
| `delete_processor` | write (destructive) | Delete a processor |
| `list_<vcdn\|vdb\|vlb\|vstorage\|vstorage_bucket>_log_mappings` | read | Resource→project log mappings |
| `list_vcdn_log_mapping_types` / `list_vstorage_log_mapping_regions` | read | Mapping type / region lookups |
| `update_<vcdn\|vdb\|vlb\|vstorage>_log_mapping[_enabled\|_disabled]` | write | Enable / disable / edit a resource log mapping |
| `update_vstorage_bucket_log_mapping` | write | Set a vStorage bucket's log mapping |
| `list_notification_types` / `list_notifications` / `get_notification_otp_info` | read | Notification channel types / channels / pending OTP info |
| `create_notification_otp` / `validate_notification_otp` | write | Send an OTP to a channel address / validate it |
| `create_notification` / `update_notification` | write | Create / edit an (OTP-verified) notification channel |
| `delete_notification` | write (destructive) | Delete a notification channel (irreversible) |
| `get_quota_usage` / `get_log_usage` / `get_composite_usage` | read | Quota usage per category / per log project / combined |
| `get_current_quota` / `list_log_quotas` / `get_log_quota` / `get_quota_detail` | read | Current active quota and its detail |
| `get_billing_settings` / `list_trash_quotas` / `get_convert_result` | read | Billing settings / trashed quotas / conversion result |
| `list_tiers` / `get_tier` / `get_tier_description` | read | Quota tier catalog (metric/synthetic/log) |
| `list_packages` / `get_package` / `get_package_detail` / `get_package_description[_detail]` | read | Purchasable package catalog |
| `list_quota_classes` / `list_quota_class_packages` | read | v2 quota classes and their packages |
| `get_creation_price` / `get_resize_price` / `get_recovery_price` / `get_renewal_price` | read | Price quotes (compute-only; no order placed). Pass `quantity` for the v2 quota-class quote, which sends the same body the matching order tool would |
| `create_log_project` | write | Buy a new log project — the log quota order is what creates the project (**spends money**) |
| `resize_log_project` | write (destructive) | Grow a log project's quota / upgrade Basic → Pro (**spends money**, irreversible) |
| `delete_log_project` | write (destructive) | Delete a log project, its quota and its stored logs (irreversible) |
| `resize_metric_quota` | write (destructive) | Resize the account's single metric quota (**spends money**, irreversible) |
| `resize_sms_quota` / `resize_email_quota` | write (destructive) | Swap the SMS / email notification quota to another package (**spends money**, irreversible) |
| `list_uptimes` / `get_uptime` / `get_uptime_config` / `validate_uptime` | read | Synthetic uptime monitors + probe preview |
| `create_uptime` / `update_uptime` / `update_uptime_status` | write | Create / edit / toggle an uptime monitor |
| `delete_uptime` | write (destructive) | Delete an uptime monitor (irreversible) |
| `list_locations` / `get_location` | read | Synthetic probing locations |
| `create_location` / `update_location` | write | Create / edit a private probing location |
| `delete_location` | write (destructive) | Delete a probing location (irreversible) |
| `get_feature_guide` | read | Step-by-step guide for a composite feature: `build_dashboard`, `query_metrics`, `create_metric_alarm`, `monitor_infrastructure`, `edit_metric_unit`, `manage_log_projects`, `manage_integrations`, `create_notification_channel`, `view_quota_usage`, `create_uptime_monitor` |

Write tools are only registered when the server runs with `--allow-write`.
The `<type>` families expand to one tool per product type (vserver, vstorage,
vdb, vlb, vbackup, vbandwidth, vas). vDB Kafka has only a list tool (no by-id
endpoints upstream). Every infrastructure host owns an auto-generated,
read-only default dashboard; the host-listing tools always send `page`/`size`
(the API returns 500 without both) and accept an optional `name` filter.

Metric catalogue (`get_metric_names` → dimensions) plus `get_statistics*` are the
read chain for exploring and plotting a metric. Variables, views and widgets are
dashboard-scoped composition tools. `create_widget` uses a typed widget shell but
accepts the polymorphic `graphs` chart-builder payload as passthrough — build a
widget's `graphs` map from the metric/log queries to plot; omit `layout` and the
widget is auto-placed on the dashboard's 10-column grid without overlapping.

Alarms come in three families on one surface: **metric** (with a **synthetic**
variant), **log** and **change-detection** (`change-method`). Alarm
create/update bodies are flat, fully-typed DTOs (`name` is the only strictly
required field); `severity` and `condition` are accepted case-insensitively.
Integrations are installable metric-source apps; metric API keys authenticate
external clients pushing custom metrics.

The **Log API** tools (log projects, search/export, archives, refills, pipelines,
processors, and resource log mappings) target a **separate host** with the same
IAM auth; the server routes them through a dedicated log client automatically.
Log list endpoints use a `content`-based paging envelope and detail endpoints
return a generic resource (id/name/status + raw `data`).

**Notification** tools manage the delivery channels alarms fire to (Email, SMS,
Slack, Webhook, Telegram, Teams); creating a channel is OTP-verified
(`create_notification_otp` → `validate_notification_otp` → `create_notification`).
**Quota & usage** tools (billing API) split in two. Reading — usages, current
quota, the tier/package catalog, quota classes and price quotes — never costs
anything. The **quota-order** tools (`create_log_project`, `resize_log_project`,
`delete_log_project`, `resize_metric_quota`, `resize_sms_quota`,
`resize_email_quota`) place real, irreversible orders and are registered only
with `--allow-write`; renew and recover-from-trash stay quote-only. Every order
has a zero-cost pre-flight: call `get_creation_price` / `get_resize_price` with
the same `quantity` and they hit the v2 pricing endpoint with the very body the
order would send.

**Synthetic** tools (uptime monitors + probing locations) target the uptime
manager host and cover the Synthetic / API-test feature; `validate_uptime`
previews a probe before you save it.

Handlers are **organized by feature area** (Dashboard overview / detail, Query,
Metric information, Alarm, Infra, Log, Integration, Notification, Quota & usage,
Synthetic), one handler per area. Together these cover five vMonitor APIs — the
metric/dashboard API, the Log API, the notification gateway, the billing /
quota-usage API, and the synthetic / uptime manager — each on its own host but
sharing one IAM auth.

### Reading a resource's metrics off its default dashboard

Every GreenNode resource owns an auto-generated **system dashboard**, and each of
its widgets stores the exact query the console plots — metric name, statistic,
grouping and the full `dimensions` string (which already carries the
`resource_id`). `list_widgets` surfaces that, so answering "how is this server
doing?" needs neither a walk through the metric catalogue nor detailed
monitoring:

```
list_dashboards searching_text="<resource name>"   →  dashboard id
list_widgets    dashboard_id=<id>                  →  metric_queries per widget
get_statistics_v2 body={"type":"SIMPLE","data":{"graph":{
    "name":       <metric_name>,
    "statistics": <statistic>,
    "dimensions": <dimensions>,     # use as-is
    "group_by":   <group_by>,
    "offset":0,"limit":"","rollup":"","rate":0},
  "start_time":<epoch_ms>,"end_time":<epoch_ms>,
  "period":<the widget's period>,"alarm":false}}
```

Reusing the widget's `period` makes the numbers match the chart on the web.
Widgets with `log_graph_count > 0` plot log data — query those with `search_logs`.

### Buying or resizing a quota (money-spending, `--allow-write`)

`packageId` is never something to guess: it lives on a quota class's retention
entries, together with the bounds the order's `quantity` has to respect. The
same call quotes the price and validates the payload, so quote before ordering.

```
list_quota_classes category=log        →  class → config.retentions[]:
                                          { amount: 7, minSize: 20, maxSize: 5000,
                                            step: 10, packageId: "<pkg>" }
quantity = <GB per day> * <retention amount>          # log: GB-days
get_creation_price category=log package_id=<pkg> quantity=<n>     # zero cost
create_log_project body={"projectName":"my-logs","packageId":"<pkg>",
                         "quantity":<n>}
```

Leave `redirectUrl` alone. The quote endpoints accept `""`, but an order
allow-lists it upstream (`must not be null` when absent, `redirect URL is
invalid` when blank, `redirect URL is incorrect` when well-formed but unlisted),
so each order tool fills in the vMonitor console's own quota page for its
category — `https://vmonitor.console.vngcloud.vn/quota-usages/{log,metric,notification}`.

Resizing follows the same shape with `get_resize_price` → `resize_log_project`
(or `resize_metric_quota`, where `quantity` is a host count and the retention
entries carry `minResource`/`maxResource`/`step` instead). SMS and email
packages are fixed bundles: pick one from `list_packages category=sms|email` and
call `resize_sms_quota` / `resize_email_quota` — no `quantity`.

An order responds with `order_id`, `amount` and `payment_url`. A non-empty
`payment_url` means the order is **pending**: the quota only changes once the
user opens that link and pays. Passing `pay: true` charges the account directly
instead.

## Prompts

Eleven portable prompts (Vietnamese) work in any MCP client and are always
available (no `--allow-write` needed). Each composite feature has one guide,
served **two ways from a single source**: an MCP **prompt** `vmonitor_<feature>`
(user-loaded) and the **`get_feature_guide`** tool `feature=<name>`
(agent-called on its own).

| Prompt | Purpose |
|--------|---------|
| `vmonitor_getting_started` | Onboarding: concepts, auth setup, the no-region model, tool routing, and every feature + its guide |
| `vmonitor_build_dashboard` | Dashboard + variables + views + widgets (incl. the widget `graphs`/auto-layout shape) |
| `vmonitor_query_metrics` | Query metrics/plot data and search/export logs (incl. the log search DSL) |
| `vmonitor_create_metric_alarm` | Create an alarm (metric / log / change-method): source → threshold → notification → confirm gate |
| `vmonitor_monitor_infrastructure` | Explore infrastructure hosts and their metrics |
| `vmonitor_edit_metric_unit` | Override a metric's display unit |
| `vmonitor_manage_log_projects` | Manage log projects, mappings and certificates |
| `vmonitor_manage_integrations` | Install / uninstall metric-source integrations |
| `vmonitor_create_notification_channel` | OTP-verified notification-channel creation |
| `vmonitor_view_quota_usage` | Read quota usage and prices, then buy / resize / delete a quota |
| `vmonitor_create_uptime_monitor` | Create a synthetic uptime monitor + probing location |

## Development

### Unit tests

```bash
cd src/vmonitor-mcp-server
uv run pytest tests/ -v

# One handler / one test
uv run pytest tests/test_alarms.py -v
uv run pytest tests/test_widgets.py -v -k "layout"

# Lint + format (what CI runs)
uv run ruff check . && uv run ruff format --check .
```

Tests use `respx` for async HTTP mocking and `pytest-asyncio` — no real API
calls, no credentials needed. See the repo-root `CLAUDE.md` for conventions on
adding new tools, and this package's `CLAUDE.md` for the vMonitor API quirks.

### Manual testing with MCP Inspector (stdio)

Interactive UI to browse tools/prompts, inspect input/output schemas, and call
tools against the **real** vMonitor API (credentials read from `~/.greennode/`,
same as the CLI):

```bash
cd src/vmonitor-mcp-server

# Read-only (110 tools)
npx @modelcontextprotocol/inspector uv run vmonitor-mcp-server

# All 213 tools (write enabled)
npx @modelcontextprotocol/inspector uv run vmonitor-mcp-server --allow-write
```

In the UI: Transport Type = `STDIO` → **Connect** → **Tools** → **List Tools**
→ pick a tool → fill parameters → **Run**. A good first call is `list_dashboards`
(confirms authentication works).

Notes:

- Inspector v0.14+ requires a **proxy session token**. Open the URL the terminal
  prints (`http://localhost:6274/?MCP_PROXY_AUTH_TOKEN=...`) instead of a bare
  `localhost:6274`, or disable it for local dev:
  `DANGEROUSLY_OMIT_AUTH=true npx @modelcontextprotocol/inspector ...`
- `uv run mcp dev` does **not** work here: the MCPServer instance is built by
  `create_server()` inside `main()`, so there is no module-level `mcp` object.
  Use the Inspector command above instead.
- To test another account without touching `~/.greennode/`, prefix env overrides:
  `GRN_PROFILE=staging npx ...`

### Live checks in `scripts/`

Both need credentials in `~/.greennode` and are read-only — they never create,
modify or delete anything:

```bash
uv run python scripts/smoke_test.py   # drives the read-only tools over real MCP
./scripts/auth-debug-local.sh         # exercises the --auth-debug diagnostic
```

`smoke_test.py` starts a local server **without** `--allow-write` (so no write
tool is even registered), lists globally, then drills into the first dashboard,
host, metric, alarm and log project it finds. Its dashboard stage walks the
`list_widgets` → `get_statistics_v2` chain end to end.

### Scripted smoke test over stdio (no UI)

The server speaks JSON-RPC on stdin/stdout, so a pipe is enough to smoke-test it
(useful in CI or a quick sanity check):

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | uv run vmonitor-mcp-server 2>/dev/null
```

Replace the last message with a `tools/call` to invoke a tool, e.g.
`{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_dashboards","arguments":{}}}`.
For multi-step flows, use the Python MCP client (`mcp.client.stdio`) instead of a
pipe — the process exits when stdin closes.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
