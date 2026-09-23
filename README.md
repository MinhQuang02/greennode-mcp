# GreenNode MCP

A monorepo of **MCP (Model Context Protocol) servers** for GreenNode products,
organized as a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/).
Each product is an independent project under `src/`, sharing the `greennode`
Python namespace.

## Servers

| Project | Package | Description |
|---------|---------|-------------|
| [`src/mcp-core`](src/mcp-core) | `greennode.mcp_core` | Shared core (config/profile loading, IAM auth, HTTP client with retry/401, validators, discovery cache) — product servers import it instead of copying plumbing. |
| [`src/vks-mcp-server`](src/vks-mcp-server) | `greennode.vks_mcp_server` | **41 tools + 3 prompts** for managing VKS (GreenNode Kubernetes Service): clusters, node groups, resource discovery (name→ID), quota, on-demand creation guides, and in-cluster Kubernetes resources. EKS-style `verb_noun` tool names, structured (JSON) outputs, MCP tool annotations, read-only by default. |
| [`src/vserver-mcp-server`](src/vserver-mcp-server) | `greennode.vserver_mcp_server` | **184 tools + 10 prompts** for managing vServer (compute/IaaS): instances (lifecycle, power, resize, console, serial log), block storage and snapshots (points, schedules, rollback), networking (VPC, subnet, security group, network ACL, route table, peering, interconnect, virtual IP, floating IP, elastic interface, DHCP), SSH keys, placement groups, user images, tags and quota. Region-scoped (`HCM-3`/`HAN`), structured outputs, MCP tool annotations, read-only by default. |
| [`src/vmonitor-mcp-server`](src/vmonitor-mcp-server) | `greennode.vmonitor_mcp_server` | **213 tools + 11 prompts** for managing vMonitor (observability): dashboards, widgets, metric/statistics queries, alarms (metric/log/change), infrastructure hosts, logs (search/export/pipelines), notifications, quota/usage, and synthetic uptime. Covers five vMonitor APIs behind one IAM auth; global (no region); structured outputs, MCP tool annotations, read-only by default. |
| [`src/vbackup-mcp-server`](src/vbackup-mcp-server) | `greennode.vbackup_mcp_server` | **68 tools + 9 prompts** for managing vBackup (scheduled backups for vServer and vDB): backup servers and backup databases, policies and schedules, destination vaults, restore points and their per-disk contents, backup/restore/audit history including server migration, volume usage. Modelled on the vServer server — region-scoped (`HCM-3`/`HAN`), structured outputs, MCP tool annotations, read-only by default (41 of the 68 tools). |

More servers will be added as sibling projects under `src/`.

## Layout

```
greennode-mcp/
├── pyproject.toml            # uv workspace root (members = ["src/*"])
├── scripts/new_server.py     # scaffold a new product server
├── templates/new-server/     # scaffolding template
├── tests/                    # cross-package convention tests (CI "Conventions" job)
└── src/
    ├── mcp-core/             # shared core (greennode.mcp_core)
    ├── vks-mcp-server/       # one product = one independent project
    │   ├── pyproject.toml
    │   ├── greennode/vks_mcp_server/
    │   ├── tests/
    │   ├── Dockerfile
    │   ├── README.md
    │   └── CLAUDE.md
    ├── vmonitor-mcp-server/  # same layout, one project per product
    ├── vserver-mcp-server/
    └── vbackup-mcp-server/
```

Each product project is self-contained (own `pyproject.toml`, `tests/`,
`Dockerfile`, `README.md`, `CLAUDE.md`) and shares the `greennode` namespace.

## Getting started

Requires Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/). Credentials come
from `~/.greennode/` (shared with greennode-cli) or `GRN_*` env vars — see each
server's README for details.

```bash
# 1. Install all workspace members into a shared environment
uv sync

# 2. Run a server (read-only by default; add --allow-write to enable mutations)
uv run vks-mcp-server                 # VKS (Kubernetes)
uv run vmonitor-mcp-server            # vMonitor (observability)
uv run vserver-mcp-server             # vServer (compute)
uv run vbackup-mcp-server             # vBackup (backups for vServer and vDB)

# HTTP transport instead of stdio (e.g. for a Gateway / container)
uv run vmonitor-mcp-server --transport streamable-http --host 0.0.0.0 --port 8080
```

Each server speaks MCP over **stdio** by default (for Claude Desktop, Cursor,
etc.) or **streamable-http** for networked deployments. Every server ships a
`Dockerfile` that serves streamable-http on port 8080. See the per-project
README for the full run matrix, MCP client config, and tool reference:
[`vks`](src/vks-mcp-server/README.md) · [`vserver`](src/vserver-mcp-server/README.md) · [`vmonitor`](src/vmonitor-mcp-server/README.md) · [`vbackup`](src/vbackup-mcp-server/README.md).

## Adding a new product server

```bash
uv run python scripts/new_server.py <product>    # e.g. vdb
```

Scaffolds `src/<product>-mcp-server/` from `templates/new-server` (working
example tool + tests + Dockerfile, conventions baked in), registers it with
release-please, and prints the remaining steps. CI (lint/test/build) discovers
the new package automatically. Agent guidance lives in `.claude/skills/`
(`new-mcp-server`, `release-mcp`) and the tiered CLAUDE.md files (repo root =
monorepo conventions; each package has its own).

See each project's own README for configuration and usage.

**Note:** Create/update tools use typed Pydantic request bodies (structured DTOs) for better code-mode support and self-documenting schemas.

### Diagnostics: `--auth-debug` (temporary, opt-in)

`--auth-debug` (env `GRN_MCP_AUTH_DEBUG=1`) makes the HTTP transport log a
**redacted** summary of every inbound request and expose an unauthenticated
`GET /whoami` that echoes the same summary. It is meant for measuring what an
upstream (e.g. the MCP Gateway) actually sends — token scheme, JWT header
(`alg`/`kid`), allow-listed claims (`iss`/`aud`/`sub`/`scope`/...), and any
`X-GreenNode-*` / `X-Forwarded-*` identity headers.

It **never verifies** signatures and **never logs the full token** (only a
6-char prefix + length). It is **off by default** and **must not be enabled in
production**. It observes requests only and never affects how they are authenticated.

## Development

```bash
uv sync --all-packages                            # one env for every member
cd src/vks-mcp-server && uv run pytest tests/ -v  # a product's tests
uv run pytest tests/ -v                           # repo-wide convention tests
uv run ruff check . && uv run ruff format --check .
```

## Documentation map

| Document | What it covers |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Monorepo conventions, written for AI coding agents working in this repo |
| `src/<product>-mcp-server/README.md` | Tool reference and usage for one product |
| `src/<product>-mcp-server/CLAUDE.md` | That product's API quirks and agent guidance |

## Contributing

Issues and pull requests are welcome. PR titles follow
[Conventional Commits](https://www.conventionalcommits.org/); releases are
automated, so versions, CHANGELOGs and tags are never edited by hand.

## License

Apache-2.0. See [LICENSE](LICENSE).
