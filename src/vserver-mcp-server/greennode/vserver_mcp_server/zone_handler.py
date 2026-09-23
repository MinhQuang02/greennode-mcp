"""Availability-zone discovery for the vServer MCP server.

Zones are the root of every vServer creation flow: a server, volume, subnet,
network interface and the flavor/volume-type catalogues are all zone-scoped, so
``list_zones`` is normally the first call an agent makes.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import Region, VserverConfig
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.models import ZoneItem, ZoneListData
from greennode.vserver_mcp_server.paging import as_list, unwrap
from greennode.vserver_mcp_server.project import require_project_id
from greennode.vserver_mcp_server.tool_annotations import READ
from greennode.vserver_mcp_server.validators import validate_id
from pydantic import Field


async def fetch_zone_list(
    config: VserverConfig,
    client: VserverClient,
    cache: DiscoveryCache,
    region: str | None = None,
    refresh: bool = False,
) -> ZoneListData:
    """Fetch the availability zones of a region as structured data (cached)."""
    pid = await require_project_id(config, client, region)
    resolved_region = region or config.default_region

    async def fetch() -> ZoneListData:
        raw = await client.get(f"/v1/{pid}/zones", region=region)
        items = [z for z in as_list(raw) if z.get("isEnabled", True)]
        return ZoneListData(
            region=resolved_region,
            zones=[ZoneItem.from_api(z) for z in items],
        )

    key = ("list_zones", resolved_region, pid)
    return await cache.get_or_fetch("list_zones", key, fetch, refresh)


class ZoneHandler:
    """Register and serve zone-discovery MCP tools."""

    def __init__(
        self,
        mcp,
        config: VserverConfig,
        client: VserverClient,
        cache: DiscoveryCache,
    ):
        self.mcp = mcp
        self.config = config
        self.client = client
        self.cache = cache

        self.mcp.tool(name="list_zones", annotations=READ)(self.list_zones)
        self.mcp.tool(name="get_zone", annotations=READ)(self.get_zone)

    async def list_zones(
        self,
        region: Region = Field(
            "HCM-3",
            description=(
                "Region to list zones from ('HCM-3' or 'HAN'); defaults to 'HCM-3'. "
                "IMPORTANT: zones are region-scoped — pass the region the user wants "
                "the resource created in."
            ),
        ),
        refresh: bool = Field(
            False,
            description="Bypass the short-lived cache and refetch from vServer.",
        ),
    ) -> ZoneListData:
        """List the enabled availability zones of a region.

        Returns {region, zones[{id, name, zone_type, is_default, description,
        enabled}]}; disabled zones are excluded because they cannot host new
        resources.

        `id` (HCM03-1C) is what every tool takes; `name` (HCM-1C) is only the
        label the console shows. Present zones the way the console does —
        grouped by `zone_type` (AVAILABILITY, then LOCAL) with the default one
        marked. A description ending in "Contact to enable" means GreenNode
        support gates that zone per account: say so before the user picks it.

        ## Workflow
        - Step 1 of every creation flow (server, volume, subnet, network
          interface): present this list and let the user choose.
          IMPORTANT: do NOT pick a zone silently when more than one exists.
        - The chosen `id` is the `zoneId` for create_server / create_volume /
          create_subnet, and it scopes the flavor and volume-type catalogues.
        """
        return await fetch_zone_list(
            self.config, self.client, self.cache, region=region, refresh=refresh
        )

    async def get_zone(
        self,
        zone_id: str = Field(..., description="Zone ID from list_zones, e.g. HCM03-1A."),
        region: Region = Field("HCM-3", description="Region ('HCM-3' or 'HAN')."),
    ) -> ZoneItem:
        """Get one availability zone by id.

        Use it to confirm a zone is still `enabled` before placing a server in
        it — list_zones is cached, and a zone can be closed to new resources
        between calls.
        """
        validate_id(zone_id, "zone_id")
        pid = await require_project_id(self.config, self.client, region)
        data = await self.client.get(f"/v1/{pid}/zones/{zone_id}", region=region)
        return ZoneItem.from_api(unwrap(data) or {})
