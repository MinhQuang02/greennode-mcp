"""Read-only catalogue models: zones, flavors, images, disk tiers, quota, tags.

These describe what a project *may* create, so they are the first thing a
creation flow reads and the source of every id the write DTOs consume.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.models._common import _image_types_from_metadata, _resource_id
from pydantic import BaseModel, Field


class ZoneItem(BaseModel):
    """One availability zone."""

    id: str = Field(
        ..., description="Zone ID, e.g. HCM03-1C — pass THIS as zoneId, never the display name"
    )
    name: str = Field("", description="Display name the console shows, e.g. HCM-1C")
    zone_type: str = Field(
        "",
        description=(
            "AVAILABILITY (a regular zone) or LOCAL (an edge location such as the "
            "Bangkok zone) — the console groups zones by this"
        ),
    )
    is_default: bool = Field(
        False, description="The zone the API falls back to when a create omits zoneId"
    )
    description: str = Field(
        "",
        description=(
            "Human-readable description. 'Contact to enable' means the zone is gated "
            "per account by GreenNode support even though `enabled` is true"
        ),
    )
    enabled: bool = Field(True, description="Whether the zone accepts new resources")

    @classmethod
    def from_api(cls, data: dict) -> "ZoneItem":
        """Build a ZoneItem from a raw vServer zone object."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            zone_type=data.get("zoneType") or "",
            is_default=bool(data.get("isDefault", False)),
            description=data.get("description") or "",
            enabled=bool(data.get("isEnabled", True)),
        )


class ZoneListData(BaseModel):
    """Structured response for list_zones."""

    region: str = Field(..., description="Region the zones were fetched from")
    zones: list[ZoneItem] = Field(default_factory=list, description="Availability zones")


class FlavorFamilyItem(BaseModel):
    """One instance family, with the sub-types it offers."""

    key: str = Field(..., description="Family key — pass this as `family` to list_flavors")
    name: str = Field("", description="Display name, e.g. 'General Purpose'")
    types: list[str] = Field(
        default_factory=list,
        description="Sub-type keys inside the family (standard, general, high-cpu, high-memory)",
    )

    @classmethod
    def from_api(cls, data: dict) -> "FlavorFamilyItem":
        """Build a FlavorFamilyItem from a raw flavor_zones/families entry."""
        return cls(
            key=data.get("key") or "",
            name=data.get("value") or "",
            types=[t.get("key", "") for t in (data.get("types") or []) if t.get("key")],
        )


class FlavorFamilyListData(BaseModel):
    """Structured response for list_flavor_families."""

    region: str = Field(..., description="Region the families were fetched from")
    families: list[FlavorFamilyItem] = Field(default_factory=list, description="Instance families")


class FlavorCodeItem(BaseModel):
    """One CPU/GPU platform code."""

    key: str = Field(..., description="Platform code key — pass this as `code` to list_flavors")
    name: str = Field("", description="Display name, e.g. 'Code A40'")
    description: str = Field("", description="Which CPU/GPU platform the code maps to")

    @classmethod
    def from_api(cls, data: dict) -> "FlavorCodeItem":
        """Build a FlavorCodeItem from a raw flavor_zones/codes entry."""
        description = data.get("description") or ""
        return cls(
            key=data.get("key") or "",
            name=data.get("value") or "",
            description="" if description == "N/A" else description,
        )


class FlavorCodeListData(BaseModel):
    """Structured response for list_flavor_codes."""

    region: str = Field(..., description="Region the codes were fetched from")
    codes: list[FlavorCodeItem] = Field(default_factory=list, description="CPU/GPU platform codes")


class FlavorItem(BaseModel):
    """One flavor (instance size)."""

    id: str = Field(..., description="Flavor ID — pass this as flavorId to create_server")
    name: str = Field("", description="Flavor name, e.g. s-general-2x4")
    vcpu: int = Field(0, description="Number of vCPUs")
    ram_gb: int = Field(0, description="RAM in GiB")
    gpu: int = Field(0, description="Number of GPUs (0 for CPU-only flavors)")
    gpu_memory_gb: int = Field(0, description="GPU memory in GiB")
    bandwidth: str = Field("", description="Network bandwidth, e.g. '1 Gbps'")
    group: str = Field("", description="Sub-type group, e.g. General / High CPU / High Memory")
    remaining_vms: int | None = Field(
        None, description="Remaining capacity in the zone (None when the API does not report it)"
    )
    supported_image_types: list[str] = Field(
        default_factory=list,
        description=(
            "Image types this flavor can boot (from the API's metaData). Cross-check "
            "against list_images before creating a server — a mismatch is rejected."
        ),
    )

    @classmethod
    def from_api(cls, data: dict) -> "FlavorItem":
        """Build a FlavorItem from a raw vServer flavor object."""
        bandwidth = data.get("bandwidth")
        unit = data.get("bandwidthUnit") or ""
        return cls(
            id=data.get("flavorId") or "",
            name=data.get("name") or "",
            vcpu=int(data.get("cpu") or 0),
            ram_gb=int(data.get("memory") or 0),
            gpu=int(data.get("gpu") or 0),
            gpu_memory_gb=int(data.get("gpuMemory") or 0),
            bandwidth=f"{bandwidth} {unit}".strip() if bandwidth is not None else "",
            group=data.get("group") or "",
            remaining_vms=data.get("remainingVms"),
            supported_image_types=_image_types_from_metadata(data.get("metaData")),
        )


class FlavorListData(BaseModel):
    """Structured response for list_flavors."""

    region: str = Field(..., description="Region the flavors were fetched from")
    zone_id: str | None = Field(None, description="Zone the list was filtered to, if any")
    family: str = Field(..., description="Instance family the flavors belong to")
    code: str = Field(..., description="CPU/GPU platform code the flavors belong to")
    flavors: list[FlavorItem] = Field(default_factory=list, description="Available flavors")


class ImageItem(BaseModel):
    """One bootable system image."""

    id: str = Field(..., description="Image ID — pass this as imageId to create_server")
    image_type: str = Field("", description="OS family, e.g. Ubuntu / CentOs / Windows")
    image_version: str = Field("", description="Version label, e.g. 1-Ubuntu-22.04x64")
    licence: bool = Field(False, description="Whether the image carries a paid OS licence")
    description: str = Field("", description="Human-readable description")

    @classmethod
    def from_api(cls, data: dict) -> "ImageItem":
        """Build an ImageItem from a raw vServer image object."""
        return cls(
            id=data.get("id") or "",
            image_type=data.get("imageType") or "",
            image_version=data.get("imageVersion") or "",
            licence=bool(data.get("licence", False)),
            description=data.get("description") or "",
        )


class ImageListData(BaseModel):
    """Structured response for list_images."""

    region: str = Field(..., description="Region the images were fetched from")
    image_type: str = Field(..., description="Catalogue queried: 'os' or 'gpu'")
    images: list[ImageItem] = Field(default_factory=list, description="Available images")


class VolumeTypeItem(BaseModel):
    """One volume type (an IOPS tier of a disk kind)."""

    id: str = Field(..., description="Volume type ID — pass this as volumeTypeId / rootDiskTypeId")
    name: str = Field("", description="Tier name (the IOPS number, e.g. '3000')")
    iops: int = Field(0, description="Provisioned IOPS")
    throughput_mbps: int = Field(0, description="Throughput in MB/s")
    min_size_gb: int = Field(0, description="Minimum volume size in GiB")
    max_size_gb: int = Field(0, description="Maximum volume size in GiB")

    @classmethod
    def from_api(cls, data: dict) -> "VolumeTypeItem":
        """Build a VolumeTypeItem from a raw vServer volume type object."""
        throughput = data.get("throughPut") or 0
        return cls(
            id=data.get("id") or "",
            name=str(data.get("name") or ""),
            iops=int(data.get("iops") or 0),
            throughput_mbps=int(throughput) // (1024 * 1024) if throughput else 0,
            min_size_gb=int(data.get("minSize") or 0),
            max_size_gb=int(data.get("maxSize") or 0),
        )


class VolumeTypeListData(BaseModel):
    """Structured response for list_volume_types."""

    region: str = Field(..., description="Region the volume types were fetched from")
    zone_id: str = Field(..., description="Availability zone the types belong to")
    disk_type: str = Field("", description="Resolved disk kind: SSD or NVMe")
    available_disk_types: list[str] = Field(
        default_factory=list, description="Disk kinds offered in this zone"
    )
    volume_types: list[VolumeTypeItem] = Field(
        default_factory=list, description="IOPS tiers of the resolved disk kind"
    )


class QuotaItem(BaseModel):
    """One quota line: how much of a resource the project may use, and does."""

    name: str = Field("", description="Quota name, e.g. SSH_KEY, ROUTE")
    type: str = Field("", description="Quota family, e.g. Server or Network")
    limit: int = Field(0, description="Maximum allowed")
    used: int = Field(0, description="Currently consumed")
    description: str = Field("", description="What the quota counts")

    @classmethod
    def from_api(cls, data: dict) -> "QuotaItem":
        """Build a QuotaItem from a raw quotaUsed entry.

        The API reports ``used`` as a string, so it is coerced here.
        """
        raw_used = data.get("used")
        try:
            used = int(raw_used)
        except (TypeError, ValueError):
            used = 0
        return cls(
            name=data.get("quotaName") or "",
            type=data.get("type") or "",
            limit=int(data.get("limit") or 0),
            used=used,
            description=data.get("description") or "",
        )


class QuotaListData(BaseModel):
    """Structured response for get_quota."""

    region: str = Field(..., description="Region the quota applies to")
    quotas: list[QuotaItem] = Field(default_factory=list, description="Quota lines")


class ResourceTagItem(BaseModel):
    """A tag attached to a resource."""

    key: str = Field("", description="Tag key")
    value: str = Field("", description="Tag value")

    @classmethod
    def from_api(cls, data: dict) -> "ResourceTagItem":
        """Build a ResourceTagItem from a raw tag object."""
        return cls(key=data.get("key") or "", value=data.get("value") or "")


class ResourceTagListData(BaseModel):
    """Structured response for the tag listing tools."""

    values: list[str] = Field(default_factory=list, description="Tag keys or values")


class TagItem(BaseModel):
    """One tag in the project's tag catalogue."""

    id: str = Field(..., description="Tag ID (uuid)")
    key: str = Field("", description="Tag key")
    value: str = Field("", description="Tag value")
    system: bool = Field(False, description="True for platform-managed tags such as vng.serverId")
    resource_type: str = Field("", description="Resource kind the tag applies to")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "TagItem":
        """Build a TagItem from a raw vServer tag object."""
        return cls(
            id=_resource_id(data),
            key=data.get("key") or "",
            value=data.get("value") or "",
            system=bool(data.get("systemTag", False)),
            resource_type=data.get("resourceType") or "",
            created_at=data.get("createdAt") or "",
        )


class TagListData(BaseModel):
    """Structured response for list_tags."""

    region: str = Field(..., description="Region the tags were fetched from")
    tags: list[TagItem] = Field(default_factory=list, description="Tags defined in the project")
