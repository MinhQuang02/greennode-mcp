"""Request DTOs for every vServer create/update call.

Typed bodies rather than free-form dicts: fields are camelCase to match the
API, value sets are ``Literal``s, numeric bounds are declared, and every DTO
sets ``extra="forbid"`` so an unknown field is rejected instead of being
forwarded blind.
"""

from __future__ import annotations

import ipaddress
from greennode.vserver_mcp_server.models._common import TagDto
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal


DESCRIPTION_RULE = (
    "Description: letters, digits, spaces and _ . @ - only, must start with a letter, "
    "max 255 chars. The API rejects anything else, including '/' — so a CIDR cannot be "
    "quoted in the text"
)


class SecondarySubnetRequestDto(BaseModel):
    """A secondary CIDR to attach to a subnet."""

    model_config = ConfigDict(extra="forbid")

    cidr: str = Field(..., description="Secondary CIDR block, inside the VPC range")
    name: str | None = Field(None, description="Optional name for the secondary subnet")
    uuid: str | None = Field(
        None, description="Existing secondary subnet ID; omit when creating a new one"
    )


# The console's create-VPC form, mirrored rule for rule. The CIDR box takes a
# base address with a FIXED "/16" suffix and lists three allowed ranges; the
# API itself would also take /20, /22 and /24, but the product only offers /16
# and this server follows the product. Outside the ranges the API answers
# "User can't use this range, reserved for system" (172.25-172.31 included).
VPC_CIDR_PREFIX = "/16"
VPC_CIDR_RANGES = (
    "10.0.0.0/16 - 10.255.0.0/16",
    "172.16.0.0/16 - 172.24.0.0/16",
    "192.168.0.0/16",
)
VPC_NAME_RULE = "5-50 characters: letters (a-z, A-Z), digits, '_' and '-' only"
_VPC_NAME_PATTERN = r"^[A-Za-z0-9_-]+$"


def _vpc_cidr(value: str) -> str:
    """Normalise and check a VPC CIDR the way the console form does.

    Accepts the bare base address the form asks for (``10.5.0.0``) as well as
    ``10.5.0.0/16``; anything but /16, a base with host bits set, or an address
    outside the three private ranges is refused with the rule spelled out —
    the API's own answers ("Invalid CIDR.", "reserved for system") name none of
    it.
    """
    raw = value.strip()
    address, _, prefix = raw.partition("/")
    if prefix and f"/{prefix}" != VPC_CIDR_PREFIX:
        raise ValueError(
            f"A VPC CIDR is always {VPC_CIDR_PREFIX} — the console offers no other size; "
            f"got '/{prefix}'. Ask the user for a base address such as 10.5.0.0 instead. "
            "(The /18 … /28 lengths in get_vpc_config_options are for SUBNETS.)"
        )
    try:
        net = ipaddress.IPv4Network(f"{address}{VPC_CIDR_PREFIX}", strict=True)
    except ValueError as exc:
        raise ValueError(
            f"'{address}' is not a {VPC_CIDR_PREFIX} network address: use four octets "
            "ending in .0.0, e.g. 10.5.0.0."
        ) from exc
    first, second = net.network_address.packed[:2]
    if not (
        first == 10 or (first == 172 and 16 <= second <= 24) or (first == 192 and second == 168)
    ):
        raise ValueError(
            f"{net} is outside the private ranges a VPC may use: {list(VPC_CIDR_RANGES)}. "
            "172.25.0.0 and above are reserved for the platform."
        )
    return str(net)


class CreateVpcDto(BaseModel):
    """Request body for POST /v2/{projectId}/networks.

    Mirrors the console's create-VPC form field for field: name, a /16 CIDR
    from three private ranges, MTU, a mandatory zone, optional tags. The API is
    laxer on two of them — it silently applies ``mtu: 1500`` and the region's
    default zone when they are omitted — and both are permanent, so they are
    required here and put to the user rather than defaulted.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        min_length=5,
        max_length=50,
        pattern=_VPC_NAME_PATTERN,
        description=f"VPC name, {VPC_NAME_RULE}. Must be unique within the project",
    )
    cidr: str = Field(
        ...,
        description=(
            "The VPC network, always /16: pass the base address the user chose "
            "(10.5.0.0) or the same with its suffix (10.5.0.0/16). Allowed ranges: "
            "10.0.0.0-10.255.0.0, 172.16.0.0-172.24.0.0 and 192.168.0.0. It must not "
            "overlap a VPC that already exists in the region — get_vpc_config_options "
            "lists them under `vpc_cidrs_in_use`. Cannot be changed after creation"
        ),
    )
    mtu: int = Field(
        ...,
        description=(
            "MTU in BYTES, one of `mtu_options` from get_vpc_config_options (currently "
            "1450, 1500, 8500, 8950; served dynamically). The console preselects 1500 — "
            "offer it as the recommended answer, but ask: it cannot be changed later. "
            "8500/8950 are jumbo frames for east-west traffic inside the VPC"
        ),
    )
    zoneId: str = Field(
        ...,
        description=(
            "Zone id (`id` from list_zones, e.g. HCM03-1C — not the display name "
            "HCM-1C). Mandatory, as in the console: an Availability zone or a Local "
            "zone (zone_type LOCAL, e.g. the Bangkok zone)"
        ),
    )
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")

    @field_validator("cidr")
    @classmethod
    def _check_cidr(cls, value: str) -> str:
        return _vpc_cidr(value)


class UpdateVpcDto(BaseModel):
    """Request body for PATCH /v2/{projectId}/networks/{networkId}.

    Carries no ``mtu`` and no ``cidr``: both are fixed at creation time.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        min_length=5,
        max_length=50,
        pattern=_VPC_NAME_PATTERN,
        description=f"New VPC name, {VPC_NAME_RULE}; the API requires it on every edit",
    )
    zoneId: str | None = Field(None, description="Availability zone id")
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class CreateSubnetDto(BaseModel):
    """Request body for POST /v2/{projectId}/networks/{networkId}/subnets."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Subnet name")
    cidr: str = Field(
        ...,
        description=(
            "Subnet CIDR, must fall inside the VPC CIDR. The prefix length must be one "
            "of `subnet_cidr_prefixes` from get_vpc_config_options (/16, /18, /20, /22, /24, "
            "/26, /28) — unlike the VPC rule, the full list applies here, and the API "
            "names the allowed set when it refuses"
        ),
    )
    zoneId: str | None = Field(
        None, description="Availability zone id from list_zones; pins the zone of its servers"
    )
    secondarySubnetRequests: list[SecondarySubnetRequestDto] | None = Field(
        None, description="Optional secondary CIDRs to attach"
    )
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class UpdateSubnetDto(BaseModel):
    """Request body for PATCH /v2/{projectId}/networks/{networkId}/subnets/{subnetId}."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="New subnet name; the API requires it on every edit")
    zoneId: str | None = Field(None, description="Availability zone id")
    secondarySubnetRequests: list[SecondarySubnetRequestDto] | None = Field(
        None,
        description=(
            "Full replacement list of secondary CIDRs: a range missing from it is "
            "deleted, [] deletes them all. Omit it to keep the current set"
        ),
    )
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class CreateSecurityGroupDto(BaseModel):
    """Request body for POST /v2/{projectId}/secgroups."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Security group name")
    description: str | None = Field(None, description=DESCRIPTION_RULE)
    zoneId: str | None = Field(None, description="Availability zone id")
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class UpdateSecurityGroupDto(BaseModel):
    """Request body for PUT /v2/{projectId}/secgroups/{secgroupId}."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="New name; the API requires it on every update")
    description: str | None = Field(None, description=DESCRIPTION_RULE)
    zoneId: str | None = Field(None, description="Availability zone id")
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class CreateSecurityGroupRuleDto(BaseModel):
    """Request body for POST /v2/{projectId}/secgroups/{secgroupId}/secgroupRules.

    The API requires ``portRangeMin``/``portRangeMax`` on every rule. They are
    optional here for ergonomics: omit both and the handler fills the
    protocol's full range before sending.
    """

    model_config = ConfigDict(extra="forbid")

    direction: Literal["ingress", "egress"] = Field(
        ..., description="ingress for inbound traffic, egress for outbound"
    )
    protocol: str = Field(
        ...,
        description=(
            "Protocol: 'tcp', 'udp', 'icmp', 'any', or an IANA protocol number as a "
            "string for the rest — '47' GRE, '50' ESP, '51' AH, '112' VRRP (keepalived "
            "needs this to run a virtual IP), '115' L2TP. "
            "list_security_group_rule_samples returns the values the API itself uses."
        ),
    )
    etherType: Literal["IPv4", "IPv6"] = Field("IPv4", description="IP version the rule covers")
    remoteIpPrefix: str = Field(
        ..., description="Remote CIDR, e.g. 0.0.0.0/0 for anywhere or 10.0.0.0/16 for a VPC"
    )
    portRangeMin: int | None = Field(
        None,
        ge=0,
        le=65535,
        description="Lowest port (ICMP type for icmp). Omit with portRangeMax for the full range.",
    )
    portRangeMax: int | None = Field(
        None,
        ge=0,
        le=65535,
        description="Highest port (ICMP type for icmp). Omit with portRangeMin for the full range.",
    )
    description: str | None = Field(None, description=DESCRIPTION_RULE)
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class UpdateSecurityGroupRuleDto(BaseModel):
    """Request body for PUT .../secgroupRules/{secgroupRuleId}.

    Only the description and tags are editable — direction, protocol, ports and
    the remote CIDR are immutable, so changing them means delete plus create.
    """

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(None, description=DESCRIPTION_RULE)
    tags: list[TagDto] | None = Field(None, description="Replacement key/value tags")


class TagRequestDto(BaseModel):
    """One entry of a tag-replacement list."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., description="Tag key")
    value: str = Field("", description="Tag value")
    isEdited: bool = Field(
        False, description="True when this tag's value changed in the current update"
    )


class CreateServerDto(BaseModel):
    """Request body for POST /v2/{projectId}/servers.

    Deliberately narrower than the raw API schema: billing options (billing
    period, auto-renew, PoC and OS-licence flags), backup and snapshot restore
    points, and marketplace fields are **not** exposed, so an agent cannot
    change what the instance costs. Use the console for those.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Server name, 5-65 chars, alphanumeric/hyphen/underscore")
    zoneId: str = Field(..., description="Availability zone id from list_zones")
    networkId: str = Field(..., description="VPC id from list_vpcs")
    subnetId: str = Field(..., description="Subnet id from list_subnets, inside that VPC")
    imageId: str = Field(..., description="Image id from list_images")
    flavorId: str = Field(..., description="Flavor id from list_flavors")
    rootDiskTypeId: str = Field(..., description="Volume type id from list_volume_types")
    rootDiskSize: int = Field(..., ge=20, description="Root disk size in GiB, minimum 20")
    encryptionVolume: bool = Field(False, description="Encrypt the root volume")
    rootDiskEncryptionType: str | None = Field(None, description="Root disk encryption type")
    dataDiskTypeId: str | None = Field(
        None, description="Volume type id for an optional extra data disk"
    )
    dataDiskSize: int | None = Field(None, ge=1, description="Data disk size in GiB")
    dataDiskName: str | None = Field(None, description="Data disk name")
    dataDiskEncryptionType: str | None = Field(None, description="Data disk encryption type")
    attachFloating: bool = Field(
        False,
        description=(
            "Allocate and attach a public floating IP, making the server reachable "
            "from the internet. Leave false for private-only servers."
        ),
    )
    externalNetworkInterfaceId: str | None = Field(
        None, description="Existing elastic interface to attach instead of a new floating IP"
    )
    securityGroup: list[str] | None = Field(
        None, description="Security group ids from list_security_groups"
    )
    sshKeyId: str | None = Field(None, description="SSH key id from list_ssh_keys")
    userName: str | None = Field(None, description="OS login username")
    userPassword: str | None = Field(None, description="OS login password")
    expirePassword: bool = Field(True, description="Force a password change on first login")
    serverGroupId: str | None = Field(
        None, description="Placement group id from list_placement_groups"
    )
    hostGroupId: str | None = Field(None, description="Dedicated host group id")
    userData: str | None = Field(
        None,
        description=(
            "First-boot init script. The platform dispatches on the FIRST line: "
            "'#cloud-config' (cloud-init YAML), '#!/bin/bash', "
            "'#!/usr/bin/env python', '#ps1' (PowerShell) or 'rem cmd' (Windows "
            "batch). Ask the user for the content — paste or a file to read — and "
            "always ask when imageId is a user image, where the script is what "
            "adapts the clone (hostname, users, keys, first-boot commands). "
            "Never invent one."
        ),
    )
    userDataBase64Encoded: bool = Field(
        False,
        description=(
            "Declares that userData is ALREADY base64-encoded; it never encodes "
            "for you. Send plain text with false — encoding the script yourself "
            "and leaving this false makes the guest run the base64 blob as code."
        ),
    )
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class ResizeServerDto(BaseModel):
    """Request body for PUT /v2/{projectId}/servers/{serverId}/resize."""

    model_config = ConfigDict(extra="forbid")

    flavorId: str = Field(..., description="New flavor id from list_flavors")
    hostGroupId: str | None = Field(None, description="Dedicated host group id")


class RenameServerDto(BaseModel):
    """Request body for PUT /v2/{projectId}/servers/{serverId}/rename."""

    model_config = ConfigDict(extra="forbid")

    newName: str = Field(..., description="New server name")


class UpdateServerSecurityGroupsDto(BaseModel):
    """Request body for PUT /v2/{projectId}/servers/{serverId}/update-sec-group."""

    model_config = ConfigDict(extra="forbid")

    securityGroup: list[str] = Field(
        ...,
        description=(
            "Complete replacement list of security group ids: any group not "
            "included is detached from the server."
        ),
    )


class SubnetRequestDto(BaseModel):
    """One interface to create when attaching internal interfaces to a server."""

    model_config = ConfigDict(extra="forbid")

    subnetId: str = Field(..., description="Subnet to create the interface on")
    ip: str | None = Field(
        None, description="Specific private IP to request; omit to let the system assign one"
    )


class AttachInternalInterfaceDto(BaseModel):
    """Request body for POST .../servers/{serverId}/internal-network-interfaces."""

    model_config = ConfigDict(extra="forbid")

    subnetRequests: list[SubnetRequestDto] = Field(
        ..., min_length=1, description="Interfaces to create and attach"
    )


class DetachInternalInterfacesDto(BaseModel):
    """Request body for DELETE .../servers/{serverId}/internal-network-interfaces."""

    model_config = ConfigDict(extra="forbid")

    networkInterfaceIds: list[str] = Field(
        ..., min_length=1, description="Internal interface ids to detach"
    )


class CreateVolumeDto(BaseModel):
    """Request body for POST /v2/{projectId}/volumes.

    Billing and restore-from-backup options are intentionally not exposed.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Volume name")
    size: int = Field(..., ge=1, description="Size in GiB, within the volume type's bounds")
    volumeTypeId: str = Field(..., description="Volume type id from list_volume_types")
    zoneId: str = Field(
        ..., description="Availability zone; a volume can only attach to servers in it"
    )
    encryptionType: str | None = Field(None, description="Encryption type")
    multiAttach: bool = Field(
        False, description="Allow the volume to be attached to several servers at once"
    )
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class ResizeVolumeDto(BaseModel):
    """Request body for PUT /v2/{projectId}/volumes/{volumeId}/resize."""

    model_config = ConfigDict(extra="forbid")

    newSize: int = Field(
        ..., ge=1, description="New size in GiB; a volume can only grow, never shrink"
    )
    newVolumeTypeId: str = Field(
        ...,
        description=(
            "Target volume type id. The API requires it on every resize — pass the "
            "volume's current volumeTypeId when only the size should change."
        ),
    )


class RenameVolumeDto(BaseModel):
    """Request body for PUT /v2/{projectId}/volumes/{volumeId}/rename."""

    model_config = ConfigDict(extra="forbid")

    newName: str = Field(..., description="New volume name")


class CreateServerImageDto(BaseModel):
    """Request body for POST /v2/{projectId}/user-images/servers/{serverId}."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Name of the image to create")
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class UpdateResourceTagsDto(BaseModel):
    """Request body for PUT /v2/{projectId}/tag/resource/{resourceId}."""

    model_config = ConfigDict(extra="forbid")

    resourceType: str = Field(
        ...,
        description=(
            "Resource family the id belongs to, e.g. NETWORK-INTERFACE, SERVER, "
            "VOLUME, USER-IMAGE."
        ),
    )
    tagRequestList: list[TagRequestDto] = Field(
        ...,
        description=(
            "Complete replacement list: any tag not included is removed. Mark "
            "entries whose value changed with isEdited=true."
        ),
    )


class CreateSshKeyDto(BaseModel):
    """Request body for POST /v2/{projectId}/sshKeys."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="SSH key name")


class ImportSshKeyDto(BaseModel):
    """Request body for POST /v2/{projectId}/sshKeys/import."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="SSH key name")
    pubKey: str = Field(
        ...,
        description=(
            "The public key material, e.g. the contents of ~/.ssh/id_ed25519.pub. "
            "Must start with ssh-rsa, ssh-ed25519, ssh-dss, ecdsa-sha2-, sk-ssh- or sk-ecdsa-."
        ),
    )


class CreatePlacementGroupDto(BaseModel):
    """Request body for POST /v2/{projectId}/serverGroups."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Placement group name")
    policyId: str = Field(..., description="Policy id from list_placement_group_policies")
    description: str | None = Field(None, description=DESCRIPTION_RULE)


class UpdatePlacementGroupDto(BaseModel):
    """Request body for PUT /v2/{projectId}/serverGroups/{serverGroupId}."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="New name; the API requires it on every update")
    description: str | None = Field(None, description=DESCRIPTION_RULE)


class CreateNetworkInterfaceDto(BaseModel):
    """Request body for POST /v2/{projectId}/network-interfaces-elastic."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Interface name")
    zoneId: str = Field(..., description="Availability zone id from list_zones")
    floatingIpId: str | None = Field(
        None, description="Existing floating IP to bind to the interface"
    )
    securityGroupIds: list[str] | None = Field(
        None, description="Security groups to attach to the interface"
    )
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class RenameNetworkInterfaceDto(BaseModel):
    """Request body for PUT .../network-interfaces-elastic/{id}/rename."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="New interface name")


class CreateDhcpOptionDto(BaseModel):
    """Request body for POST /v2/{projectId}/dhcp_option."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Name of the DHCP option set")
    dnsServers: list[str] | None = Field(
        None,
        description=(
            "Extra DNS server IPs. The two GreenNode defaults are always included, "
            "and at most two more may be added (four in total)."
        ),
    )
    mtu: int | None = Field(None, description="MTU to hand out to instances")


class CreateRouteTableDto(BaseModel):
    """Request body for POST /v2/{projectId}/route-table."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        min_length=5,
        max_length=50,
        description="Route table name, 5-50 chars: letters, digits, '_' and '-' only",
    )
    networkId: str = Field(..., description="VPC ID from list_vpcs")
    routes: list["RouteRequestDto"] | None = Field(
        None, description="Static routes to create the table with"
    )
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class RouteRequestDto(BaseModel):
    """One static route in a route-table request."""

    model_config = ConfigDict(extra="forbid")

    destinationCidrBlock: str = Field(
        ..., description="Destination network in CIDR notation, e.g. 10.21.0.0/24"
    )
    target: str = Field(..., description="Next hop to forward the matching traffic to")


class UpdateRouteTableRoutesDto(BaseModel):
    """Request body for PUT /v2/{projectId}/route-table/{uuid}/routes."""

    model_config = ConfigDict(extra="forbid")

    routes: list[RouteRequestDto] = Field(
        ...,
        description=(
            "The COMPLETE set of routes the table should end up with — this "
            "replaces the current set, so include every route you want to keep"
        ),
    )


class CreateNetworkAclDto(BaseModel):
    """Request body for POST /v2/{projectId}/network-acl."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Network ACL name")
    vpc: str = Field(..., description="VPC ID from list_vpcs")
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class NetworkAclRuleDto(BaseModel):
    """One inbound or outbound rule in a network-ACL rule request."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["inbound", "outbound"] = Field(
        ...,
        description="'inbound' filters traffic entering the subnet, 'outbound' traffic leaving",
    )

    @field_validator("protocol", mode="before")
    @classmethod
    def _lowercase_protocol(cls, value: object) -> object:
        """Accept the uppercase spelling reads return, send the lowercase one writes need."""
        return value.lower() if isinstance(value, str) else value

    seqNumber: int = Field(
        ...,
        ge=0,
        le=32766,
        description=(
            "Evaluation order — the first matching rule decides, so a lower number "
            "outranks a higher one. Leave gaps so rules can be inserted later."
        ),
    )
    protocol: Literal["any", "tcp", "udp", "icmp"] = Field(
        ...,
        description=(
            "Protocol to match, lowercase — the API rejects the uppercase spelling it "
            "returns on reads"
        ),
    )
    port: str = Field(
        ...,
        description=(
            "Single port ('443') or a range whose ends differ ('0-65535'). A range like "
            "'443-443' is rejected as an invalid port."
        ),
    )
    source: str = Field(
        ...,
        description="Source CIDR for inbound rules, destination CIDR for outbound, e.g. 10.0.0.0/8",
    )
    action: Literal["pass", "drop"] = Field(
        ..., description="'pass' allows the traffic, 'drop' denies it"
    )


class UpdateNetworkAclRulesDto(BaseModel):
    """Request body for PUT /v2/{projectId}/network-acl/{aclId}/rules."""

    model_config = ConfigDict(extra="forbid")

    detailAclRuleList: list[NetworkAclRuleDto] = Field(
        ...,
        description=(
            "The COMPLETE rule set, inbound and outbound together — this replaces "
            "every non-default rule, so include the rules you want to keep"
        ),
    )


class UpdateNetworkAclSubnetsDto(BaseModel):
    """Request body for PUT /v2/{projectId}/network-acl/{uuid}/subnets."""

    model_config = ConfigDict(extra="forbid")

    subnetUuids: list[str] = Field(
        ...,
        description=(
            "The COMPLETE set of subnets this ACL should govern — subnets left out "
            "fall back to the VPC's default ACL. Pass [] to detach every subnet."
        ),
    )


class CreateInterconnectDto(BaseModel):
    """Request body for POST /v2/{projectId}/interconnects."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Interconnect name")
    typeId: str = Field(..., description="Circuit type id from list_interconnect_circuit_types")
    packageId: str = Field(
        ..., description="Bandwidth package id from list_interconnect_packages, e.g. itp-1Gbps"
    )
    description: str | None = Field(None, description=DESCRIPTION_RULE)
    circuitId: int | None = Field(None, description="Physical circuit number, when pre-assigned")
    enableGw2: bool | None = Field(
        None, description="Provision a redundant second gateway (raises the cost)"
    )
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class UpdateInterconnectDto(BaseModel):
    """Request body for PUT /v2/{projectId}/interconnects/{interconnectId}."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(None, description=DESCRIPTION_RULE)
    enableGw2: bool | None = Field(None, description="Turn the redundant second gateway on or off")
    tags: list[TagDto] | None = Field(None, description="Replacement key/value tags")


class UpdateInterconnectPackageDto(BaseModel):
    """Request body for PUT .../interconnects/{interconnectId}/change-package."""

    model_config = ConfigDict(extra="forbid")

    packageId: str = Field(
        ..., description="New bandwidth package id from list_interconnect_packages"
    )
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class CreateInterconnectConnectionDto(BaseModel):
    """Request body for POST .../interconnects/{interconnectId}/connections."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Connection name")
    networkId: str = Field(..., description="VPC ID to expose over the circuit")
    subnets: list[str] = Field(
        ...,
        description=(
            "Customer-side CIDRs reachable through the circuit. They must not "
            "overlap the VPC's own CIDR."
        ),
    )
    description: str | None = Field(None, description=DESCRIPTION_RULE)
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class UpdateInterconnectConnectionDto(BaseModel):
    """Request body for PUT .../connections/{interconnectionId}."""

    model_config = ConfigDict(extra="forbid")

    subnets: list[str] = Field(
        ...,
        description=(
            "The COMPLETE set of customer-side CIDRs for this connection — it "
            "replaces the current set"
        ),
    )
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class CreateVirtualIpDto(BaseModel):
    """Request body for POST /v2/{projectId}/virtualIpAddress."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="VIP name")
    subnetId: str = Field(
        ...,
        description=(
            "Subnet the VIP lives in. Only instances in THIS subnet can share the "
            "VIP, so it must be the subnet of every instance in the HA pair."
        ),
    )
    mode: Literal["Active/Active", "Active/Passive"] = Field(
        ...,
        description=(
            "'Active/Passive' for keepalived-style failover (one instance answers "
            "at a time); 'Active/Active' to load-share across instances"
        ),
    )
    ipAddress: str | None = Field(
        None, description="Specific free IP inside the subnet; omit to let vServer pick one"
    )
    description: str | None = Field(None, description=DESCRIPTION_RULE)


class UpdateVirtualIpDto(BaseModel):
    """Request body for PUT /v2/{projectId}/virtualIpAddress/{virtualIpAddressId}."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["Active/Active", "Active/Passive"] = Field(
        ...,
        description="The API requires the mode on every update; pass the current one to keep it",
    )
    name: str | None = Field(None, description="New name")
    description: str | None = Field(None, description=DESCRIPTION_RULE)


class CreatePublicVirtualIpDto(BaseModel):
    """Request body for POST /v2/{projectId}/public-vips."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Public VIP name")
    type: Literal["public-vm", "public-mkp"] = Field(
        ...,
        description=(
            "'public-vm' for a VIP shared by instances, 'public-mkp' for a vMarketplace appliance"
        ),
    )
    ipAddress: str | None = Field(None, description="Specific public IP; omit to be allocated one")
    mode: str | None = Field(None, description="Sharing mode, when the type supports a choice")
    description: str | None = Field(None, description=DESCRIPTION_RULE)
    tags: list[TagDto] | None = Field(None, description="Optional key/value tags")


class AddressPairDto(BaseModel):
    """Request body for the address-pair create endpoints."""

    model_config = ConfigDict(extra="forbid")

    networkInterfaceId: str = Field(
        ...,
        description=(
            "Network interface to bind to the VIP — from "
            "list_virtual_ip_candidate_interfaces (private) or "
            "list_public_virtual_ip_candidate_interfaces (public)"
        ),
    )


class CreateSnapshotDto(BaseModel):
    """Request body for the 'snapshot now' endpoints (server and volume)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Snapshot name")
    description: str = Field(
        ..., description="Why the snapshot is being taken; the API requires it"
    )
    isPermanently: bool | None = Field(
        None,
        description=(
            "Keep the snapshot forever. Permanent snapshots are billed until "
            "deleted by hand — prefer retainedDays unless the user asks for this."
        ),
    )
    retainedDays: int | None = Field(
        None,
        ge=1,
        description="Days to keep the snapshot before it is deleted automatically",
    )


class RollbackSnapshotDto(BaseModel):
    """Request body for the snapshot rollback endpoints."""

    model_config = ConfigDict(extra="forbid")

    restartServerWhenRevertCompleted: bool | None = Field(
        None, description="Power the server back on once the rollback finishes"
    )


class CreateSnapshotPolicyDto(BaseModel):
    """Request body for POST /v2/{projectId}/servers/{serverId}/server-snapshots."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(..., description="Why the server is being snapshotted; required")
    name: str | None = Field(None, description="Name for the snapshot configuration")
    enableSnapshot: bool | None = Field(
        None, description="Start taking automatic snapshots straight away"
    )
    snapshotPolicyId: str | None = Field(
        None,
        description=(
            "Schedule policy (frequency and retention); ids come from list_snapshot_policies"
        ),
    )
    volumeIds: list[str] | None = Field(
        None, description="Volumes to include; omit to include every volume of the server"
    )


class UpdateSnapshotPolicyDto(BaseModel):
    """Request body for the snapshot-policy update endpoints."""

    model_config = ConfigDict(extra="forbid")

    snapshotPolicyId: str = Field(
        ...,
        description=("Schedule policy to switch to; ids come from list_snapshot_policies"),
    )


class ChangeVolumeTypeDto(BaseModel):
    """Request body for PUT /v2/{projectId}/volumes/{volumeId}/change-device-type."""

    model_config = ConfigDict(extra="forbid")

    volumeTypeId: str = Field(
        ..., description="Target volume type from list_volume_types, in the volume's own zone"
    )
    action: str | None = Field(
        None, description="Migration action the API should take, when the tier change needs one"
    )
    confirmMigrate: bool | None = Field(
        None, description="Confirm a migration that moves the data to different hardware"
    )


class DeletePersistentVolumeDto(BaseModel):
    """Request body for DELETE /v2/{projectId}/persistent-volumes/{pvId}."""

    model_config = ConfigDict(extra="forbid")

    forceDelete: bool | None = Field(
        None,
        description=(
            "Delete even while the Kubernetes cluster still references the volume. "
            "This orphans the PV inside the cluster — leave it off unless the user "
            "asked for it."
        ),
    )


class CreateSecondarySubnetDto(BaseModel):
    """Request body for POST .../subnets/{subnetId}/secondary-subnets."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Secondary subnet name")
    cidr: str = Field(
        ...,
        description=(
            "Extra CIDR to add to the subnet. It must sit inside the VPC's CIDR "
            "and must not overlap any existing subnet."
        ),
    )
