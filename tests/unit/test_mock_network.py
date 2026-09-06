"""The mock must reproduce FRR's behaviour for this fault, not just 'ping fails'."""

from __future__ import annotations

import json

import pytest


async def test_healthy_end_to_end(healthy_lab):
    p, lab = healthy_lab
    assert (await p.ping(lab, "AUS-CLIENT1", "10.10.10.10")).success
    assert (await p.ping(lab, "AUS-CLIENT2", "10.10.10.10")).success
    assert (await p.ping(lab, "HQ-CLIENT", "10.20.10.51")).success
    assert (await p.ping(lab, "APP-SRV", "10.20.10.52")).success


async def test_prober_vantages(healthy_lab):
    p, lab = healthy_lab
    assert (await p.ping(lab, "PROBER", "10.10.10.10", source="10.20.10.250")).success
    assert (await p.ping(lab, "PROBER", "10.10.10.10", source="10.10.30.2")).success
    assert (await p.ping(lab, "PROBER", "10.255.0.2", source="10.10.30.2")).success


async def test_broken_state_matches_spec_9_3(broken_lab):
    p, lab = broken_lab
    # symptom
    assert not (await p.ping(lab, "AUS-CLIENT1", "10.10.10.10")).success
    # adjacency stays Full — the trap
    nb = json.loads((await p.vtysh(lab, "AUS-RTR1", ["show ip ospf neighbor json"])).stdout)
    states = [n["converged"] for lst in nb["neighbors"].values() for n in lst]
    assert states == ["Full"]
    # outbound route present on AUS-RTR1
    aus = json.loads((await p.vtysh(lab, "AUS-RTR1", ["show ip route json"])).stdout)
    assert aus["10.10.10.0/24"][0]["protocol"] == "ospf"
    # return route missing on HQ-RTR1
    hq = json.loads((await p.vtysh(lab, "HQ-RTR1", ["show ip route json"])).stdout)
    assert "10.20.10.0/24" not in hq
    # forward path reaches APP-SRV; only the reply is lost
    net = p.network(lab)
    import ipaddress

    ok, hops = net.trace("aus-client1", ipaddress.ip_address("10.10.10.10"))
    assert ok and [h[0] for h in hops] == ["aus-rtr1", "hq-rtr1", "app-srv"]
    assert [h[1] for h in hops] == ["10.20.10.1", "10.255.0.1", "10.10.10.10"]
    # HQ side is untouched
    assert (await p.ping(lab, "HQ-CLIENT", "10.10.10.10")).success


async def test_show_ip_route_text_looks_like_frr(broken_lab):
    p, lab = broken_lab
    out = (await p.vtysh(lab, "HQ-RTR1", ["show ip route"])).stdout
    assert "Codes: K - kernel route" in out
    assert "C>* 10.10.10.0/24 is directly connected, eth1" in out
    assert "10.20.10.0/24" not in out
    out = (await p.vtysh(lab, "AUS-RTR1", ["show ip route ospf"])).stdout
    assert "O>* 10.10.10.0/24 [110/20] via 10.255.0.1, eth1" in out


async def test_shutdown_wan_interface_drops_adjacency(broken_lab):
    p, lab = broken_lab
    await p.vtysh(lab, "AUS-RTR1", ["configure terminal", "interface eth1", "shutdown", "end"])
    nb = json.loads((await p.vtysh(lab, "AUS-RTR1", ["show ip ospf neighbor json"])).stdout)
    assert nb["neighbors"] == {}
    iface = json.loads((await p.vtysh(lab, "AUS-RTR1", ["show interface eth1 json"])).stdout)
    assert iface["eth1"]["administrativeStatus"] == "down"
    assert not (await p.ping(lab, "PROBER", "10.255.0.2", source="10.10.30.2")).success
    await p.vtysh(lab, "AUS-RTR1", ["configure terminal", "interface eth1", "no shutdown", "end"])
    nb = json.loads((await p.vtysh(lab, "AUS-RTR1", ["show ip ospf neighbor json"])).stdout)
    assert len(nb["neighbors"]) == 1


async def test_host_shell_commands(broken_lab):
    p, lab = broken_lab
    r = await p.exec(lab, "AUS-CLIENT1", "ping -c 1 -W 1 10.10.10.10")
    assert r.exit_code == 1 and "100% packet loss" in r.stdout
    r = await p.exec(lab, "AUS-CLIENT1", "traceroute 10.10.10.10")
    assert "10.20.10.1" in r.stdout
    r = await p.exec(lab, "AUS-CLIENT1", "nmap 10.10.10.10")
    assert r.exit_code == 127


async def test_unknown_vtysh_command_is_an_error(broken_lab):
    p, lab = broken_lab
    r = await p.vtysh(lab, "AUS-RTR1", ["show ip bgp summary"], check=False)
    assert r.exit_code == 1 and "Unknown command" in r.stdout


async def test_vtysh_on_host_rejected(broken_lab):
    from app.labs.base import LabError

    p, lab = broken_lab
    with pytest.raises(LabError):
        await p.vtysh(lab, "AUS-CLIENT1", ["show ip route"])


async def test_running_config_reflects_changes_and_write(broken_lab):
    p, lab = broken_lab
    cfg = (await p.vtysh(lab, "AUS-RTR1", ["show running-config"])).stdout
    assert "network 10.255.0.0/30 area 0" in cfg
    assert "network 10.20.10.0/24 area 0" not in cfg
    await p.vtysh(lab, "AUS-RTR1", ["configure terminal", "router ospf", "network 10.20.10.0/24 area 0", "end", "write memory"])
    cfg = (await p.vtysh(lab, "AUS-RTR1", ["show running-config"])).stdout
    assert "network 10.20.10.0/24 area 0" in cfg
