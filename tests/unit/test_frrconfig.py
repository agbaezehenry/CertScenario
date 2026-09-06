from app.labs.frrconfig import (
    config_lines_diff,
    config_section,
    parse_frr_config,
    render_running_config,
)

SAMPLE = """
hostname AUS-RTR1
!
interface eth1
 description WAN
 ip address 10.255.0.2/30
exit
!
interface eth2
 ip address 10.20.10.1/24
 shutdown
exit
!
ip route 10.99.0.0/24 10.255.0.1
!
router ospf
 ospf router-id 10.0.0.2
 redistribute connected
 network 10.255.0.0/30 area 0
exit
!
access-list MGMT-SSH seq 10 permit 10.10.20.0/24
!
end
"""


def test_parse_roundtrip():
    cfg = parse_frr_config(SAMPLE)
    assert cfg.hostname == "AUS-RTR1"
    assert cfg.interfaces["eth1"].address == "10.255.0.2/30"
    assert cfg.interfaces["eth2"].shutdown
    assert cfg.ospf.router_id == "10.0.0.2"
    assert cfg.ospf.redistribute_connected
    assert cfg.ospf.networks == [("10.255.0.0/30", 0)]
    assert cfg.static_routes == [("10.99.0.0/24", "10.255.0.1")]
    assert cfg.access_lists == ["access-list MGMT-SSH seq 10 permit 10.10.20.0/24"]
    again = parse_frr_config(render_running_config(cfg))
    assert again == cfg


def test_network_statement_covers_interface_address():
    import ipaddress

    cfg = parse_frr_config(SAMPLE)
    assert cfg.ospf.covers(ipaddress.ip_address("10.255.0.2"))
    assert not cfg.ospf.covers(ipaddress.ip_address("10.20.10.1"))


def test_config_section_block_and_prefix():
    text = render_running_config(parse_frr_config(SAMPLE))
    assert config_section(text, "access-list") == ["access-list MGMT-SSH seq 10 permit 10.10.20.0/24"]
    assert config_section(text, "router ospf") == [
        "router ospf",
        "ospf router-id 10.0.0.2",
        "redistribute connected",
        "network 10.255.0.0/30 area 0",
    ]


def test_config_lines_diff():
    a = "router ospf\n network 1.0.0.0/8 area 0\n"
    b = "router ospf\n network 1.0.0.0/8 area 0\n redistribute connected\n"
    assert config_lines_diff(a, b) == (["redistribute connected"], [])
