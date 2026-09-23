"""VPC (network) management for the vServer MCP server.

A VPC is the outermost network container: every subnet, and therefore every
server, lives inside one. Mirrors the `grn vserver vpc` command group.
"""

from __future__ import annotations

import ipaddress
from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    VPC_CIDR_PREFIX,
    VPC_CIDR_RANGES,
    VPC_NAME_RULE,
    CreateVpcDto,
    UpdateVpcDto,
    VpcCidrInUse,
    VpcConfigOptionsData,
    VpcItem,
    VpcListData,
)
from greennode.vserver_mcp_server.paging import fetch_all_items, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from greennode.vserver_mcp_server.zone_handler import fetch_zone_list
from pydantic import Field


class VpcHandler:
    """Register and serve VPC MCP tools."""

    def __init__(
        self,
        mcp,
        config: VserverConfig,
        client: VserverClient,
        cache: DiscoveryCache,
        allow_write: bool = False,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache
        self.allow_write = allow_write

        self.mcp.tool(name="list_vpcs", annotations=READ)(self.list_vpcs)
        self.mcp.tool(name="get_vpc", annotations=READ)(self.get_vpc)
        self.mcp.tool(name="list_active_vpcs", annotations=READ)(self.list_active_vpcs)
        self.mcp.tool(name="get_vpc_config_options", annotations=READ)(self.get_vpc_config_options)

        if self.allow_write:
            self.mcp.tool(name="create_vpc", annotations=WRITE)(self.create_vpc)
            self.mcp.tool(name="update_vpc", annotations=WRITE)(self.update_vpc)
            self.mcp.tool(name="enable_vpc_dns", annotations=WRITE)(self.enable_vpc_dns)
            self.mcp.tool(name="delete_vpc", annotations=DESTRUCTIVE)(self.delete_vpc)

    async def list_vpcs(
        self,
        name_filter: str | None = Field(
            None, description="Optional substring match on the VPC name, applied by the API."
        ),
        include_inactive: bool = Field(
            False,
            description=(
                "Include VPCs that are not ACTIVE (CREATING, DELETING, ERROR). Off by "
                "default because only ACTIVE VPCs can host new subnets or servers."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> VpcListData:
        """List the VPCs (networks) in the project.

        Returns {region, vpcs[{id, name, cidr, status, zone_id, mtu,
        dhcp_option_id, route_table_id, dns_status}]}.

        ## Workflow
        - Step 1 of the create_server flow: present this list and let the user
          choose. IMPORTANT: do NOT pick a VPC silently when more than one exists.
        - Use the chosen `id` as `networkId` in create_server, and as `vpc_id`
          in list_subnets to enumerate its subnets.
        """
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[VpcItem]:
            params = {"name": name_filter} if name_filter else None
            raw = await fetch_all_items(
                self.client, f"/v2/{pid}/networks", region=region, params=params
            )
            return [VpcItem.from_api(v) for v in raw]

        key = ("list_vpcs", resolved_region, pid, name_filter)
        vpcs = await self.cache.get_or_fetch("list_vpcs", key, fetch, refresh)

        if not include_inactive:
            vpcs = [v for v in vpcs if v.status == "ACTIVE"]
        return VpcListData(region=resolved_region, vpcs=vpcs)

    async def get_vpc(
        self,
        vpc_id: str = Field(..., description="VPC ID from list_vpcs."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VpcItem:
        """Get one VPC by id.

        Returns the same fields as list_vpcs for a single VPC. Use it to confirm
        a VPC reached ACTIVE after create_vpc, or to read its `cidr` before
        choosing a subnet CIDR inside it.
        """
        validate_id(vpc_id, "vpc_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v2/{pid}/networks/{vpc_id}", region=region)
        return VpcItem.from_api(unwrap(data) or {})

    async def get_vpc_config_options(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the MTU catalogue cache."),
    ) -> VpcConfigOptionsData:
        """Get everything the console's create-VPC form offers, for one region.

        Returns {region, name_rule, mtu_options (BYTES), recommended_mtu,
        vpc_cidr_prefix, vpc_cidr_ranges, vpc_cidrs_in_use[{id, name, cidr,
        status}], subnet_cidr_prefixes}.

        - A VPC is ALWAYS `/16` (`vpc_cidr_prefix`). There is no size to choose —
          ask the user for a base address inside `vpc_cidr_ranges` instead.
        - `vpc_cidrs_in_use` is read live, every status included: a new VPC
          must not overlap any of them.
        - `subnet_cidr_prefixes` is for create_subnet. Never offer it as a VPC
          size.

        ## Workflow
        - Call this FIRST when the user wants a VPC, then list_zones.
        - Show the CIDRs in use so the user picks a free /16 knowingly, and
          offer `recommended_mtu` (the console's preselected value) as the
          suggested MTU — still ask; the MTU is permanent.
        """
        resolved_region = region or self.config.default_region
        catalogue = await self._mtu_catalogue(region=region, refresh=refresh)
        pid = await require_project_id(self.config, self.client, region)
        return VpcConfigOptionsData.from_api(
            catalogue,
            resolved_region,
            name_rule=VPC_NAME_RULE,
            vpc_cidr_prefix=VPC_CIDR_PREFIX,
            vpc_cidr_ranges=list(VPC_CIDR_RANGES),
            in_use=await self._cidrs_in_use(pid, region),
        )

    async def _mtu_catalogue(self, region: str | None = None, refresh: bool = False) -> dict:
        """Fetch the raw dynamic-config catalogue (cached per region).

        Same gateway as everything else, but **not** project-scoped and
        answered as a bare object with no envelope.
        """
        resolved_region = region or self.config.default_region

        async def fetch() -> dict:
            raw = await self.client.get("/v1/common/dynamic-config", region=region)
            return raw if isinstance(raw, dict) else {}

        key = ("get_vpc_config_options", resolved_region)
        return await self.cache.get_or_fetch("get_vpc_config_options", key, fetch, refresh)

    async def _cidrs_in_use(self, pid: str, region: str | None) -> list[VpcCidrInUse]:
        """Read every VPC of the region, uncached and whatever its status.

        Deliberately not the list_vpcs cache: a VPC created a moment ago must
        count, and so must one still CREATING or DELETING.
        """
        raw = await fetch_all_items(self.client, f"/v2/{pid}/networks", region=region)
        vpcs = [VpcItem.from_api(v) for v in raw if isinstance(v, dict)]
        return [VpcCidrInUse(id=v.id, name=v.name, cidr=v.cidr, status=v.status) for v in vpcs]

    async def _check_mtu(self, body: CreateVpcDto, region: str | None) -> None:
        """Reject an MTU the catalogue does not offer, naming the ones it does.

        The API checks too (before quota, even), but answers with a bare 400.
        """
        catalogue = await self._mtu_catalogue(region=region)
        offered = VpcConfigOptionsData.from_api(
            catalogue,
            region or self.config.default_region,
            name_rule=VPC_NAME_RULE,
            vpc_cidr_prefix=VPC_CIDR_PREFIX,
            vpc_cidr_ranges=list(VPC_CIDR_RANGES),
        ).mtu_options
        if offered and body.mtu not in offered:
            raise ValueError(
                f"Invalid mtu {body.mtu}. The platform accepts {offered} (bytes) — see "
                "get_vpc_config_options."
            )

    async def _check_zone(self, body: CreateVpcDto, region: str | None) -> None:
        """Reject a zone id the region does not have, naming the real ones.

        The likeliest mistake is the console's display name (HCM-1C) where the
        id (HCM03-1C) belongs.
        """
        zones = (await fetch_zone_list(self.config, self.client, self.cache, region=region)).zones
        if any(z.id == body.zoneId for z in zones):
            return
        by_name = next((z for z in zones if z.name == body.zoneId), None)
        hint = f" '{body.zoneId}' is a display name — its id is '{by_name.id}'." if by_name else ""
        raise ValueError(
            f"Unknown zoneId '{body.zoneId}' in region {region or self.config.default_region}."
            f"{hint} Valid ids: {[z.id for z in zones]} (see list_zones)."
        )

    @staticmethod
    def _check_no_overlap(body: CreateVpcDto, in_use: list[VpcCidrInUse]) -> None:
        """Refuse a CIDR that overlaps a VPC already in the region.

        The API refuses it too, but only as "VPC is overlap with another." —
        without saying which VPC, so the user cannot pick a better one.
        """
        wanted = ipaddress.IPv4Network(body.cidr)
        clashes = []
        for vpc in in_use:
            try:
                if wanted.overlaps(ipaddress.IPv4Network(vpc.cidr, strict=False)):
                    clashes.append(vpc)
            except ValueError:
                continue
        if clashes:
            detail = ", ".join(f"{v.name} ({v.id}, {v.cidr}, {v.status})" for v in clashes)
            taken = sorted({v.cidr for v in in_use if v.cidr})
            raise ValueError(
                f"{wanted} overlaps an existing VPC in this region: {detail}. "
                f"CIDRs already in use: {taken}. Ask the user for another /16 base address."
            )

    async def create_vpc(
        self,
        body: CreateVpcDto = Field(..., description="VPC to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VpcItem:
        """Create a VPC (network), exactly as the console's create-VPC form does.

        ## Requirements
        - Requires `--allow-write`. Provisions billable infrastructure.
        - Five inputs, all decided by the user: `name` (5-50 chars, letters,
          digits, `_`, `-`), `cidr` (a **/16** base address in 10.0-10.255,
          172.16-172.24 or 192.168 — there is no other size), `mtu` (bytes, from
          get_vpc_config_options), `zoneId` (from list_zones, mandatory) and
          optional `tags`.
        - The CIDR must not overlap any VPC of the region; this tool re-reads
          them live and refuses an overlap, naming the VPC it clashes with.
        - **CIDR and MTU are permanent** — update_vpc edits name and tags only.
        - The API would silently default `mtu` to 1500 and the zone to the
          region's default; this tool requires both instead.

        ## Workflow
        - get_vpc_config_options → list_zones → ask for every input above,
          showing the CIDRs in use and offering `recommended_mtu` (1500) as
          the suggested MTU → summarise → explicit confirmation → create.
        - VPC quota is per project and region, and a DELETING VPC still counts.
        - A new VPC starts in CREATING; poll get_vpc until ACTIVE (seconds),
          then create_subnet before any server.
        """
        require_write(self.allow_write)
        await self._check_mtu(body, region)
        await self._check_zone(body, region)
        pid = await require_project_id(self.config, self.client, region)
        self._check_no_overlap(body, await self._cidrs_in_use(pid, region))
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(f"/v2/{pid}/networks", region=region, json=payload)
        self.cache.invalidate("list_vpcs")
        return VpcItem.from_api(unwrap(data) or {})

    async def update_vpc(
        self,
        vpc_id: str = Field(..., description="VPC ID from list_vpcs."),
        body: UpdateVpcDto = Field(..., description="Fields to update."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VpcItem:
        """Rename a VPC or replace its tags.

        ## Requirements
        - Requires `--allow-write`.
        - `name` is mandatory on every call: the API treats this as a full
          replacement of the editable fields, so pass the current name when you
          only intend to change tags.
        - The CIDR and the MTU of an existing VPC cannot be changed: both are
          fixed at creation time and this body carries neither. Changing either
          means creating a new VPC and moving the workload.
        """
        require_write(self.allow_write)
        validate_id(vpc_id, "vpc_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.patch(f"/v2/{pid}/networks/{vpc_id}", region=region, json=payload)
        self.cache.invalidate("list_vpcs")
        return VpcItem.from_api(unwrap(data) or {})

    async def delete_vpc(
        self,
        vpc_id: str = Field(..., description="VPC ID from list_vpcs."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a VPC. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - The VPC must be empty: delete its subnets first, and those subnets
          must have no servers or network interfaces attached. The API rejects
          the call otherwise.

        ## Workflow
        - Show the user the VPC's id, name and CIDR and get explicit
          confirmation before calling.
        - Call list_subnets first so the user sees what has to go first.
        """
        require_write(self.allow_write)
        validate_id(vpc_id, "vpc_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/networks/{vpc_id}", region=region)
        self.cache.invalidate("list_vpcs")
        return f"VPC {vpc_id} deleted."

    async def list_active_vpcs(
        self,
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VpcListData:
        """List only the VPCs the API itself reports as usable.

        list_vpcs filters on `status == "ACTIVE"` client-side; this asks the API
        for its own answer. Use it when list_vpcs shows a VPC as ACTIVE but a
        create call still rejects it — the two disagreeing is the signal that
        the VPC is mid-transition.

        Note: this endpoint is permission-gated. A `403 IAM_PERMISSION_DENIED`
        means the caller's IAM policy lacks the right, not that no VPC is
        active; fall back to list_vpcs.
        """
        pid = await require_project_id(self.config, self.client, region)
        raw = await fetch_all_items(self.client, f"/v2/{pid}/networks/active", region=region)
        return VpcListData(
            region=region or self.config.default_region,
            vpcs=[VpcItem.from_api(v) for v in raw],
        )

    async def enable_vpc_dns(
        self,
        vpc_id: str = Field(..., description="VPC ID from list_vpcs."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> VpcItem:
        """Turn on private DNS resolution inside a VPC.

        ## Requirements
        - Requires `--allow-write`.
        - Instances get internal name resolution for resources in the VPC. This
          endpoint only **enables** it — vServer exposes no matching disable, so
          treat it as one-way.
        - Check `dns_status` in get_vpc first; enabling twice is pointless.

        ## Workflow
        - Instances already running may need their resolver refreshed (a DHCP
          renew or a reboot) before they pick the change up. Say so.
        """
        require_write(self.allow_write)
        validate_id(vpc_id, "vpc_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.patch(f"/v2/{pid}/networks/{vpc_id}/enableDns", region=region)
        self.cache.invalidate("list_vpcs")
        return VpcItem.from_api(unwrap(data) or {})
