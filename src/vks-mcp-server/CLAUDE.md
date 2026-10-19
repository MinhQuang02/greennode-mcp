# CLAUDE.md — VKS MCP Server

Product-specific guidance for `src/vks-mcp-server`. Monorepo-wide conventions
(tool naming, DTOs, TDD, branch/release flow, security rules) live in the
**repo-root CLAUDE.md** — read that first.

## Product overview

MCP server for VKS (GreenNode Kubernetes Service) clusters and the Kubernetes
resources inside them.

- **41 tools** across 7 handlers: Auth, Cluster, NodeGroup, Version, Discovery, K8s, Guidance (PromptsHandler)
- **3 MCP prompts** (`vks_getting_started`, `vks_create_cluster`, `vks_create_nodegroup`) — portable Vietnamese guidance for any MCP client; always available, no `--allow-write` needed. The two create guides are ALSO served as the `get_creation_guide` tool (same text, one source of truth): prompts must be loaded by the user, tools get called by agents on their own. Edit the guidance in `_create_*_guidance()` — it propagates to both.
- **Every tool declares ToolAnnotations** (`READ`/`WRITE`/`DESTRUCTIVE` from `tool_annotations.py`), picked by effect, not name (dry-run delete = READ; version upgrade = DESTRUCTIVE). Conventions tests enforce it.
- **SERVER_INSTRUCTIONS are mode-aware**: `create_server()` appends a runtime addendum (write on/off, sensitive-data on/off) so agents refuse impossible flows up front.
- **Structured output** — data tools return Pydantic models; FastMCP emits `outputSchema` + `structuredContent` (JSON). Blob tools (`get_access_token`, `get_cluster_kubeconfig`) stay `str`. Region is a fixed `Literal["HCM-3", "HAN"]`.

## Guidance placement policy

Four layers, each with ONE job — do not let content drift between them:

| Layer | Carries | Never carries |
|---|---|---|
| Docstring / param description | The tool CONTRACT: semantics, ranges, formats, hard API constraints, cross-tool id mapping | Conversation choreography, rendering rules |
| `get_creation_guide` / prompts | Choreography: question order, ask-the-user steps, confirm gates, defaults | — |
| `SERVER_INSTRUCTIONS` | Session-wide principles (region model, id-first rendering, never-silent-defaults) | Per-tool detail |
| Error messages | The next step to fix THIS failure | — |

A behavioral hint may live in a docstring only when the tool is not covered
by any guide AND it fits in one line — and prefer generalizing it into an
instructions principle instead. (History: the id-first docstring hints were
reverted for exactly this reason — see #46/#47.)

## VKS API quirks

- **Pagination is 0-based**: page 0 = first page
- **API returns 202** for most successful operations (not 200)
- **The greennode-cli is the source of truth for the current API** — the bundled `~/.greennode/mcp-specs/vks.json` OpenAPI file is stale
- Discovery (vpc/subnet/flavor/sshkey/secgroup/volume-type/placement-group) goes to the **vServer API** (token-only auth); `project_id` is auto-discovered from `GET /v1/projects` when unset
- **vServer list pagination is effectively a no-op**: `page`/`size` query params are ignored and every list endpoint returns the full set in one response (envelope reports `page=0 / pageSize=0 / totalPage=0`, `len(listData) == totalItem`). Discovery fetchers go through `_fetch_all_items`, which uses that single-call fast path but pages explicitly as a safety net if a response ever reports `totalItem > len(listData)` — so results never truncate silently as an account grows.
- **VKS list pagination IS enforced** (opposite of vServer): server-side default `pageSize=10` silently truncates bare calls. `list_clusters` / `list_nodegroups` / `list_nodes` go through `paging.fetch_all_vks_items` and never expose paging params.
- **project_id is region-scoped**: each region's vServer endpoint has its own project. `_require_project_id` resolves + caches per region (`config.project_id_by_region`); the configured `GRN_PROJECT_ID` belongs to the default region only.
- **The kubeconfig endpoint returns a JSON envelope** `{kubeConfig, status, ...}` (was bare YAML); a new cluster has NO kubeconfig until `generate_kubeconfig` mints one (async, `POST .../kubeconfig`). `kubeconfig.extract_kubeconfig` handles both shapes. `expiration_days` (1-365) has **no default** — a credential lifetime is a user decision (never-silent-defaults), so the schema forces the ask.
- **Several write endpoints answer 202 with an EMPTY body** (e.g. auto-upgrade-config) — `mcp_core.http.BaseClient` returns `None` for empty success bodies.
- **Flavors and volume types are zone-scoped** (zone = the chosen subnet's availability zone). `list_flavors` / `list_volume_types` take `cluster_id` + `subnet_id`; `_resolve_zone_context` locates the cluster (tries each region, cached) and derives the zone — agents never pass region/zone. Worker flavors come from the two-step `flavor_zones/customs/clusters/master/false?zoneId=` → `/{fzid}/flavors` flow (the flat `/flavors/customs/clusters` endpoint always returns `[]`); volume types default to NVME (AUTO falls back to SSD when the zone has no NVME; explicit `type_name=SSD` supported), users pick an IOPS tier.

## Server flags

```bash
# Read-only mode (default)
uv run vks-mcp-server

# Enable create/update/delete operations
uv run vks-mcp-server --allow-write

# Enable reading Kubernetes Secrets + the cluster kubeconfig (admin credentials)
uv run vks-mcp-server --allow-sensitive-data-access

# HTTP transport (default: stdio); Docker image serves this on port 8080
uv run vks-mcp-server --transport streamable-http --host 0.0.0.0 --port 8080

# HTTP: serve requests WITHOUT a bearer token as the service account (default: 401)
uv run vks-mcp-server --transport streamable-http --allow-service-account-fallback

```

## Auth (HTTP transport)

Per-request upstream identity: an IAM bearer token in `Authorization`
(forwarded by the AgentBase Gateway) → every VKS/vServer call runs as that
caller (per-user projects; caches isolated per caller identity; a rejected user
token never falls back to the service account). No token → 401, unless the
operator opts in with `--allow-service-account-fallback` (env
`GRN_MCP_ALLOW_SERVICE_ACCOUNT_FALLBACK`) and service-account credentials are
configured — the fallback hands the service account to anyone who can reach the
port, so it is off by default. The server boots credential-less on HTTP
(passthrough-only); stdio requires service-account credentials. `/health` is
always open. `get_access_token` reports the identity mode only — no tool ever
returns a token.
`--auth-debug` (env `GRN_MCP_AUTH_DEBUG=1`) is an opt-in, redacted, HTTP-only diagnostic: logs a summary of inbound request auth and exposes `GET /whoami`. Never verifies signatures, never logs the full token; off by default; not for production.

## Write DTO field scope

All write DTOs (`CreateClusterComboDto`, `UpdateClusterDto`, `CreateNodeGroupDto`, `UpdateNodeGroupDto`, `UpdateNodeGroupMetadataDto`, `NodeGroupSpec`, `UpgradeConfig`, and the nested `NodeGroupTaint`, `AutoScaleConfig`, `PlacementGroupConfig`, `AutoUpgradeConfig`, `AutoHealingConfig`) are configured with `extra="forbid"`. They mirror the greennode-cli field set.

Cluster write DTOs:

- **Create** (`CreateClusterComboDto`, `POST /v1/clusters`): required `name`, `version`, `networkType`, `vpcId`, `listSubnetIds` (exactly one id when `azStrategy=SINGLE`; at least two when `MULTI`; duplicates rejected). The zone rule needs discovery, so `validate_cluster_create` and `create_cluster` both cross-check against `list_subnets`: every id must be in the VPC, and `MULTI` must span ≥2 availability zones (two subnets in the same zone is rejected). A discovery failure never blocks a create — the API stays the authority. Creates the **control plane only** (matching the CLI) — add workers via `create_nodegroup`. Rejected (`extra="forbid"`): the API's deprecated `nodeGroups` array, `secondarySubnets` (belongs to node groups, each sets its own at creation), and the deprecated single-subnet `subnetId` — one and many subnets both travel in `listSubnetIds`, same as `grn vks create-cluster --subnet-ids`. `cidr` is the pod range: private IPv4 network address from 10.0.0.0-10.255.0.0, 172.16.0.0-172.24.0.0 or 192.168.0.0, API default `172.16.0.0/16` — format and private-space membership are enforced on the model, the exact ranges are prose in the spec so they are only stated in the error. Optional: `enablePrivateCluster`, `releaseChannel`, `enabledLoadBalancerPlugin`, `enabledBlockStoreCsiPlugin`, `enabledServiceEndpoint` (private clusters only, default true), `azStrategy`, `description`, `cidr`, `nodeNetmaskSize` (24–26), `autoUpgradeConfig`, `autoHealingConfig`. Network rules are enforced on the model, so they fail at parse time on both `create_cluster` and `validate_cluster_create`: `CILIUM_OVERLAY`/`TIGERA` need `cidr`, `CILIUM_NATIVE_ROUTING` needs `nodeNetmaskSize`.
- **Update** (`UpdateClusterDto`, `PUT /v1/clusters/{id}`): **partial update — all fields optional**, send only what changes (`version`, `whitelistNodeCIDRs`, plugin toggles `enabledLoadBalancerPlugin` / `enabledBlockStoreCsiPlugin`); an empty body is rejected by the handler. Name, description, and release channel are **not** editable via this endpoint.

Node-group write DTOs (validate a create body with `validate_nodegroup_create` before calling `create_nodegroup`: local name/bounds rules + cached discovery cross-checks — subnet in the cluster's VPC; on CILIUM_NATIVE_ROUTING clusters secondarySubnets = the chosen subnet's `secondary_subnets` CIDRs verbatim, non-empty — the subnet must have them (other networkTypes don't use the field); flavorId/diskType in the subnet's zone; sshKeyId/securityGroups in the region):

- **Create** (`CreateNodeGroupDto`/`NodeGroupSpec`): required `name`, `flavorId`, `diskType` (a volume-type **ID** from `list_volume_types`), `sshKeyId`, `diskSize`, `numNodes`, `subnetId`. `secondarySubnets` is required on CILIUM_NATIVE_ROUTING clusters ONLY (derived, never asked: the chosen subnet's `secondary_subnets` CIDRs copied verbatim from `list_subnets`, non-empty, so the subnet must HAVE them; other networkTypes omit the field). Optional: `os` (ubuntu/linux/rocky, top level — NOT inside `upgradeConfig`), `enablePrivateNodes`, `enabledEncryptionVolume`, `securityGroups`, `upgradeConfig`, `labels`, `taints`, `tags`, `autoScaleConfig`, `placementGroupConfigDto`.
- **Update** (`UpdateNodeGroupDto`, `PUT .../node-groups/{id}`): `numNodes`, `securityGroups`, `autoScaleConfig`, `upgradeConfig`. Autoscaling is tri-state, matching the API's JsonNullable field: omit `autoScaleConfig` to keep the current config, pass an object to set it, or set `disable_auto_scale=true` (a local sentinel, never sent on the wire) to send `autoScaleConfig: null` and delete it. The object and the sentinel are mutually exclusive.
- **Delete order**: `DELETE /v1/clusters/{id}` takes no cascade/force parameter and is rejected while any node group exists ("Cannot delete cluster as it still has existing node groups"). `delete_cluster_dryrun` lists the node groups as a precondition plus the order, and `delete_cluster` refuses up front rather than surfacing the API's 400. Deleting those node groups is a separate per-node-group decision (`delete_nodegroup` needs `force_delete`) — never a side effect of approving the cluster delete.
- **Metadata** (`UpdateNodeGroupMetadataDto`, `PATCH .../node-groups/{id}/metadata`): `labels`, `tags`, `taints` — updated **only** through this endpoint, never via `update_nodegroup`.

## Key files

| File | Purpose |
|------|---------|
| `server.py` | FastMCP entry point, handler registration, CLI flags, auth modes, SERVER_INSTRUCTIONS + runtime-mode addendum |
| `tool_annotations.py` | Shared `READ`/`WRITE`/`DESTRUCTIVE` ToolAnnotations constants |
| `paging.py` | `fetch_all_vks_items` — fetch-all for VKS's enforced paging |
| `config.py` | VksConfig + REGIONS endpoints; credential/profile loading delegates to `mcp_core.config.load_profile` |
| `auth.py` | Re-export of `mcp_core.auth.TokenManager` (IAM client credentials, auto-refresh) |
| `client.py` | VksClient extends `mcp_core.http.BaseClient` — adds the vServer service and default service `vks` |
| `validators.py` | Re-export of `mcp_core.validators.validate_id` |
| `cluster_handler.py` | 13 cluster tools (CRUD + kubeconfig get/generate + events + auto-upgrade + validation + auto-healing) |
| `nodegroup_handler.py` | 10 nodegroup tools (CRUD + metadata + nodes + dry-run + `validate_nodegroup_create` + version upgrade) |
| `k8s_handler.py` | 7 K8s tools (list/manage resources + logs + events + apply YAML + generate app manifest) |
| `k8s_apis.py` | K8s API client wrapper using kubernetes library |
| `k8s_client_cache.py` | TTL cache for K8s clients (840s) |
| `kubeconfig.py` | Extract kubeconfig YAML from the endpoint's JSON envelope (new clusters need generate_kubeconfig first) |
| `version_handler.py` | 1 tool (cluster versions) |
| `discovery_handler.py` | 8 discovery tools (vpc/subnet/flavor/sshkey/secgroup/volumetype/placementgroup lists + quota) — vServer + VKS quota, name→ID resolution, `_resolve_zone_context` / `_locate_cluster` |
| `discovery_cache.py` | Package TTL config on top of `mcp_core.cache.DiscoveryCache` |
| `prompts_handler.py` | 3 MCP prompts + the `get_creation_guide` tool (same guidance text) |
| `models.py` | Pydantic models + markdown formatters for responses |

## Testing

```bash
cd src/vks-mcp-server && uv run pytest tests/ -v
```

- Tests cover all handlers (incl. tool-schema introspection and outputSchema assertions); `respx` mocks all HTTP — no credentials needed
- **Manual testing** (real API, credentials from `~/.greennode/`): MCP Inspector over stdio or piped JSON-RPC — full walkthrough in `README.md` → Development. Do NOT use `uv run mcp dev` (FastMCP is built inside `create_server()`/`main()`, no module-level `mcp` object). Verify auth first with the `get_access_token` tool.

## Relationship with greennode-cli

Both projects share config files (`~/.greennode/`, legacy `~/.greenode` fallback), the REGIONS endpoints, the
IAM auth flow, and the `GRN_*` env var names. Tool names map 1:1 to CLI
command names. Key differences: the MCP server is **async** (CLI is sync),
returns **structured JSON** for data tools, and adds **K8s resource
management** which the CLI does not have.
