"""Fault injection (spec §18). Idempotent and reversible.

Only ``remove_ospf_network`` exists for the MVP. New fault types register
themselves in ``FAULT_ACTIONS``; scenarios reference them by ``action``.
"""

from __future__ import annotations

import re
from typing import Protocol

from app.models.domain import Fault

from .base import LabError, LabProvider


class FaultAction(Protocol):
    async def apply(self, provider: LabProvider, lab_id: str, fault: Fault) -> bool:
        """Apply the fault. Return True if state changed, False if already applied."""
        ...

    async def revert(self, provider: LabProvider, lab_id: str, fault: Fault) -> bool:
        """Undo the fault. Return True if state changed, False if already healthy."""
        ...

    async def is_applied(self, provider: LabProvider, lab_id: str, fault: Fault) -> bool: ...


def _network_line(fault: Fault) -> str:
    return f"network {fault.target} area {fault.area}"


class RemoveOspfNetwork:
    """Remove ``network <prefix> area <n>`` from ``router ospf`` on a device."""

    async def is_applied(self, provider: LabProvider, lab_id: str, fault: Fault) -> bool:
        cfg = await provider.vtysh(lab_id, fault.device, ["show running-config"])
        pattern = re.compile(r"^\s*" + re.escape(_network_line(fault)) + r"\s*$", re.MULTILINE)
        return pattern.search(cfg.stdout) is None

    async def apply(self, provider: LabProvider, lab_id: str, fault: Fault) -> bool:
        if await self.is_applied(provider, lab_id, fault):
            return False
        res = await provider.vtysh(
            lab_id,
            fault.device,
            ["configure terminal", "router ospf", f"no {_network_line(fault)}", "end"],
        )
        if res.exit_code != 0:
            raise LabError(f"fault apply failed on {fault.device}: {res.stderr or res.stdout}")
        return True

    async def revert(self, provider: LabProvider, lab_id: str, fault: Fault) -> bool:
        if not await self.is_applied(provider, lab_id, fault):
            return False
        res = await provider.vtysh(
            lab_id,
            fault.device,
            ["configure terminal", "router ospf", _network_line(fault), "end"],
        )
        if res.exit_code != 0:
            raise LabError(f"fault revert failed on {fault.device}: {res.stderr or res.stdout}")
        return True


FAULT_ACTIONS: dict[str, FaultAction] = {
    "remove_ospf_network": RemoveOspfNetwork(),
}


class FaultInjector:
    def __init__(self, provider: LabProvider) -> None:
        self.provider = provider

    def _action(self, fault: Fault) -> FaultAction:
        try:
            return FAULT_ACTIONS[fault.action]
        except KeyError as e:
            raise LabError(f"unsupported fault action {fault.action!r}") from e

    async def apply(self, lab_id: str, fault: Fault) -> bool:
        return await self._action(fault).apply(self.provider, lab_id, fault)

    async def revert(self, lab_id: str, fault: Fault) -> bool:
        return await self._action(fault).revert(self.provider, lab_id, fault)

    async def is_applied(self, lab_id: str, fault: Fault) -> bool:
        return await self._action(fault).is_applied(self.provider, lab_id, fault)
