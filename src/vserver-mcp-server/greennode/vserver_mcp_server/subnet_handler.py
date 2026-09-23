"""Subnet management for the vServer MCP server.

A subnet lives inside a VPC and pins the availability zone of every server
placed on it. Mirrors the `grn vserver subnet` command group.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.guards import require_write
from greennode.vserver_mcp_server.models import (
    CreateSecondarySubnetDto,
    CreateSubnetDto,
    SubnetItem,
    SubnetListData,
    UpdateSubnetDto,
)
from greennode.vserver_mcp_server.paging import as_list, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


class SubnetHandler:
    """Register and serve subnet MCP tools."""

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

        self.mcp.tool(name="list_subnets", annotations=READ)(self.list_subnets)
        self.mcp.tool(name="get_subnet", annotations=READ)(self.get_subnet)

        if self.allow_write:
            self.mcp.tool(name="create_subnet", annotations=WRITE)(self.create_subnet)
            self.mcp.tool(name="update_subnet", annotations=WRITE)(self.update_subnet)
            self.mcp.tool(name="create_secondary_subnet", annotations=WRITE)(
                self.create_secondary_subnet
            )
            self.mcp.tool(name="delete_secondary_subnet", annotations=DESTRUCTIVE)(
                self.delete_secondary_subnet
            )
            self.mcp.tool(name="delete_subnet", annotations=DESTRUCTIVE)(self.delete_subnet)

    async def list_subnets(
        self,
        vpc_id: str = Field(..., description="VPC ID from list_vpcs."),
        include_inactive: bool = Field(
            False,
            description=(
                "Include subnets that are not ACTIVE. Off by default because only "
                "ACTIVE subnets can receive new servers."
            ),
        ),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
        refresh: bool = Field(False, description="Bypass the cache and refetch from vServer."),
    ) -> SubnetListData:
        """List the subnets of a VPC.

        Returns {vpc_id, subnets[{id, name, cidr, status, zone_id, vpc_id,
        secondary_subnets}]}.

        ## Workflow
        - Step 2 of the create_server flow, after the user picks a VPC.
          Present the list and let the user choose.
          IMPORTANT: do NOT pick a subnet silently when more than one exists.
        - The chosen subnet's `zone_id` determines the server's availability
          zone, so it also scopes list_flavors and list_volume_types — read the
          zone from here rather than asking the user twice.
        - Use the chosen `id` as `subnetId` in create_server.
        """
        validate_id(vpc_id, "vpc_id")
        pid = await require_project_id(self.config, self.client, region)
        resolved_region = region or self.config.default_region

        async def fetch() -> list[SubnetItem]:
            raw = await self.client.get(f"/v2/{pid}/networks/{vpc_id}/subnets", region=region)
            return [SubnetItem.from_api(s) for s in as_list(raw)]

        key = ("list_subnets", resolved_region, pid, vpc_id)
        subnets = await self.cache.get_or_fetch("list_subnets", key, fetch, refresh)

        if not include_inactive:
            subnets = [s for s in subnets if s.status == "ACTIVE"]
        return SubnetListData(vpc_id=vpc_id, subnets=subnets)

    async def get_subnet(
        self,
        vpc_id: str = Field(..., description="VPC ID the subnet belongs to."),
        subnet_id: str = Field(..., description="Subnet ID from list_subnets."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SubnetItem:
        """Get one subnet by id.

        Both ids are required because the path is nested under the VPC. Use it
        to confirm a subnet reached ACTIVE after create_subnet, or to read its
        `zone_id` before listing flavors and volume types.
        """
        validate_id(vpc_id, "vpc_id")
        validate_id(subnet_id, "subnet_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(
            f"/v2/{pid}/networks/{vpc_id}/subnets/{subnet_id}", region=region
        )
        return SubnetItem.from_api(unwrap(data) or {})

    async def create_subnet(
        self,
        vpc_id: str = Field(..., description="VPC ID to create the subnet in."),
        body: CreateSubnetDto = Field(..., description="Subnet to create."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SubnetItem:
        """Create a subnet inside a VPC.

        ## Requirements
        - Requires `--allow-write`.
        - `cidr` must be a strict sub-range of the VPC's CIDR and must not
          overlap an existing subnet — read the VPC's `cidr` via get_vpc and the
          taken ranges via list_subnets before choosing.
        - `zoneId` must come from list_zones and fixes the zone of every server
          placed on this subnet; it cannot be changed later.

        ## Workflow
        - Ask the user for the zone and the CIDR; do not invent either.
        - Poll get_subnet until `status` is ACTIVE before using it in
          create_server.
        """
        require_write(self.allow_write)
        validate_id(vpc_id, "vpc_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        data = await self.client.post(
            f"/v2/{pid}/networks/{vpc_id}/subnets", region=region, json=payload
        )
        self.cache.invalidate("list_subnets")
        return SubnetItem.from_api(unwrap(data) or {})

    async def update_subnet(
        self,
        vpc_id: str = Field(..., description="VPC ID the subnet belongs to."),
        subnet_id: str = Field(..., description="Subnet ID from list_subnets."),
        body: UpdateSubnetDto = Field(..., description="Fields to update."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SubnetItem:
        """Rename a subnet or replace its secondary CIDRs and tags.

        ## Requirements
        - Requires `--allow-write`.
        - `name` is mandatory on every call — pass the current name when you
          only mean to change the secondary subnets or tags.
        - `secondarySubnetRequests` is a **full replacement** when you pass it:
          any secondary CIDR missing from the list is removed, and `[]` removes
          them all. Leave it out to keep the current set — the API itself would
          delete every range on a PATCH without it, so this tool re-sends them.
        - To add ONE range, prefer create_secondary_subnet; to drop one,
          delete_secondary_subnet. Both leave the others untouched.
        - The primary `cidr` and the zone of an existing subnet are immutable.
        """
        require_write(self.allow_write)
        validate_id(vpc_id, "vpc_id")
        validate_id(subnet_id, "subnet_id")
        pid = await require_project_id(self.config, self.client, region)
        payload = body.model_dump(exclude_none=True)
        if body.secondarySubnetRequests is None:
            # Verified live: a PATCH without this key deletes every secondary
            # range, so a plain rename must carry the current set back.
            current = SubnetItem.from_api(await self._subnet_raw(pid, vpc_id, subnet_id, region))
            payload["secondarySubnetRequests"] = [
                {"name": s.name, "cidr": s.cidr, "uuid": s.id} for s in current.secondary_subnets
            ]
        data = await self.client.patch(
            f"/v2/{pid}/networks/{vpc_id}/subnets/{subnet_id}", region=region, json=payload
        )
        self.cache.invalidate("list_subnets")
        return SubnetItem.from_api(unwrap(data) or {})

    async def delete_subnet(
        self,
        vpc_id: str = Field(..., description="VPC ID the subnet belongs to."),
        subnet_id: str = Field(..., description="Subnet ID from list_subnets."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a subnet. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - The subnet must have no servers, network interfaces or secondary
          subnets; the API rejects the call otherwise ("Having Virtual Subnet
          Address is using this subnet" for a leftover secondary range).

        ## Workflow
        - Show the user the subnet's id, name and CIDR and get explicit
          confirmation before calling.
        """
        require_write(self.allow_write)
        validate_id(vpc_id, "vpc_id")
        validate_id(subnet_id, "subnet_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(f"/v2/{pid}/networks/{vpc_id}/subnets/{subnet_id}", region=region)
        self.cache.invalidate("list_subnets")
        return f"Subnet {subnet_id} deleted."

    async def _subnet_raw(self, pid: str, vpc_id: str, subnet_id: str, region: str | None) -> dict:
        """Read one subnet uncached, whichever envelope the detail comes in."""
        data = await self.client.get(
            f"/v2/{pid}/networks/{vpc_id}/subnets/{subnet_id}", region=region
        )
        payload = unwrap(data)
        return payload if isinstance(payload, dict) else {}

    async def create_secondary_subnet(
        self,
        vpc_id: str = Field(..., description="VPC ID the subnet belongs to."),
        subnet_id: str = Field(..., description="Subnet ID from list_subnets."),
        body: CreateSecondarySubnetDto = Field(..., description="Extra CIDR to add."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> SubnetItem:
        """Add a secondary CIDR to a subnet, leaving its other ranges untouched.

        Returns the PARENT subnet, re-read after the add — {id, name, cidr,
        status, zone_id, vpc_id, secondary_subnets}; the new range is in
        `secondary_subnets` with its own id (`sec-sub-…`), which
        delete_secondary_subnet and the address-pair tools take.

        ## Requirements
        - Requires `--allow-write`.
        - `cidr` must sit inside the VPC's own CIDR and must not overlap any
          existing subnet or secondary subnet.
        - A secondary subnet does not hand addresses out by itself: bind each
          interface that should use the range with
          create_secondary_subnet_address_pair, and configure the addresses
          inside the guest OS.

        ## Workflow
        - This is the way to give instances extra addresses (LVS, sub-interfaces)
          without a second NIC. Confirm the CIDR with the user — it cannot be
          changed later, only deleted.
        """
        require_write(self.allow_write)
        validate_id(vpc_id, "vpc_id")
        validate_id(subnet_id, "subnet_id")
        pid = await require_project_id(self.config, self.client, region)
        # The POST answers with the new range alone ({cidr, name, uuid}); parsed
        # as a subnet it has no status, zone or VPC and its uuid poses as the
        # subnet id. Re-read the parent so callers get the subnet they asked about.
        await self.client.post(
            f"/v2/{pid}/networks/{vpc_id}/subnets/{subnet_id}/secondary-subnets",
            region=region,
            json=body.model_dump(exclude_none=True),
        )
        self.cache.invalidate("list_subnets")
        return SubnetItem.from_api(await self._subnet_raw(pid, vpc_id, subnet_id, region))

    async def delete_secondary_subnet(
        self,
        vpc_id: str = Field(..., description="VPC ID the subnet belongs to."),
        subnet_id: str = Field(..., description="Subnet ID from list_subnets."),
        secondary_subnet_id: str = Field(..., description="Secondary subnet ID from get_subnet."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> str:
        """Delete a secondary CIDR from a subnet. This is irreversible.

        ## Requirements
        - Requires `--allow-write`.
        - Every interface bound to the range loses the right to use it, and
          traffic to those addresses stops.
        - Unbind the address pairs first with
          delete_secondary_subnet_address_pair so the failure is visible rather
          than silent.

        ## Workflow
        - Call list_secondary_subnet_address_pairs and show the user which
          instances are affected, then confirm.
        """
        require_write(self.allow_write)
        validate_id(vpc_id, "vpc_id")
        validate_id(subnet_id, "subnet_id")
        validate_id(secondary_subnet_id, "secondary_subnet_id")
        pid = await require_project_id(self.config, self.client, region)
        await self.client.delete(
            f"/v2/{pid}/networks/{vpc_id}/subnets/{subnet_id}"
            f"/secondary-subnets/{secondary_subnet_id}",
            region=region,
        )
        self.cache.invalidate("list_subnets")
        return f"Secondary subnet {secondary_subnet_id} deleted."
