# GreenNode vServer MCP Server

An MCP (Model Context Protocol) server for **vServer** — GreenNode's compute /
IaaS product: virtual machine instances, block storage volumes, snapshots,
images, networking (VPC, subnet, security group, network ACL, route table,
peering, interconnect, virtual IP, floating IP, network interface, DHCP option
set), SSH keys and placement groups.

**184 tools** with `--allow-write` (85 in the default read-only mode) and
**10 guided flows**. Coverage goes beyond the `greennode-cli` command set:
snapshots, route tables, network ACLs, VPC peering, interconnects and virtual
IPs have no CLI equivalent. Read and write tools alike have been exercised
against the live API on throwaway resources — see *Testing status*.

## Configuration

Credentials are read from `~/.greennode/credentials` and `~/.greennode/config`
(INI format, shared with greennode-cli; `GRN_*` env vars override — see the
repo-root CLAUDE.md).

| Variable | Purpose |
|----------|---------|
| `GRN_CLIENT_ID` / `GRN_CLIENT_SECRET` | Service-account credentials |
| `GRN_PROFILE` | Profile to select (default: `default`) |
| `GRN_DEFAULT_REGION` | `HCM-3` (default) or `HAN` |
| `GRN_PROJECT_ID` | Project for the **default region** only — other regions are auto-discovered |

## Running

```bash
# Read-only mode (default)
uv run vserver-mcp-server

# Enable create/update/delete/power operations
uv run vserver-mcp-server --allow-write

# HTTP transport (Docker image serves this on port 8080)
uv run vserver-mcp-server --transport streamable-http --host 0.0.0.0 --port 8080
```

### Auth (HTTP transport)

Per-request upstream identity, no flags: an IAM bearer token in `Authorization`
(forwarded by the AgentBase Gateway) → every vServer call runs as that caller
(per-user projects; caches isolated per caller identity; a rejected user token
never falls back to the service account). No token → the shared service account.
Neither → 401. The server boots credential-less on HTTP (passthrough-only);
stdio requires service-account credentials. `/health` is always open.

`--auth-debug` (env `GRN_MCP_AUTH_DEBUG=1`) is an opt-in, redacted, HTTP-only
diagnostic: it logs a summary of inbound request auth and exposes `GET /whoami`.
It never verifies signatures and never logs the full token. Not for production.

## Regions and projects

Every resource is region-scoped (`HCM-3` or `HAN`), and **each region's gateway
exposes its own project** — the server resolves the project id itself, so tools
never take a `project_id` parameter. Zone ids are readable strings
(`HCM03-1A` … `HCM03-BKK-01`), not UUIDs.

## Tools

Write tools (create / update / delete / power) are registered **only** with
`--allow-write`. Destructive ones additionally carry a `destructiveHint`
annotation so MCP clients can warn before running them.

### Guidance

| Tool | Access | Description |
|------|--------|-------------|
| `get_feature_guide` | read | Step-by-step guide for a multi-step flow — call it **first** |

`get_feature_guide` takes one of: `getting_started`, `create_server`,
`manage_server`, `create_volume`, `create_network`, `secure_server`,
`snapshot_and_restore`, `network_acl`, `connect_networks`,
`high_availability`.

The same ten are available as MCP prompts under the `vserver_` prefix
(`vserver_getting_started`, `vserver_snapshot_and_restore`, …). Each guide
describes a *feature* — a capability assembled from several tools — and is
written in Vietnamese, like the other GreenNode MCP servers.

### Discovery

| Tool | Access | Description |
|------|--------|-------------|
| `get_access_token` | read | Current IAM token, region and endpoint |
| `list_zones` | read | Enabled availability zones — step 1 of every creation flow |
| `list_flavor_families` | read | Instance families (`general-purpose`, `gpu`) |
| `list_flavor_codes` | read | CPU/GPU platform codes |
| `list_flavors` | read | Flavors of one family × code, filtered by zone and capacity |
| `list_images` | read | Bootable OS or GPU images, with optional name filter |
| `list_volume_types` / `get_volume_type` | read | Disk IOPS tiers of a zone (NVMe preferred, SSD fallback), and one tier by id |
| `get_default_volume_type` | read | The tier vServer falls back to when none is given |
| `get_flavor` | read | One flavor by id — what a server runs on today |
| `get_zone` | read | One zone by id; check `enabled` before placing a resource |
| `get_quota` | read | Project quota and current usage, per region |

### Servers

| Tool | Access | Description |
|------|--------|-------------|
| `list_servers` / `get_server` | read | Instances, with private/public IPs and zone |
| `list_server_interfaces` | read | Internal and external NICs of a server |
| `list_server_security_groups` | read | Attached groups plus effective inbound/outbound rules |
| `list_server_actions` | read | Audit trail of creates, resizes, reboots |
| `list_subnet_servers` | read | Servers with an interface in one subnet |
| `get_server_console_url` | read | Time-limited browser VNC console URL |
| `get_server_console_log` | read | Serial-console output (tail) — boot and kernel failures |
| `get_server_external_interface` | read | One attached elastic interface by id |
| `create_server` | write | Create an instance (billing/backup fields deliberately excluded) |
| `start_server` / `stop_server` / `reboot_server` | write | Power operations |
| `resize_server` | write | Change flavor; restarts the instance |
| `rename_server` | write | Change the display name |
| `update_server_security_groups` | write | Replace the attached group set |
| `create_server_image` | write | Capture the instance as a reusable user image |
| `attach_server_internal_interface` | write | Add private NICs on given subnets |
| `detach_server_internal_interfaces` | destructive | Remove private NICs |
| `attach_server_internal_interface_floating_ip` | write | Add private NICs that each come with a public IP |
| `detach_server_internal_interface_floating_ip` | destructive | Remove those NICs and release their public IPs |
| `attach_server_external_interface` / `detach_server_external_interface` | write / destructive | Move an elastic interface on or off |
| `attach_server_floating_ip` / `detach_server_floating_ip` | write / destructive | Public IP on or off an interface |
| `delete_server` | destructive | Delete the instance, optionally with its volumes |

### Storage

| Tool | Access | Description |
|------|--------|-------------|
| `list_volumes` / `get_volume` | read | Block-storage volumes |
| `list_server_volumes` | read | Volumes attached to one server |
| `get_server_boot_volume` | read | Just the root disk of a server |
| `list_volume_history` | read | Size and IOPS changes over a volume's life |
| `list_persistent_volumes` | read | Kubernetes PVs backed by vServer storage |
| `create_volume` | write | Create a volume in a zone |
| `resize_volume` | write | Grow a volume or change its IOPS tier |
| `update_volume_type` | write | Move a volume to another IOPS tier only |
| `delete_persistent_volume` | destructive | Delete a Kubernetes PV (prefer deleting it through the cluster) |
| `rename_volume` | write | Change the display name |
| `attach_volume` | write | Attach to a server in the same zone |
| `detach_volume` | destructive | Detach (unmount inside the guest OS first) |
| `delete_volume` | destructive | Delete a detached volume and its data |
| `list_user_images` / `get_user_image` | read | Custom images captured from servers |
| `delete_user_image` | destructive | Delete a user image |

### Snapshots

| Tool | Access | Description |
|------|--------|-------------|
| `list_snapshot_policies` | read | Schedule policies (cadence + retention) — the only source of a `snapshotPolicyId` |
| `list_server_snapshots` / `list_volume_snapshots` | read | Snapshot points of a server or a volume |
| `get_server_snapshot_policy` / `get_volume_snapshot_policy` | read | The auto-snapshot configuration (`configured=false` when unset) |
| `list_shared_server_snapshots` | read | Who else may restore from this server's snapshots |
| `create_server_snapshot` / `create_volume_snapshot` | write | Take a snapshot now, with a retention period |
| `create_server_snapshot_policy` | write | Set up the snapshot configuration of a server |
| `update_server_snapshot_policy` / `update_volume_snapshot_policy` | write | Switch schedule policy |
| `enable_server_auto_snapshot` / `disable_server_auto_snapshot` | write | Start or stop the server schedule |
| `enable_volume_auto_snapshot` / `disable_volume_auto_snapshot` | write | Start or stop the volume schedule (needs the attached server id) |
| `rollback_server_snapshot` / `rollback_volume_snapshot` | destructive | Revert to a point — **destroys everything written since** |
| `delete_server_snapshot` / `delete_volume_snapshot` | destructive | Delete one recovery point |
| `delete_server_snapshot_policy` / `delete_volume_snapshot_policy` | destructive | Delete the configuration **and every point under it** |
| `delete_shared_server_snapshot` | destructive | Revoke a share grant |

### Networking

| Tool | Access | Description |
|------|--------|-------------|
| `list_vpcs` / `get_vpc` | read | VPCs in the project |
| `get_vpc_config_options` | read | Everything the console's create-VPC form offers: MTU values (bytes), the fixed `/16` and its allowed ranges, and the CIDRs already in use in the region — call before `create_vpc` |
| `create_vpc` / `update_vpc` / `delete_vpc` | write / write / destructive | Manage VPCs. `create_vpc` follows the console form: name 5-50 chars, a **/16** CIDR in `10.x`, `172.16-172.24` or `192.168` that must not overlap an existing VPC, a mandatory MTU and zone. CIDR and MTU cannot be changed later |
| `list_subnets` / `get_subnet` | read | Subnets of a VPC (a subnet pins a server's zone) |
| `create_subnet` / `update_subnet` / `delete_subnet` | write / write / destructive | Manage subnets. `update_subnet` keeps the secondary ranges unless you pass a new list (the API alone would drop them on a rename) |
| `list_security_groups` / `get_security_group` | read | Security groups; `system=true` = platform-managed |
| `create_security_group` / `update_security_group` / `delete_security_group` | write / write / destructive | Manage groups |
| `list_security_group_rules` / `get_security_group_rule` | read | Rules of a group |
| `list_security_group_rule_samples` | read | The API's 30 named presets (SSH, All TCP, All ICMP …) |
| `create_security_group_rule` / `update_security_group_rule` / `delete_security_group_rule` | write / write / destructive | Manage rules |
| `list_floating_ips` | read | Public WAN IPs and whether they are attached |
| `delete_floating_ip` | destructive | Release an address back to the pool |
| `list_network_interfaces` / `get_network_interface` | read | Elastic network interfaces |
| `create_network_interface` / `rename_network_interface` / `update_network_interface_tags` | write | Manage elastic interfaces |
| `delete_network_interface` | destructive | Delete an elastic interface |
| `list_dhcp_options` / `get_dhcp_option` / `list_dhcp_option_vpcs` | read | DHCP option sets and their VPCs |
| `create_dhcp_option` / `update_vpc_dhcp_option` | write | Create a set, bind or unbind it to a VPC |
| `delete_dhcp_option` | destructive | Delete a DHCP option set |
| `list_active_vpcs` | read | The API's own view of which VPCs are usable |
| `enable_vpc_dns` | write | Turn on private DNS in a VPC (one-way — no disable exists) |
| `list_security_group_servers` | read | Servers a security group is attached to — its blast radius |
| `list_elastic_ips` | read | The console-side view of public addresses |
| `create_secondary_subnet` / `delete_secondary_subnet` | write / destructive | Extra CIDRs on a subnet; create returns the parent subnet with the new range in `secondary_subnets`. A subnet with ranges left cannot be deleted |

GreenNode system images move remote administration off the default ports: SSH
listens on **234** and RDP on **3490**. `list_security_group_rule_samples`
returns both the standard presets (`SSH` 22, `RDP` 3389) and the GreenNode ones
(`SSH VNG` 234, `RDP VNG` 3490) — pick by image, not by habit. Rules are
allow-only and stateful, so replies to an allowed flow need no second rule.

### Network ACLs, route tables and peering

| Tool | Access | Description |
|------|--------|-------------|
| `list_network_acls` / `get_network_acl` | read | Subnet-level firewalls; `is_default` cannot be deleted |
| `list_network_acl_rules` | read | Rules split by direction, in evaluation order |
| `create_network_acl` | write | Create an ACL in a VPC |
| `update_network_acl_rules` | write | Replace the **whole** rule set — ACLs are stateless, so both directions are needed |
| `update_network_acl_subnets` | write | Set which subnets the ACL governs (full replacement) |
| `delete_network_acl` | destructive | Delete an ACL — its subnets fall back to allow-all |
| `list_route_tables` / `get_route_table` / `list_route_table_routes` | read | Static routing out of a VPC |
| `create_route_table` | write | Create a route table (name 5-50 chars) |
| `update_route_table_routes` | write | Replace the **whole** route set |
| `delete_route_table` | destructive | Delete a route table |
| `list_peerings` | read | VPC peerings — **creating one goes through GreenNode support** |
| `delete_peering` | destructive | Remove a peering (re-creation needs a support request) |

### Interconnect

| Tool | Access | Description |
|------|--------|-------------|
| `list_interconnects` / `get_interconnect` | read | Private circuits to on-premises, another cloud or the other region |
| `list_interconnect_packages` / `list_interconnect_circuit_types` | read | The bandwidth and circuit-type catalogues |
| `list_interconnect_connections` / `get_interconnect_connection` | read | VPC attachments on a circuit |
| `create_interconnect` | write | Order a circuit — **contracted, monthly billed** |
| `update_interconnect` | write | Description, tags, gateway-2 redundancy |
| `update_interconnect_package` | write | Change the committed bandwidth — **changes the price** |
| `create_interconnect_connection` / `update_interconnect_connection` | write | Attach a VPC; set its remote CIDRs (full replacement) |
| `ping_interconnect` | write | Diagnostic reachability test (changes nothing) |
| `delete_interconnect_connection` / `delete_interconnect` | destructive | Detach a VPC; remove the circuit |

### Virtual IPs

| Tool | Access | Description |
|------|--------|-------------|
| `list_virtual_ips` / `get_virtual_ip` | read | Shared addresses for HA pairs, private and public |
| `list_virtual_ip_address_pairs` / `get_virtual_ip_address_pair` | read | Which interfaces answer for a VIP |
| `list_virtual_ip_candidate_interfaces` | read | Internal interfaces eligible to join a private VIP |
| `list_public_virtual_ip_candidate_interfaces` | read | External interfaces eligible to join a public VIP |
| `list_secondary_subnet_address_pairs` | read | Interfaces bound to a secondary subnet |
| `create_virtual_ip` / `update_virtual_ip` | write | Create a private VIP; rename or change its mode |
| `create_public_virtual_ip` | write | Create a public VIP (consumes a public IP) |
| `create_virtual_ip_address_pair` | write | Bind an instance's interface to a private VIP |
| `create_public_virtual_ip_address_pair` | write | Bind an external interface to a public VIP |
| `create_secondary_subnet_address_pair` | write | Bind an interface to a secondary subnet |
| `delete_virtual_ip_address_pair` / `delete_public_virtual_ip_address_pair` | destructive | Unbind an interface |
| `delete_secondary_subnet_address_pair` | destructive | Unbind from a secondary subnet |
| `delete_virtual_ip` / `delete_public_virtual_ip` | destructive | Release the shared address |

### Keys, placement and tags

| Tool | Access | Description |
|------|--------|-------------|
| `list_ssh_keys` / `get_ssh_key` | read | SSH keys registered in the project |
| `create_ssh_key` | write | Generate a pair; the private key is returned **once** |
| `import_ssh_key` | write | Register an existing public key (preferred) |
| `delete_ssh_key` | destructive | Remove a key |
| `list_placement_groups` / `get_placement_group` | read | Placement groups and their servers |
| `list_placement_group_policies` | read | Affinity / anti-affinity policies |
| `create_placement_group` / `update_placement_group` | write | Manage placement groups |
| `delete_placement_group` | destructive | Delete an empty placement group |
| `list_tag_keys` / `list_tag_values` / `list_resource_tags` | read | Tag vocabulary and a resource's tags |
| `list_tags` | read | Every tag object in the project (system tags hidden by default) |
| `get_tag_quota` | read | How many tags one resource may carry |
| `update_resource_tags` | write | Replace a resource's whole tag list |

## Not covered

- **Bandwidth** (Network → Bandwidth in the console) has no public API — it is
  console-only. The `bandwidth` field on a flavor is its NIC speed, a different
  thing.
- Marketplace, server live migration, custom flavor / zone allocation and a few
  redundant internal endpoints are deliberately left out. `CLAUDE.md` lists each
  with the reason.

## Testing status

- **209 unit tests**, all HTTP mocked with `respx` — no credentials needed.
- Every **read** tool has been exercised against a live HCM-3 gateway. Five of
  them (`list_interconnect_circuit_types`, `list_shared_server_snapshots`,
  `get_volume_snapshot_policy`, `list_active_vpcs`, `list_elastic_ips`) answer
  `403 IAM_PERMISSION_DENIED` unless the calling identity's IAM policy covers
  them — that is an account permission, not a missing feature.
- `scripts/smoke_test.py` runs the read-only tools end to end over the real MCP
  protocol against a live gateway.
- Write tools verified live end to end (create → update → delete on throwaway
  resources): instances (create, power, delete with disks), volumes (create,
  attach, detach, resize-type, delete), subnets, network ACLs, route tables,
  security groups and rules, SSH keys, placement groups, DHCP option sets,
  snapshot configurations and resource tags.
- **VPC creation verified live** (HAN): create with a non-default MTU, subnet
  inside it, delete, all cleaned up. `create_vpc` follows the console form
  (always `/16`, three private ranges, no overlap, mandatory MTU and zone)
  even where the API alone is laxer — it would silently default the MTU to
  1500 and the zone to the region default. Subnets take the wider catalogue
  `/16, /18, /20, /22, /24, /26, /28`.
- Still unverified: adding a **custom network-ACL rule** — the platform accepts
  the request and drops the rule; see the open question in `CLAUDE.md`.
  Floating-IP and elastic-interface writes are mock-tested only.

## Development

```
greennode/vserver_mcp_server/
├── server.py            MCPServer entry point, CLI flags, handler registration
├── client.py            HTTP client on top of greennode.mcp_core
├── config.py            regions and profile loading
├── project.py           region-scoped project resolution
├── paging.py            response-envelope and pagination normalisers
├── *_handler.py         one handler per resource area — the tools
└── models/              response models and write DTOs, split by domain
```

`models/` re-exports everything from its `__init__.py`; import from
`greennode.vserver_mcp_server.models`, never from a submodule.

```bash
cd src/vserver-mcp-server
uv run pytest tests/ -v
uv run ruff check . && uv run ruff format --check .
```

Manual testing with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv run vserver-mcp-server
```

`scripts/` holds two live checks that need credentials in `~/.greennode`:
`smoke_test.py` drives the read-only tools over the real MCP protocol, and
`auth-debug-local.sh` exercises the `--auth-debug` diagnostic locally.

Do **not** use `uv run mcp dev` — MCPServer is built inside `create_server()` /
`main()`, so there is no module-level `mcp` object. Verify auth first with the
`get_access_token` tool.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
