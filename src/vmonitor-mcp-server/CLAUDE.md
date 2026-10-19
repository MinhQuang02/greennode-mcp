# CLAUDE.md — vMonitor MCP Server

Product-specific guidance for `src/vmonitor-mcp-server`. Monorepo-wide
conventions (tool naming, DTOs, TDD, branch/release flow) live in the
**repo-root CLAUDE.md** — read that first.

## Product overview

MCP server for GreenNode vMonitor (observability: dashboards, metrics, alarms).

- **213 tools** (111 read / 69 write / 33 destructive) across handlers
  **organized by feature area** + a guidance handler, plus **11 MCP prompts**
  (110 read tools in read-only mode, all 213 with `--allow-write`). One handler
  per feature area. **Five vMonitor APIs are covered**, each on its own host but
  sharing one IAM auth: the metric/dashboard API, the Log API, the notification
  gateway, the billing / quota-usage API, and the synthetic / uptime manager.

### Feature-area taxonomy (handler file → feature)

| Feature area | Handler file(s) |
|---|---|
| Dashboard — Overview | `dashboard_handler.py` |
| Dashboard — Detail | `variable_handler.py`, `view_handler.py`, `widget_handler.py` |
| Query (metric & log) | `metric_catalogue_handler.py`, `statistic_handler.py`, `log_search_handler.py` |
| Metric information | `metric_unit_handler.py` |
| Alarm | `alarm_handler.py`, `change_alarm_handler.py` |
| Infra | `infrastructure_handler.py`, `log_mapping_handler.py` |
| Log | `log_project_handler.py`, `log_archive_handler.py`, `log_refill_handler.py`, `log_pipeline_handler.py`, `log_processor_handler.py` |
| Integration | `integration_handler.py`, `apikey_handler.py`, `certificate_handler.py` |
| Notification | `notification_handler.py` |
| Quota & usage | `quota_usage_handler.py`, `quota_catalog_handler.py`, `quota_price_handler.py`, `quota_order_handler.py` |
| Synthetic | `synthetic_uptime_handler.py`, `synthetic_location_handler.py` |

`get_statistics_synthetic` (single-stat statistics) and the synthetic *alarm*
reads stay in their families (Query / Alarm) — "synthetic" there is the
single-stat sense, not uptime monitoring. Tool **names are stable** — one handler
per resource area, one guide per composite feature. The full per-tool inventory
(name → access → description) lives in `README.md`; the handler → purpose map is
in **Key files** below.

## Guidance placement policy

Four layers, each with ONE job — do not let content drift between them:

| Layer | Carries | Never carries |
|---|---|---|
| Docstring / param description | The tool CONTRACT: semantics, ranges, formats, hard API constraints, cross-tool id mapping | Feature choreography, rendering rules |
| `get_feature_guide` / prompts | Choreography: tool order, ask-the-user steps, guardrails, confirm gates | — |
| `SERVER_INSTRUCTIONS` | Session-wide principles (no-region model, id-first rendering, write confirm gate) | Per-tool detail |
| Error messages | The next step to fix THIS failure | — |

Multi-step composite features get a guide in `prompts_handler.py`, NOT extra
docstring prose. There is now **one guide per composite feature** (build a
dashboard, query metrics/logs,
create an alarm, monitor infra, edit a metric unit, manage log projects, manage
integrations, create a notification channel, view quota/usage, create an uptime
monitor) — add new ones by appending a `_<name>_guidance()` function, a
`_FEATURE_GUIDES` entry, the `Feature` `Literal` value, and a `vmonitor_<name>`
prompt method (kept uniform so `get_feature_guide` and the prompt stay one
source). Example flow — metric-info edit-unit: list mapping → pick metric →
optional dimensions → pick unit from `list_metric_units` → confirm → create;
reset via `delete_metric_unit_mapping` on the row's `metricUnitMappingUserId`.
Guardrail: editing a metric unit requires **active metric quota/billing** (the
API rejects otherwise with "Please buy metric quota before edit metric unit").
- **Host by-id naming**: API `enable`/`disable` map to `update_host_enabled` /
  `update_host_disabled`; typed `updateXHost` (a `{enabled}` toggle) →
  `update_<type>_host`; `getMoreInfoHostById` → `get_host_metrics` /
  `get_<type>_host_metrics` (no `enable`/`disable`/`clone` verb in `ALLOWED_VERBS`).
- **vDB Kafka** has only the list tool — no by-id (get/update/delete/metric)
  endpoints exist upstream for it.
- **Infrastructure hosts vs dashboards**: every monitored resource is a *host*
  that auto-creates a read-only default dashboard named after it; user-created
  or cloned dashboards are the editable ones. The infra tools list those hosts
  per product type (from a host id you can reach its default dashboard). That
  default dashboard is also the **shortcut to a resource's metrics**: its widgets
  already store the metric name + `dimensions` of every chart, so
  `list_dashboards` → `list_widgets` → `get_statistics_v2` answers "how is this
  resource doing" with no catalogue walk and **without detailed monitoring**.
- **Tool names are constrained to the approved verb set** (`test_conventions.py`
  `ALLOWED_VERBS`): the API's `clone`/`rename`/`favorite` operations map to
  `create_dashboard_clone` / `update_dashboard_name` / `update_dashboard_favorite`.
- **Structured output** — data tools return Pydantic models; MCPServer emits
  `outputSchema` + `structuredContent`.
- **SERVER_INSTRUCTIONS are mode-aware**: `create_server()` appends a runtime
  addendum (write on/off).

## vMonitor API quirks

- **No regions** — vMonitor is a global service. `VmonitorConfig.get_base_url`
  ignores the region argument `BaseClient` passes through; there is a single
  base URL `https://vmonitorapis.vngcloud.vn/vmonitor-api`.
- **Auth is standard GreenNode IAM** — client-credentials token in
  `Authorization: Bearer`, handled by `mcp_core.auth.TokenManager`. The API
  path prefix is `/api/v1/...`.
- **Stale token surfaces as HTTP 500, not 401**: the vMonitor gateway (both the
  metric and log hosts) answers an invalid/expired bearer token with
  **`500 {"code":"IAM_VALIDATION_ERROR"}`**, unlike most GreenNode APIs which
  return 401. `mcp_core.http.BaseClient` treats that body code (at any 4xx/5xx)
  as an auth failure → refresh the token once and retry, exactly like a 401
  (without this a drifted token is retried as a generic 5xx and then fails).
  `IAM_PERMISSION_DENIED` (403) is a real authorization denial and is **not**
  refreshed. `TokenManager.get_token` is single-flighted (one lock) so a burst of
  concurrent calls triggers a single refresh, not a storm.
- **Pagination is 1-based**: `page=1` is the first page; `page=0` returns 400.
- **Omitting `page` returns the full result set** in one response (the envelope
  then reports `page=0`, `pageSize=0`, `totalItem=N`, `lstData` = all items).
  Sending `page`+`size` paginates.
- **List envelope** is `Paging_<Dto>_`: `{ lstData: [...], page, pageSize,
  totalItem, totalPage }`.
- **Dashboards** (`GET /api/v1/dashboards`) accept `searching-field` +
  `searching-text` (e.g. field `name`) to filter; the `filter` query param is
  left unexposed (its accepted values are unconfirmed).
- **Dashboard write responses have two envelope shapes**: create / rename
  (`PUT .../rename`) / favorite (`PUT .../favorite`) return the `DashboardDto`
  directly; clone (`POST .../clone`) wraps it in `{ data: {...} }`.
  `models._unwrap` normalises both, so `DashboardDetail.from_api` handles any.
- **`create_dashboard` needs only `name`**; `period` / `startTime` / `endTime` /
  `extra` are optional time-range config (a new dashboard defaults to
  `timeRangeType=DEFAULT`, no widgets). `delete` answers 200 with an empty body
  (`BaseClient` returns `None`).
- **Three overlapping dashboard writes**: `update_dashboard` is the general
  `PUT /dashboards` settings editor (id + darkMode/favorite/refresh*/timeRange*/
  viewSelectedId); `update_dashboard_name` (`PUT .../rename`) and
  `update_dashboard_favorite` (`PUT .../favorite`) are the dedicated single-field
  actions. All three are exposed because all three endpoints exist.
- **The generic PUTs are full-object replaces, not partial patches** (verified
  live). `update_dashboard` rejects a thin body with `Missing field favorite` /
  `Missing field time range type` — resend the full settings (favorite +
  timeRange + timeRangeType + id at minimum). `update_widget_v2` rejects a thin
  body with `Missing field widget type id` — resend name + typeChart + `type`
  (the string `"Metric"`) + the graphs map; the widget's `type` reads back as an
  object `{id,name}` but is **written as the plain string `"Metric"`** (the id is
  resolved server-side). `update_widget` (v1, metricGraphs arrays) is legacy and
  can 500 on some accounts; prefer `update_widget_v2`. The single-field `rename` /
  `favorite` writes are true partials and always work.
- **Dashboard views are widget- and state-dependent**: `create_dashboard_view`
  500s on a name-only body AND on a dashboard with **no widgets** — add a widget
  first and send the state fields (query/timeRange/filters/variables).
  `update_dashboard_view` is a full-state replace (variables list + filters +
  query + timeRange together; a partial body 500s). `get_dashboard_view` (single
  view GET) can return **403 IAM_PERMISSION_DENIED** depending on account
  permissions — use `list_dashboard_views` instead.
- **Widget `name` must be 5-255 chars** (shorter names 400 with
  `The length of field widget name must be 5 to 255`).
- **`get_dashboard_by_name` lags for freshly-created dashboards**: the name index
  is eventually-consistent, so a just-created dashboard is `Not found` by name for
  several seconds even though `get_dashboard` by id works immediately. Look up new
  dashboards by id; by-name is reliable only for established dashboards.
- **By-name path endpoint**: `get_dashboard_by_name` (`GET .../name/{name}`)
  URL-encodes the name via `_name_segment` (rejects `/`/`\` for traversal safety —
  `validate_id` is too strict for names with spaces).
- **Infrastructure host lists 500 without paging**: `GET /api/v1/infrastructure/.../hosts`
  returns HTTP 500 unless BOTH `page` and `size` are present (either alone still
  500s). `InfrastructureHandler` always sends both; the `name` filter is optional.
- **Infra list filter param differs**: the base `/infrastructure/hosts` filters
  by `searching_text`; every typed `/infrastructure/<type>/hosts` filters by
  `name`. The tools expose one uniform `name` parameter and map it to the right
  query key per endpoint.
- **Infra responses are uniform** (`lstData/page/pageSize/totalItem/totalPage`)
  across every host type, including vbandwidth — trust this live shape.
  Host items are **snake_case** (`created_at`, `monitor_enabled`, `user_id`) and
  carry a `<product>_id` / `<product>_name` pair (server_id, load_balancer_id,
  database_id, vstorage_id, ...) — `HostSummary` normalises these to
  `resource_id` / `name` generically.
- **`/infrastructure/vdb-kafka/hosts`** is live and returns the same envelope; it
  lists Kafka brokers provisioned via vDB.
- **Typed host update is a `{enabled: bool}` toggle** (`PUT /infrastructure/<type>/hosts/{id}`)
  returning the `<Type>HostResponse` (same shape as the list item → reuse
  `HostSummary`). Delete has no body/response (confirmation string synthesised).
- **Per-product metric shape is undocumented and differs per type** (vServerCPUUsage,
  vLBActiveConnection, backupQuotaUsed, projectQuotaTotal, ...) with a common
  `status`; each value is `{name,value,createdAt}` or `null`. `HostMetricSnapshot`
  keeps them generically in a `metrics` map so new/renamed metrics never break
  parsing (base agent host keeps the fixed-field `HostMetricInfo`).
- **Metric information** (`MetricHandler`) is distinct from *host* metrics: it
  describes the metric *catalogue*, not a host's current values.
  - `get_metric_dimensions` (`GET /api/v1/metrics/dimensions`, required `name`)
    returns a **bare array** of `{key, value: [...]}` (not a paging envelope) —
    each dimension of the metric plus its observed values (e.g. metric
    `cpu.usage_guest` → `host=[<host-id>]`, `cpu=[cpu0,cpu1]`).
  - `list_metric_units` (`GET /api/v1/metricUnits/list`) → `Paging_MetricUnitDto_`;
    each `MetricUnitDto` is `{id, name, newUnit, threshold}` (the unit converts to
    `newUnit` above `threshold`). These `name`s are what `create_metric_unit_mapping`
    accepts as `unit`.
  - `list_metric_unit_mappings` (`GET /api/v1/metric-unit-mappings/list`) →
    `Paging_MetricUnitMappingDto_`. A row's **`metricUnitMappingUserId` is set only
    when the current user overrode the default** unit; empty ⇒ platform default.
    That id is what you delete to reset.
  - `create_metric_unit_mapping` (`POST /api/v1/metric-unit-mapping-users`, body
    `{metricName, unit, description?}`) overrides a metric's display unit for the
    caller; `delete_metric_unit_mapping` (`DELETE /api/v1/metric-unit-mapping-users/{id}`)
    resets it. The delete `{id}` is the **user-mapping id** (create result `id` /
    the `metricUnitMappingUserId` from the mappings list), not the mapping `id`.
- **Metric catalogue** (also `MetricHandler`) — the discovery chain for building a
  widget/alarm: `get_metric_names` (`GET /api/v1/metrics/metric-name`) → pick a
  metric; `list_metric_dimension_names` (`GET .../dimensions-names`) and
  `list_metric_dimension_values` (`GET .../dimensions-values?dimension_name=`) →
  the label keys and their values; `get_metric_dimensions` → both scoped to one
  metric. These three each return an **array of single-field objects** —
  `[{name}]`, `[{dimension_name}]`, `[{dimension_value}]` (NOT bare strings) —
  flattened to `{count, items:[str]}`. **`get_metric_names` 500s without a time
  window** on data-heavy accounts — pass `start_time`/`end_time` (epoch millis).
- **Statistics** (`StatisticHandler`) return the actual **time-series data** behind
  a chart (distinct from the catalogue and from *host* metric snapshots). All three
  return a **bare array** of series objects whose per-item shape is undocumented and
  varies (dimensions, group-by keys, `[ts, value]` point pairs), so `StatisticData`
  keeps the raw series dicts generically in `series` rather than dropping fields.
  - `get_statistics` (`GET /api/v1/statistics`) and `get_statistics_synthetic`
    (`GET .../synthetics`) take flat query params (`name`, `statistics`,
    `dimensions`, `start_time`, `end_time`, `group_by`, `period`, `alarm`; `limit`
    only on the non-synthetic one). Synthetic collapses the window to one value.
  - **`dimensions` is a comma-separated string of `key:value` pairs** (colon
    between key and value, NOT `=`), e.g.
    `resource_id:ins-0001,product:vserver`; `group_by` is the string
    `"none"` when not grouping. The old descriptions said `host=srv-1` (wrong
    separator), which made a first-time agent build an empty/failing filter and
    retry — the docstrings/guide now lead with the real format and steer to v2
    SIMPLE (the primary path vMonitor itself uses). Find `resource_id` and other
    dimension values with `get_metric_dimensions`.
  - `get_statistics_v2` (`POST /api/v1/statistics`, body `{type, data}`) is a
    **read** even though it POSTs. `type` ∈ {`SIMPLE`, `CUSTOM`} (other values 400
    with "Not support query statistics with type X"): `SIMPLE` carries one
    `data.graph` (`{name, statistics, dimensions?, group_by?, rollup?, rate?,
    offset?, limit?}`) plus `start_time`/`end_time` (epoch millis)/`period`/
    `alarm`/`reduction?`; `CUSTOM` carries `data.expression` + `data.graphs`
    (map `a`/`b`/… → graph) for formulas. **The backend does NOT validate `data`:
    a wrong field TYPE answers an uncaught HTTP 500 ("Internal server error"), not
    a 4xx** — `statistics` as a list, `dimensions` as an object, `start_time`/
    `end_time` as ISO strings, or a missing `graph` wrapper each crash it. This was
    the "statistics sometimes works, sometimes 500s": callers reconstructed the
    opaque `data` inconsistently. `StatisticGraphDto`/`StatisticDataDto` now type
    every field (`statistics: str`, `dimensions: str`, times `int`), so a crashing
    shape fails schema validation instead of reaching the backend.
- **Variables / Views / Widgets are dashboard-scoped** (`/dashboards/{id}/...`) and
  their **list endpoints return bare arrays**, not the `Paging_` envelope — wrapped
  as `{count, items}`.
  - **Variables**: `update_dashboard_variables` (`PUT .../variables`) is a
    **whole-list replace** (`{variables:[...]}`), not a per-item patch — omitted
    variables are dropped. Read the list first, edit, resubmit the full set.
  - **Views** are saved query/filter/time-range presets. `create` takes `{name,
    variables(map key→value), filters, query, timeRange}`; the API's `update` body
    types `variables` as an **array** of `{variableId, value, id?}` (differs from
    create's map); `query`/`timeRange` are JSON-encoded strings.
  - **Widgets**: there is **no list endpoint and no v1 create** — widgets come
    embedded in the dashboard detail (`GET /api/v1/dashboards/{id}` → `widgets[]`);
    `create_widget` is the **v2** POST (`.../widgets/v2`).
    - **`list_widgets` is that read**, projected: it maps each widget's
      `metricGraphs` items to `metric_queries`, whose fields land 1:1 on a
      `get_statistics_v2` SIMPLE graph (`name`→`metric_name`,
      `statistic`→`statistic`, **`filter`→`dimensions`**, `groupBy`→`group_by`).
      The wire field is called `filter` but its value IS the `dimensions` string
      (`resource_id:ins-...,product:vserver`), so a widget on a resource's
      auto-generated **system dashboard** is already a runnable query. That is the
      documented shortcut for "show me this resource's metrics" — no catalogue
      walk, no `get_metric_dimensions`, and **no detailed monitoring needed**.
      `WidgetDetail` (from `get_widget`) still exposes the raw `metric_graphs`
      passthrough; `WidgetSummary` is the projected view. The chart content is a polymorphic chart-builder payload the
    API types as opaque `object`/`JsonNode`: the v2 shape is a `graphs` **map**
    (key `a`/`b`/... → `{type, data}`), the v1 `update_widget` shape uses
    `metricGraphs`/`logGraphs` **arrays**. Per decision, widget DTOs type every
    documented **widget-level** field but accept the graph payloads / `extra` /
    `topListChart` / `chartExtra`(JSON string) as passthrough. `get_widget` /
    `create_widget` return `ResponseResult_WidgetDto_` (`{data:...}`, unwrapped);
    the PUT edits (`update_widget*`, `update_widget_layout`) and `delete_widget`
    return **empty bodies** → tools synthesise a confirmation string.
    - **A v2 `graphs` entry `{type:"METRIC_GRAPH", data:{...}}` persists as a
      `metricGraphs` item** (verified live): `data` mirrors the item —
      `{name (metric), statistic (singular, avg/max/...), alias, groupBy, color
      (#hex), filter (e.g. "resource_id:X,product:vserver"), enabled:true}`.
    - **`layout` placement is the string `"cols:C, rows:R, x:X, y:Y"` on a
      10-column grid** (NOT JSON) — verified against the system dashboards (widgets
      are `cols:5, rows:2` half-width packed two per row at `y:0,2,4…`; `NUMBER`
      is `cols:3`). **The v2 create HONOURS `layout`, but a body with no layout
      stores an EMPTY layout** that the web UI cannot place → widgets pile up and
      overlap. This was "adding widgets looks broken on the web". `create_widget`
      now auto-assigns a non-overlapping slot (`_next_grid_slot`, first-fit over
      the existing widgets it reads from the dashboard detail; `cols:3` for
      NUMBER/GAUGE else `cols:5`) and defaults `position="BOTTOM"` /
      `fixedTimeRange="global"` for the native look, so a bare `create_widget`
      (name + typeChart + graphs) renders cleanly. An explicit `layout` is
      respected as-is.
- **Alarms come in three families** on one `/alarms` surface: **metric** (`/metrics`,
  also a **synthetic** variant under `/synthetic/...`), **log** (`/logs`) and
  **change-detection** (`/change-method`, in `change_alarm_handler`). `AlarmDto`
  carries only the block that applies
  (`alarmMetric` / `alarmLog` / `changeAlarmMetric`); `AlarmDetail` keeps those
  blocks raw. Create/update bodies are **flat scalar fields** (only
  `metricFilter` / `filter` are opaque maps) so every alarm DTO is fully typed —
  `name` is the only required field, the rest the API validates. `inAlarm` / `ok`
  / `undetermined` are the per-state notification actions; `thresholdMethod` ∈
  {static, static advanced, pct_change, change, frequency, flatline,
  metric_aggregation}. **Update DTOs omit `id`** — the handler injects the path
  `alarm_id` into the body. `list_alarms` filter param is **`type-alarm`**
  (hyphen). `list_alarms` also 500s ("List alarm by userId is failed") unless
  `name`/`status`/`severity` are all present and 500s / returns 0 without a
  `type-alarm`, so the tool always sends the three filters and, when no type is
  given, queries Metric/Log/Change and merges the results. Definitions/histories
  are upstream-evaluator passthroughs kept generic
  (`AlarmDefinitionData.definition`, `AlarmHistoryData.items`). The
  change-method get + histories **require** `start_time`+`end_time`. The
  **metric/synthetic sub-alarm histories** (`.../metrics/mona/{id}/histories`,
  `.../synthetic/metrics/mona/{id}/histories`) likewise 500 unless
  `start_time`+`end_time`+`interval` are all present — the tools default them
  (last 7 days, `interval=0`). Their `{id}` is an **evaluator sub-alarm id** (an
  `alarms[].id` from `get_metric_alarm_definition` /
  `get_synthetic_alarm_definition`, which themselves take the vMonitor alarm id),
  **not** the vMonitor alarm id — the log-alarm histories default the same way
  (`start`/`end`/`order`/`page`/`len`). `get_log_alarm_status` takes the
  **vMonitor alarm id** (top-level `id`), not the nested `alarmLog.id`.
- **Alarm severity is `LOW`/`MEDIUM`/`HIGH` — this product has NO `CRITICAL`
  tier** (confirmed with the product owner). `CreateMetricAlarmDto.severity` is a
  `Literal["LOW","MEDIUM","HIGH"]`, so `CRITICAL` is rejected like any unknown
  value; do not offer it.
- **Alarm enum casing is normalised on write** (verified live). The API is strict
  — `severity` must be UPPER-CASE (`LOW`/`MEDIUM`/`HIGH`) and `condition`
  lower-case (`gt`/`gte`/`lt`/`lte`) — even though it reads them back in another
  case, and a first-time agent naturally types `"High"` / `"GT"` / `">"` (indeed
  `list_alarms` documents severity title-case). This was the "metric alarm takes
  several tries": every metric/log/change alarm create+update DTO now runs
  `_norm_severity` (→UPPER) and `_norm_condition` (→lower, and `>`/`>=`/`<`/`<=`
  → gt/gte/lt/lte) as `mode="before"` field validators, so the first reasonable
  payload is accepted; a genuinely-invalid value still fails (metric DTO keeps the
  `Literal`).
- **Metric-alarm create needs a full evaluator payload; `create_metric_alarm`
  fills it** (verified live). Beyond the semantic core, the backend needs
  `formula` (the expression over the metric graph — `"a"` for a single-metric
  alarm), `thresholdMethod` (`"static"`), `metricProduct` (`""` for agent/custom
  metrics), `metricGroupBy` (`"none"`), the **`metricFilter` KEY present** (even
  `{}` is accepted; an ABSENT key → **500 "Creating alarm metric failed"**) and
  `timeshift` (a string, e.g. `"-300"`). The handler `setdefault`s all of these
  (`timeshift` = `-metricPeriod`), so a semantic-core payload creates a *working*
  alarm on the first try. `CreateMetricAlarmDto` carries `formula` (default `"a"`)
  and `timeshift`; both are also on `UpdateMetricAlarmDto` (full-object replace).
- **Alarm notification actions take a channel's `metricMappingId`, NOT its `id`**
  (verified live — THE main cause of "alarm creation still unstable"). The
  `inAlarm`/`ok`/`undetermined` fields want the `metric_mapping_id` a channel
  carries in `list_notifications`; passing the plain channel `id` (the value
  list_notifications leads with) makes the backend answer a **misleading 500
  "Internal server error"**. `AlarmHandler._resolve_notification_actions` (run on
  metric+log create/update) fetches the notification list via a
  `VmonitorNotificationClient` (reusing `self.client._token_manager`) and rewrites
  each comma-separated channel `id` → its `metricMappingId` (trailing comma
  preserved; already-correct mapping ids and unknown tokens pass through; a failed
  lookup is a no-op so it never blocks the create). `NotificationSummary.id` /
  `.metric_mapping_id` descriptions and the alarm docstrings/guide state the rule.
- **Log alarms need a `filter` and specific enum casings** (all verified live).
  `create_log_alarm`/`update_log_alarm` require a `filter` = Elasticsearch-style
  `{type, value}` query; a null/absent filter answers **500 "Creating alarm log
  failed"** and `{}` answers 400 — so both tools default it to
  `{"type":"match_all","value":{}}` (all logs) when omitted. `thresholdType` is
  **snake_case** (`frequency`, `flatline`, `metric_aggregation`, `static`,
  `static_advanced`, `percent_change`, `change`) and `condition` is **lower-case**
  (`gt`/`gte`/`lt`/`lte`) on write, even though the API reads them back
  upper-cased (`GT`). `update_log_alarm` is a **full-object replace** (needs
  logProjectId + severity + thresholdType + condition + timeFrame + thresholdValue
  + filter). `name` is 5-255 chars, no spaces.
- **Integrations**: install/uninstall are modeled as `update_integration_installed`
  (PUT `/install/{id}`, body `{logProjectId?}`) and `update_integration_uninstalled`
  (PUT `/uninstall/{id}`, no body) — `install`/`uninstall` aren't approved verbs, so
  they map to `update_` (mirroring the host enable/disable precedent). `delete_integration`
  is separate (full removal, not just uninstall).
- **Metric API keys**: `delete_metric_api_key` takes the **key value** in the path;
  `_key_segment` URL-encodes it and rejects `/`/`\` (keys can contain non-id chars,
  so `validate_id` is not used). This endpoint deletes an **API key**, not an alarm.

## Log API quirks

The Log API is a **separate service** on a **different base URL** but the **same
IAM auth**. `VmonitorConfig.get_base_url` returns the log base for the
`vmonitor-log` service; `VmonitorLogClient` (default_service `vmonitor-log`)
routes there. Paths are `/v1/...` (no `/api` prefix).

- **Base URL**: **`https://vmonitorapis.vngcloud.vn/log-api`** — the same host as
  the metric API (note the trailing `s` in `vmonitorapis`); the `vmonitorapi`
  host without it 404s ("no Route matched").
- **Different paging envelope**: the Log API returns `PageDto`
  (`content` / `currentPage` / `pageSize` / `totalElements` / `totalPages`) — NOT
  the metric API's `Paging_` (`lstData` / ...). `LogPageData.from_api` reads
  `content`; items are kept as **raw dicts** (the log resource types are many and
  varied, so a generic paging model avoids per-type summary classes).
- **Pagination is 0-indexed**: `page=0` is the FIRST page — the OPPOSITE of the
  metric API's 1-based paging (`page=1&size=3` on a 3-item list returns empty
  `content`; `page=0` returns the items). The log tools document `page` as 0-based
  (`ge=0`); omitting `page` also returns the first page.
- **Generic detail model**: single-resource GETs return `LogResource`
  (`id`/`name`/`status` surfaced + full `data` dict). Primitive responses are
  returned as primitives — `get_project_log_data_exists` → `bool`,
  `list_date_formats` → `list[str]`, `search_logs*` → the raw result JSON as a
  `str`.
- **`search_logs` / `search_logs_default` speak a small `{type, value}` DSL, not
  raw Elasticsearch** (verified live). Valid clause **types**: `match` (value
  `{field, value}`), `range` (value `{field, gte/lte/gt/lt}`), `exists` (value
  `{field}`), and `bool` (value with `filter`/`should`/`must`/`mustNot` arrays of
  clauses). `match_all`, `term`, `phrase`, `query_string`, `wildcard` all 400
  ("Invalid query type: X"); a `bool` missing any of its four arrays 400s
  ("should/filter/mustNot must not be null"). To match every log, omit `query`
  (normalised to the empty bool). This was the "log search sometimes works,
  sometimes 400s": a first-time agent naturally reaches for ES syntax
  (`{"match": {field: value}}`, `{"range": {...}}`, a `bool` with `must_not`).
  `_normalize_query` / `_to_clause` now **translate those ES shorthands** into the
  vMonitor DSL (recursively for `bool`, mapping `must_not`→`mustNot`, dropping
  nested `match_all`), so the natural payload works on the first try.
  - **`sorts` clauses are `{type:"field_sort", value:{field, order}}`** (order
    `asc`/`desc`), NOT ES sort maps — any other sort `type` 400s "Invalid sort
    type". `_normalize_sorts` accepts the natural `{field, order}` /
    `{field, direction}` shape and the ES `{field: "desc"}` shorthand and wraps
    them; order defaults to `desc` (newest first).
  - **`search_logs_default` supports a narrower clause set** than `search_logs`:
    `match`/`range`/`bool` only — it rejects `exists` ("Invalid query type:
    exists"). Use `search_logs` for an exists clause.
  - The log-*alarm* `filter` DOES accept `match_all` (different endpoint) — don't
    conflate the two.
- **Certificate download is binary**: `get_project_certificate_download`
  (`/v1/downloads/certificates/projects/{project_id}/{cert_id}`) returns a
  **binary certificate bundle (a ZIP — magic `PK\x03\x04`)**, so it is fetched
  via `BaseClient.get_bytes` (no text/JSON decoding) and returned **base64**;
  decoding it as UTF-8 crashes. The required `cert_id` is **not** exposed by a
  list endpoint — obtain it from `list_projects`, where each project item carries
  a `certInfos[]` array of `{certId, status, createdAt, expireAt}`.
- **Request DTOs are fully typed** (flat fields); genuinely-opaque nested objects
  (`storageSettings`, the log `query`, processor `rulePreset`, processor-group
  `source`/`destination`, project `mappings`) pass through as `dict`/`list` — the
  established "typed shell + opaque payload" strategy. `LogSearchDto` aliases the
  `from` field (`from_offset` → serialized as `from` via `by_alias=True`).
- **Resource log mappings** (vCDN/vDB/vLB/vStorage/bucket) are **PATCH** endpoints
  with the resource identifier in the path (`cdn-domain`, `vdb-resource-id`,
  `bucket-name`, ...) — these can hold dots/non-id chars, so `_seg` URL-encodes and
  rejects `/`/`\` instead of `validate_id`. enable/disable/edit map to
  `update_<type>_log_mapping_enabled` / `_disabled` / (edit →) `update_<type>_log_mapping`
  (the API verbs enable/disable/edit aren't in `ALLOWED_VERBS`). vStorage's list
  filter is `region-id` (hyphen); vDB/vLB use `region`.
- **`validate_*` tools are reads** (test-connection, grok debug) — always
  registered, not gated behind `--allow-write` (they don't mutate vMonitor state).

## Notification gateway quirks

- **Separate host, same IAM**: base `https://vmonitorapis.vngcloud.vn/notification-gateway/api`
  (paths `/v1/...`), selected by the `vmonitor-notification` service /
  `VmonitorNotificationClient`.
- **Third paging envelope**: `{lstData, page, pageSize, totalItem, totalPage}`
  (yet another shape — the metric API uses `lstData` too but the field mix differs
  from the Log API's `content`). Type/notification lists both use it.
- **OTP-verified create flow**: for Email/SMS/Slack/Telegram you must
  `create_notification_otp` (sends a REAL email/SMS) → `validate_notification_otp`
  (returns a `code`) → `create_notification`/`update_notification` with
  `otpCode=code`. Webhook needs no OTP. All OTP + create/update/delete tools are
  gated behind `--allow-write`; the three list/get reads are always on.
- **Channel type is a `Literal`** (Email/SMS/Slack/Webhook/Telegram/Teams — the
  server still returns Teams even though it is no longer offered). `address` in
  `get_notification_otp_info` can be an email → `_seg` URL-encodes it.

## Billing / quota-usage quirks

- **Separate host, same IAM**: base `https://vmonitorapis.vngcloud.vn/billing-api`
  (paths `/v1/...` and `/v2/...`), service `vmonitor-billing` /
  `VmonitorBillingClient`. **The prod path is `billing-api`, without any
  `vmonitor/` prefix** — verified live (`vmonitor/billing-api` → 404 "no Route
  matched", `billing-api` → 200).
- **Read + quote + a narrow set of orders**: 24 READ tools (usage, catalogue,
  price quotes — `get_*_price` POST upstream but only compute, hence read-only)
  plus the 6 order tools in `quota_order_handler.py`, which **spend real money**
  and are registered only under `--allow-write`. Renew, recover-from-trash,
  stop-poc, auto-renew and billing-convert stay unexposed.
- **v1 vs v2 is the packaging generation, not a version bump.** v1 prices/orders
  a whole fixed package from query params; **v2 is quota-class based and takes a
  JSON body** `{redirectUrl, packageId, quantity, ...}` — that is what the
  console's current Quota & Usage screens send, so the order tools use v2
  (metric `/v2/metric/quota/resize`, log `/v2/log/quotas[/{id}/resize]`).
  Exception: **SMS/email have no v2 order endpoint** — only a v2 *price*
  endpoint — so `resize_sms_quota`/`resize_email_quota` POST v1
  `/v1/{sms|email}/quota/resize`, and delete is v1 `/v1/log/quotas/{id}`.
  `get_creation_price`/`get_resize_price` switch to v2 when given a `quantity`,
  which makes them the exact pre-flight for an order body.
- **`packageId` is not free-form: it lives on the quota class.**
  `list_quota_classes` → `config.retentions[]` → each entry is
  `{amount (retention days), packageId, step, minSize/maxSize (log) |
  minResource/maxResource (metric)}`. `quantity` means **GB-days for log**
  (GB per day × retention days) and **host count for metric**; sms/email have no
  quantity (fixed bundles; the console sends 1 in the price call).
- **The log quota IS the log project**: same UUID in `list_log_quotas` and
  `list_projects`, so buying a quota creates the project (`projectName` travels
  on the order) and `delete_log_project` destroys the stored logs with it. The
  `projectType=required` project backs the platform — never delete it.
- **`redirectUrl` is required on an ORDER and allow-listed upstream** — the
  asymmetry that breaks a naive first order. The `prices/*` endpoints accept
  `redirectUrl: ""`, the order endpoints do not: absent/null → `redirectUrl:
  must not be null`, blank → `redirect URL is invalid`, well-formed but unlisted
  → `redirect URL is incorrect`. The accepted value is the console's own
  payment-return page, **`https://vmonitor.console.vngcloud.vn`** — note the
  domain differs from the console users browse (`vmonitor.console.greennode.ai`)
  and comes from that app's runtime config `PAYMENT_REDIRECT_URL`
  (`GET /assets/configs/vmonitor-dashboard.json` on the console host, which is
  where to look again if it ever moves). `quota_order_handler` keeps it in
  `PAYMENT_REDIRECT_BASE` + `QUOTA_PAGE[category]` and `_order_payload`
  `setdefault`s it, so callers never pass one.
- **Renewal / recovery quotes are account-state gated**, and say so via 409:
  `get_renewal_price` answers `This feature is only applied for prepaid user`
  on a postpaid account, `get_recovery_price` answers
  `There is not <category> quota in trash` when the trash is empty. Neither is a
  tool failure — do not chase them as bugs.
- **The free Basic log class is one per account**: a second `create_log_project`
  on the Basic package (retention 1 day, 10 GB) answers
  `409 You can only create 1 log free project`. Every other log package is Pro
  and paid (cheapest quote at the time of writing: 7-day × 20 GB/day), so an
  account that already holds a Basic project cannot buy another log project for
  free — worth saying out loud before proposing a throwaway test project.
- **`pay` decides whether money moves now**: `pay=false` creates a pending order
  and returns `paymentUrl` (the console uses this for root accounts, and passes
  `pay=true` for IAM sub-users); the quota does not change until it is paid.
  `BillingOrderResult` surfaces `order_id` / `amount` / `payment_url`.
- **Category is a `Literal`**, but the supported set differs per sub-resource
  (verified live): usage/current-quota = metric/synthetic/sms/email (+ log via its
  own `get_log_usage`/`list_log_quotas`); tiers & quota-classes & quota-detail =
  metric/synthetic/log; packages = all five but `*/details` &
  `package-description-details` are metric/synthetic/log only (sms/email → 403).
- **Log price quotes route on a `resource_id` in the path**
  (`/v1/log/prices/quotas/{id}/...`) while other categories use a flat
  `/v1/{cat}/prices/...` — `get_*_price` require `resource_id` when `category="log"`.
- **Price results depend on account state**, not tool correctness: a POST-PAID
  account gets 409 on `time-extension-price` ("only for prepaid user"),
  `recovery-price` 409s with an empty trash, and some free/postpaid combinations
  500. Generic outputs: `BillingResource` (single object, `id`/`name`/`status`/`type`
  surfaced + full `data`) and `BillingListData` (bare-list envelopes → `items`).

## Synthetic / uptime quirks

- **Separate host, same IAM**: base `https://vmonitorapis.vngcloud.vn/vmonitor-uptime-manager/v1`,
  service `vmonitor-uptime` / `VmonitorUptimeClient`. Verified live (`GET /uptimes`,
  `/locations` → 200).
- **snake_case fields** (`user_id`, `monitor_status`, `test_frequency`) — unlike
  the camelCase metric API. Lists are **bare JSON arrays** (no paging envelope) →
  `SyntheticListData`; single objects → `SyntheticResource` (id/name/status/type +
  raw `data`).
- **Typed shell + opaque payload**: `CreateUptimeDto` types name/type/subtype/
  locations; the probe `config` (assertions + request), `options` (schedule) and
  `notifications` (per-state channel IDs) pass through as maps.
- `update_uptime_status` is a **toggle** (PUT `/uptimes/status/{id}`, no body) —
  flips enabled↔disabled. `validate_uptime` (POST `/uptimes/test`) is a read-only
  preview, always registered.
- **"synthetic" is overloaded**: `get_statistics_synthetic` (single-stat
  statistics) and the synthetic *alarm* reads are NOT uptime monitoring — they
  stay in the Query / Alarm groups. The Synthetic feature here = uptime monitors +
  locations (this host).

## Server flags

```bash
# Read-only mode (default)
uv run vmonitor-mcp-server

# Enable create/update/delete operations
uv run vmonitor-mcp-server --allow-write

# HTTP transport (default: stdio); Docker image serves this on port 8080
uv run vmonitor-mcp-server --transport streamable-http --host 0.0.0.0 --port 8080

# Redacted inbound-auth diagnostic (HTTP only; also env GRN_MCP_AUTH_DEBUG=1)
uv run vmonitor-mcp-server --transport streamable-http --auth-debug

# HTTP: serve requests WITHOUT a bearer token as the service account (default: 401;
# also env GRN_MCP_ALLOW_SERVICE_ACCOUNT_FALLBACK=1). Only behind an authenticating proxy.
uv run vmonitor-mcp-server --transport streamable-http --allow-service-account-fallback
```

`--auth-debug` logs a redacted `AUTH-DEBUG {...}` line per request and exposes
`GET /whoami`. It never verifies signatures and never emits the full bearer token
(prefix + length only, claims filtered by an allow-list). Diagnostic only — never
in production. Exercise it locally with `scripts/auth-debug-local.sh`.

## Live scripts

`scripts/` holds two read-only live checks that need credentials in
`~/.greennode` (neither creates, modifies or deletes anything):

| Script | Purpose |
|--------|---------|
| `smoke_test.py` | Starts a local server **without** `--allow-write`, drives the read-only tools over the real MCP protocol, and prints a PASS/FAIL table. Staged: global listings → dashboard drill (incl. the `list_widgets` → `get_statistics_v2` chain) → per-resource drill-downs |
| `auth-debug-local.sh` | Mints a throwaway JWT, probes `/whoami` with simulated Gateway headers, and asserts the full token never leaks into the response or the log |

## Key files

| File | Purpose |
|------|---------|
| `server.py` | MCPServer entry point, handler registration, CLI flags, SERVER_INSTRUCTIONS + runtime-mode addendum, HTTP passthrough middleware |
| `config.py` | VmonitorConfig (no region); five base URLs — metric/dashboard default + Log API (`vmonitor-log`) + notification gateway (`vmonitor-notification`) + billing (`vmonitor-billing`) + uptime manager (`vmonitor-uptime`), routed by `get_base_url(service)`; credential/profile loading delegates to `mcp_core.config.load_profile` |
| `client.py` | `VmonitorClient` (metric) + `VmonitorLogClient` + `VmonitorNotificationClient` + `VmonitorBillingClient` + `VmonitorUptimeClient` — all `mcp_core.http.BaseClient` subclasses differing only by `default_service` |
| `auth.py` | Re-export of `mcp_core.auth.TokenManager` (IAM client credentials, auto-refresh) |
| `auth_debug.py` | Opt-in redacted inbound-auth diagnostics (HTTP only) — `summarize_request`, never verifies a signature, never returns the full token |
| `validators.py` | Re-export of `mcp_core.validators.validate_id` |
| `useragent.py` | `USER_AGENT` string sent on every outbound request |
| `tool_annotations.py` | Shared `READ`/`WRITE`/`DESTRUCTIVE` ToolAnnotations constants |
| `dashboard_handler.py` | Dashboard tools (list/get + create/clone/rename/favorite/delete) |
| `infrastructure_handler.py` | Infrastructure host-listing tools (base + per-product), shared `_list_hosts` helper |
| `metric_catalogue_handler.py` | Query group: metric catalogue (names, dimension names/values, dimensions) |
| `metric_unit_handler.py` | Metric-information group: units, unit mappings + user override create/delete |
| `statistic_handler.py` | Statistics tools (metric time-series data behind charts): `get_statistics`, `get_statistics_synthetic`, `get_statistics_v2` |
| `variable_handler.py` | Dashboard-variable tools (list/get + whole-list update) |
| `view_handler.py` | Saved dashboard-view tools (list/get + create/update/delete) |
| `widget_handler.py` | Dashboard-widget tools (list + get + create v2 / update v1 & v2 / update layout / delete); `list_widgets` projects each widget's metric queries into replayable `get_statistics_v2` graphs; typed shell, opaque graph payloads elsewhere |
| `alarm_handler.py` | Alarm tools — metric + log families (list/get/definitions/histories/status + create/update/delete) |
| `change_alarm_handler.py` | Change-detection alarm tools (get/history + create/update/delete + clear history) |
| `integration_handler.py` | Integration app tools (list/get + install/uninstall/delete) |
| `apikey_handler.py` | Metric API key tools (list + create/revoke); `_key_segment` URL-encodes the key path |
| `log_project_handler.py` | Log API: projects, field mappings, client certificates |
| `log_search_handler.py` | Log API: search / export / exists-data |
| `log_archive_handler.py` | Log API: archives (export destinations) + test-connection |
| `log_refill_handler.py` | Log API: refills (re-ingest jobs) + from-archive + test-connection |
| `log_pipeline_handler.py` | Log API: log processing pipelines |
| `log_processor_handler.py` | Log API: processor groups, processors, library, reorder, grok/date helpers |
| `log_mapping_handler.py` | Log API: vCDN/vDB/vLB/vStorage(+bucket) resource→project log mappings; `_seg` encodes path ids |
| `notification_handler.py` | Notification gateway: channel types + channels + OTP-verified create/update/delete; `_seg` encodes email addresses |
| `quota_usage_handler.py` | Billing: quota usages + current quota + quota-detail + settings/trash/convert (read) |
| `quota_catalog_handler.py` | Billing: tiers + packages + descriptions + quota-classes catalog (read); `category` Literal |
| `quota_price_handler.py` | Billing: creation/resize/recovery/renewal price quotes (read; compute-only POSTs); a `quantity` switches creation/resize to the v2 quota-class body — the pre-flight for an order |
| `quota_order_handler.py` | Billing: the only money-spending tools — `create_log_project`, `resize_log_project`, `delete_log_project`, `resize_metric_quota`, `resize_sms_quota`, `resize_email_quota` (all `--allow-write`; resize/delete are DESTRUCTIVE) |
| `certificate_handler.py` | Integration group: log project client certs (download/create/delete); uses `VmonitorLogClient` |
| `synthetic_uptime_handler.py` | Synthetic: uptime monitors (list/get/config/validate + create/update/toggle/delete); `VmonitorUptimeClient` |
| `synthetic_location_handler.py` | Synthetic: probing locations (list/get + create/update/delete) |
| `prompts_handler.py` | Guidance: `get_feature_guide` tool + `vmonitor_getting_started` and one `vmonitor_<feature>` prompt per composite feature (one source of truth; `Feature` Literal is the canonical feature list) |
| `models.py` | Pydantic models (dashboards, hosts, metric catalogue/units/mappings, `StatisticData`, `VariableSummary`/`VariableListData`, `ViewSummary`/`ViewListData`, `WidgetDetail`, and the `*Dto` request bodies incl. `CreateDashboardDto`/`UpdateDashboardDto`, `StatisticQueryDto`, `UpdateVariableListDto`, `CreateViewDto`/`UpdateViewDto`, `GraphRequestDto`, `CreateWidgetDto`/`UpdateWidgetDto`/`UpdateWidgetV2Dto`/`UpdateWidgetLayoutDto`) + `_unwrap` / `_str_field_list` / `_pick_resource_*` helpers |

## Testing

```bash
cd src/vmonitor-mcp-server && uv run pytest tests/ -v
```

Tests use `respx` for async HTTP mocking — no real API calls, no credentials.
