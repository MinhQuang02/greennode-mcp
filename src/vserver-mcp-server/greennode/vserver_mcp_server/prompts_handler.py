"""MCP prompts for vServer: portable onboarding + feature-flow guidance.

A vServer "feature" here means a capability that is built by *composing several
endpoints/tools* — creating an instance means resolving a zone, a network, an
image, a flavor and a disk tier before a single write call is possible. The
step-by-step choreography of such a composite feature (which tools to call, in
what order, the guardrails and the confirm gates) lives here, not in tool
docstrings. Docstrings keep the per-tool contract; these prompts/guides carry
the multi-endpoint flow.

Each guide is served BOTH as an MCP prompt (`vserver_<name>`, loaded by the
user) and via the `get_feature_guide` tool (agents call it themselves) from a
single source of truth. Guide text is Vietnamese, matching the other GreenNode
MCP servers; code, docstrings and parameter descriptions stay English.
"""

from __future__ import annotations

from greennode.vserver_mcp_server.tool_annotations import READ
from pydantic import Field
from typing import Literal


_GETTING_STARTED = """\
# vServer (GreenNode Compute) — Bắt đầu

vServer là dịch vụ máy chủ ảo (IaaS) của GreenNode. Bạn mô tả nhu cầu bằng ngôn
ngữ tự nhiên; trợ lý tự khám phá tài nguyên, chọn default an toàn, và xác nhận
trước khi thực thi. Bạn KHÔNG cần biết ID tài nguyên thô.

## Khái niệm
- Instance (server): máy ảo. Gắn với đúng MỘT zone và không đổi zone được.
- Volume: ổ đĩa block. Boot volume là đĩa hệ điều hành; data volume gắn thêm.
  Volume và server phải CÙNG zone mới attach được.
- VPC / subnet: mạng riêng. Subnet quyết định zone của server đặt trên nó.
- Security group: tường lửa mức **instance**, chỉ có luật cho phép, có state.
  Network ACL: tường lửa mức **subnet**, có cả cho phép lẫn chặn, không state.
- Snapshot: bản sao tại một thời điểm của server (mọi volume) hoặc một volume.
- Floating IP / elastic NIC: IP public và card mạng rời gắn/tháo được.
- Virtual IP (VIP): một IP dùng chung cho nhiều instance để làm HA/failover.

## Chuẩn bị
1. MCP server đã cấu hình trong client. Thao tác đọc chạy mặc định; tạo/sửa/xoá/
   power cần chạy server với `--allow-write` (write lỗi vì read-only → báo người
   dùng khởi động lại với `--allow-write`, đừng tìm cách lách).
2. Xác thực qua `~/.greennode/` (GreenNode IAM) hoặc env (`GRN_CLIENT_ID`,
   `GRN_CLIENT_SECRET`, `GRN_PROFILE`, `GRN_PROJECT_ID`, `GRN_DEFAULT_REGION`).
   Kiểm tra bằng tool `get_access_token`.

## Region & zone — quyết định mọi thứ
- Region: `HCM-3` (mặc định) hoặc `HAN`. Tài nguyên hai region KHÔNG thấy nhau.
  Không tìm thấy thứ người dùng nhắc → thử region còn lại TRƯỚC khi kết luận.
- Zone: trong region, dạng `HCM03-1A`, `HCM03-1C`... Server + volume + subnet
  phải cùng zone. Subnet người dùng chọn CHỐT zone cho mọi bước sau.
- Project id tự resolve theo region — không bao giờ hỏi người dùng.

## Định hướng, theo thứ tự
1. `get_quota` — project còn được tạo bao nhiêu (hết quota thì create sẽ lỗi).
2. `list_servers`, `list_volumes`, `list_vpcs` — đang có sẵn những gì.
3. `list_zones` — đặt cái mới ở đâu.

## Quy tắc phiên làm việc
- Render danh sách: LUÔN để `id` và `name` là hai cột đầu. Mọi lệnh sau cần id.
- KHÔNG tự chọn thay người dùng khi có nhiều lựa chọn (zone, flavor, image, tier
  đĩa, security group). Trình ra rồi hỏi.
- Trước mọi lệnh ghi: tóm tắt plan và xin xác nhận rõ ràng. Với DESTRUCTIVE
  (xoá, rollback, detach) phải nêu đúng cái gì mất và mất vĩnh viễn.

## Tính năng tổng hợp → guide tương ứng
Mỗi tính năng dưới đây được tạo nên bằng cách kết hợp NHIỀU tool/endpoint. Lấy
choreography bằng `get_feature_guide feature=<tên>` (hoặc mở prompt
`vserver_<tên>`):
- `create_server` — Tạo instance: kết hợp discovery zone/mạng/image/flavor/đĩa
  + tạo + poll trạng thái.
- `manage_server` — Vận hành hằng ngày: power, resize, đổi tên, đĩa, mạng,
  console, xoá.
- `create_volume` — Thêm ổ đĩa: chọn tier IOPS + tạo + attach + mount trong OS.
- `create_network` — Dựng VPC + subnet + DHCP option set.
- `secure_server` — Chặn/mở cổng bằng security group.
- `snapshot_and_restore` — Sao lưu và khôi phục: snapshot thủ công, lịch tự
  động, rollback.
- `network_acl` — Tường lửa mức subnet và cách xếp lớp với security group.
- `connect_networks` — Nối ra ngoài VPC: peering, interconnect, route table.
- `high_availability` — VIP, address pair, failover kiểu keepalived.
"""


def _create_server_guidance() -> str:
    return """\
# Tạo instance vServer

Tính năng: dựng một máy ảo hoàn chỉnh. Được tạo nên bằng cách kết hợp các tool
discovery (zone → mạng → image → flavor → đĩa → khoá/tường lửa) rồi mới gọi
`create_server`. Mọi ID phải lấy từ tool discovery, TUYỆT ĐỐI không bịa.

## Chuỗi discovery (đúng thứ tự, hỏi người dùng ở mỗi bước)
1. `list_zones` — zone khả dụng. Zone `enabled=false` tồn tại nhưng không nhận
   tài nguyên mới.
2. `list_vpcs` → `list_subnets(vpc_id)` — subnet CHỐT zone. Nhiều VPC thì hỏi,
   đừng tự chọn. Zone của subnet có thể KHÁC zone ghi trên VPC — mọi bước sau
   dùng zone của SUBNET, không dùng zone của VPC.
3. `list_images` — image hệ thống. Nếu người dùng muốn nhân bản một máy đã cấu
   hình sẵn thì dùng `list_user_images`, id của nó truyền thẳng vào `imageId`.
   Ghi nhớ `image_type` của image đã chọn.
4. `list_flavor_families` → `list_flavor_codes` → `list_flavors(family, code,
   zone_id)` — cỡ máy, `zone_id` là zone của subnet. Lấy flavor ở zone khác thì
   `create_server` báo "This flavor don't support zone with ID". Kiểm tra
   `image_type` nằm trong `supported_image_types` của flavor, lệch là API từ
   chối.
5. `list_volume_types(zone_id)` — tier IOPS cho đĩa boot. `rootDiskSize` tối
   thiểu 20 GiB và phải nằm trong min/max của tier. `get_default_volume_type`
   là mặc định của cả project nên có thể không có ở zone này — đối chiếu lại,
   không có thì chọn từ danh sách của zone.
6. `list_ssh_keys` và/hoặc user+password; `list_security_groups`.
7. **UserData** — hỏi người dùng, không được lặng lẽ bỏ qua. BẮT BUỘC hỏi khi
   `imageId` là user image (xem mục UserData bên dưới). Với image hệ thống thì
   vẫn hỏi một câu, người dùng có quyền để trống.
8. `get_quota` — còn chỗ không.

## Tạo
`create_server` `{name, zoneId, networkId, subnetId, imageId, flavorId,
rootDiskTypeId, rootDiskSize, sshKeyId hoặc userName+userPassword,
securityGroup[], attachFloating?, userData?, tags?}`.

- Phải có `sshKeyId` HOẶC `userName`+`userPassword` — thiếu cả hai thì không có
  đường đăng nhập.
- `attachFloating=true` cấp IP public → máy lộ ra Internet. Nói rõ và kiểm tra
  security group chỉ mở đúng cổng cần.
- Image là **user image** thì phải chốt `userData` trước khi gọi. Bản clone
  mang theo user, hostname và service của máy gốc, nên `userName`/`userPassword`
  nhập ở đây chưa chắc là đường đăng nhập thật — nói rõ điều đó cho người dùng.
- Trình plan đầy đủ (tên, zone, flavor, image, đĩa, mạng, cách đăng nhập, có IP
  public hay không, userData gửi kèm hay không) NGAY cùng câu hỏi xác nhận.
  HARD GATE.

## UserData — script chạy ở lần boot đầu tiên
Nền tảng nhận diện loại script theo **dòng đầu tiên** của nội dung:

| Dòng đầu | Chạy bằng |
|---|---|
| `#cloud-config` | cloud-init (YAML) |
| `#!/bin/bash` | bash |
| `#!/usr/bin/env python` | python |
| `#ps1`, `#ps1_sysnative`, `#ps1_x86` | PowerShell (Windows) |
| `rem cmd` | cmd.exe (Windows) |

Directive cloud-config dùng được: `write_files`, `set_timezone`, `set_hostname`,
`ntp`, `groups`, `users`, `runcmd` — chạy theo đúng thứ tự đó; `set_hostname` và
`runcmd` có thể làm máy khởi động lại.

**Cách lấy nội dung — luôn đưa cả hai lựa chọn cho người dùng:**
1. dán thẳng nội dung script vào cuộc hội thoại, hoặc
2. chỉ đường dẫn file (`cloud-init.yaml`, `bootstrap.sh`, `init.ps1`) để bạn đọc
   file rồi đưa **nguyên văn** vào `userData`.

Gửi plain text với `userDataBase64Encoded=false`. Chỉ đặt `true` khi người dùng
đưa sẵn chuỗi đã base64 — tự encode rồi để `false` thì máy sẽ chạy chính chuỗi
base64 như script và hỏng.

Script chạy quyền root ngay lần boot đầu: đọc lại nội dung cho người dùng xác
nhận cùng plan, và TUYỆT ĐỐI không tự bịa script thay họ.

## Sau khi tạo
- Máy ở trạng thái CREATING; poll `get_server` tới ACTIVE.
- Không SSH được? `get_server_console_log` để phân biệt "OS chưa boot" với "mạng
  sai", `get_server_console_url` để vào console trực tiếp.
- Gửi userData mà máy không có gì thay đổi? `get_server_console_log` in cả log
  cloud-init — đó là chỗ duy nhất nhìn thấy script chạy tới đâu và hỏng ở đâu.
- **Image GreenNode nghe SSH ở cổng 234, RDP ở 3490** (preset `SSH VNG` /
  `RDP VNG`), không phải 22/3389. Mở nhầm 22 là không vào được.

## Không đặt được ở đây
Chu kỳ thanh toán, tự động gia hạn, PoC, OS licence, backup plan, khôi phục từ
snapshot và marketplace — cố tình không expose để agent không đổi chi phí. Người
dùng cần thì làm trên console.
"""


def _manage_server_guidance() -> str:
    return """\
# Vận hành instance đang chạy

Tính năng: thao tác ngày-thứ-hai trên một máy đã có. Được tạo nên bằng cách kết
hợp tool xem trạng thái + power + đổi cấu hình + đĩa + mạng.

## Xem trạng thái
`list_servers` → `get_server` (id, status, IP, zone, flavor, image).
`list_server_interfaces`, `list_server_volumes`, `list_server_security_groups`,
`list_server_actions` (nhật ký create/resize/reboot — giải thích trạng thái lạ).

## Power
`start_server`, `stop_server`, `reboot_server`. ACTIVE = đang chạy, STOPPED =
đã tắt. Tắt máy KHÔNG ngừng tính tiền đĩa và IP public.

## Đổi cấu hình
- `resize_server` — đổi flavor, máy **khởi động lại**. Flavor mới phải hỗ trợ
  `image_type` hiện tại (`get_flavor` để đọc `supported_image_types`). Nên
  `create_server_snapshot` trước.
- `rename_server` — chỉ đổi tên hiển thị, không ảnh hưởng OS.
- `update_server_security_groups` — THAY THẾ toàn bộ nhóm đang gắn; gửi thiếu
  một nhóm là gỡ nhóm đó. `list_server_security_groups` trước.
- `create_server_image` — chụp máy thành user image tái sử dụng được.

## Đĩa
`attach_volume` / `detach_volume` (cùng zone; unmount trong OS TRƯỚC khi
detach), `resize_volume` (chỉ tăng), `update_volume_type` (đổi tier IOPS).
Chi tiết xem guide `create_volume`.

## Mạng
- `attach_server_internal_interface` — thêm NIC private; bản
  `..._floating_ip` thêm luôn IP public.
- `attach_server_floating_ip` / `detach_server_floating_ip` — bật/tắt IP public
  trên một interface.
- `attach_server_external_interface` — chuyển một elastic NIC sang máy này.
- Gỡ NIC mang IP private chính = mất SSH. Xem `list_server_interfaces` và xác
  nhận trước.

## Chẩn đoán
`get_server_console_log` (đuôi log serial: kernel panic, lỗi filesystem,
cloud-init hỏng) và `get_server_console_url` (VNC trong trình duyệt, URL có hạn
— đưa cho người dùng, đừng log ra chỗ chung).

## Xoá
`delete_server` — DESTRUCTIVE. `delete_all_volumes=true` xoá luôn mọi đĩa của
máy. Gọi `list_server_volumes` trước, chỉ rõ đĩa nào sẽ mất, xin xác nhận. IP
public trả về pool và KHÔNG lấy lại được địa chỉ cũ.
"""


def _create_volume_guidance() -> str:
    return """\
# Thêm ổ đĩa block

Tính năng: cấp thêm dung lượng cho một máy. Được tạo nên bằng cách kết hợp tool
chọn tier IOPS + tạo volume + attach + thao tác trong OS.

## Quy trình
1. `get_server` — lấy zone của máy đích. Volume **phải cùng zone**, không có
   ngoại lệ.
2. `list_volume_types(zone_id)` — người dùng chọn tier IOPS. `size` phải nằm
   trong `min_size_gb`/`max_size_gb` của tier.
3. `create_volume` `{name, zoneId, volumeTypeId, size, multiAttach?}` — tính
   tiền ngay cả khi chưa attach.
4. `attach_volume(volume_id, server_id)` — volume phải AVAILABLE (trừ khi tạo
   với `multiAttach=true`).
5. **Trong guest OS vẫn phải partition + format + mount.** vServer không làm hộ
   — nói rõ, nếu không người dùng sẽ báo "không thấy ổ đĩa".

## Sửa đĩa đã có
- `resize_volume` — chỉ TĂNG, không giảm. Cần cả `newVolumeTypeId`: đọc
  `volume_type_id` hiện tại bằng `get_volume` rồi gửi lại nếu chỉ đổi dung
  lượng. Tăng đĩa KHÔNG tự nới filesystem trong OS.
- `update_volume_type` — chỉ đổi tier IOPS. Có thể phải migrate dữ liệu
  (`confirmMigrate`), lúc đó hiệu năng giảm và đĩa lớn mất hàng giờ.
- `list_volume_history` — đã từng tăng/đổi tier lúc nào.

## Dọn dẹp
`detach_volume` (unmount trong OS trước, tránh hỏng dữ liệu) rồi `delete_volume`
(DESTRUCTIVE, mất dữ liệu). Volume AVAILABLE không gắn ai vẫn bị tính tiền —
`list_volumes` rồi chỉ ra volume nào không có `server_id` khi người dùng soát
chi phí.

## Lưu ý
- `get_server_boot_volume` cho biết đĩa hệ điều hành: KHÔNG detach/xoá đĩa này.
- `list_persistent_volumes` là đĩa do cụm VKS tạo. Xoá đúng cách là xoá qua
  Kubernetes; `delete_persistent_volume` đi vòng sau lưng cụm.
"""


def _create_network_guidance() -> str:
    return """\
# Dựng mạng: VPC, subnet, DHCP

Tính năng: tạo lớp mạng cho instance chạy trên đó. Được tạo nên bằng cách kết
hợp tool VPC + subnet (+ DHCP option set nếu cần DNS riêng).

## Tạo VPC — theo đúng form "Create VPC" trên console
1. `get_vpc_config_options(region)` — một lần gọi trả đủ những gì form cần:
   luật đặt tên, `mtu_options` + `recommended_mtu`, luật CIDR của VPC
   (`vpc_cidr_prefix` luôn là `/16`, `vpc_cidr_ranges`) và
   `vpc_cidrs_in_use` — CIDR mà các VPC hiện có trong region đang giữ.
2. `list_zones(region)` — trình bày như console: nhóm **Availability Zone**
   rồi **Local Zone** (`zone_type`), đánh dấu zone mặc định (`is_default`).
   Zone có mô tả "Contact to enable" phải được GreenNode support mở cho
   account — nói rõ trước khi người dùng chọn.
3. HỎI người dùng từng trường của form, KHÔNG tự điền thay:
   - **Tên VPC** — 5–50 ký tự, chỉ chữ (a-z, A-Z), số, `_` và `-`.
   - **CIDR** — người dùng chỉ chọn **địa chỉ gốc**, đuôi luôn là **/16**
     (không có lựa chọn kích thước). Dải cho phép:
     `10.0.0.0 – 10.255.0.0`, `172.16.0.0 – 172.24.0.0`, `192.168.0.0`.
     Hiển thị `vpc_cidrs_in_use` để người dùng chọn một /16 CHƯA dùng — VPC
     mới không được trùng/chồng lấn VPC nào trong region (kể cả VPC đang
     CREATING/DELETING). TUYỆT ĐỐI không đưa /18, /20, … ra làm lựa chọn cho
     VPC: đó là `subnet_cidr_prefixes`, chỉ dùng cho subnet.
   - **MTU** (byte) — từ `mtu_options`; console chọn sẵn **1500**, gợi ý 1500
     là đáp án khuyến nghị nhưng vẫn hỏi, vì **không đổi được về sau**. Khi
     người dùng phân vân: 1500 là Ethernet chuẩn, an toàn nhất; 8500/8950 là
     jumbo frame, tăng throughput east-west trong VPC nhưng hỏng mọi đường
     không mang nổi (VPN, peering, ra internet); 1450 chừa chỗ cho tunnel.
   - **Zone** — BẮT BUỘC, như dấu `*` trên form. Truyền `id` (vd `HCM03-1C`),
     không truyền tên hiển thị (`HCM-1C`).
   - **Tag** — tuỳ chọn, cặp key/value.
4. Tóm tắt đủ 5 trường → xin xác nhận → `create_vpc`. Tool tự kiểm lại
   chồng lấn CIDR với danh sách VPC mới nhất và báo đúng VPC bị trùng.
5. Poll `get_vpc` tới ACTIVE (vài giây).

## Tạo subnet
- `create_subnet(vpc_id, {name, cidr, zoneId})` — CIDR phải nằm trong CIDR
  của VPC, không chồng lấn subnet khác, prefix lấy từ `subnet_cidr_prefixes`
  (/16, /18, /20, /22, /24, /26, /28). Subnet CHỐT zone cho mọi server đặt lên.
- `list_subnets(vpc_id)` xác nhận ACTIVE rồi mới tạo server.

## Sai MTU/CIDR thì sao?
Không sửa được: `update_vpc` chỉ đổi tên và tag. Muốn khác thì phải xoá VPC
(sau khi dọn hết subnet/server) và tạo lại — nên đây là câu hỏi phải hỏi kỹ
ngay từ đầu, không phải thứ điền đại rồi sửa sau.

## Tuỳ chọn
- `create_secondary_subnet` — thêm dải CIDR phụ cho subnet (LVS, sub-interface).
  Phải bind từng interface bằng `create_secondary_subnet_address_pair` và cấu
  hình địa chỉ trong OS.
- `create_dhcp_option` + `update_vpc_dhcp_option` — DNS server riêng và MTU cấp
  qua DHCP. Hai DNS mặc định của GreenNode luôn có; thêm tối đa 2 cái nữa
  (tổng 4). Lưu ý MTU ở đây là MTU DHCP phát cho instance, KHÁC với `mtu` của
  chính VPC (chỉ set được lúc `create_vpc`).
- `enable_vpc_dns` — bật phân giải tên nội bộ trong VPC. **Một chiều**, API
  không có lệnh tắt. Máy đang chạy có thể cần renew DHCP/reboot mới nhận.

## Dọn dẹp (đúng thứ tự)
Server/NIC → subnet → VPC. `delete_subnet` bị từ chối khi còn instance
(`list_subnet_servers` cho biết máy nào), `delete_vpc` bị từ chối khi còn subnet.

## Quota
`get_quota` trước khi tạo — số VPC mỗi project có trần (đã gặp 8 ở HCM-3, 5 ở
HAN), hết quota thì `create_vpc` lỗi `Exceeded VPC quota` dù mọi tham số đều
đúng. VPC đang `DELETING` vẫn bị tính, nên xoá xong phải chờ mới tạo được cái
mới. Lưu ý MTU được API kiểm TRƯỚC quota: báo lỗi quota nghĩa là MTU đã hợp lệ.
"""


def _secure_server_guidance() -> str:
    return """\
# Bảo vệ instance bằng security group

Tính năng: mở đúng cổng cần và không hơn. Được tạo nên bằng cách kết hợp tool
nhóm + luật + preset + gắn vào máy.

## Bản chất
Security group nằm ở mức **instance**, chỉ có luật CHO PHÉP (không có luật
chặn), và **có state** — đã cho phép chiều vào thì chiều trả lời tự động qua,
không cần luật ngược. Cần chặn một dải IP → dùng network ACL (guide
`network_acl`).

## Quy trình
1. `list_security_groups` — `system=true` là nhóm nền tảng quản lý, đừng sửa.
2. `create_security_group {name, description}` nếu cần nhóm mới.
3. `list_security_group_rule_samples` — 30 preset của chính API. Người dùng nói
   "mở SSH" thì khớp vào preset thay vì đoán cổng.
   **`SSH` là 22 nhưng `SSH VNG` là 234; `RDP` là 3389 còn `RDP VNG` là 3490.**
   Image GreenNode nghe ở cổng VNG và security group mặc định mở sẵn
   234/3490/80/443/ICMP — với image GreenNode hãy đề xuất preset VNG trước và
   giải thích vì sao.
4. `create_security_group_rule` `{direction (ingress/egress), protocol,
   portRangeMin/Max, remoteIpPrefix, etherType}`. Bỏ trống cặp port = full
   range. `protocol` nhận tcp/udp/icmp/any và cả số hiệu IANA dạng chuỗi: `47`
   GRE, `50` ESP, `51` AH, `112` VRRP (keepalived cần cái này).
5. `update_server_security_groups` — THAY THẾ toàn bộ nhóm đang gắn trên máy.

## Guardrail
- `remoteIpPrefix=0.0.0.0/0` là mở ra toàn Internet. Nói thẳng và xin xác nhận,
  nhất là với cổng truy cập từ xa và cổng database. Ưu tiên CIDR hẹp nhất —
  CIDR của VPC nếu chỉ cần nội bộ.
- Luật chỉ sửa được `description` và tags; đổi cổng/protocol/CIDR phải xoá rồi
  tạo lại.
- Trước khi sửa/xoá nhóm: `list_security_group_servers` cho biết máy nào chịu
  ảnh hưởng. Xoá luật truy cập từ xa = tự khoá mình ra ngoài.
"""


def _snapshot_and_restore_guidance() -> str:
    return """\
# Snapshot và khôi phục

Tính năng: tạo điểm khôi phục và quay lại nó. Được tạo nên bằng cách kết hợp
tool chụp thủ công + cấu hình lịch tự động + rollback.

Snapshot là "undo" duy nhất của vServer, và cũng là thao tác sắc nhất: rollback
xoá sạch mọi thứ ghi sau thời điểm snapshot.

## Hai cấp
- **Server snapshot** (`create_server_snapshot`): chụp MỌI volume của máy tại
  cùng một thời điểm. Dùng khi cả máy phải quay về nguyên khối.
- **Volume snapshot** (`create_volume_snapshot`): chụp một đĩa.

Trộn hai loại là cách người ta có boot disk của thứ Ba và data disk của thứ Năm.
Người dùng nói "backup máy này" → chụp server snapshot.

## Chụp ngay
1. `list_servers` / `list_volumes` lấy id.
2. Hỏi **thời gian giữ**: `retainedDays` tự xoá; `isPermanently` giữ mãi và
   tính tiền tới khi ai đó xoá tay. ĐỪNG mặc định chọn permanent.
3. `description` là bắt buộc — ghi mục đích, không ghi "snapshot".
4. Cảnh báo tính nhất quán: snapshot máy đang chạy là crash-consistent, database
   có thể phải recovery khi khôi phục. Workload có state thì nên dừng/quiesce.

## Lịch tự động
1. `get_server_snapshot_policy` — `configured=false` nghĩa là chưa có gì để bật.
2. `create_server_snapshot_policy` dựng cấu hình; `enable_server_auto_snapshot`
   khởi động lịch.
3. `snapshotPolicyId` (tần suất + retention) lấy từ `list_snapshot_policies`;
   đọc `name` + `schedule` cho người dùng chọn, đừng đoán id.
4. Với volume, `enable_volume_auto_snapshot` cần CẢ volume id LẪN id của server
   đang gắn nó.

Nói trước rằng đây là dung lượng tính tiền lặp lại theo lịch.

## Rollback — HARD GATE
`rollback_server_snapshot` / `rollback_volume_snapshot` không thể hoàn tác:
1. **Chụp một snapshot mới TRƯỚC** — đó là đường lui duy nhất nếu chọn nhầm
   điểm. Tự làm, không cần đợi người dùng nhắc.
2. `list_server_snapshots` — chỉ ra `created_at` của điểm đích và nói rõ bằng
   lời sẽ mất bao nhiêu ("mọi thay đổi từ 14:00 thứ Ba trở đi sẽ mất").
3. Máy bị tắt để rollback; hỏi có bật lại không
   (`restartServerWhenRevertCompleted`).
4. Bắt buộc có câu đồng ý rõ ràng. Không bao giờ nối rollback vào một thao tác
   khác.

## Xoá
- `delete_server_snapshot` / `delete_volume_snapshot`: bỏ MỘT điểm khôi phục.
- `delete_*_snapshot_policy`: bỏ cấu hình **và mọi điểm khôi phục dưới nó**.
  Đây KHÔNG phải cách "ngừng chụp tự động" — cái đó là
  `disable_*_auto_snapshot`. Hỏi lại cho rõ người dùng muốn gì.

Snapshot là incremental nên xoá một điểm giải phóng ít hơn `size_gb` của nó.
"""


def _network_acl_guidance() -> str:
    return """\
# Network ACL (tường lửa mức subnet)

Tính năng: đặt luật cho cả một subnet, gồm cả luật CHẶN. Được tạo nên bằng cách
kết hợp tool tạo ACL + đặt luật + gắn subnet.

## Chọn cái nào
| | Network ACL | Security group |
|---|---|---|
| Phạm vi | cả subnet | từng instance |
| Luật | cho phép **và** chặn | chỉ cho phép |
| Xét luật | khớp đầu tiên theo `seqNumber` thắng | xét hết mọi luật |
| State | **không** có state | có state |

Mặc định dùng security group. Chỉ dùng ACL khi cần **chặn** (một dải IP), hoặc
cần một mức sàn cho cả subnet mà từng instance không tự bỏ qua được.

## Bẫy lớn nhất: không có state
ACL không nhớ kết nối. Cho phép vào cổng 443 KHÔNG cho phép gói trả lời đi ra.
Mỗi cặp request/response cần luật ở CẢ HAI chiều — thường là dải ephemeral
1024-65535 ở chiều về. Nói rõ điều này khi đề xuất luật; đây là cách phổ biến
nhất khiến ACL làm sập một hệ thống đang chạy.

## Quy trình
1. `list_vpcs` — chọn VPC.
2. `create_network_acl {name, vpc}` — ACL mới chỉ có 4 luật mặc định bất biến
   (cho phép tất cả ở seq 0, chặn tất cả ở seq 2000, mỗi chiều) và chưa quản
   subnet nào.
3. `update_network_acl_rules` — gửi **toàn bộ** tập luật, cả hai chiều. Đây là
   thay thế: gọi `list_network_acl_rules` trước và gửi lại luật muốn giữ. Đừng
   gửi lại 4 luật `system=true`. Số `seqNumber` để trong khoảng 1-1999 (ngoài
   khoảng đó không bao giờ chạy) và chừa khoảng trống (10, 20, 30) để chèn sau.
4. `update_network_acl_subnets` — gắn subnet **sau cùng**. Gắn trước sẽ có một
   khoảng thời gian subnet chạy bằng luật allow-all mặc định.

## Trước khi gắn hoặc sửa
- `list_subnet_servers` cho từng subnet liên quan — đó là bán kính ảnh hưởng,
  mọi máy trong đó đổi cùng lúc.
- Một subnet chỉ thuộc MỘT ACL, nên gắn vào đây sẽ âm thầm gỡ nó khỏi ACL cũ.
- Xoá ACL làm subnet **mở toang**: rơi về ACL mặc định của VPC, vốn cho qua tất.

Trình ra danh sách luật đã sắp thứ tự và các máy chịu ảnh hưởng, rồi mới hỏi.
"""


def _connect_networks_guidance() -> str:
    return """\
# Kết nối ra ngoài VPC

Tính năng: cho tài nguyên trong VPC nói chuyện với mạng khác. Được tạo nên bằng
cách kết hợp tool tạo đường đi (peering / interconnect) + tool định tuyến
(route table). Chọn theo đầu bên kia nằm ở đâu:

| Đầu bên kia | Dùng |
|---|---|
| VPC khác trong cùng project | **peering** |
| On-premises, cloud khác, region còn lại | **interconnect** |
| Bất kỳ — điều hướng traffic tới đó | **route table** |

Peering/interconnect tạo ra **đường**; route table quyết định traffic nào đi vào
đường đó. Thiếu một trong hai thì không thông.

## Peering
vServer **không có API tạo peering** — phải yêu cầu GreenNode hỗ trợ
(support@greennode.ai). `list_peerings` xem cái đang có, `delete_peering` gỡ.
Peering ACTIVE mà hai bên vẫn không thấy nhau thì gần như chắc chắn thiếu route:
kiểm tra `list_route_tables` ở CẢ HAI phía.

Xoá peering là phải mở ticket mới có lại. Nói trước khi gọi.

## Route table
1. `list_route_tables` — mỗi VPC có sẵn một cái; `routes` rỗng nghĩa là chỉ định
   tuyến mặc định, không phải hỏng.
2. `get_route_table` — đọc route hiện tại.
3. `update_route_table_routes` — **thay thế toàn bộ**. Gửi lại mọi route muốn
   giữ kèm route mới; bỏ sót một route là traffic đó rơi vào hố đen ngay.

Mỗi route gồm `destinationCidrBlock` và `target` (next hop). Destination không
được chồng lấn CIDR của chính VPC — traffic nội bộ luôn thắng.

## Interconnect
Đường truyền riêng, có hợp đồng, **tính tiền hàng tháng**. Không bao giờ tạo khi
chưa được duyệt rõ ràng về gói băng thông và tuỳ chọn dự phòng.
1. `list_interconnect_packages` và `list_interconnect_circuit_types` — trình cả
   hai, để người dùng chọn. Gói quyết định băng thông cam kết và giá.
2. `create_interconnect` — `enableGw2` thêm gateway thứ hai để dự phòng và
   **tăng chi phí**; mặc định để tắt. Đường này còn cần đấu nối vật lý nên tạo
   xong chưa chạy được ngay.
3. `create_interconnect_connection` — mỗi VPC muốn expose một cái. `subnets` là
   CIDR **phía khách hàng**, không được chồng lấn CIDR của VPC.
4. `update_route_table_routes` — thêm route cho các CIDR đó.
5. `ping_interconnect` — chứng minh đường đã lên, tách bạch "sai route" với
   "đường chưa sẵn sàng".

`update_interconnect_package` đổi giá; hạ gói làm rớt traffic vượt mức mới. Xác
nhận trước cả hai.
"""


def _high_availability_guidance() -> str:
    return """\
# High availability bằng Virtual IP

Tính năng: một địa chỉ IP được nhiều instance cùng nhận, để failover không làm
client thấy gì thay đổi. Được tạo nên bằng cách kết hợp tool tạo VIP + liệt kê
interface ứng viên + bind address pair. Đây là phương án rẻ thay cho load
balancer với cặp active/standby.

## Ràng buộc cứng
**Chỉ instance CÙNG subnet với VIP mới dùng chung được VIP đó.** Chốt subnet
trước; VIP không kéo dài qua subnet hay qua zone.

## VIP private, theo thứ tự
1. `list_subnets` — chọn subnet chứa các instance.
2. `create_virtual_ip` — `mode` bắt buộc:
   - `Active/Passive` cho failover kiểu keepalived, một máy trả lời tại một thời
     điểm.
   - `Active/Active` để chia tải giữa các máy.
   Bỏ trống `ipAddress` để hệ thống cấp một địa chỉ trống.
3. `list_virtual_ip_candidate_interfaces` — **tự lọc theo `subnet_id` của VIP**,
   API không lọc hộ.
4. `create_virtual_ip_address_pair` — mỗi instance trong cặp HA một lần.
5. `list_virtual_ip_address_pairs` — kiểm tra đủ mặt.

## Bước vServer không làm thay
Bind interface chỉ báo cho hạ tầng chấp nhận địa chỉ đó. **Trong guest OS vẫn
phải cấu hình** keepalived hoặc gán địa chỉ phụ lên interface. Nói rõ, nếu không
người dùng sẽ báo VIP hỏng trong khi phía nền tảng hoàn toàn đúng.

Nếu dùng keepalived: mở protocol VRRP (`112`) trong security group giữa các máy,
xem guide `secure_server`.

## VIP public
Cùng cấu trúc, khác tool: `create_public_virtual_ip` (`type` = `public-vm` cho
instance, `public-mkp` cho appliance vMarketplace),
`list_public_virtual_ip_candidate_interfaces`,
`create_public_virtual_ip_address_pair`. Tốn một IP public trong quota và tính
tiền; máy phía sau lộ ra Internet nên soát security group trước khi bind.

## Secondary subnet
Khi cần nhiều địa chỉ cho MỘT máy chứ không phải địa chỉ dùng chung (LVS,
sub-interface): `create_secondary_subnet` thêm dải CIDR vào subnet, rồi
`create_secondary_subnet_address_pair` bind từng interface được phép dùng. Địa
chỉ vẫn phải cấu hình trong OS.

## Tháo dỡ
Gỡ pair của máy đang giữ VIP làm traffic ngưng tới khi máy khác tiếp quản. Xoá
VIP là trả địa chỉ về pool — bản ghi DNS và allow-list bên ngoài sẽ hỏng, và
không lấy lại được địa chỉ cũ. Trình ra thứ đang phụ thuộc rồi mới hỏi.
"""


_FEATURE_GUIDES: dict[str, tuple[str, str]] = {
    "getting_started": ("vServer — định hướng", _GETTING_STARTED),
    "create_server": ("Tạo instance vServer", _create_server_guidance()),
    "manage_server": ("Vận hành instance đang chạy", _manage_server_guidance()),
    "create_volume": ("Thêm ổ đĩa block", _create_volume_guidance()),
    "create_network": ("Dựng VPC và subnet", _create_network_guidance()),
    "secure_server": ("Bảo vệ instance bằng security group", _secure_server_guidance()),
    "snapshot_and_restore": ("Snapshot và khôi phục", _snapshot_and_restore_guidance()),
    "network_acl": ("Tường lửa mức subnet bằng network ACL", _network_acl_guidance()),
    "connect_networks": ("Peering, interconnect và route table", _connect_networks_guidance()),
    "high_availability": ("Virtual IP và failover", _high_availability_guidance()),
}

Feature = Literal[
    "getting_started",
    "create_server",
    "manage_server",
    "create_volume",
    "create_network",
    "secure_server",
    "snapshot_and_restore",
    "network_acl",
    "connect_networks",
    "high_availability",
]


class PromptsHandler:
    """Register portable vServer guidance prompts + the get_feature_guide tool.

    Each guide covers one composite capability — a feature built by combining
    several endpoints/tools. Guides are served BOTH as MCP prompts (loaded by
    the user) and as the get_feature_guide tool (agents call it on their own)
    from one source of truth.
    """

    def __init__(self, mcp):
        self.mcp = mcp

        self.mcp.tool(name="get_feature_guide", annotations=READ)(self.get_feature_guide)

        self.mcp.prompt(name="vserver_getting_started")(self.vserver_getting_started)
        self.mcp.prompt(name="vserver_create_server")(self.vserver_create_server)
        self.mcp.prompt(name="vserver_manage_server")(self.vserver_manage_server)
        self.mcp.prompt(name="vserver_create_volume")(self.vserver_create_volume)
        self.mcp.prompt(name="vserver_create_network")(self.vserver_create_network)
        self.mcp.prompt(name="vserver_secure_server")(self.vserver_secure_server)
        self.mcp.prompt(name="vserver_snapshot_and_restore")(self.vserver_snapshot_and_restore)
        self.mcp.prompt(name="vserver_network_acl")(self.vserver_network_acl)
        self.mcp.prompt(name="vserver_connect_networks")(self.vserver_connect_networks)
        self.mcp.prompt(name="vserver_high_availability")(self.vserver_high_availability)

    async def get_feature_guide(
        self,
        feature: Feature = Field(
            ...,
            description=(
                "Which composite capability to load the flow for: "
                "'getting_started' (orientation, regions and zones), "
                "'create_server' (the full instance-creation chain), "
                "'manage_server' (power, resize, disks, networking, deletion), "
                "'create_volume' (block storage), 'create_network' (VPC, subnets, "
                "DHCP), 'secure_server' (security groups), 'snapshot_and_restore' "
                "(snapshots, schedules, rollback), 'network_acl' (subnet-level "
                "firewalling), 'connect_networks' (peering, interconnect, route "
                "tables), 'high_availability' (virtual IPs and failover)."
            ),
        ),
    ) -> str:
        """Get the step-by-step guide for a multi-endpoint vServer feature.

        Call this BEFORE starting any creation or management flow. The guide
        gives the tool order, which questions to ask the user, and the confirm
        gates — detail the individual tool docstrings deliberately leave out.
        Guide text is Vietnamese.
        """
        title, body = _FEATURE_GUIDES[feature]
        return f"{title}\n\n{body}"

    async def vserver_getting_started(self) -> str:
        """Onboarding: what vServer manages, regions and zones, the composite guides."""
        return _GETTING_STARTED

    async def vserver_create_server(self) -> str:
        """Guided flow for creating a vServer instance end to end."""
        return _create_server_guidance()

    async def vserver_manage_server(self) -> str:
        """Guided flow for power, resize, storage, networking and deletion."""
        return _manage_server_guidance()

    async def vserver_create_volume(self) -> str:
        """Guided flow for creating and attaching a block-storage volume."""
        return _create_volume_guidance()

    async def vserver_create_network(self) -> str:
        """Guided flow for building a VPC with subnets and DHCP options."""
        return _create_network_guidance()

    async def vserver_secure_server(self) -> str:
        """Guided flow for firewalling an instance with security groups."""
        return _secure_server_guidance()

    async def vserver_snapshot_and_restore(self) -> str:
        """Guided flow for taking snapshots, scheduling them and rolling back."""
        return _snapshot_and_restore_guidance()

    async def vserver_network_acl(self) -> str:
        """Guided flow for subnet-level firewalling with network ACLs."""
        return _network_acl_guidance()

    async def vserver_connect_networks(self) -> str:
        """Guided flow for peering, interconnect and route tables."""
        return _connect_networks_guidance()

    async def vserver_high_availability(self) -> str:
        """Guided flow for virtual IPs, address pairs and failover."""
        return _high_availability_guidance()
