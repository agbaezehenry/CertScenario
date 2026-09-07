"""Line-based terminal sessions (spec §8, §28).

The learner lands on a jump host and ``ssh``es to devices. Router lines are
executed with their full mode path (``configure terminal`` / ``router ospf``
/ line / ``end``) through ``SessionService.exec``, so every line is recorded
as telemetry, works identically on real FRR and the mock, and never needs a
PTY. Show commands inside config modes are prefixed with ``do``.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from app.events import EventType
from app.models.domain import DeviceKind
from app.services.session_service import SessionService

JUMP_HOST = "netops-jump"
HELP = """Northstar NetOps jump host. Commands:
  hosts               list devices you can reach
  ssh <device>        connect (e.g. ssh AUS-RTR1)
  exit                log out
Router CLI is IOS-like (FRRouting vtysh). Type '?' on a device for basics."""

DEVICE_HELP = """Useful commands:
  show interface brief        show interface <name>
  show ip route [ospf]           show ip ospf neighbor
  show ip ospf interface         show running-config
  configure terminal             router ospf / interface <name>
  ping <ip>                      exit"""


@dataclass
class TerminalSession:
    sessions: SessionService
    session_id: str
    device: str | None = None
    kind: DeviceKind | None = None
    mode: str = "exec"  # exec | config | router | interface
    iface: str | None = None
    history: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- prompts
    def prompt(self) -> str:
        s = self.sessions.get(self.session_id)
        if self.device is None:
            return f"{s.learner_id}@{JUMP_HOST}:~$ "
        if self.kind == DeviceKind.ROUTER:
            suffix = {"exec": "#", "config": "(config)#", "router": "(config-router)#", "interface": "(config-if)#"}[self.mode]
            return f"{self.device}{suffix} "
        return f"{self.device.lower()}:~# "

    def banner(self) -> str:
        return f"Connected to {JUMP_HOST}. Type 'help' for commands.\n"

    # ------------------------------------------------------------------ input
    async def handle(self, line: str) -> str:
        line = line.rstrip("\r\n")
        stripped = line.strip()
        if stripped:
            self.history.append(stripped)
        if not stripped:
            return ""
        if self.device is None:
            return await self._jump(stripped)
        if self.kind == DeviceKind.ROUTER:
            return await self._router(stripped)
        return await self._host(stripped)

    async def _jump(self, line: str) -> str:
        s = self.sessions.get(self.session_id)
        scenario = self.sessions.scenario_for(s)
        parts = line.split()
        cmd = parts[0].lower()
        if cmd in ("help", "?"):
            return HELP + "\n"
        if cmd == "hosts":
            rows = [f"  {d.name:<12} {d.kind.value:<7} {d.site:<7} {d.address or ''}" for d in scenario.topology.devices]
            return "\n".join(rows) + "\n"
        if cmd in ("ssh", "connect", "telnet") and len(parts) >= 2:
            target = parts[1].split("@")[-1]
            try:
                dev = scenario.topology.device(target)
            except KeyError:
                return f"ssh: Could not resolve hostname {target}: Name or service not known\n"
            if dev.kind == DeviceKind.INFRA:
                return f"ssh: connect to host {target} port 22: Permission denied (monitoring infrastructure)\n"
            self.device, self.kind, self.mode, self.iface = dev.name, dev.kind, "exec", None
            await self.sessions.bus(s.id).emit(
                EventType.DEVICE_CONNECTED, learner_id=s.learner_id, scenario_id=s.scenario_id, session_id=s.id, source="terminal", device=dev.name
            )
            self.sessions._touch(s)
            if dev.kind == DeviceKind.ROUTER:
                return "\nHello, this is FRRouting (version 10.2.1).\nCopyright 1996-2005 Kunihiro Ishiguro, et al.\n\n"
            return f"Welcome to Alpine Linux on {dev.name.lower()}\n"
        if cmd in ("exit", "logout", "quit"):
            return "logout\n"
        return f"-sh: {parts[0]}: not found\n"

    async def _router(self, line: str) -> str:
        low = line.lower()
        assert self.device
        if low in ("?", "help"):
            return DEVICE_HELP + "\n"
        if self.mode == "exec":
            if low in ("exit", "quit", "logout"):
                self._disconnect()
                return "Connection closed.\n"
            if low in ("end",):
                return ""
            if low in ("configure terminal", "conf t", "configure", "config t", "conf term", "config terminal"):
                self.mode = "config"
                return ""
            if low.startswith("ping"):
                return await self._ping(line)
            res = await self.sessions.exec(self.session_id, self.device, line)
            return self._out(res.stdout, res.stderr)
        # configuration modes
        if low == "end" or low in ("exit", "quit") and self.mode == "config":
            self.mode, self.iface = "exec", None
            return ""
        if low in ("exit", "quit"):
            self.mode, self.iface = "config", None
            return ""
        if low.startswith("do "):
            res = await self.sessions.exec(self.session_id, self.device, line[3:])
            return self._out(res.stdout, res.stdout and res.stderr)
        if low.startswith("show "):
            return "% Unknown command. Use 'do show ...' inside configuration mode.\n"
        m = re.match(r"(?:no\s+)?interface\s+(\S+)$", low)
        if self.mode in ("config", "router", "interface") and m and not low.startswith("no "):
            self.mode, self.iface = "interface", m.group(1)
            return ""
        if self.mode in ("config", "router", "interface") and low == "router ospf":
            self.mode, self.iface = "router", None
            return ""
        path = ["configure terminal"]
        if self.mode == "router":
            path.append("router ospf")
        elif self.mode == "interface" and self.iface:
            path.append(f"interface {self.iface}")
        path += [line, "end"]
        res = await self.sessions.exec(self.session_id, self.device, "; ".join(path))
        return self._out(res.stdout, res.stderr)

    async def _host(self, line: str) -> str:
        low = line.lower()
        if low in ("exit", "logout", "quit"):
            self._disconnect()
            return "logout\n"
        if low in ("help", "?"):
            return "Alpine Linux client. Try: ping -c 3 <ip>, traceroute <ip>, ip addr, ip route, cat /etc/hostname\n"
        if low.startswith("ping"):
            return await self._ping(line)
        res = await self.sessions.exec(self.session_id, self.device or "", line)
        return self._out(res.stdout, res.stderr)

    async def _ping(self, line: str) -> str:
        """Bound pings so an unbounded 'ping X' cannot hang the session (spec §28)."""
        try:
            argv = shlex.split(line)
        except ValueError:
            argv = line.split()
        if "-c" not in argv:
            argv = [argv[0], "-c", "4", *argv[1:]]
        if "-W" not in argv:
            argv = [argv[0], "-W", "1", *argv[1:]]
        res = await self.sessions.exec(self.session_id, self.device or "", " ".join(argv))
        return self._out(res.stdout, res.stderr)

    def _disconnect(self) -> None:
        self.device, self.kind, self.mode, self.iface = None, None, "exec", None

    @staticmethod
    def _out(stdout: str, stderr: str | None) -> str:
        text = (stdout or "") + (stderr or "")
        if text and not text.endswith("\n"):
            text += "\n"
        return text
