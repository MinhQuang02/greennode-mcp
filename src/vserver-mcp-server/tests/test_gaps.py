"""Tests for the tools filling gaps in the original handlers.

These cover the endpoints the CLI never exposed: console log, servers by
subnet, boot volume, volume history and tier change, persistent volumes,
secondary subnets, the tag catalogue and the v1 by-id detail envelope.
"""

from __future__ import annotations

import httpx
import json
import pytest
import respx
from greennode.vserver_mcp_server.auth import TokenManager
from greennode.vserver_mcp_server.client import VserverClient
from greennode.vserver_mcp_server.config import load_config
from greennode.vserver_mcp_server.discovery_cache import DiscoveryCache
from greennode.vserver_mcp_server.flavor_handler import FlavorHandler
from greennode.vserver_mcp_server.models import (
    ChangeVolumeTypeDto,
    CreateSecondarySubnetDto,
    DeletePersistentVolumeDto,
    PersistentVolumeItem,
    UpdateSubnetDto,
    VolumeHistoryItem,
)
from greennode.vserver_mcp_server.paging import unwrap_one
from greennode.vserver_mcp_server.server_handler import ServerHandler
from greennode.vserver_mcp_server.subnet_handler import SubnetHandler
from greennode.vserver_mcp_server.userimage_handler import UserImageHandler
from greennode.vserver_mcp_server.volume_handler import VolumeHandler
from greennode.vserver_mcp_server.volumetype_handler import VolumeTypeHandler
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError


IAM_URL = "https://iamapis.vngcloud.vn/accounts-api/v1/auth/token"
HCM3 = "https://hcm-3.api.vngcloud.vn/vserver/vserver-gateway"
PROJECT = "pro-test-0001"
SERVER = "ins-1111"
VOLUME = "vol-2222"
VPC = "net-3333"
SUBNET = "sub-4444"


def _mock_iam(mock: respx.MockRouter) -> None:
    mock.post(IAM_URL).mock(
        return_value=httpx.Response(200, json={"accessToken": "tok", "expiresIn": 1800})
    )


@pytest.fixture
def config(sample_config):
    return load_config(sample_config)


@pytest.fixture
def client(config):
    return VserverClient(config, TokenManager(config))


@pytest.fixture
def servers(config, client):
    return ServerHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def volumes(config, client):
    return VolumeHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def volumes_ro(config, client):
    return VolumeHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=False)


@pytest.fixture
def subnets(config, client):
    return SubnetHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def tags(config, client):
    return UserImageHandler(MCPServer("t"), config, client, DiscoveryCache(), allow_write=True)


@pytest.fixture
def volume_types(config, client):
    return VolumeTypeHandler(MCPServer("t"), config, client, DiscoveryCache())


@pytest.fixture
def flavors(config, client):
    return FlavorHandler(MCPServer("t"), config, client, DiscoveryCache())


# ── the v1 by-id envelope ─────────────────────────────────────────────────────


def test_v1_detail_envelope_yields_the_single_resource():
    payload = {
        "success": True,
        "errorCode": None,
        "errorMsg": None,
        "extra": {},
        "volumeTypes": [{"id": "vtype-1", "name": "5000", "iops": 5000}],
    }
    assert unwrap_one(payload)["id"] == "vtype-1"


def test_a_resource_holding_one_list_is_not_collapsed():
    route_table = {
        "uuid": "rt-1",
        "name": "rt",
        "routes": [{"destinationCidrBlock": "10.0.0.0/8"}],
    }
    assert unwrap_one(route_table)["uuid"] == "rt-1"


@respx.mock
@pytest.mark.asyncio
async def test_get_volume_type_reads_the_v1_envelope(volume_types):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v1/{PROJECT}/volume_types/vtype-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "volumeTypes": [
                    {"id": "vtype-1", "name": "5000", "iops": 5000, "minSize": 1, "maxSize": 30000}
                ],
            },
        )
    )
    result = await volume_types.get_volume_type(volume_type_id="vtype-1", region="HCM-3")
    assert (result.id, result.iops, result.max_size_gb) == ("vtype-1", 5000, 30000)


@respx.mock
@pytest.mark.asyncio
async def test_get_flavor_reads_the_v1_envelope(flavors):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v1/{PROJECT}/flavors/flav-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "flavors": [
                    {"flavorId": "flav-1", "name": "s-general-2x4", "cpu": 2, "memory": 4}
                ],
            },
        )
    )
    result = await flavors.get_flavor(flavor_id="flav-1", region="HCM-3")
    assert (result.id, result.vcpu, result.ram_gb) == ("flav-1", 2, 4)


@respx.mock
@pytest.mark.asyncio
async def test_default_volume_type_is_resolved_to_its_detail(volume_types):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v1/{PROJECT}/volume_default_id").mock(
        return_value=httpx.Response(200, json={"volumeTypeId": "vtype-9", "volumeTypeZoneId": "z"})
    )
    respx.get(f"{HCM3}/v1/{PROJECT}/volume_types/vtype-9").mock(
        return_value=httpx.Response(
            200, json={"success": True, "volumeTypes": [{"id": "vtype-9", "name": "3000"}]}
        )
    )
    result = await volume_types.get_default_volume_type(region="HCM-3")
    assert (result.id, result.name) == ("vtype-9", "3000")


# ── server gaps ───────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_console_log_keeps_the_tail_and_flags_truncation(servers):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/console-log").mock(
        return_value=httpx.Response(200, json={"data": "\n".join(f"line{i}" for i in range(50))})
    )
    result = await servers.get_server_console_log(server_id=SERVER, lines=5, region="HCM-3")
    assert result.truncated is True
    assert result.log.splitlines() == ["line45", "line46", "line47", "line48", "line49"]


@respx.mock
@pytest.mark.asyncio
async def test_console_log_short_output_is_not_truncated(servers):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/console-log").mock(
        return_value=httpx.Response(200, json={"data": "boot ok"})
    )
    result = await servers.get_server_console_log(server_id=SERVER, lines=200, region="HCM-3")
    assert (result.truncated, result.log) == (False, "boot ok")


@respx.mock
@pytest.mark.asyncio
async def test_list_subnet_servers_reads_a_bare_array(servers):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/servers/subnets/{SUBNET}").mock(
        return_value=httpx.Response(
            200, json=[{"uuid": SERVER, "name": "web-1", "status": "ACTIVE"}]
        )
    )
    result = await servers.list_subnet_servers(subnet_id=SUBNET, region="HCM-3")
    assert [s.id for s in result.servers] == [SERVER]


@respx.mock
@pytest.mark.asyncio
async def test_floating_interface_detach_uses_its_own_path(servers):
    _mock_iam(respx.mock)
    from greennode.vserver_mcp_server.models import DetachInternalInterfacesDto

    route = respx.delete(
        f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/internal-network-interfaces-floating"
    ).mock(return_value=httpx.Response(200, json={"data": {}}))
    respx.get(f"{HCM3}/v2/{PROJECT}/servers/{SERVER}/network-interfaces").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await servers.detach_server_internal_interface_floating_ip(
        server_id=SERVER,
        body=DetachInternalInterfacesDto(networkInterfaceIds=["net-in-1"]),
        region="HCM-3",
    )
    assert route.called


# ── volume gaps ───────────────────────────────────────────────────────────────


def test_volume_history_maps_the_start_field():
    item = VolumeHistoryItem.from_api(
        {"type": "RESIZE", "size": 40, "iops": "5000", "start": "2026-07-31T15:45:23.000+07:00"}
    )
    assert (item.type, item.size_gb, item.iops) == ("RESIZE", 40, "5000")
    assert item.started_at.startswith("2026-07-31")


def test_persistent_volume_maps_volume_id_not_uuid():
    item = PersistentVolumeItem.from_api(
        {"volumeId": VOLUME, "name": "pvc-1", "size": 20, "clusterId": "cl-1", "vmId": SERVER}
    )
    assert (item.id, item.cluster_id, item.server_id) == (VOLUME, "cl-1", SERVER)


@respx.mock
@pytest.mark.asyncio
async def test_boot_volume_reads_the_success_envelope(volumes):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/volumes/servers/{SERVER}/boot").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "volumes": [{"uuid": VOLUME, "name": "root", "size": 20, "bootable": True}],
            },
        )
    )
    result = await volumes.get_server_boot_volume(server_id=SERVER, region="HCM-3")
    assert (result.id, result.bootable) == (VOLUME, True)


@respx.mock
@pytest.mark.asyncio
async def test_change_volume_type_posts_the_target_tier(volumes):
    _mock_iam(respx.mock)
    route = respx.put(f"{HCM3}/v2/{PROJECT}/volumes/{VOLUME}/change-device-type").mock(
        return_value=httpx.Response(200, json={"data": {"uuid": VOLUME}})
    )
    await volumes.update_volume_type(
        volume_id=VOLUME,
        body=ChangeVolumeTypeDto(volumeTypeId="vtype-9", confirmMigrate=True),
        region="HCM-3",
    )
    body = route.calls[0].request.read()
    assert b'"volumeTypeId":"vtype-9"' in body and b'"confirmMigrate":true' in body


@respx.mock
@pytest.mark.asyncio
async def test_persistent_volume_delete_defaults_to_non_forced(volumes):
    _mock_iam(respx.mock)
    route = respx.delete(f"{HCM3}/v2/{PROJECT}/persistent-volumes/{VOLUME}").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    await volumes.delete_persistent_volume(
        persistent_volume_id=VOLUME, body=DeletePersistentVolumeDto(), region="HCM-3"
    )
    body = route.calls[0].request.read()
    assert b"forceDelete" not in body
    assert b'"persistentVolumeId":"' + VOLUME.encode() + b'"' in body


@pytest.mark.asyncio
async def test_new_volume_write_tools_are_gated(volumes_ro):
    names = {t.name for t in await volumes_ro.mcp.list_tools()}
    assert "update_volume_type" not in names
    assert {"list_volume_history", "get_server_boot_volume", "list_persistent_volumes"} <= names


# ── secondary subnets and tags ────────────────────────────────────────────────


def _subnet_payload(*secondaries: dict, name: str = "web") -> dict:
    """A subnet object shaped exactly like the live detail/list/PATCH answer."""
    return {
        "uuid": SUBNET,
        "status": "ACTIVE",
        "cidr": "10.0.0.0/22",
        "networkUuid": VPC,
        "name": name,
        "zone": {"uuid": "HCM03-1C", "name": "HCM-1C", "zoneType": "AVAILABILITY"},
        "secondarySubnets": list(secondaries),
    }


SEC_A = {"cidr": "10.0.8.0/24", "name": "sec-a", "uuid": "sec-sub-aaaa"}
SEC_B = {"cidr": "10.0.9.0/24", "name": "sec-b", "uuid": "sec-sub-bbbb"}
SUBNET_PATH = f"{HCM3}/v2/{PROJECT}/networks/{VPC}/subnets/{SUBNET}"


@respx.mock
@pytest.mark.asyncio
async def test_create_secondary_subnet_returns_the_parent_subnet(subnets):
    """The POST answers with the NEW RANGE ({cidr, name, uuid}), not the subnet.

    Parsed as a SubnetItem that gave status/zone_id/vpc_id = "" and, worse, an
    `id` holding the secondary range's uuid — which an agent then passes on as
    a subnet id. The tool re-reads the parent subnet instead.
    """
    _mock_iam(respx.mock)
    post = respx.post(f"{SUBNET_PATH}/secondary-subnets").mock(
        return_value=httpx.Response(200, json={"data": SEC_B})
    )
    respx.get(SUBNET_PATH).mock(
        return_value=httpx.Response(200, json=_subnet_payload(SEC_A, SEC_B))
    )
    result = await subnets.create_secondary_subnet(
        vpc_id=VPC,
        subnet_id=SUBNET,
        body=CreateSecondarySubnetDto(name="sec-b", cidr="10.0.9.0/24"),
        region="HCM-3",
    )
    assert post.calls[0].request.read() == b'{"name":"sec-b","cidr":"10.0.9.0/24"}'
    assert (result.id, result.status, result.zone_id, result.vpc_id) == (
        SUBNET,
        "ACTIVE",
        "HCM03-1C",
        VPC,
    )
    assert [(s.id, s.cidr) for s in result.secondary_subnets] == [
        ("sec-sub-aaaa", "10.0.8.0/24"),
        ("sec-sub-bbbb", "10.0.9.0/24"),
    ]


@respx.mock
@pytest.mark.asyncio
async def test_rename_only_update_keeps_the_secondary_subnets(subnets):
    """Verified live: a PATCH without `secondarySubnetRequests` DELETES them all.

    So a plain rename re-sends the current set, read from the subnet first.
    """
    _mock_iam(respx.mock)
    respx.get(SUBNET_PATH).mock(
        return_value=httpx.Response(200, json=_subnet_payload(SEC_A, SEC_B))
    )
    patch = respx.patch(SUBNET_PATH).mock(
        return_value=httpx.Response(
            200, json={"data": _subnet_payload(SEC_A, SEC_B, name="web-renamed")}
        )
    )
    result = await subnets.update_subnet(
        vpc_id=VPC,
        subnet_id=SUBNET,
        body=UpdateSubnetDto(name="web-renamed"),
        region="HCM-3",
    )
    sent = json.loads(patch.calls[0].request.read())
    assert sent == {
        "name": "web-renamed",
        "secondarySubnetRequests": [
            {"name": "sec-a", "cidr": "10.0.8.0/24", "uuid": "sec-sub-aaaa"},
            {"name": "sec-b", "cidr": "10.0.9.0/24", "uuid": "sec-sub-bbbb"},
        ],
    }
    assert result.name == "web-renamed" and len(result.secondary_subnets) == 2


@respx.mock
@pytest.mark.asyncio
async def test_update_with_an_explicit_empty_list_clears_them(subnets):
    """An explicit [] is the caller's decision to remove every range — sent as is."""
    _mock_iam(respx.mock)
    detail = respx.get(SUBNET_PATH)
    patch = respx.patch(SUBNET_PATH).mock(
        return_value=httpx.Response(200, json={"data": _subnet_payload()})
    )
    await subnets.update_subnet(
        vpc_id=VPC,
        subnet_id=SUBNET,
        body=UpdateSubnetDto(name="web", secondarySubnetRequests=[]),
        region="HCM-3",
    )
    assert json.loads(patch.calls[0].request.read()) == {
        "name": "web",
        "secondarySubnetRequests": [],
    }
    assert not detail.calls


def test_secondary_subnet_dto_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        CreateSecondarySubnetDto(name="x", cidr="10.0.9.0/24", uuid="sub-1")


@respx.mock
@pytest.mark.asyncio
async def test_list_tags_hides_system_tags_by_default(tags):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/tag").mock(
        return_value=httpx.Response(
            200,
            json={
                "listData": [
                    {"uuid": "tag-1", "key": "env", "value": "prod", "systemTag": False},
                    {"uuid": "tag-2", "key": "vng.serverId", "value": SERVER, "systemTag": True},
                ],
                "totalItem": 2,
            },
        )
    )
    visible = await tags.list_tags(include_system=False, region="HCM-3")
    assert [t.key for t in visible.tags] == ["env"]


@respx.mock
@pytest.mark.asyncio
async def test_tag_quota_coerces_the_string_used_count(tags):
    _mock_iam(respx.mock)
    respx.get(f"{HCM3}/v2/{PROJECT}/tag/quota").mock(
        return_value=httpx.Response(
            200,
            json={
                "quotaName": "TAG_PER_RESOURCE",
                "limit": 10,
                "used": "3",
                "type": "Server",
                "description": "Max tag per resource",
            },
        )
    )
    quota = await tags.get_tag_quota(region="HCM-3")
    assert (quota.name, quota.limit, quota.used) == ("TAG_PER_RESOURCE", 10, 3)
