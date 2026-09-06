"""Lab provider abstraction (spec §7.2).

A provider owns the lifecycle of a topology and mediates every command.
Nothing above this layer knows about docker, containerlab, or the mock.

Router commands go through ``vtysh`` (a list of CLI lines executed in one
session — config mode is just ``configure terminal`` followed by lines).
Host commands go through ``exec`` (a shell command). ``ping`` is a first-class
call because verification and the prober both need a parsed result, and
because the mock must implement it from its network model rather than by
faking text.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.models.domain import CommandResult, Device, LabInstance, LabState, PingResult
from app.scenarios.schema import ScenarioDefinition


class LabError(Exception):
    pass


class UnknownDevice(LabError):
    pass


class LabNotFound(LabError):
    pass


@runtime_checkable
class LabProvider(Protocol):
    name: str

    async def provision(self, scenario: ScenarioDefinition, *, lab_id: str) -> LabInstance: ...

    async def destroy(self, lab_id: str) -> None: ...

    async def exec(self, lab_id: str, device: str, command: str) -> CommandResult:
        """Run a shell command inside a device container."""
        ...

    async def vtysh(self, lab_id: str, device: str, lines: list[str]) -> CommandResult:
        """Run router CLI lines inside one vtysh session (routers only)."""
        ...

    async def ping(
        self,
        lab_id: str,
        device: str,
        destination: str,
        *,
        source: str | None = None,
        count: int = 1,
        timeout_s: float = 1.0,
    ) -> PingResult: ...

    async def get_state(self, lab_id: str) -> LabState: ...

    async def list_labs(self) -> list[str]:
        """Lab IDs the provider currently has running (for orphan reconciliation)."""
        ...

    def devices(self, lab_id: str) -> list[Device]: ...


def device_lookup(devices: list[Device], name: str) -> Device:
    for d in devices:
        if d.name.upper() == name.upper() or d.node == name.lower():
            return d
    raise UnknownDevice(name)
