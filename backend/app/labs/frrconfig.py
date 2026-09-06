"""Parse and render FRR configuration text.

Used by the mock provider (initial state from configs/healthy, running-config
rendering) and by the grader (healthy vs broken diff = ground truth).
The subset covers what INC-1042 and its sledgehammer fixes need.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field


@dataclass
class InterfaceConfig:
    name: str
    description: str = ""
    address: str | None = None  # "10.0.0.1/24"
    shutdown: bool = False

    @property
    def network(self) -> ipaddress.IPv4Network | None:
        return ipaddress.ip_interface(self.address).network if self.address else None  # type: ignore[return-value]

    @property
    def ip(self) -> ipaddress.IPv4Address | None:
        return ipaddress.ip_interface(self.address).ip if self.address else None  # type: ignore[return-value]


@dataclass
class OspfConfig:
    enabled: bool = False
    router_id: str | None = None
    networks: list[tuple[str, int]] = field(default_factory=list)  # (prefix, area)
    redistribute_connected: bool = False
    passive: list[str] = field(default_factory=list)

    def covers(self, ip: ipaddress.IPv4Address) -> bool:
        return any(ip in ipaddress.ip_network(p, strict=False) for p, _ in self.networks)


@dataclass
class RouterConfig:
    hostname: str = ""
    interfaces: dict[str, InterfaceConfig] = field(default_factory=dict)
    ospf: OspfConfig = field(default_factory=OspfConfig)
    static_routes: list[tuple[str, str]] = field(default_factory=list)  # (prefix, nexthop)
    access_lists: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)

    def interface(self, name: str) -> InterfaceConfig:
        return self.interfaces.setdefault(name, InterfaceConfig(name=name))


def parse_frr_config(text: str) -> RouterConfig:
    cfg = RouterConfig()
    mode: str | None = None
    cur_if: InterfaceConfig | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s or s.startswith(("!", "#")):
            continue
        if s in ("exit", "exit-address-family"):
            mode, cur_if = None, None
            continue
        if s == "end":
            break
        if s.startswith("hostname "):
            cfg.hostname = s.split(None, 1)[1]
            continue
        if s.startswith("interface "):
            mode = "interface"
            cur_if = cfg.interface(s.split(None, 1)[1])
            continue
        if s == "router ospf":
            mode = "ospf"
            cfg.ospf.enabled = True
            continue
        if s.startswith("access-list "):
            cfg.access_lists.append(s)
            mode = None
            continue
        if s.startswith("ip route "):
            parts = s.split()
            cfg.static_routes.append((parts[2], parts[3]))
            mode = None
            continue
        if mode == "interface" and cur_if is not None:
            if s.startswith("description "):
                cur_if.description = s.split(None, 1)[1]
            elif s.startswith("ip address "):
                cur_if.address = s.split()[2]
            elif s == "shutdown":
                cur_if.shutdown = True
            elif s == "no shutdown":
                cur_if.shutdown = False
            elif s.startswith("ip ospf area "):
                cfg.ospf.enabled = True
                cfg.ospf.networks.append((cur_if.address or "0.0.0.0/0", int(s.split()[-1])))
            continue
        if mode == "ospf":
            parts = s.split()
            if parts[:2] == ["ospf", "router-id"]:
                cfg.ospf.router_id = parts[2]
            elif parts[0] == "network" and "area" in parts:
                prefix = str(ipaddress.ip_network(parts[1], strict=False))
                cfg.ospf.networks.append((prefix, int(parts[parts.index("area") + 1])))
            elif parts[:2] == ["redistribute", "connected"]:
                cfg.ospf.redistribute_connected = True
            elif parts[:1] == ["passive-interface"]:
                cfg.ospf.passive.append(parts[1])
            continue
        if s.startswith(("frr ", "service integrated-vtysh-config", "Building configuration", "Current configuration")):
            continue  # vtysh preamble / boilerplate; not meaningful config
        cfg.other.append(s)
    return cfg


def render_running_config(cfg: RouterConfig, *, version: str = "10.2.1") -> str:
    out: list[str] = [
        "Building configuration...",
        "",
        "Current configuration:",
        "!",
        f"frr version {version}",
        "frr defaults traditional",
        f"hostname {cfg.hostname}",
        "service integrated-vtysh-config",
        "!",
    ]
    for name in sorted(cfg.interfaces, key=_ifsort):
        i = cfg.interfaces[name]
        out.append(f"interface {name}")
        if i.description:
            out.append(f" description {i.description}")
        if i.address:
            out.append(f" ip address {i.address}")
        if i.shutdown:
            out.append(" shutdown")
        out += ["exit", "!"]
    for prefix, nh in cfg.static_routes:
        out.append(f"ip route {prefix} {nh}")
    if cfg.static_routes:
        out.append("!")
    if cfg.ospf.enabled:
        out.append("router ospf")
        if cfg.ospf.router_id:
            out.append(f" ospf router-id {cfg.ospf.router_id}")
        if cfg.ospf.redistribute_connected:
            out.append(" redistribute connected")
        for p in cfg.ospf.passive:
            out.append(f" passive-interface {p}")
        for prefix, area in cfg.ospf.networks:
            out.append(f" network {prefix} area {area}")
        out += ["exit", "!"]
    for acl in cfg.access_lists:
        out.append(acl)
    if cfg.access_lists:
        out.append("!")
    out.append("end")
    return "\n".join(out) + "\n"


def _ifsort(name: str) -> tuple[str, int]:
    head = name.rstrip("0123456789")
    tail = name[len(head):]
    return (head, int(tail) if tail else 0)


def config_section(text: str, section: str) -> list[str]:
    """Extract lines belonging to a section for ConfigUnchanged comparisons.

    ``section`` may be a block header ("router ospf", "interface eth1") or a
    line prefix ("access-list", "ip route").
    """
    lines = [ln.rstrip() for ln in text.splitlines()]
    block: list[str] = []
    in_block = False
    for ln in lines:
        s = ln.strip()
        if s == section:
            in_block = True
            block.append(s)
            continue
        if in_block:
            if s in ("exit", "!", "end") or (ln and not ln.startswith(" ")):
                in_block = False
            else:
                block.append(s)
                continue
        if s.startswith((section + " ", "no " + section + " ")):
            block.append(s)
    return block


def config_lines_diff(before: str, after: str) -> tuple[list[str], list[str]]:
    """Return (added, removed) meaningful lines between two configs."""
    def norm(t: str) -> list[str]:
        return [
            ln.strip()
            for ln in t.splitlines()
            if ln.strip() and not ln.strip().startswith(("!", "Building", "Current", "frr version"))
        ]
    b, a = norm(before), norm(after)
    bset, aset = set(b), set(a)
    return [ln for ln in a if ln not in bset], [ln for ln in b if ln not in aset]
