"""Deterministic assertions (spec §19). No LLM anywhere near this module.

Each assertion parses structured (JSON) output from the provider so the same
code grades the real FRR and the mock. Every assertion carries a category.
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
from dataclasses import dataclass
from typing import Any, ClassVar

from app.labs.base import LabProvider
from app.labs.frrconfig import config_section
from app.models.domain import AssertionCategory, CheckResult


@dataclass(kw_only=True)
class Assertion:
    id: str
    category: AssertionCategory
    on_fail: str | None = None
    check: ClassVar[str] = "base"

    async def evaluate(self, provider: LabProvider, lab_id: str, ctx: VerificationContext) -> CheckResult:
        started = time.perf_counter()
        try:
            passed, detail, evidence = await self._run(provider, lab_id, ctx)
        except Exception as e:  # noqa: BLE001 — an exception is a failing check with evidence
            passed, detail, evidence = False, f"error: {e}", {"error": repr(e)}
        return CheckResult(
            id=self.id,
            check=self.check,
            category=self.category,
            passed=passed,
            detail=detail,
            evidence=evidence,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    async def _run(self, provider: LabProvider, lab_id: str, ctx: VerificationContext) -> tuple[bool, str, dict[str, Any]]:
        raise NotImplementedError


@dataclass
class VerificationContext:
    """State captured at session start that some assertions compare against."""

    baseline_configs: dict[str, str]


def _json(stdout: str) -> Any:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        raise ValueError(f"expected JSON from device, got: {stdout[:200]!r}") from e


@dataclass(kw_only=True)
class PingAssertion(Assertion):
    check: ClassVar[str] = "ping"
    source: str
    destination: str
    expect: str = "success"
    count: int = 2

    async def _run(self, provider, lab_id, ctx):  # type: ignore[no-untyped-def]
        res = await provider.ping(lab_id, self.source, self.destination, count=self.count, timeout_s=1.0)
        want = self.expect == "success"
        return (
            res.success == want,
            f"{self.source} -> {self.destination}: {'reachable' if res.success else 'unreachable'}"
            + (f" ({res.rtt_ms:.2f} ms)" if res.rtt_ms is not None else ""),
            {"success": res.success, "rtt_ms": res.rtt_ms, "raw": res.raw[-600:]},
        )


@dataclass(kw_only=True)
class RouteAssertion(Assertion):
    check: ClassVar[str] = "route_exists"
    device: str
    prefix: str
    via: str | None = None  # protocol: ospf | static | connected
    expect: str = "present"

    async def _run(self, provider, lab_id, ctx):  # type: ignore[no-untyped-def]
        res = await provider.vtysh(lab_id, self.device, ["show ip route json"], check=False)
        table = _json(res.stdout)
        want = str(ipaddress.ip_network(self.prefix, strict=False))
        entries = table.get(want, [])
        matched = [e for e in entries if e.get("selected", True) and (self.via is None or e.get("protocol") == self.via)]
        present = bool(matched)
        ok = present if self.expect == "present" else not present
        via = matched[0]["nexthops"][0] if matched else None
        detail = f"{self.device}: {want} {'present' if present else 'absent'}"
        if via:
            detail += f" via {via.get('ip', 'connected')} ({matched[0].get('protocol')})"
        return ok, detail, {"entries": entries}


@dataclass(kw_only=True)
class OSPFNeighborAssertion(Assertion):
    check: ClassVar[str] = "ospf_neighbor"
    device: str
    neighbor: str  # device name or router-id
    expect: str = "Full"

    async def _run(self, provider, lab_id, ctx):  # type: ignore[no-untyped-def]
        res = await provider.vtysh(lab_id, self.device, ["show ip ospf neighbor json"], check=False)
        data = _json(res.stdout)
        nbrs = data.get("neighbors", {})
        states: dict[str, str] = {}
        for rid, lst in nbrs.items():
            for n in lst:
                st = n.get("converged") or n.get("nbrState") or n.get("state") or ""
                states[rid] = st
        ok = any(st.split("/")[0].lower() == self.expect.lower() for st in states.values())
        if self.expect.lower() == "full":
            ok = any(st.lower().startswith("full") for st in states.values())
        return (
            ok,
            f"{self.device} neighbors: " + (", ".join(f"{k}={v}" for k, v in states.items()) or "none"),
            {"neighbors": states},
        )


@dataclass(kw_only=True)
class InterfaceAssertion(Assertion):
    check: ClassVar[str] = "interface"
    device: str
    interface: str
    expect: str = "up"

    async def _run(self, provider, lab_id, ctx):  # type: ignore[no-untyped-def]
        res = await provider.vtysh(lab_id, self.device, [f"show interface {self.interface} json"], check=False)
        data = _json(res.stdout)
        info = data.get(self.interface) or next(iter(data.values()), {})
        admin = str(info.get("administrativeStatus", "")).lower()
        oper = str(info.get("operationalStatus", "")).lower()
        is_up = admin == "up" and oper == "up"
        ok = is_up if self.expect == "up" else not is_up
        return ok, f"{self.device} {self.interface}: admin={admin} oper={oper}", {"admin": admin, "oper": oper}


@dataclass(kw_only=True)
class VLANAssertion(Assertion):
    """Placeholder for the Network Access domain (needs cEOS, spec §7.1)."""

    check: ClassVar[str] = "vlan"
    device: str
    vlan: int

    async def _run(self, provider, lab_id, ctx):  # type: ignore[no-untyped-def]
        return False, "VLAN assertions require a switching NOS; not available on FRR", {}


@dataclass(kw_only=True)
class ConfigPresentAssertion(Assertion):
    check: ClassVar[str] = "config_present"
    device: str
    pattern: str

    async def _run(self, provider, lab_id, ctx):  # type: ignore[no-untyped-def]
        res = await provider.vtysh(lab_id, self.device, ["show running-config"], check=False)
        found = re.search(r"^\s*" + re.escape(self.pattern) + r"\b", res.stdout, re.MULTILINE) is not None
        return found, f"{self.device}: {self.pattern!r} {'present' if found else 'absent'}", {"pattern": self.pattern}


@dataclass(kw_only=True)
class ConfigAbsentAssertion(Assertion):
    check: ClassVar[str] = "config_absent"
    device: str
    pattern: str

    async def _run(self, provider, lab_id, ctx):  # type: ignore[no-untyped-def]
        res = await provider.vtysh(lab_id, self.device, ["show running-config"], check=False)
        found = re.search(r"^\s*" + re.escape(self.pattern) + r"\b", res.stdout, re.MULTILINE) is not None
        return (not found), f"{self.device}: {self.pattern!r} {'present' if found else 'absent'}", {"pattern": self.pattern}


@dataclass(kw_only=True)
class ConfigUnchangedAssertion(Assertion):
    check: ClassVar[str] = "config_unchanged"
    device: str
    section: str

    async def _run(self, provider, lab_id, ctx):  # type: ignore[no-untyped-def]
        baseline = ctx.baseline_configs.get(self.device)
        if baseline is None:
            return False, f"no baseline config captured for {self.device}", {}
        res = await provider.vtysh(lab_id, self.device, ["show running-config"], check=False)
        before = config_section(baseline, self.section)
        after = config_section(res.stdout, self.section)
        same = before == after
        return (
            same,
            f"{self.device} section {self.section!r} {'unchanged' if same else 'modified'}",
            {"before": before, "after": after},
        )


@dataclass(kw_only=True)
class DNSAssertion(Assertion):
    check: ClassVar[str] = "dns"
    source: str
    name: str

    async def _run(self, provider, lab_id, ctx):  # type: ignore[no-untyped-def]
        res = await provider.exec(lab_id, self.source, f"nslookup {self.name}")
        return res.exit_code == 0, res.stdout[-300:], {"exit_code": res.exit_code}


@dataclass(kw_only=True)
class ServiceAssertion(Assertion):
    check: ClassVar[str] = "service"
    source: str
    url: str

    async def _run(self, provider, lab_id, ctx):  # type: ignore[no-untyped-def]
        res = await provider.exec(lab_id, self.source, f"wget -q -T 3 -O - {self.url} >/dev/null")
        return res.exit_code == 0, f"{self.source} -> {self.url}: exit {res.exit_code}", {"exit_code": res.exit_code}


ASSERTION_TYPES: dict[str, type[Assertion]] = {
    "ping": PingAssertion,
    "route_exists": RouteAssertion,
    "ospf_neighbor": OSPFNeighborAssertion,
    "interface": InterfaceAssertion,
    "vlan": VLANAssertion,
    "config_present": ConfigPresentAssertion,
    "config_absent": ConfigAbsentAssertion,
    "config_unchanged": ConfigUnchangedAssertion,
    "dns": DNSAssertion,
    "service": ServiceAssertion,
}

# YAML key -> dataclass field renames
_PARAM_ALIASES = {"from": "source"}


def build_assertion(spec: dict[str, Any]) -> Assertion:
    """Build from a scenario ``verification`` entry (AssertionSpec.model_dump())."""
    check = spec["check"]
    try:
        cls = ASSERTION_TYPES[check]
    except KeyError as e:
        raise ValueError(f"unknown check type {check!r}") from e
    params = {_PARAM_ALIASES.get(k, k): v for k, v in spec.get("params", {}).items()}
    return cls(id=spec["id"], category=AssertionCategory(spec["category"]), on_fail=spec.get("on_fail"), **params)
