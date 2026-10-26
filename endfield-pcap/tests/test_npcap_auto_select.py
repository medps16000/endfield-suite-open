import socket

import dpkt

from endfield_pcap.npcap import PCAP_IF_LOOPBACK, DeviceInfo, _parse_ipv4_from_capture, _select_requested_devices


def test_auto_select_keeps_regular_ipv4_devices() -> None:
    ethernet = DeviceInfo(
        name="eth0",
        description="Realtek Ethernet",
        ipv4_addrs=["10.0.0.2"],
        flags=0,
    )
    selected = _select_requested_devices([ethernet], "auto")
    assert selected == [ethernet]


def test_auto_select_includes_loopback_and_wan_miniport_ip_without_ipv4() -> None:
    loopback = DeviceInfo(
        name="loopback",
        description="Adapter for loopback traffic capture",
        ipv4_addrs=[],
        flags=PCAP_IF_LOOPBACK,
    )
    wan_ip = DeviceInfo(
        name="wan-ip",
        description="WAN Miniport (IP)",
        ipv4_addrs=[],
        flags=0,
    )
    selected = _select_requested_devices([loopback, wan_ip], "auto")
    assert selected == [loopback, wan_ip]


def test_auto_select_still_skips_irrelevant_devices_without_ipv4() -> None:
    monitor = DeviceInfo(
        name="wan-monitor",
        description="WAN Miniport (Network Monitor)",
        ipv4_addrs=[],
        flags=0,
    )
    selected = _select_requested_devices([monitor], "auto")
    assert selected == []


def test_parse_ipv4_prefers_ethernet_when_destination_mac_looks_like_ipv4() -> None:
    tcp = dpkt.tcp.TCP(sport=30000, dport=51529, seq=1, data=b"hello")
    ip = dpkt.ip.IP(
        src=socket.inet_aton("106.14.187.47"),
        dst=socket.inet_aton("192.168.0.105"),
        p=dpkt.ip.IP_PROTO_TCP,
        data=tcp,
    )
    ip.len = len(ip)
    eth = dpkt.ethernet.Ethernet(
        dst=b"\x45\x11\x22\x33\x44\x55",
        src=b"\x10\x20\x30\x40\x50\x60",
        type=dpkt.ethernet.ETH_TYPE_IP,
        data=ip,
    )

    parsed = _parse_ipv4_from_capture(bytes(eth))

    assert parsed is not None
    assert socket.inet_ntoa(parsed.src) == "106.14.187.47"
    assert isinstance(parsed.data, dpkt.tcp.TCP)
    assert parsed.data.data == b"hello"
