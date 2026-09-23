"""Short-lived TTL cache for read-only vServer discovery results."""

from __future__ import annotations

from greennode.mcp_core.cache import DEFAULT_MAXSIZE
from greennode.mcp_core.cache import DiscoveryCache as _CoreDiscoveryCache
from greennode.mcp_core.http import current_identity


TTL_CONFIG: dict[str, int] = {
    "list_zones": 3600,
    "list_flavor_families": 1800,
    "list_flavor_codes": 1800,
    "list_flavors": 1800,
    "list_images": 1800,
    "list_volume_types": 1800,
    "list_placement_group_policies": 3600,
    "list_snapshot_policies": 1800,
    "get_vpc_config_options": 3600,
    "list_vpcs": 120,
    "list_subnets": 120,
    "list_security_groups": 120,
    "list_placement_groups": 120,
    "list_ssh_keys": 30,
    "project_lookup": 3600,
}


class DiscoveryCache(_CoreDiscoveryCache):
    """vServer discovery cache: core cache preconfigured with this package's TTLs.

    Every key is prefixed with the caller identity (hash of the passthrough
    user token, or 'service'), so under token passthrough one user's cached
    results can never be served to another.
    """

    def __init__(
        self, ttl_config: dict[str, int] | None = None, maxsize: int = DEFAULT_MAXSIZE, timer=None
    ):
        super().__init__(
            ttl_config if ttl_config is not None else TTL_CONFIG, maxsize=maxsize, timer=timer
        )

    async def get_or_fetch(self, tool, key, fetch, refresh=False):
        """Cache lookup with the caller identity baked into the key."""
        scoped_key = (current_identity(), key)
        return await super().get_or_fetch(tool, scoped_key, fetch, refresh)
