"""Mock lab provider (spec §30).

This is a *network model*, not canned output. It reads the same topology
template and FRR configs the real provider uses, then:

* builds L2 segments from containerlab links (a switch node bridges its ports),
* applies FRR ``network`` statement semantics — an interface is in OSPF when a
  network statement covers its address; adjacency forms only when both ends of
  a segment are in OSPF and up,
* floods advertised prefixes across the adjacency graph,
* forwards pings hop by hop **in both directions**, so a missing return route
  fails exactly as it does on real FRR (adjacency stays Full, outbound route
  present, reply dropped at HQ).

Command output mimics FRR/busybox closely enough that the verification
parsers are shared with the real provider. The mock must never become the
tested path for grading — CI runs the integration suite against containerlab.
"""

from __future__ import annotations

import ipaddress
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.models.domain import CommandResult, Device, DeviceKind, LabInstance, LabState, PingResult
from app.scenarios.schema import ScenarioDefinition

from .base import LabError, LabNotFound, device_lookup
from .frrconfig import RouterConfig, parse_frr_config, render_running_config
from .pingparse import parse_ping

IPv4 = ipaddress.IPv4Address
Net = ipaddress.IPv4Network


@dataclass
class HostIface:
    name: str
    address: str | None = None  # a/len
    up: bool = True

    @property
    def ip(self) -> IPv4 | None:
        return ipaddress.ip_interface(self.address).ip if self.address else None  # type: ignore[return-value]

    @property
    def network(self) -> Net | None:
        return ipaddress.ip_interface(self.address).network if self.address else None  # type: ignore[return-value]


@dataclass
class HostNode:
    node: str
    kind: DeviceKind
    ifaces: dict[str, HostIface] = field(default_factory=dict)
    default_routes: dict[str, tuple[str, str | None]] = field(default_factory=dict)  # table -> (gw, dev)
    rules: dict[str, str] = field(default_factory=dict)  # source ip -> table
    bridged: set[str] = field(default_factory=set)

    def iface_for_ip(self, ip: IPv4) -> HostIface | None:
        for i in self.ifaces.values():
            if i.ip == ip:
                return i
        return None


@dataclass
class RouterNode:
    node: str
    cfg: RouterConfig


@dataclass
class Route:
    prefix: Net
    protocol: str  # connected | ospf | static
    iface: str | None = None
    nexthop: IPv4 | None = None
    metric: int = 0
    distance: int = 0


class SimulatedNetwork:
    def __init__(self, template_text: str, configs: dict[str, str], devices: list[Device]) -> None:
        topo = yaml.safe_load(template_text)["topology"]
        self.template_text = template_text
        self.devices = devices
        self.routers: dict[str, RouterNode] = {}
        self.hosts: dict[str, HostNode] = {}
        self._seg_of: dict[tuple[str, str], int] = {}
        self._segments: dict[int, set[tuple[str, str]]] = {}
        nodes: dict[str, Any] = topo["nodes"]
        for node, spec in nodes.items():
            role = (spec.get("labels") or {}).get("northstar.role", "host")
            if role == "router":
                self.routers[node] = RouterNode(node=node, cfg=parse_frr_config(configs[node]))
            else:
                self.hosts[node] = self._host_from_exec(node, DeviceKind(role), spec.get("exec") or [])
        self._build_segments(topo["links"])
        for node in self.routers:
            for (n, iface) in self._seg_of:
                if n == node:
                    self.routers[node].cfg.interface(iface)

    # -------------------------------------------------------------- topology
    @staticmethod
    def _host_from_exec(node: str, kind: DeviceKind, cmds: list[str]) -> HostNode:
        h = HostNode(node=node, kind=kind)
        for c in cmds:
            p = c.split()
            if p[:3] == ["ip", "addr", "add"]:
                h.ifaces[p[-1]] = HostIface(name=p[-1], address=p[3])
            elif p[:2] == ["ip", "route"] and p[2] in ("add", "replace") and p[3] == "default":
                gw = p[p.index("via") + 1]
                dev = p[p.index("dev") + 1] if "dev" in p else None
                table = p[p.index("table") + 1] if "table" in p else "main"
                h.default_routes[table] = (gw, dev)
            elif p[:3] == ["ip", "rule", "add"]:
                h.rules[p[p.index("from") + 1]] = p[p.index("table") + 1]
            elif p[:3] == ["ip", "link", "set"] and "master" in p:
                h.bridged.add(p[3])
        return h

    def _build_segments(self, links: list[dict[str, Any]]) -> None:
        parent: dict[tuple[str, str], tuple[str, str]] = {}

        def find(x: tuple[str, str]) -> tuple[str, str]:
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: tuple[str, str], b: tuple[str, str]) -> None:
            parent[find(a)] = find(b)

        for link in links:
            a, b = link["endpoints"]
            ea, eb = tuple(a.split(":")), tuple(b.split(":"))
            union(ea, eb)  # type: ignore[arg-type]
        for host in self.hosts.values():
            ports = [(host.node, p) for p in host.bridged]
            for p in ports[1:]:
                union(ports[0], p)
        seg_ids: dict[tuple[str, str], int] = {}
        for ep in list(parent):
            root = find(ep)
            sid = seg_ids.setdefault(root, len(seg_ids))
            self._seg_of[ep] = sid
            self._segments.setdefault(sid, set()).add(ep)

    def _endpoint_up(self, node: str, iface: str) -> bool:
        if node in self.routers:
            i = self.routers[node].cfg.interfaces.get(iface)
            return bool(i and i.address and not i.shutdown)
        h = self.hosts[node]
        if iface in h.bridged:
            return True
        i = h.ifaces.get(iface)
        return bool(i and i.up)

    def _peers(self, node: str, iface: str) -> list[tuple[str, str]]:
        """Other endpoints reachable at L2 from (node, iface). Switch ports are transparent."""
        sid = self._seg_of.get((node, iface))
        if sid is None or not self._endpoint_up(node, iface):
            return []
        out = []
        for (n, i) in self._segments[sid]:
            if (n, i) == (node, iface):
                continue
            if n in self.hosts and i in self.hosts[n].bridged:
                continue
            if self._endpoint_up(n, i):
                out.append((n, i))
        return out

    def _addr_of(self, node: str, iface: str) -> IPv4 | None:
        if node in self.routers:
            return self.routers[node].cfg.interfaces[iface].ip
        return self.hosts[node].ifaces[iface].ip

    def _owner_of_ip(self, ip: IPv4) -> tuple[str, str] | None:
        for r in self.routers.values():
            for i in r.cfg.interfaces.values():
                if i.ip == ip and not i.shutdown:
                    return (r.node, i.name)
        for h in self.hosts.values():
            for i in h.ifaces.values():
                if i.ip == ip and i.up:
                    return (h.node, i.name)
        return None

    # --------------------------------------------------------------- routing
    def ospf_interfaces(self, node: str) -> list[str]:
        cfg = self.routers[node].cfg
        if not cfg.ospf.enabled:
            return []
        return [
            i.name
            for i in cfg.interfaces.values()
            if i.ip and not i.shutdown and cfg.ospf.covers(i.ip)
        ]

    def ospf_neighbors(self, node: str) -> list[dict[str, Any]]:
        out = []
        for iface in self.ospf_interfaces(node):
            for (pn, pi) in self._peers(node, iface):
                if pn in self.routers and pi in self.ospf_interfaces(pn):
                    out.append(
                        {
                            "router_id": self.routers[pn].cfg.ospf.router_id or str(self._addr_of(pn, pi)),
                            "node": pn,
                            "address": str(self._addr_of(pn, pi)),
                            "iface": iface,
                            "local_address": str(self._addr_of(node, iface)),
                        }
                    )
        return out

    def _advertised(self, node: str) -> list[Net]:
        cfg = self.routers[node].cfg
        nets: list[Net] = []
        for name in self.ospf_interfaces(node):
            n = cfg.interfaces[name].network
            if n and n not in nets:
                nets.append(n)
        if cfg.ospf.redistribute_connected:
            for i in cfg.interfaces.values():
                if i.network and not i.shutdown and i.network not in nets:
                    nets.append(i.network)
        return nets

    def routing_table(self, node: str) -> list[Route]:
        cfg = self.routers[node].cfg
        routes: list[Route] = []
        connected: dict[Net, str] = {}
        for i in cfg.interfaces.values():
            if i.address and not i.shutdown:
                connected[i.network] = i.name  # type: ignore[index]
                routes.append(Route(prefix=i.network, protocol="connected", iface=i.name))  # type: ignore[arg-type]
        # static
        for prefix, nh in cfg.static_routes:
            nh_ip = ipaddress.ip_address(nh)
            for net, iface in connected.items():
                if nh_ip in net:
                    routes.append(Route(prefix=Net(prefix), protocol="static", iface=iface, nexthop=nh_ip, distance=1))  # type: ignore[arg-type]
                    break
        # ospf: BFS over adjacency graph
        dist: dict[str, int] = {node: 0}
        first_hop: dict[str, tuple[IPv4, str]] = {}
        frontier = [node]
        while frontier:
            nxt: list[str] = []
            for cur in frontier:
                for nb in self.ospf_neighbors(cur):
                    pn = nb["node"]
                    if pn in dist:
                        continue
                    dist[pn] = dist[cur] + 1
                    first_hop[pn] = first_hop[cur] if cur != node else (ipaddress.ip_address(nb["address"]), nb["iface"])  # type: ignore[assignment]
                    nxt.append(pn)
            frontier = nxt
        learned: dict[Net, Route] = {}
        for other, d in sorted(dist.items(), key=lambda kv: kv[1]):
            if other == node:
                continue
            for net in self._advertised(other):
                if net in connected or net in learned:
                    continue
                nh, iface = first_hop[other]
                learned[net] = Route(prefix=net, protocol="ospf", iface=iface, nexthop=nh, metric=10 * d + 10, distance=110)
        routes.extend(learned.values())
        return routes

    def lookup(self, node: str, dst: IPv4) -> Route | None:
        best: Route | None = None
        for r in self.routing_table(node):
            if dst in r.prefix and (best is None or r.prefix.prefixlen > best.prefix.prefixlen):
                best = r
        return best

    # ------------------------------------------------------------- forwarding
    def _egress_host(self, host: HostNode, dst: IPv4, src: IPv4 | None) -> tuple[str, IPv4 | None] | None:
        """Return (iface, gateway-or-None) for a host sending to dst."""
        for i in host.ifaces.values():
            if i.up and i.network and dst in i.network and (src is None or i.ip == src or src not in [x.ip for x in host.ifaces.values()]):
                return (i.name, None)
        table = "main"
        if src and str(src) in host.rules:
            table = host.rules[str(src)]
        route = host.default_routes.get(table) or host.default_routes.get("main")
        if not route:
            return None
        gw, dev = route
        gw_ip = ipaddress.ip_address(gw)
        if dev is None:
            for i in host.ifaces.values():
                if i.network and gw_ip in i.network:
                    dev = i.name
                    break
        if dev is None:
            return None
        return (dev, gw_ip)  # type: ignore[return-value]

    def _deliver_on_segment(self, node: str, iface: str, target: IPv4) -> tuple[str, str] | None:
        for (pn, pi) in self._peers(node, iface):
            if self._addr_of(pn, pi) == target:
                return (pn, pi)
        return None

    def trace(self, src_node: str, dst: IPv4, src_ip: IPv4 | None = None, max_hops: int = 16) -> tuple[bool, list[tuple[str, str]]]:
        """Forward a packet from src_node to dst. Returns (delivered, [(hop node, ingress ip)])."""
        hops: list[tuple[str, str]] = []
        node = src_node
        for _ in range(max_hops):
            if self._owner_of_ip(dst) and self._owner_of_ip(dst)[0] == node:  # type: ignore[index]
                return True, hops
            if node in self.hosts:
                eg = self._egress_host(self.hosts[node], dst, src_ip if node == src_node else None)
                if not eg:
                    return False, hops
                iface, gw = eg
                target = gw or dst
            else:
                r = self.lookup(node, dst)
                if not r or not r.iface:
                    return False, hops
                iface, target = r.iface, (r.nexthop or dst)
            nxt = self._deliver_on_segment(node, iface, target)
            if not nxt:
                return False, hops
            node = nxt[0]
            hops.append((node, str(self._addr_of(*nxt))))
        return False, hops

    def ping(self, src_node: str, dst: IPv4, src_ip: IPv4 | None = None) -> tuple[bool, float | None, list[str]]:
        # pick the source address the reply must come back to
        if src_ip is None:
            if src_node in self.hosts:
                eg = self._egress_host(self.hosts[src_node], dst, None)
                if not eg:
                    return False, None, []
                src_ip = self.hosts[src_node].ifaces[eg[0]].ip
            else:
                r = self.lookup(src_node, dst)
                if not r or not r.iface:
                    return False, None, []
                src_ip = self.routers[src_node].cfg.interfaces[r.iface].ip
        if src_ip is None:
            return False, None, []
        fwd_ok, hops = self.trace(src_node, dst, src_ip)
        if not fwd_ok:
            return False, None, hops
        owner = self._owner_of_ip(dst)
        assert owner
        back_ok, _ = self.trace(owner[0], src_ip)
        if not back_ok:
            return False, None, hops
        return True, round(0.08 + 0.11 * (len(hops) + 1), 3), hops

    # ---------------------------------------------------------------- vtysh
    def vtysh(self, node: str, lines: list[str]) -> tuple[str, int]:
        r = self.routers[node]
        cfg = r.cfg
        out: list[str] = []
        mode = "exec"
        cur_if: str | None = None
        rc = 0
        for raw in lines:
            s = raw.strip()
            if not s:
                continue
            if s.startswith("do "):
                s = s[3:]
                text, code = self._show(r, s)
                out.append(text)
                rc = rc or code
                continue
            if mode == "exec":
                if s in ("configure terminal", "conf t", "configure", "config t", "conf term"):
                    mode = "config"
                elif s.startswith(("write", "copy running-config", "wr")):
                    out.append("Note: this version of vtysh never writes vtysh.conf\nBuilding Configuration...\nIntegrated configuration saved to /etc/frr/frr.conf\n[OK]")
                elif s in ("exit", "end", "quit"):
                    pass
                else:
                    text, code = self._show(r, s)
                    out.append(text)
                    rc = rc or code
                continue
            # --- configuration modes
            if s == "end":
                mode, cur_if = "exec", None
                continue
            if s == "exit":
                mode = "config" if mode != "config" else "exec"
                cur_if = None
                continue
            neg = s.startswith("no ")
            body = s[3:] if neg else s
            p = body.split()
            if mode == "config":
                if body == "router ospf":
                    if neg:
                        cfg.ospf = type(cfg.ospf)()
                    else:
                        cfg.ospf.enabled = True
                        mode = "ospf"
                elif p[0] == "interface" and len(p) == 2:
                    cur_if, mode = p[1], "interface"
                    cfg.interface(cur_if)
                elif p[:2] == ["ip", "route"] and len(p) >= 4:
                    entry = (str(ipaddress.ip_network(p[2], strict=False)), p[3])
                    if neg:
                        cfg.static_routes = [e for e in cfg.static_routes if e != entry]
                    elif entry not in cfg.static_routes:
                        cfg.static_routes.append(entry)
                elif p[0] == "access-list":
                    if neg:
                        cfg.access_lists = [a for a in cfg.access_lists if not a.startswith(body)]
                    elif body not in cfg.access_lists:
                        cfg.access_lists.append(body)
                elif p[0] == "hostname":
                    cfg.hostname = p[1]
                else:
                    out.append(f"% Unknown command: {s}")
                    rc = 1
            elif mode == "ospf":
                if p[0] == "network" and "area" in p:
                    entry = (str(ipaddress.ip_network(p[1], strict=False)), int(p[p.index("area") + 1]))
                    if neg:
                        if entry not in cfg.ospf.networks:
                            out.append("% Can't find specified network area configuration.")
                            rc = 1
                        else:
                            cfg.ospf.networks.remove(entry)
                    elif entry not in cfg.ospf.networks:
                        cfg.ospf.networks.append(entry)
                elif p[:2] == ["redistribute", "connected"]:
                    cfg.ospf.redistribute_connected = not neg
                elif p[:2] == ["ospf", "router-id"]:
                    cfg.ospf.router_id = None if neg else p[2]
                elif p[0] == "passive-interface":
                    if neg:
                        cfg.ospf.passive = [x for x in cfg.ospf.passive if x != p[1]]
                    elif p[1] not in cfg.ospf.passive:
                        cfg.ospf.passive.append(p[1])
                elif p[0] == "router":
                    mode = "config"
                    return self.vtysh(node, [s] + [])  # re-enter (rare)
                else:
                    out.append(f"% Unknown command: {s}")
                    rc = 1
            elif mode == "interface" and cur_if:
                i = cfg.interface(cur_if)
                if body == "shutdown":
                    i.shutdown = not neg
                elif p[:2] == ["ip", "address"]:
                    i.address = None if neg else p[2]
                elif p[0] == "description":
                    i.description = "" if neg else body.split(None, 1)[1]
                elif p[:3] == ["ip", "ospf", "area"]:
                    cfg.ospf.enabled = True
                    entry = (i.address or "0.0.0.0/0", int(p[3]))
                    if neg:
                        cfg.ospf.networks = [n for n in cfg.ospf.networks if n != entry]
                    elif entry not in cfg.ospf.networks:
                        cfg.ospf.networks.append(entry)
                elif p[0] == "interface":
                    cur_if = p[1]
                    cfg.interface(cur_if)
                else:
                    out.append(f"% Unknown command: {s}")
                    rc = 1
        return "\n".join(x for x in out if x is not None), rc

    # ----------------------------------------------------------------- shows
    def _show(self, r: RouterNode, cmd: str) -> tuple[str, int]:
        cfg = r.cfg
        want_json = cmd.endswith(" json")
        c = cmd[:-5].strip() if want_json else cmd.strip()
        c = re.sub(r"\s+", " ", c)
        if c in ("show running-config", "show run", "show running-config ospfd", "show configuration"):
            return render_running_config(cfg), 0
        if c == "show version":
            return "FRRouting 10.2.1 (mock).\nCopyright 1996-2005 Kunihiro Ishiguro, et al.\n", 0
        if c.startswith("show ip route"):
            filt = c[len("show ip route"):].strip()
            proto = filt if filt in ("ospf", "connected", "static") else None
            routes = [x for x in self.routing_table(r.node) if proto is None or x.protocol == proto]
            if filt and proto is None:
                try:
                    target = ipaddress.ip_address(filt)
                    best = self.lookup(r.node, target)  # type: ignore[arg-type]
                    routes = [best] if best else []
                except ValueError:
                    return f"% Unknown command: {cmd}", 1
            return (json.dumps(self._routes_json(routes), indent=2) if want_json else self._routes_text(routes)), 0
        if c == "show ip ospf neighbor":
            nbrs = self.ospf_neighbors(r.node)
            if want_json:
                d: dict[str, Any] = {"neighbors": {}}
                for n in nbrs:
                    d["neighbors"].setdefault(n["router_id"], []).append(
                        {
                            "priority": 1,
                            "state": "Full/DR",
                            "nbrState": "Full/DR",
                            "converged": "Full",
                            "role": "DR",
                            "upTimeInMsec": 605000,
                            "deadTimeMsecs": 38000,
                            "ifaceAddress": n["address"],
                            "ifaceName": f"{n['iface']}:{n['local_address']}",
                        }
                    )
                return json.dumps(d, indent=2), 0
            hdr = "Neighbor ID     Pri State           Up Time         Dead Time Address         Interface                        RXmtL RqstL DBsmL"
            rows = [
                f"{n['router_id']:<16}{1:>3} {'Full/DR':<15} {'10m05s':<15} {'38.123s':>9} {n['address']:<15} {n['iface'] + ':' + n['local_address']:<32} 0     0     0"
                for n in nbrs
            ]
            return "\n".join(["", hdr, *rows, ""]), 0
        if c == "show ip ospf interface":
            names = self.ospf_interfaces(r.node)
            if want_json:
                d = {"interfaces": {}}
                for nm in names:
                    i = cfg.interfaces[nm]
                    d["interfaces"][nm] = {
                        "ifUp": True,
                        "ipAddress": str(i.ip),
                        "ipAddressPrefixlen": i.network.prefixlen if i.network else 0,
                        "area": "0.0.0.0",
                        "networkType": "BROADCAST",
                        "state": "DR",
                        "nbrCount": len([n for n in self.ospf_neighbors(r.node) if n["iface"] == nm]),
                    }
                return json.dumps(d, indent=2), 0
            blocks = []
            for nm in names:
                i = cfg.interfaces[nm]
                nb = len([n for n in self.ospf_neighbors(r.node) if n["iface"] == nm])
                blocks.append(
                    f"{nm} is up\n  ifindex 3, MTU 1500 bytes, BW 10000 Mbit <UP,BROADCAST,RUNNING,MULTICAST>\n"
                    f"  Internet Address {i.address}, Broadcast {i.network.broadcast_address if i.network else ''}, Area 0.0.0.0\n"
                    f"  Router ID {cfg.ospf.router_id}, Network Type BROADCAST, Cost: 10\n"
                    f"  State DR, Priority 1\n  Neighbor Count is {nb}, Adjacent neighbor count is {nb}"
                )
            if not blocks:
                blocks.append("")
            return "\n".join(blocks) + "\n", 0
        if c in ("show ip ospf", "show ip ospf database"):
            return f" OSPF Routing Process, Router ID: {cfg.ospf.router_id}\n Number of areas attached to this router: 1\n", 0
        if c in ("show interface brief", "show int brief", "show int br"):  # FRR has no `show ip interface brief`
            lines = ["Interface       Status  VRF             Addresses", "---------       ------  ---             ---------"]
            for nm in sorted(cfg.interfaces):
                i = cfg.interfaces[nm]
                st = "down" if i.shutdown else "up"
                lines.append(f"{nm:<16}{st:<8}{'default':<16}{i.address or ''}")
            return "\n".join(lines) + "\n", 0
        m = re.match(r"show interface(?:s)?(?: (\S+))?$", c)
        if m:
            names = [m.group(1)] if m.group(1) else sorted(cfg.interfaces)
            if want_json:
                d = {}
                for nm in names:
                    i = cfg.interfaces.get(nm)
                    if not i:
                        return f"% Can't find interface {nm}", 1
                    d[nm] = {
                        "administrativeStatus": "down" if i.shutdown else "up",
                        "operationalStatus": "down" if i.shutdown else "up",
                        "description": i.description,
                        "ipAddresses": [{"address": i.address}] if i.address else [],
                        "mtu": 1500,
                    }
                return json.dumps(d, indent=2), 0
            blocks = []
            for nm in names:
                i = cfg.interfaces.get(nm)
                if not i:
                    return f"% Can't find interface {nm}", 1
                st = "down" if i.shutdown else "up"
                blocks.append(
                    f"Interface {nm} is {st}, line protocol is {st}\n  Link ups: 1  Link downs: 0\n  Description: {i.description}\n"
                    f"  index 3 metric 0 mtu 1500 speed 10000\n  inet {i.address or ''}"
                )
            return "\n".join(blocks) + "\n", 0
        return f"% Unknown command: {cmd}", 1

    def _routes_json(self, routes: list[Route]) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for r in sorted(routes, key=lambda x: (int(x.prefix.network_address), x.prefix.prefixlen)):
            nh = (
                {"directlyConnected": True, "interfaceName": r.iface, "active": True, "weight": 1}
                if r.protocol == "connected"
                else {"ip": str(r.nexthop), "afi": "ipv4", "interfaceName": r.iface, "active": True, "weight": 1}
            )
            d.setdefault(str(r.prefix), []).append(
                {
                    "prefix": str(r.prefix),
                    "prefixLen": r.prefix.prefixlen,
                    "protocol": r.protocol,
                    "selected": True,
                    "installed": True,
                    "distance": r.distance,
                    "metric": r.metric,
                    "nexthops": [nh],
                }
            )
        return d

    def _routes_text(self, routes: list[Route]) -> str:
        hdr = (
            "Codes: K - kernel route, C - connected, L - local, S - static,\n"
            "       R - RIP, O - OSPF, I - IS-IS, B - BGP, E - EIGRP, N - NHRP,\n"
            "       T - Table, v - VNC, V - VNC-Direct, A - Babel, F - PBR,\n"
            "       f - OpenFabric, t - Table-Direct,\n"
            "       > - selected route, * - FIB route, q - queued, r - rejected, b - backup\n"
            "       t - trapped, o - offload failure\n\n"
        )
        code = {"connected": "C", "ospf": "O", "static": "S"}
        rows = []
        for r in sorted(routes, key=lambda x: (int(x.prefix.network_address), x.prefix.prefixlen)):
            if r.protocol == "connected":
                rows.append(f"C>* {r.prefix} is directly connected, {r.iface}, weight 1, 00:10:12")
            else:
                rows.append(f"{code[r.protocol]}>* {r.prefix} [{r.distance}/{r.metric}] via {r.nexthop}, {r.iface}, weight 1, 00:09:50")
        return hdr + "\n".join(rows) + "\n"

    # ------------------------------------------------------------- host shell
    def shell(self, node: str, command: str) -> tuple[str, str, int]:
        try:
            argv = shlex.split(command)
        except ValueError:
            return "", "sh: syntax error", 2
        if not argv:
            return "", "", 0
        prog = argv[0]
        if prog == "ping":
            return self._ping_cmd(node, argv[1:])
        if prog == "traceroute":
            dst = ipaddress.ip_address(argv[-1])
            ok, hops = self.trace(node, dst)  # type: ignore[arg-type]
            lines = [f"traceroute to {dst} ({dst}), 30 hops max, 46 byte packets"]
            for n, (_, ip) in enumerate(hops, 1):
                lines.append(f" {n}  {ip} ({ip})  0.{n}12 ms")
            if not ok:
                lines.append(f" {len(hops)+1}  *  *  *")
            return "\n".join(lines) + "\n", "", 0
        if prog == "hostname" or command.strip() == "cat /etc/hostname":
            return node + "\n", "", 0
        if prog == "ip" and argv[1:2] in (["addr"], ["a"], ["address"]):
            h = self.hosts.get(node)
            if h:
                return "\n".join(f"{n}: {i.name}: <BROADCAST,MULTICAST,UP,LOWER_UP>\n    inet {i.address} scope global {i.name}" for n, i in enumerate(h.ifaces.values(), 2)) + "\n", "", 0
        if prog == "ip" and argv[1:2] in (["route"], ["r"]):
            h = self.hosts.get(node)
            if h:
                rows = [f"default via {gw} dev {dev or 'eth1'}" for t, (gw, dev) in h.default_routes.items() if t == "main"]
                rows += [f"{i.network} dev {i.name} scope link  src {i.ip}" for i in h.ifaces.values() if i.network]
                return "\n".join(rows) + "\n", "", 0
        if prog == "vtysh" and node in self.routers:
            lines = [argv[i + 1] for i, a in enumerate(argv) if a == "-c" and i + 1 < len(argv)]
            out, rc = self.vtysh(node, lines)
            return out, "", rc
        return "", f"sh: {prog}: not found\n", 127

    def _ping_cmd(self, node: str, args: list[str]) -> tuple[str, str, int]:
        count, src, dst = 1, None, None
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-c":
                count = int(args[i + 1]); i += 2
            elif a == "-W" or a == "-w" or a == "-i":
                i += 2
            elif a == "-I":
                src = args[i + 1]; i += 2
            elif a.startswith("-"):
                i += 1
            else:
                dst = a; i += 1
        if not dst:
            return "", "ping: usage error\n", 2
        try:
            dst_ip = ipaddress.ip_address(dst)
        except ValueError:
            return "", f"ping: bad address '{dst}'\n", 1
        src_ip: IPv4 | None = None
        if src:
            try:
                src_ip = ipaddress.ip_address(src)  # type: ignore[assignment]
            except ValueError:
                h = self.hosts.get(node)
                if h and src in h.ifaces:
                    src_ip = h.ifaces[src].ip
        ok, rtt, _ = self.ping(node, dst_ip, src_ip)  # type: ignore[arg-type]
        head = f"PING {dst} ({dst}): 56 data bytes\n"
        if ok:
            body = "".join(f"64 bytes from {dst}: seq={n} ttl=62 time={rtt} ms\n" for n in range(count))
            stats = f"\n--- {dst} ping statistics ---\n{count} packets transmitted, {count} packets received, 0% packet loss\nround-trip min/avg/max = {rtt}/{rtt}/{rtt} ms\n"
            return head + body + stats, "", 0
        stats = f"\n--- {dst} ping statistics ---\n{count} packets transmitted, 0 packets received, 100% packet loss\n"
        return head + stats, "", 1


class MockLabProvider:
    """LabProvider backed by SimulatedNetwork instances.

    With ``state_dir`` set, each lab is persisted as ``labs/<lab>/mock-state.json``
    (template + current router configs) so separate CLI processes share it and
    orphan reconciliation has something real to find.
    """

    name = "mock"

    def __init__(self, state_dir: Path | None = None) -> None:
        self._labs: dict[str, tuple[LabInstance, SimulatedNetwork]] = {}
        self.command_log: list[tuple[str, str, str]] = []
        self.state_dir = state_dir

    # persistence
    def _lab_dir(self, lab_id: str) -> Path | None:
        return (self.state_dir / "labs" / lab_id) if self.state_dir else None

    def _persist(self, lab_id: str) -> None:
        d = self._lab_dir(lab_id)
        if not d:
            return
        inst, net = self._labs[lab_id]
        d.mkdir(parents=True, exist_ok=True)
        payload = {
            "instance": inst.model_dump(mode="json"),
            "template": net.template_text,
            "configs": {n: render_running_config(r.cfg) for n, r in net.routers.items()},
        }
        (d / "mock-state.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load(self, lab_id: str) -> tuple[LabInstance, SimulatedNetwork] | None:
        d = self._lab_dir(lab_id)
        if not d or not (d / "mock-state.json").exists():
            return None
        payload = json.loads((d / "mock-state.json").read_text(encoding="utf-8"))
        inst = LabInstance.model_validate(payload["instance"])
        net = SimulatedNetwork(payload["template"], payload["configs"], inst.devices)
        self._labs[lab_id] = (inst, net)
        return self._labs[lab_id]

    def _get(self, lab_id: str) -> tuple[LabInstance, SimulatedNetwork]:
        if lab_id in self._labs:
            return self._labs[lab_id]
        loaded = self._load(lab_id)
        if loaded is None:
            raise LabNotFound(lab_id)
        return loaded

    # lifecycle
    async def provision(self, scenario: ScenarioDefinition, *, lab_id: str) -> LabInstance:
        template = scenario.template_path.read_text(encoding="utf-8")
        configs: dict[str, str] = {}
        for d in scenario.topology.devices:
            if d.kind == DeviceKind.ROUTER:
                configs[d.node] = (scenario.configs_path / d.node / "frr.conf").read_text(encoding="utf-8")
        devices = list(scenario.topology.devices)
        if scenario.topology.prober:
            devices.append(Device(name="PROBER", node=scenario.topology.prober.node, kind=DeviceKind.INFRA))
        net = SimulatedNetwork(template, configs, devices)
        lab_dir = self._lab_dir(lab_id)
        inst = LabInstance(id=lab_id, scenario_id=scenario.id, provider=self.name, devices=devices, workdir=str(lab_dir) if lab_dir else None)
        self._labs[lab_id] = (inst, net)
        self._persist(lab_id)
        return inst

    async def destroy(self, lab_id: str) -> None:
        self._labs.pop(lab_id, None)
        d = self._lab_dir(lab_id)
        if d and d.exists():
            import shutil

            shutil.rmtree(d, ignore_errors=True)

    async def list_labs(self) -> list[str]:
        names = set(self._labs)
        if self.state_dir and (self.state_dir / "labs").exists():
            names |= {p.parent.name for p in (self.state_dir / "labs").glob("*/mock-state.json")}
        return sorted(names)

    def network(self, lab_id: str) -> SimulatedNetwork:
        return self._get(lab_id)[1]

    def devices(self, lab_id: str) -> list[Device]:
        return self._get(lab_id)[0].devices

    def _dev(self, lab_id: str, device: str) -> Device:
        return device_lookup(self.devices(lab_id), device)

    # commands
    async def exec(self, lab_id: str, device: str, command: str) -> CommandResult:
        dev = self._dev(lab_id, device)
        net = self.network(lab_id)
        self.command_log.append((lab_id, dev.name, command))
        out, err, rc = net.shell(dev.node, command)
        return CommandResult(device=dev.name, command=command, stdout=out, stderr=err, exit_code=rc, duration_ms=1.0)

    async def vtysh(self, lab_id: str, device: str, lines: list[str], *, check: bool = True) -> CommandResult:
        dev = self._dev(lab_id, device)
        if dev.kind != DeviceKind.ROUTER:
            raise LabError(f"{dev.name} is not a router")
        net = self.network(lab_id)
        self.command_log.append((lab_id, dev.name, " ; ".join(lines)))
        out, rc = net.vtysh(dev.node, lines)
        if any(ln.strip().startswith(("conf", "configure")) for ln in lines):
            self._persist(lab_id)
        if check and rc != 0:
            raise LabError(f"vtysh on {dev.name} failed: {out}")
        return CommandResult(device=dev.name, command=" ; ".join(lines), stdout=out, exit_code=rc, duration_ms=1.0)

    async def ping(
        self,
        lab_id: str,
        device: str,
        destination: str,
        *,
        source: str | None = None,
        count: int = 1,
        timeout_s: float = 1.0,
    ) -> PingResult:
        dev = self._dev(lab_id, device)
        net = self.network(lab_id)
        cmd = f"ping -c {count} -W {max(1, int(timeout_s))}" + (f" -I {source}" if source else "") + f" {destination}"
        out, _, rc = net.shell(dev.node, cmd)
        ok, rtt = parse_ping(out)
        return PingResult(
            source=dev.name if not source else f"{dev.name}:{source}",
            destination=destination,
            success=ok and rc == 0,
            rtt_ms=rtt,
            raw=out,
        )

    async def get_state(self, lab_id: str) -> LabState:
        devices = self.devices(lab_id)
        net = self.network(lab_id)
        configs = {d.name: render_running_config(net.routers[d.node].cfg) for d in devices if d.kind == DeviceKind.ROUTER}
        return LabState(lab_id=lab_id, devices=devices, running_configs=configs)
