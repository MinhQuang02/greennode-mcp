# CLAUDE.md — vServer MCP Server

Product-specific guidance for `src/vserver-mcp-server`. Monorepo-wide conventions
(tool naming, DTOs, TDD, branch/release flow, security rules) live in the
**repo-root CLAUDE.md** — read that first.

## Product overview

MCP server for GreenNode **vServer** — the compute/IaaS product: virtual machine
instances plus everything around them (volumes, images, networking, security
groups, SSH keys, placement groups, floating IPs, DHCP option sets).

Coverage goes past the `greennode-cli` command set: snapshots, route tables,
network ACLs, VPC peering, interconnects and virtual IPs have no CLI equivalent.

- **184 tools** with `--allow-write`, **85** in the default read-only mode, plus
  **10 MCP prompts** and a `get_feature_guide` tool serving the same guidance.
- Endpoints left out on purpose are listed under *Deliberate scope limits* with
  the reason for each.
- Every read tool has been exercised against the live HCM-3 gateway, and the
  write tools through a full create → update → delete cycle on throwaway
  resources (instances, volumes, subnets, ACLs, route tables, security groups
  and rules, SSH keys, placement groups, DHCP option sets, snapshot
  configurations, tags, and VPCs including their MTU/CIDR rules). Custom ACL
  rules are the one remaining gap — see *Known open question*.
- Region-scoped like VKS (`HCM-3` / `HAN`), unlike the global vMonitor server.
- Product documentation: <https://docs.greennode.ai/vserver>.

## vServer API quirks

Everything below is **verified against the live HCM-3 gateway**, not inferred.

### Endpoints and scoping

- Two region gateways: `https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway`
  and `https://han-1.api.vngcloud.vn/vserver/vserver-gateway`.
- **The console's host is the same backend.** Its
  `https://<region>.console.greennode.ai/vserver/iam-vserver-gateway` answers
  every path with byte-identical bodies to the gateway above (compared live on
  both regions: `dynamic-config`, networks, zones, projects, subnet list,
  detail and PATCH). Use the `api.vngcloud.vn` gateway for everything —
  including what a browser capture shows on the console host. vBackup is the
  one genuinely separate host (see *Snapshots*).
- `GET /v1/common/dynamic-config` is **not project-scoped** and answers a
  **bare object** with no envelope of any kind:
  `{"mtuConfig": [1500, 1450, 8500, 8950], "cidrNotationConfigs": ["/16", … "/28"]}`
  — the catalogue the console's create-VPC form reads; `get_vpc_config_options`
  exposes it.
- Nearly every path is project-scoped, and **path versions are mixed**: `/v2/{projectId}/...`
  for resources (servers, volumes, networks, secgroups, sshKeys, serverGroups…)
  and `/v1/{projectId}/...` for catalogues (zones, images, flavors, volume types,
  projects). Check per endpoint — there is no single rule.
- **project_id is region-scoped**: each region's gateway exposes a different
  project. `project.require_project_id` resolves it from `GET /v1/projects`
  (field `projectId`) and caches per (caller identity, region); the configured
  `GRN_PROJECT_ID` belongs to the service account's default region only.
- **Zone ids are human-readable strings**, not UUIDs: `HCM03-1A`, `HCM03-1B`,
  `HCM03-1C`, `HCM03-BKK-01` (a Bangkok local zone served by the HCM-3 gateway).
  Some zones are marked `isEnabled: false` — they exist but reject new resources.

### Envelope families (the single biggest source of parsing bugs)

| Family | Shape | Used by |
|---|---|---|
| v2 list | `{listData: [...], page, pageSize, totalItem, totalPage}` | `/v2/**` list endpoints |
| v2 detail / mutation | `{data: {...}}` | `/v2/**` get + most writes |
| v1 catalogue | `{success, errorCode, errorMsg, extra, <resource>: [...]}` | `/v1/**` — the array key **differs per endpoint** (`images`, `volumeTypeZones`, `volumeTypes`, `flavors`, `volumes`…) |
| snapshot list | `{items: [...], page, pageSize, **totalItems**, totalPages}` | `/v2/**/snapshots` — note the **plural** `totalItems`/`totalPages`, which no other collection uses |

The snapshot spelling matters: `fetch_all_items` reads the reported total to
decide whether a response was truncated, and a checker that only knows
`totalItem` treats every snapshot page as complete no matter how much was
withheld. `paging._total` accepts both.

Some `/v1` endpoints break the pattern again: `/v1/{pid}/zones` answers
`{data: [...]}`, while `/v1/{pid}/flavor_zones/families` and
`/v1/{pid}/flavor_zones/codes` return a **bare array**. `paging.as_list`
normalises all of these (explicit key → known keys → the single list-valued
field of a `success` envelope); `paging.unwrap` handles the `{data: {...}}` case.

**A v1 by-id GET still answers with a one-element array.**
`GET /v1/{pid}/flavors/{id}` returns `{success: true, flavors: [{...}]}` and
`GET /v1/{pid}/volume_types/{id}` returns `{success: true, volumeTypes: [{...}]}`
— a detail endpoint dressed as a list. Plain `unwrap` returns the envelope and
the model comes back **empty with no error**. `paging.unwrap_one` handles it,
gated on the `success` marker so an ordinary resource that happens to hold one
list (a route table with a single route) is not collapsed to that route.

### A create/update response is not shaped like the list it came from

Verified live on security groups and their rules: a `POST` answers with the
platform's **internal numeric key** in `id`, the id every other endpoint expects
in `uuid`, and the name under `<resource>Name` (`secgroupName`) instead of
`name`:

```
create -> {"data": {"id": 481287, "uuid": "secg-…", "secgroupName": "web", …}}
list   -> {"listData": [{"id": "secg-…", "name": "web", …}]}
```

A model that reads `id` first therefore fails validation on a **successful**
create (`Input should be a valid string`), which reads to an agent as "the
create failed" — it then retries and duplicates the resource. `models._common
._resource_id` prefers `uuid`, falls back to `id` and stringifies, so one model
serves both shapes; keep new models on it. Rule creates also name their parent
`secgroupUuid` where a list uses `remoteGroupId`.

### The ID field name differs per resource (verified live)

There is **no single id field**. Getting this wrong yields empty ids that fail
only later, at the call that consumes them — always check this table before
writing a `from_api`.

| Resource | Endpoint | ID field | Name field | List envelope |
|---|---|---|---|---|
| Zone | `/v1/{pid}/zones` | `uuid` | `name` | `{data}` |
| VPC | `/v2/{pid}/networks` | `id` | **`displayName`** | `{listData}` |
| Subnet | `/v2/{pid}/networks/{id}/subnets` | `uuid` | `name` | **bare array** |
| Security group | `/v2/{pid}/secgroups` | `id` | `name` | `{listData}` |
| Secgroup rule | `.../secGroupRules` | `id` | — | `{data}` |
| Server | `/v2/{pid}/servers` | `uuid` | `name` | `{listData}` |
| Volume | `/v2/{pid}/volumes` | `id` | `name` | `{listData}` |
| SSH key | `/v2/{pid}/sshKeys` | `id` | `name` | `{listData}` |
| Floating IP (WAN IP) | `/v2/{pid}/wanIps` | `uuid` | — (`ip`) | `{listData}` |
| Elastic NIC | `/v2/{pid}/network-interfaces-elastic` | `uuid` | `name` | `{listData}` |
| Placement group | `/v2/{pid}/serverGroups` | `uuid` | `name` | `{listData}` |
| DHCP option set | `/v2/{pid}/dhcp_option` | `uuid` | `name` | `{listData}` |
| Image | `/v1/{pid}/images/{os,gpu}` | `id` | `imageVersion` | `{success, images}` |
| Flavor | `/v1/{pid}/flavors/families/…` | **`flavorId`** | `name` | bare array |
| Volume type | `/v1/{pid}/{vtz}/volume_types` | `id` | `name` (= IOPS) | `{success, volumeTypes}` |

Further traps in the same family:

- **`GET /v2/{pid}/networks/{id}` returns the VPC object directly** — no `data`
  envelope, unlike most other v2 detail endpoints. Always go through
  `paging.unwrap`.
- **The subnet list is a bare array** even though the sibling VPC list uses the
  `listData` envelope.
- Placement groups carry **two** ids: the string `uuid` (use this) and a
  numeric `serverGroupId` (internal — do not expose it).
- Flavors report `zoneId: null` even when the request filtered by `zoneId`; the
  zone the caller asked for is the source of truth, not the response.
- A flavor's `metaData` is a **JSON string**, not an object, and holds
  `imageTypeSupport` — the list of image types that flavor can boot. Parse it
  defensively (`models._image_types_from_metadata`) and use it to catch an
  incompatible flavor/image pair before create_server fails.

### Three list endpoints REQUIRE their query params

`GET /v2/{pid}/route-table`, `/v2/{pid}/network-acl/list` and
`/v2/{pid}/peering` declare `name`, `page` and `size` as **required** query
params, and omitting any of them returns **`500 Internal server error`** — not
a 400, so it reads like an outage rather than a malformed request. An empty
`name` means "no filter". `paging.fetch_paged_items` always sends all three;
use it for these three collections and never the bare fetch.

### Pagination

- **Omitting `page`/`size` returns the full collection in one response**, and the
  envelope then reports `page=0, pageSize=0, totalPage=0` with
  `totalItem == len(listData)`.
- **Passing them does paginate**, 1-based: `page=1&size=2` → `page=1, pageSize=2,
  totalPage=10`. So paging is real, it is just off by default.
- `paging.fetch_all_items` relies on the single-call fast path but re-pages
  explicitly if a response ever reports `totalItem > len(listData)`, so lists
  never truncate silently as an account grows.

### Security-group rules (all verified live)

- **The path casing differs between operations**: listing is
  `GET .../secgroups/{id}/secGroupRules` (capital **G**), while create, detail,
  update and delete use `.../secgroupRules` (lowercase **g**). Using the wrong
  spelling returns **403 `IAM_PERMISSION_DENIED`**, not 404 — so a casing typo
  looks exactly like a permissions problem and will send you hunting in IAM.
- **The rule detail endpoint returns a one-element array**:
  `GET .../secgroupRules/{ruleId}` answers `{"data": [ {...} ]}`, not
  `{"data": {...}}`. `paging.unwrap_one` normalises it; using plain `unwrap`
  silently yields an object with empty fields.
- **For `icmp` the port range is an ICMP _type_ range, not ports** — the API's
  own "All ICMP" preset is `1-255`. Do not assume icmp rules are portless: real
  rules in the account carry `portRangeMin=1, portRangeMax=255`. `any` spans
  `0-65535`.
- `GET .../secgroupRules/samples` returns **30 named presets** (All TCP, All
  UDP, All ICMP, SSH, SSH VNG, SMTP, DNS, HTTP …) using different field names —
  `ipProtocol`, `fromPort`, `toPort`. Exposed as
  `list_security_group_rule_samples`; use it to turn "allow SSH" into the right
  protocol and port pair instead of guessing.
- **Only the description and tags of a rule are editable.** Direction,
  protocol, ports, ether type and remote CIDR are immutable — changing them
  means delete plus create.
- **`description` has a charset the API enforces**: letters, digits, spaces and
  `_ . @ -`, starting with a letter, up to 255 characters. A slash is rejected,
  so the obvious "allow SSH from 10.0.0.0/8" description fails with a 400 while
  the rule itself was fine. `models.requests.DESCRIPTION_RULE` states this on
  every description field.

### Server, volume and image traps (all verified live)

- **`GET .../servers/{id}/sec-groups` does not return security groups.** It
  returns the server's effective **rules**, split into `inbounds` and
  `outbounds`, each tagged with the owning group's *name* (`secGroupName`) and
  no group id. `list_server_security_groups` resolves those names against
  `/secgroups` so callers still get ids, and reports anything it could not match
  in `unresolved_group_names`.
- **`GET .../servers/{id}/console-url` answers `{"data": "<url string>"}`** —
  the envelope wraps a bare string, not an object.
- **`bootVolumeId` is `null` in the server *list* and filled only in the
  detail** — `list_servers` therefore always reports an empty `boot_volume_id`.
  Its docstring says so, because an empty value there means "not reported", not
  "no root disk".
- **Interfaces are spelled differently depending on where they come from.** A
  server's own interface links back with `serverUuid` / `networkUuid` /
  `subnetUuid` and carries a private `fixedIp`; an elastic interface uses
  `serverId` / `vpcId` and puts its **public** address in `ip` next to
  `elasticIpId`. Reading only one spelling leaves the links empty and reports a
  WAN address as a private IP — `NetworkInterfaceItem.from_api` handles both.
- **User images use different field names again**: the id is `uuid` (not `id`)
  and the size is `imageSize` (not `size`). Reading `id`/`size` yields an empty
  id and a zero size, silently.
- **`DELETE /v2/{pid}/servers/{id}` takes `deleteAllVolume`** (singular) —
  confirmed on a live delete: with the flag set, the root disk *and* an
  attached data volume both disappear from `list_volumes`.
- **A stopped server reports `STOPPED`, not `SHUTOFF`.** The OpenStack-style
  spelling never appears; polling for SHUTOFF waits forever. Every transition
  has its own status to poll through — `TURNING-OFF`, `STARTING`, `CREATING`,
  `DELETING` for servers, `CREATING`, `ATTACHING`, `DETACHING` for volumes.
- **`GET /v1/{pid}/volume_default_id` is project-wide, not zone-scoped**, and
  routinely names a tier that the target zone does not offer. Always confirm it
  against `list_volume_types(zone_id)` before using it in a create.
- **`PUT .../volumes/{id}/servers/{id}/attach` needs a body.** Sent without
  one it answers `400 {'message': None}` — an error with no message, which
  looks like a platform fault rather than a malformed request. An empty `{}` is
  enough, and the same holds for `/detach`. The power endpoints
  (`start`/`stop`/`reboot`) and the auto-snapshot toggles are happy without a
  body, so this is not a blanket rule.
- **A flavor is bound to a zone, and a subnet's zone can differ from its VPC's.**
  `create_server` rejects a flavor discovered for another zone with "This flavor
  don't support zone with ID …". Always take the zone from the chosen subnet.
- **`GET /v2/{pid}/tag/tag-key` returns a bare array of plain strings**, not
  objects; `tag-value` may return either. `_tag_strings` flattens both.
- **Platform tag keys are dotted** (`vng.vpc.id`, `vng.billing.product`) and go
  into the path of `.../tag-key/{key}/tag-value`. `validate_id` refuses a dot,
  which made every platform key unusable through `list_tag_values`; the tool
  uses `mcp_core.validate_path_segment` instead — dots and underscores allowed,
  separators and `..` still refused.
- **`PUT .../tag/resource/{id}` replaces the user tags** (verified: a second
  call drops the first tag, an empty list clears them) but the platform's own
  `vng.*` tags live outside that contract. They survive on their own, can show
  up twice in a read, and **cannot be resent** — `vng.createdBy` holds a colon,
  which the tag validation rejects, so "read the tags and send them all back"
  fails on exactly the resources that have system tags.
- **Tag values must be 3-255 characters.** A one-character value is refused
  with "Tag Value required value must length from 3 to 255".
- Server detail nests what callers need: the flavor id is at
  `flavor.flavorId`, the image id at `image.id`, and the private/public
  addresses come from `internalInterfaces[0].fixedIp` / `.floatingIp` — there
  are no top-level IP fields.
- `GET .../servers/{id}/actions` is a useful audit trail (`action`, `startTime`,
  `userAction`) that explains a state the user did not expect — exposed as
  `list_server_actions`.
- **`GET .../servers/{id}/console-log` answers `{"data": "<whole log>"}`** — one
  string holding the entire serial console, tens of thousands of characters on a
  long-running instance. `get_server_console_log` keeps the tail (`lines`,
  default 200) and reports `truncated`, so it cannot flood an agent's context.
- **`GET .../servers/subnets/{id}` and `.../secgroups/{id}/servers` return bare
  arrays** of full server objects — the same shape `list_servers` produces, no
  envelope.
- **`GET .../volumes/servers/{id}/boot` uses the v1 `success` envelope** with a
  `volumes` key, even though it lives under `/v2`.
- **`GET .../volumes/{id}/mapping` returns an object whose every field is
  `null`** except `uuid`. Not exposed — there is nothing in it.

### UserData

- **`userData` is the configuration hook a user image needs.** A custom image
  boots as a clone of the machine it was captured from — accounts, hostname and
  services included — so the `userName`/`userPassword` fields of the create body
  are not necessarily the credentials that end up working on it. Creating from
  `list_user_images` without asking the user for a first-boot script is the
  common way an agent hands back a machine nobody can log into, so the
  `create_server` docstring and the `create_server` guide both make the question
  mandatory in that branch.
- **The script type comes from the first line**, not from a field:
  `#cloud-config` (cloud-init YAML), `#!/bin/bash`, `#!/usr/bin/env python`,
  `#ps1` / `#ps1_sysnative` / `#ps1_x86` (PowerShell) and `rem cmd` (Windows
  batch). Cloud-config supports `write_files`, `set_timezone`, `set_hostname`,
  `ntp`, `groups`, `users` and `runcmd`, applied in that order, and the last two
  can reboot the instance.
- **`userDataBase64Encoded` only declares an encoding, it never applies one.**
  Base64-encoding the script and leaving the flag `false` makes the guest run
  the blob itself as a script — a silent failure with a healthy-looking
  instance.

### Snapshots

- Each server or volume has at most **one snapshot configuration** (the
  auto-snapshot policy object) and any number of **snapshot points** under it.
  The ids are different things: rollback and delete take the *point* id.
- `GET .../servers/{id}/snapshots/detail` answers a bare **`null`** when no
  configuration has ever been created. `SnapshotPolicyData.from_api` reports
  that as `configured=false` rather than raising, because "no policy" is a
  normal state an agent has to be able to see.
- **Snapshot schedules live on a second gateway.** The `snapshotPolicyId` three
  write bodies take is not listed anywhere under the vServer gateway; the
  catalogue is `GET /v1/snapshot-policies` on the **vBackup** host
  (`https://<region>.console.greennode.ai/vserver/vbackup-gateway`), served with
  the same IAM token and scoped by the caller — `backendId` / `projectId` query
  params are accepted but ignored. `VbackupClient` + `list_snapshot_policies`
  expose it; the cadence arrives as four enable flags with their own config
  objects and float numbers, flattened into one `schedule` string.
- Volume auto-snapshot is scoped by **both** the volume and the server it is
  attached to (`.../volumes/{id}/volume-snapshots/servers/{serverId}/enable-auto`),
  so an unattached volume cannot have a schedule.
- `delete_*_snapshot_policy` removes the configuration **and every point under
  it**; `disable_*_auto_snapshot` only stops the schedule. Agents conflate these
  — the docstrings point at each other explicitly.

### Network ACLs, route tables and peering

- An ACL rule's direction is the field **`type`** (`"inbound"` / `"outbound"`),
  and its action is `"pass"` / `"drop"` — not `allow`/`deny`. `seqNumber` is
  evaluation order and the **first match wins**, unlike a security group where
  every rule is considered.
- Every ACL ships with four platform rules: allow-all at seq 0 and deny-all at
  seq 2000, per direction. A custom rule outside 1-1999 never runs. **Nothing in
  the payload marks them as system-owned** — `NetworkAclRuleItem` infers it from
  the sequence number — and they are not immutable either: they are part of the
  replaceable set, so a rules PUT that omits them deletes them and leaves the
  subnet deny-all inbound. `_merge_with_platform_defaults` re-appends whichever
  of them the caller left out.
- **The ACL rules PUT wants lowercase protocols.** `"tcp"` is accepted, `"TCP"`
  — the spelling every read returns — fails with `Bad request: Invalid protocol`.
  The handler lowercases before sending. A port range whose ends are equal
  (`443-443`) is rejected as an invalid port; use the single port.
- **The rules PUT is asynchronous and can silently drop rules.** It answers
  `status: UPDATING` with empty rule lists, refuses a second write while busy
  ("The ACL … is busy doing something"), and a custom rule can be accepted and
  then not persisted. `update_network_acl_rules` waits for the ACL to settle,
  re-reads the rules and raises if anything it sent is missing, so a partial
  apply cannot pass for success. Creating custom ACL rules through this endpoint
  is **not yet working** — see the open question at the end of this file.
- ACLs are **stateless** — an inbound allow does not permit the reply. This is
  the most common way an ACL silently breaks a working system, so it is called
  out in the tool docstring and in the `network_acl` guide.
- `update_network_acl_rules` and `update_network_acl_subnets` both need the ACL
  id **in the body** (`aclId`) as well as in the path; the handlers inject it so
  callers cannot get the two out of sync.
- The ACL detail response nests rules under `aclPolicyRules` and associations
  under `subnetAssociationList`; the rules **sub-endpoint** returns them under
  `{data: [...]}` instead.
- Route table and ACL ids carry unusual prefixes: `rt-`, `netPolicy-` (the ACL
  detail also exposes a *different* `aclPolicyId` starting `acl-` — not the id
  any tool takes), `aclr-` for rules.
- **VPC peering has no create endpoint** — only list and delete; peerings are
  provisioned by GreenNode support. `list_peerings` says so, so an agent stops
  looking for a create tool.

### Interconnect and virtual IPs

- `remoteSubnets` on an interconnect connection is a **comma-separated string**,
  not an array — `InterconnectConnectionItem.from_api` splits it.
- `PUT .../interconnects/{id}/ping` is a diagnostic modelled as a write. It is
  registered as a write tool (so `--allow-write` gates it) but changes nothing.
- A VIP can only be shared by interfaces **in its own subnet**, and the
  candidate-interface endpoint does **not** filter by subnet — the caller must.
- The three address-pair create endpoints (`.../virtualIpAddress/{id}/addressPairs`,
  `.../virtual-subnets/{id}/addressPairs` and the public-VIP variant) are
  documented inconsistently. Live address pairs all carry `networkInterfaceId`,
  so all three send it. If a live create ever rejects it, this is the assumption
  to revisit.
- Private VIPs live under `/virtualIpAddress`, public ones under `/public-vips`,
  with parallel but **separate** create/delete/address-pair endpoints. The
  single `list_virtual_ips` returns both, distinguished by `type`.

### Write bodies

- `create_vpc` accepts only `name`, `cidr`, `mtu`, `zoneId`, `tags` — a VPC has
  no description and no "default" flag.
- Every editable resource treats its update body as a **full replacement of the
  editable fields** and requires `name` on each call — passing only a
  description would blank the name. The DTOs therefore mark `name` required on
  `UpdateVpcDto`, `UpdateSubnetDto` and `UpdateSecurityGroupDto`.
- `secondarySubnetRequests` on a subnet update replaces the whole set — and
  **so does leaving it out**; see *Secondary subnets* below.

### Secondary subnets (verified live on HAN, 2026-09-23)

- **`POST .../subnets/{id}/secondary-subnets` answers with the new range
  only**: `{"data": {"cidr", "name", "uuid": "sec-sub-…"}}`. Parsed as a
  `SubnetItem` it came back with empty `status` / `zone_id` / `vpc_id` and an
  `id` holding the *range's* uuid, which an agent then passed on as a subnet
  id. `create_secondary_subnet` now re-reads the parent subnet and returns
  that; the new range is in its `secondary_subnets`.
- **A subnet PATCH without `secondarySubnetRequests` deletes every secondary
  range.** A rename-only body `{"name": …}` wiped them all. `update_subnet`
  therefore re-sends the current set (read first) whenever the caller omits
  the key; an explicit `[]` still clears them, as asked.
- The PATCH list is matched to existing ranges by CIDR: entries sent **with or
  without** `uuid` keep their uuid, a missing entry is deleted, a new one is
  created. The console uses this PATCH to add ranges; the tool keeps the POST,
  which appends atomically and cannot drop a range another caller just added.
- `DELETE .../secondary-subnets/{sec-sub-id}` works and answers an empty body.
- **A subnet with a secondary range cannot be deleted**: `Cannot delete subnet:
  Having Virtual Subnet Address is using this subnet.` Remove the ranges first.
- Right after its last subnet goes, `DELETE` on the VPC can still answer
  `Cannot delete this VPC because it contains the subnet.` for a few seconds —
  retry rather than report failure.

### Creating a VPC: the console form is the spec

`create_vpc` mirrors the console's **Create VPC** dialog field for field, and
the product owner confirmed that form — not the laxer API — is the business
rule. Where the two differ, this server follows the form:

| Field | Console form | API alone (probed live on HAN) |
|---|---|---|
| Name | 5-50 chars, `a-z A-Z 0-9 _ -` | — |
| CIDR | a base address with a **fixed `/16`**, in `10.0.0.0-10.255.0.0`, `172.16.0.0-172.24.0.0` or `192.168.0.0` | also takes /20, /22, /24; refuses /17, /18, /26, /28 with a bare `Invalid CIDR.` |
| MTU | dropdown from `dynamic-config`, **1500 preselected** | omitted → silently **1500** |
| Zone | **mandatory** (`*`), grouped Availability / Local | omitted → silently the region's `isDefault` zone |
| Tags | optional | optional |

So `CreateVpcDto` requires `name`, `cidr`, `mtu` and `zoneId`, validates the
name pattern and the CIDR rule itself (accepting the bare base `10.5.0.0` the
form asks for and normalising it to `/16`), and `create_vpc` then checks:

- **MTU** against the live catalogue. The API enforces the same list
  (`mtu: MTU value must be one of the following: [1500, 1450, 8500, 8950];`)
  and does so *before* the quota check, so a quota error means the MTU was
  fine — but answers with a bare 400, hence the local check that names the
  allowed values.
- **Zone** against `list_zones`. The console shows `HCM-1C`, the API wants
  `HCM03-1C`; the error maps one to the other.
- **CIDR overlap** against the region's VPCs, read **uncached** and in every
  status (a DELETING VPC still holds its range; a VPC created seconds ago must
  count). The API refuses an overlap too, but only as `VPC is overlap with
  another.` — naming no VPC — so the tool names the clash and lists the CIDRs
  in use. Overlap is region-scoped: the same /16 exists in both HCM-3 and HAN
  on the verified account.

Other verified facts:

- Outside the three ranges the API answers `User can't use this range,
  reserved for system.` — 172.25.0.0 and above included. A base with host bits
  set (`10.50.7.0/16`) is a bare `Invalid CIDR.`.
- `dynamic-config`'s `cidrNotationConfigs` (`/16 … /28`) is the **subnet**
  rule: the subnet endpoint echoes that exact list when it refuses a prefix
  (`CIDR notation /25 is not allowed. Allowed CIDR notations are [...]`).
  `get_vpc_config_options` therefore returns it as `subnet_cidr_prefixes` and
  states the VPC size separately as `vpc_cidr_prefix: "/16"`. An earlier
  version returned it as plain `cidr_prefixes`, and agents offered /18, /20 …
  to users as VPC sizes — do not merge the two again.
- An MTU the catalogue no longer offers can still exist on an old VPC (`8000`
  on the verified account, now refused on create). The catalogue is "what you
  may create today".
- `mtu` round-trips: create response, list and detail all report it.
- **MTU and CIDR are immutable**; `UpdateVpcDto` carries neither.
- `mtu` is deliberately **not** a `Literal` — the endpoint is named
  `dynamic-config` and is the authority on its own value set.
- VPC quota is per project and region (8 in HCM-3, 5 in HAN on the verified
  account), a DELETING VPC still counts, and the message is `Bad request:
  Exceeded VPC quota. Current used: N, max: N.`
- A VPC reaches `ACTIVE` in seconds; a subnet cannot be deleted while still
  creating (`Cannot delete the creating subnet`).
- Zones carry `zoneType` (`AVAILABILITY` / `LOCAL`), `isDefault`, and a
  display `name` different from the id. "Contact to enable" in a description
  means support gates the zone per account even though `isEnabled` is true.

### Misc

- Auth is plain GreenNode IAM client-credentials (`mcp_core.auth.TokenManager`);
  no project header is needed because the project lives in the path. A bad token
  returns a normal 401 here (unlike vMonitor's `IAM_VALIDATION_ERROR`-as-500).
- Errors are `{message: ...}`-shaped and surface through `mcp_core`'s handler,
  e.g. `Resource not found: Cannot get server with id ...`.
- **Several deletes take a JSON body**, not query params — `DELETE /v2/{pid}/servers/{id}`
  with `{"deleteAllVolumes": bool}`, and the internal/external network-interface
  detaches. Use `VserverClient.delete_with_body`.
- Filter non-usable resources before returning them: VPCs / subnets / security
  groups are usable only when `status == "ACTIVE"`; flavors only when not
  `isSoldOut` and `remainingVms > 0`.
- Tag writes go through a **generic tagging endpoint**, not the resource itself:
  `PUT /v2/{pid}/tag/resource/{resourceId}` with `{resourceId, resourceType,
  tagRequestList[{key, value, isEdited}]}`. The list is a **full replacement**.
- Creating a DHCP option set always includes the two default DNS servers
  (`10.166.12.196`, `10.166.12.197`); at most 2 more may be added (4 total).

## Key files

| File | Purpose |
|------|---------|
| `server.py` | MCPServer entry point, handler registration, CLI flags, auth modes, SERVER_INSTRUCTIONS + runtime-mode addendum |
| `config.py` | VserverConfig + REGIONS endpoints; profile loading delegates to `mcp_core.config.load_profile` |
| `auth.py` / `validators.py` | Re-exports of the `mcp_core` TokenManager / `validate_id` |
| `client.py` | VserverClient extends `mcp_core.http.BaseClient`; adds `delete_with_body`. Plus `VbackupClient` for the snapshot schedules on the vBackup host |
| `project.py` | `require_project_id` — region-scoped project resolution + cache |
| `paging.py` | `as_list` / `unwrap` / `unwrap_one` / `fetch_all_items` / `fetch_paged_items` — the envelope + paging normalisers |
| `discovery_cache.py` | Package TTL config on top of `mcp_core.cache.DiscoveryCache` |
| `tool_annotations.py` | Shared `READ` / `WRITE` / `DESTRUCTIVE` ToolAnnotations |
| `auth_handler.py` | `get_access_token` |
| `guards.py` | `require_write` — the shared `--allow-write` gate |
| `zone_handler.py` | `list_zones` — reference handler for the porting pattern |
| `flavor_handler.py` | Flavor families, platform codes, flavors |
| `image_handler.py` | System image catalogue |
| `volumetype_handler.py` | Disk kinds and their IOPS tiers, per zone |
| `vpc_handler.py` / `subnet_handler.py` | VPC and subnet CRUD, plus `get_vpc_config_options` — everything the console's create-VPC form offers (MTU catalogue, the /16 rule, CIDRs in use) |
| `secgroup_handler.py` | Security groups, rules and the API's rule presets |
| `server_handler.py` | Instance lifecycle, power, interfaces, floating IPs, console |
| `volume_handler.py` | Block storage, including attach/detach |
| `userimage_handler.py` | User images, the generic tag endpoint, quota |
| `sshkey_handler.py` / `placementgroup_handler.py` | SSH keys, placement groups and policies |
| `networkinterface_handler.py` | Floating IPs, elastic IPs, elastic interfaces, DHCP option sets |
| `snapshot_handler.py` | Server and volume snapshot points, policies, rollback, sharing |
| `routetable_handler.py` | Route tables and their static routes |
| `networkacl_handler.py` | Subnet-level ACLs, their rules and subnet associations |
| `peering_handler.py` | VPC peering (list + delete; the API has no create) |
| `virtualip_handler.py` | Private/public VIPs, address pairs, secondary-subnet pairs |
| `interconnect_handler.py` | Interconnect circuits, VPC connections, packages, ping |
| `prompts_handler.py` | 10 guided flows (Vietnamese), served as prompts and via `get_feature_guide` |
| `models/` | Pydantic response models and write DTOs (structured output), split by domain |
| `auth_debug.py` | Opt-in redacted inbound-auth diagnostics (HTTP only) |

### The `models/` package

Response models and request DTOs are one import surface — everything is
re-exported from `models/__init__.py`, so `from greennode.vserver_mcp_server.models
import X` works for every name regardless of which file it lives in. Handlers
must import from the package, never from a submodule, so files can be
reorganised without touching them.

| Module | Holds |
|---|---|
| `_common.py` | `TagDto`, `_zone_id`, `_image_types_from_metadata` — helpers shared by several domains |
| `catalogue.py` | Zones, flavors, images, volume types, quota, tags |
| `compute.py` | Servers, interfaces, console, actions, SSH keys, placement groups |
| `storage.py` | Volumes, volume history, user images, persistent volumes |
| `network.py` | VPC, subnet, security groups and rules, floating IPs, DHCP options |
| `advanced_network.py` | Route tables, ACLs, peering, interconnects, virtual IPs |
| `snapshot.py` | Snapshot points and policies |
| `requests.py` | Every `*Dto` write body (`extra="forbid"`) |

The repo-wide conventions test resolves a package's models whether they live in
`models.py` or `models/__init__.py`, so the DTO rules keep applying after a
split.

## Guidance placement policy

Four layers, each with one job — do not let content drift between them:

| Layer | Carries | Never carries |
|---|---|---|
| Docstring / param description | The tool CONTRACT: semantics, ranges, formats, hard API constraints, cross-tool id mapping | Conversation choreography |
| `get_feature_guide` / prompts | Choreography: question order, ask-the-user steps, confirm gates | Per-tool field detail |
| `SERVER_INSTRUCTIONS` | Session-wide principles (region/zone model, id-first rendering, never-silent-defaults) | Per-tool detail |
| Error messages | The next step to fix THIS failure | — |

Guides live in `prompts_handler.py` as `_<name>_guidance()` functions and are
served twice from one source: as an MCP prompt the user loads, and through the
`get_feature_guide` tool an agent calls itself. Add a new one by appending the
function, a `_FEATURE_GUIDES` entry, the `Feature` literal value and a prompt
method.

## Deliberate scope limits

### Fields left out of write bodies

`create_server` and `create_volume` do **not** expose billing (period,
auto-renew, PoC, OS licence), backup plans, snapshot restore or marketplace
fields, even though the API accepts them. An agent should not be able to change
what a resource costs or restore from a backup point; those stay in the
console. The DTOs use `extra="forbid"`, so passing one is rejected rather than
silently forwarded.

### Endpoints deliberately not exposed

| Endpoints | Why not |
|---|---|
| Marketplace: `/v1/app-category`, `/app-instance/{}`, `/app-package/...`, `/app-template/{}`, `POST /v1/mp-migrate` (5) | An app catalogue whose payloads embed base64 logos, plus a billing migration. `create_server` deliberately excludes marketplace fields, so the catalogue has nothing to feed. |
| Server live migration: `PUT .../migrate`, `.../start-migrating`, `.../complete-migrating`, `GET /v1/{pid}/histories/server-migration` (4) | Operator-level host evacuation, not a tenant workflow. The history endpoint answers **500** on a normal account. |
| Custom flavors / flavor zones: `/flavors/customs*`, `/flavor_zones/customs*`, `/flavor_zones/product*`, `/flavor_zones/{id}`, `/flavor_zones/families/clusters`, `/volume_type_zones/{id}` (9) | Reserved-capacity and VKS-cluster allocation. Empty on a standard project, and `list_flavors` / `list_volume_types` already cover instance sizing. |
| `GET /v1/{pid}/volume_types` (project-wide) | Superseded by the zone-scoped `list_volume_types`; a volume can never use a type from another zone, so the unscoped list only invites the wrong id. |
| `GET /v1/{pid}/images/os_default` | Returns a non-JSON body. `list_images` covers image discovery. |
| `GET /v2/protocols` | 403 for a normal service account, and the protocol sets are already `Literal`s on the secgroup and ACL rule DTOs. |
| `GET /v2/{pid}/region`, `/region/{id}/users/validation` (2) | The region set is a fixed `Literal` in `config.py`; the response also carries a multi-thousand-entry `userId` array. |
| `GET /v1/projects`, `/v1/projects/{id}` (2) | Used internally by `project.require_project_id`; tools never take a project id, so exposing them would only invite one. |
| `GET /v2/{pid}/volumes/{id}/mapping` | Every field comes back `null` except `uuid`. |
| `GET /v2/{pid}/tag/{tagId}/resource-types/{types}` | `list_resource_tags` already answers the useful direction (resource -> tags). |

### Endpoints whose access depends on the caller's IAM policy

These are implemented and correct, but a caller whose IAM policy does not cover
them gets **403 `IAM_PERMISSION_DENIED`** rather than an empty result:
`list_interconnect_circuit_types`, `list_shared_server_snapshots`,
`get_volume_snapshot_policy`, `list_active_vpcs`, `list_elastic_ips`. Each
docstring says so, so a 403 is not mistaken for "nothing there".

The grants are per-endpoint, not per-feature: `get_server_snapshot_policy` and
`get_volume_snapshot_policy` are separate permissions, so one can succeed while
the other is denied for the same identity.

### Not in this API at all

**Bandwidth** (the console's Network -> Bandwidth section: shared, dedicated and
data-transfer packages) is a vServer feature with **no endpoint on this
gateway** — the only `bandwidth` field the API exposes is a flavor attribute (a
flavor's NIC speed, surfaced on `FlavorItem`). Bandwidth management is
console-only; porting it would need a separate API.

## Testing

```bash
cd src/vserver-mcp-server && uv run pytest tests/ -v
```

209 tests, `respx` for async HTTP mocking — no real API calls, no credentials.

| File | Covers |
|---|---|
| `test_server.py` | Config, auth, server construction, the write gate |
| `test_catalogue.py` | Zones, flavors, images, volume types |
| `test_network.py` | VPC (the console-form rules: name, /16 CIDR ranges, overlap, MTU, zone), subnet, security groups and rules |
| `test_compute.py` | Servers and volumes |
| `test_infrastructure.py` | SSH keys, placement groups, interfaces, DHCP, tags, guides |
| `test_snapshots.py` | Snapshot points, policies, rollback, the `items`/`totalItems` envelope |
| `test_advanced_network.py` | Route tables, ACLs, peering, VIPs, interconnects |
| `test_gaps.py` | The v1 by-id envelope, console log, boot volume, tier change, secondary subnets (create returns the parent; a rename keeps the ranges), tag catalogue |

`scripts/smoke_test.py` drives the read-only tools over the real MCP protocol
against a live gateway; `scripts/auth-debug-local.sh` exercises the
`--auth-debug` diagnostic locally. Both need credentials in `~/.greennode`.

Live verification so far: every **read** tool has been run against a real HCM-3
gateway (except the IAM-gated ones above, which depend on the caller's policy).
Write tools have had a full create → update → delete cycle on throwaway
resources: instances (create, power, delete with disks), volumes (create,
attach, detach, delete), subnets and secondary subnets, network ACLs, route
tables, security groups and rules, SSH keys, placement groups, DHCP option
sets, snapshot configurations and tags. Floating-IP and elastic-interface
writes remain mock-tested (both are billable), and custom ACL rules do not
persist — see *Known open question*.

**VPC creation is verified live** (HAN, 2026-09-22/23): create with a
non-default MTU, poll to ACTIVE, subnet create/delete inside it, and delete —
plus the MTU, CIDR-prefix, range and overlap probes written up under
*Creating a VPC*. Every test resource was removed afterwards. `GET /v1/common/dynamic-config` is
verified on both regions, and `mtu` comes back on a real VPC in **both** the
list and the detail response (unlike `bootVolumeId`, which the list withholds).

Credentials, vendor API dumps and third-party checkouts stay out of git; the
package `.gitignore` blocks the usual filenames. Never commit them and never
quote real ids, addresses or account names into code, tests or docs — use the
documentation ranges (`10.0.0.0/8`, `192.0.2.0/24`) and placeholder ids.

## Relationship with greennode-cli

Tool names mirror `greennode-cli` command names where one exists
(`list-servers` -> `list_servers`), per the repo-root convention. The CLI is the
smaller surface: it has no snapshots, route tables, network ACLs, peering,
interconnects or virtual IPs, and no console log, boot-volume lookup, volume
history, secondary subnets or tag catalogue. This server talks to the API
directly — async, structured Pydantic output, `allow_write` gating, discovery
caching — rather than shelling out to a binary, and **what the live gateway
returns is the specification** whenever documentation and behaviour disagree.

## Known open question

**Custom network-ACL rules do not persist.** `PUT .../network-acl/{id}/rules`
accepts a body whose rules mirror the read shape exactly (`type`, `seqNumber`,
lowercase `protocol`, `port`, `source`, `action`), answers `UPDATING`, and
settles with the custom rule gone — tried at several sequence numbers, with and
without the platform rules alongside, with single ports and ranges. Everything
else on the ACL works (create, subnet association, delete, and the platform
rules round-trip untouched).

`update_network_acl_rules` therefore fails loudly instead of reporting a
success it cannot verify. Closing this needs the request the console itself
sends when a rule is added — capture it from the browser's network tab and
compare the body field by field.
