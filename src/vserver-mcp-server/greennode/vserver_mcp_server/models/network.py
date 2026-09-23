"""Core networking models: VPCs, subnets, security groups, public IPs, DHCP.

The layer every instance needs. The optional layers — route tables, ACLs,
peering, interconnects, virtual IPs — live in ``advanced_network``.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.models._common import _resource_id, _zone_id
from pydantic import BaseModel, Field


class VpcItem(BaseModel):
    """One VPC (network)."""

    id: str = Field(..., description="VPC ID — pass this as networkId/vpcId to other tools")
    name: str = Field("", description="VPC name")
    cidr: str = Field("", description="CIDR block of the VPC, e.g. 10.0.0.0/16")
    status: str = Field("", description="Lifecycle status; only ACTIVE VPCs accept new resources")
    zone_id: str = Field("", description="Availability zone the VPC belongs to")
    mtu: int | None = Field(None, description="MTU of the network")
    dhcp_option_id: str | None = Field(None, description="Associated DHCP option set, if any")
    route_table_id: str | None = Field(None, description="Associated route table, if any")
    dns_status: str | None = Field(None, description="vDNS status; needed by some VKS setups")

    @classmethod
    def from_api(cls, data: dict) -> "VpcItem":
        """Build a VpcItem from a raw vServer network object.

        The VPC list calls the display name ``displayName``, not ``name`` — a
        vServer inconsistency this mapping hides from callers.
        """
        return cls(
            id=_resource_id(data),
            name=data.get("displayName") or data.get("name") or "",
            cidr=data.get("cidr") or "",
            status=data.get("status") or "",
            zone_id=_zone_id(data),
            mtu=data.get("mtu"),
            dhcp_option_id=data.get("dhcpOptionId"),
            route_table_id=data.get("routeTableId"),
            dns_status=data.get("dnsStatus"),
        )


class VpcCidrInUse(BaseModel):
    """A CIDR an existing VPC already occupies in the region."""

    id: str = Field(..., description="VPC ID")
    name: str = Field("", description="VPC name")
    cidr: str = Field("", description="The CIDR it occupies")
    status: str = Field("", description="Lifecycle status; a CREATING/DELETING VPC still holds it")


class VpcConfigOptionsData(BaseModel):
    """Everything the console's create-VPC form offers, in one answer.

    The MTU list and the subnet prefix list come from
    ``/v1/common/dynamic-config``; the VPC CIDR rule is the form's own
    (always /16, three private ranges); the CIDRs in use are read live from the
    region's VPCs, because a new VPC must not overlap any of them.
    """

    region: str = Field(..., description="Region the options apply to")
    name_rule: str = Field("", description="What a VPC name may contain")
    mtu_options: list[int] = Field(
        default_factory=list, description="Selectable MTU values in BYTES — ask the user"
    )
    recommended_mtu: int = Field(
        1500,
        description="What the console preselects; offer it as the recommended answer, still ask",
    )
    vpc_cidr_prefix: str = Field(
        "/16", description="The ONLY prefix length a VPC is created with — not a choice"
    )
    vpc_cidr_ranges: list[str] = Field(
        default_factory=list,
        description="Private ranges the /16 base address must fall in — ask the user for one",
    )
    vpc_cidrs_in_use: list[VpcCidrInUse] = Field(
        default_factory=list,
        description=(
            "CIDRs existing VPCs of this region already hold, every status included. "
            "Show them to the user; a new VPC must not overlap any"
        ),
    )
    subnet_cidr_prefixes: list[str] = Field(
        default_factory=list,
        description=(
            "Prefix lengths a SUBNET may use (create_subnet). NOT options for the VPC "
            "itself, which is always /16"
        ),
    )

    @classmethod
    def from_api(
        cls,
        data: dict,
        region: str,
        *,
        name_rule: str,
        vpc_cidr_prefix: str,
        vpc_cidr_ranges: list[str],
        in_use: list[VpcCidrInUse] | None = None,
    ) -> "VpcConfigOptionsData":
        """Build the form options from the raw dynamic-config object.

        The endpoint answers with a **bare object** — no ``data`` or ``success``
        envelope, unlike everything on the vServer gateway. Its
        ``cidrNotationConfigs`` is the SUBNET rule (the subnet endpoint echoes
        the same list when it refuses a prefix), so it lands in
        ``subnet_cidr_prefixes`` and never next to the VPC choices.
        """
        payload = data if isinstance(data, dict) else {}
        mtus = payload.get("mtuConfig")
        prefixes = payload.get("cidrNotationConfigs")
        mtu_options = [int(m) for m in (mtus if isinstance(mtus, list) else []) if _is_int(m)]
        return cls(
            region=region,
            name_rule=name_rule,
            mtu_options=mtu_options,
            recommended_mtu=1500 if not mtu_options or 1500 in mtu_options else mtu_options[0],
            vpc_cidr_prefix=vpc_cidr_prefix,
            vpc_cidr_ranges=list(vpc_cidr_ranges),
            vpc_cidrs_in_use=list(in_use or []),
            subnet_cidr_prefixes=[
                str(p) for p in (prefixes if isinstance(prefixes, list) else []) if str(p).strip()
            ],
        )


def _is_int(value: object) -> bool:
    """True when *value* is a whole number the MTU list can carry."""
    return isinstance(value, int) and not isinstance(value, bool)


class VpcListData(BaseModel):
    """Structured response for list_vpcs."""

    region: str = Field(..., description="Region the VPCs were fetched from")
    vpcs: list[VpcItem] = Field(default_factory=list, description="VPCs in the project")


class SecondarySubnetItem(BaseModel):
    """A secondary CIDR attached to a subnet."""

    id: str = Field("", description="Secondary subnet ID")
    name: str = Field("", description="Secondary subnet name")
    cidr: str = Field("", description="Secondary CIDR block")

    @classmethod
    def from_api(cls, data: dict) -> "SecondarySubnetItem":
        """Build a SecondarySubnetItem from a raw secondary-subnet object."""
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            cidr=data.get("cidr") or "",
        )


class SubnetItem(BaseModel):
    """One subnet of a VPC."""

    id: str = Field(..., description="Subnet ID — pass this as subnetId to create_server")
    name: str = Field("", description="Subnet name")
    cidr: str = Field("", description="CIDR block of the subnet")
    status: str = Field("", description="Lifecycle status; only ACTIVE subnets can host servers")
    zone_id: str = Field("", description="Availability zone — this pins the server's zone")
    vpc_id: str = Field("", description="VPC the subnet belongs to")
    secondary_subnets: list[SecondarySubnetItem] = Field(
        default_factory=list, description="Secondary CIDRs attached to this subnet"
    )

    @classmethod
    def from_api(cls, data: dict) -> "SubnetItem":
        """Build a SubnetItem from a raw vServer subnet object."""
        secondaries = data.get("secondarySubnets")
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            cidr=data.get("cidr") or "",
            status=data.get("status") or "",
            zone_id=_zone_id(data),
            vpc_id=data.get("networkUuid") or data.get("networkId") or "",
            secondary_subnets=[
                SecondarySubnetItem.from_api(s)
                for s in (secondaries if isinstance(secondaries, list) else [])
                if isinstance(s, dict)
            ],
        )


class SubnetListData(BaseModel):
    """Structured response for list_subnets."""

    vpc_id: str = Field(..., description="VPC the subnets belong to")
    subnets: list[SubnetItem] = Field(default_factory=list, description="Subnets of the VPC")


class SecurityGroupItem(BaseModel):
    """One security group."""

    id: str = Field(..., description="Security group ID")
    name: str = Field("", description="Security group name")
    description: str = Field("", description="Description")
    status: str = Field("", description="Lifecycle status; only ACTIVE groups can be attached")
    system: bool = Field(
        False,
        description="True for platform-managed groups (e.g. created by VKS) — do not delete them",
    )

    @classmethod
    def from_api(cls, data: dict) -> "SecurityGroupItem":
        """Build a SecurityGroupItem from a raw vServer secgroup object.

        A create answers with ``secgroupName`` where a list answers with
        ``name``, so both are read — otherwise a freshly created group comes
        back nameless.
        """
        return cls(
            id=_resource_id(data),
            name=data.get("name") or data.get("secgroupName") or "",
            description=data.get("description") or "",
            status=data.get("status") or "",
            system=bool(data.get("system", False)),
        )


class SecurityGroupListData(BaseModel):
    """Structured response for list_security_groups."""

    region: str = Field(..., description="Region the security groups were fetched from")
    security_groups: list[SecurityGroupItem] = Field(
        default_factory=list, description="Security groups in the project"
    )


class SecurityGroupRuleItem(BaseModel):
    """One rule inside a security group."""

    id: str = Field(..., description="Rule ID")
    direction: str = Field("", description="ingress (inbound) or egress (outbound)")
    protocol: str = Field("", description="tcp, udp, icmp or any")
    ether_type: str = Field("", description="IPv4 or IPv6")
    port_range_min: int | None = Field(None, description="Lowest port covered by the rule")
    port_range_max: int | None = Field(None, description="Highest port covered by the rule")
    remote_ip_prefix: str = Field("", description="Remote CIDR the rule applies to")
    remote_group_id: str | None = Field(
        None, description="Security group the rule belongs to, as reported by the API"
    )
    description: str = Field("", description="Rule description")
    status: str = Field("", description="Lifecycle status")

    @classmethod
    def from_api(cls, data: dict) -> "SecurityGroupRuleItem":
        """Build a SecurityGroupRuleItem from a raw vServer rule object.

        A create answers with the owning group in ``secgroupUuid`` instead of
        ``remoteGroupId``.
        """
        return cls(
            id=_resource_id(data),
            direction=data.get("direction") or "",
            protocol=data.get("protocol") or "",
            ether_type=data.get("etherType") or "",
            port_range_min=data.get("portRangeMin"),
            port_range_max=data.get("portRangeMax"),
            remote_ip_prefix=data.get("remoteIpPrefix") or "",
            remote_group_id=data.get("remoteGroupId") or data.get("secgroupUuid"),
            description=data.get("description") or "",
            status=data.get("status") or "",
        )


class SecurityGroupRuleListData(BaseModel):
    """Structured response for list_security_group_rules."""

    security_group_id: str = Field(..., description="Security group the rules belong to")
    rules: list[SecurityGroupRuleItem] = Field(
        default_factory=list, description="Rules of the security group"
    )


class SecurityGroupRuleSampleItem(BaseModel):
    """A preset the console offers when composing a security group rule."""

    name: str = Field("", description="Preset name, e.g. 'SSH', 'All TCP', 'All ICMP'")
    protocol: str = Field("", description="Protocol the preset uses")
    port_range_min: int | None = Field(None, description="Lowest port of the preset")
    port_range_max: int | None = Field(None, description="Highest port of the preset")

    @classmethod
    def from_api(cls, data: dict) -> "SecurityGroupRuleSampleItem":
        """Build a sample from a raw secgroupRules/samples entry.

        The samples endpoint uses ``ipProtocol``/``fromPort``/``toPort`` rather
        than the field names the rule endpoints use.
        """
        return cls(
            name=data.get("name") or "",
            protocol=data.get("ipProtocol") or "",
            port_range_min=data.get("fromPort"),
            port_range_max=data.get("toPort"),
        )


class SecurityGroupRuleSampleListData(BaseModel):
    """Structured response for list_security_group_rule_samples."""

    samples: list[SecurityGroupRuleSampleItem] = Field(
        default_factory=list, description="Rule presets offered by the API"
    )


class FloatingIpItem(BaseModel):
    """One floating (public WAN) IP address."""

    id: str = Field(..., description="Floating IP ID")
    ip: str = Field("", description="The public IPv4 address")
    status: str = Field("", description="ATTACHED or AVAILABLE")
    fixed_ip: str = Field("", description="Private IP it currently maps to, when attached")
    network_interface_id: str = Field(
        "", description="Network interface the IP is attached to, when attached"
    )
    zone_id: str = Field("", description="Availability zone")
    created_at: str = Field("", description="Creation timestamp")

    @classmethod
    def from_api(cls, data: dict) -> "FloatingIpItem":
        """Build a FloatingIpItem from a raw wanIps object."""
        return cls(
            id=_resource_id(data),
            ip=data.get("ip") or "",
            status=data.get("status") or "",
            fixed_ip=data.get("fixedIp") or "",
            network_interface_id=data.get("networkInterfaceId") or "",
            zone_id=_zone_id(data),
            created_at=data.get("createdAt") or "",
        )


class FloatingIpListData(BaseModel):
    """Structured response for list_floating_ips."""

    region: str = Field(..., description="Region the IPs were fetched from")
    floating_ips: list[FloatingIpItem] = Field(
        default_factory=list, description="Floating IPs in the project"
    )


class DhcpOptionItem(BaseModel):
    """One DHCP option set."""

    id: str = Field(..., description="DHCP option set ID")
    name: str = Field("", description="Name of the option set")
    status: str = Field("", description="Lifecycle status")
    dns_servers: list[str] = Field(default_factory=list, description="DNS servers handed out")
    mtu: int | None = Field(None, description="MTU handed out to instances")
    associated_vpc_ids: list[str] = Field(
        default_factory=list, description="VPCs currently using this option set"
    )

    @classmethod
    def from_api(cls, data: dict) -> "DhcpOptionItem":
        """Build a DhcpOptionItem from a raw dhcp_option object."""
        dns = data.get("dnsServers")
        networks = data.get("associatedNetworks")
        return cls(
            id=_resource_id(data),
            name=data.get("name") or "",
            status=data.get("status") or "",
            dns_servers=[str(x) for x in (dns if isinstance(dns, list) else [])],
            mtu=data.get("mtu"),
            associated_vpc_ids=[str(x) for x in (networks if isinstance(networks, list) else [])],
        )


class DhcpOptionListData(BaseModel):
    """Structured response for list_dhcp_options."""

    region: str = Field(..., description="Region the option sets were fetched from")
    dhcp_options: list[DhcpOptionItem] = Field(
        default_factory=list, description="DHCP option sets in the project"
    )
