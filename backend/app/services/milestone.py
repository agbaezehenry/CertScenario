"""The Phase 1 first technical milestone (spec §48), runnable against any provider.

Each step returns evidence and a pass/fail. The runner stops at the first
failing step because later steps are meaningless once the loop is broken.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import Settings
from app.labs.base import LabProvider
from app.labs.faults import FaultInjector
from app.labs.reconcile import reconcile
from app.labs.store import SessionStore
from app.services.session_service import SessionService
from app.verification import VerificationContext


@dataclass
class Step:
    n: int
    title: str
    passed: bool
    detail: str = ""
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass
class MilestoneReport:
    steps: list[Step] = field(default_factory=list)
    session_id: str | None = None

    @property
    def ok(self) -> bool:
        return all(s.passed for s in self.steps) and len(self.steps) == 12


Reporter = Callable[[Step], None]


async def collect_diagnostics(provider: LabProvider, lab: str) -> dict[str, str]:
    """Device state dump for a failed step: routers, hosts, switch."""
    from app.models.domain import DeviceKind

    out: dict[str, str] = {}
    for dev in provider.devices(lab):
        try:
            if dev.kind == DeviceKind.ROUTER:
                for cmd in ("show ip interface brief", "show ip ospf neighbor", "show ip route", "show ip ospf interface"):
                    res = await provider.vtysh(lab, dev.name, [cmd], check=False)
                    out[f"{dev.name}: {cmd}"] = (res.stdout or res.stderr).strip()
                res = await provider.exec(lab, dev.name, "sysctl net.ipv4.ip_forward; ip -br addr")
                out[f"{dev.name}: sysctl/ip"] = (res.stdout + res.stderr).strip()
            else:
                res = await provider.exec(lab, dev.name, "ip -br addr; ip route; ip -br link")
                out[f"{dev.name}: ip"] = (res.stdout + res.stderr).strip()
        except Exception as e:  # noqa: BLE001
            out[f"{dev.name}: error"] = repr(e)
    for src, dst in (("AUS-CLIENT1", "10.20.10.1"), ("AUS-RTR1", "10.255.0.1"), ("HQ-RTR1", "10.10.10.10"), ("AUS-RTR1", "10.10.10.10")):
        try:
            res = await provider.ping(lab, src, dst, count=1)
            out[f"ping {src} -> {dst}"] = "ok" if res.success else "FAIL " + res.raw.strip()[-200:]
        except Exception as e:  # noqa: BLE001
            out[f"ping {src} -> {dst}"] = repr(e)
    return out


async def wait_until(check, *, timeout_s: float, interval_s: float = 2.0):  # type: ignore[no-untyped-def]
    """Poll an async predicate until it returns truthy or the timeout elapses.

    Real routers converge asynchronously; the mock is instantaneous. Returns the
    last predicate result so callers can report evidence either way.
    """
    import asyncio
    import time

    deadline = time.monotonic() + timeout_s
    result = await check()
    while not result and time.monotonic() < deadline:
        await asyncio.sleep(interval_s)
        result = await check()
    return result


async def run_milestone(
    settings: Settings,
    provider: LabProvider,
    store: SessionStore,
    *,
    scenario_id: str = "INC-1042",
    learner_id: str = "henry",
    report: Reporter | None = None,
) -> MilestoneReport:
    svc = SessionService(settings, provider, store)
    out = MilestoneReport()

    diagnostics_for: dict[int, str] = {}

    def step(n: int, title: str, passed: bool, detail: str = "", **evidence: object) -> bool:
        s = Step(n=n, title=title, passed=passed, detail=detail, evidence=evidence)
        out.steps.append(s)
        if not passed and n in diagnostics_for:
            s.evidence["diagnostics"] = diagnostics_for[n]
        if report:
            report(s)
        return passed

    async def fail_with_diagnostics(n: int) -> None:
        if lab:
            diag = await collect_diagnostics(provider, lab)
            diagnostics_for[n] = "\n".join(f"--- {k}\n{v}" for k, v in diag.items())

    # 1. provision healthy (no fault, no prober yet)
    scenario = svc.scenario_for  # noqa: F841 (kept for readability)
    from app.scenarios import find_scenario

    sc = find_scenario(settings.scenarios_dir, scenario_id, max_nodes=settings.max_nodes)
    from app.labs.naming import new_lab_id
    from app.models.domain import ScenarioSession, ScenarioState

    session = ScenarioSession(id="milestone", scenario_id=sc.id, learner_id=learner_id, lab_id=new_lab_id(), state=ScenarioState.PROVISIONING)
    store.save(session)
    out.session_id = session.id
    lab = session.lab_id
    assert lab
    try:
        inst = await provider.provision(sc, lab_id=lab)
        if not step(1, "Provision topology (healthy)", True, f"lab {lab}: {', '.join(d.name for d in inst.devices)}"):
            return out
        session.state = ScenarioState.ACTIVE
        store.save(session)
        injector = FaultInjector(provider)
        fault = sc.faults[0]

        async def austin_reaches_hq() -> bool:
            return (await provider.ping(lab, "AUS-CLIENT1", "10.10.10.10", count=2)).success

        async def austin_cannot_reach_hq() -> bool:
            return not (await provider.ping(lab, "AUS-CLIENT1", "10.10.10.10", count=2)).success

        # 2. healthy connectivity (real OSPF needs time to converge)
        ok = await wait_until(austin_reaches_hq, timeout_s=90)
        if not ok:
            await fail_with_diagnostics(2)
        if not step(2, "Austin -> HQ works when healthy", ok, f"AUS-CLIENT1 -> 10.10.10.10 {'ok' if ok else 'FAIL'}"):
            return out

        # 3. inject fault
        changed = await injector.apply(lab, fault)
        applied = await injector.is_applied(lab, fault)
        if not step(3, "Inject fault", applied, f"{fault.action} {fault.target} on {fault.device} (changed={changed})"):
            return out

        # 4. broken (LSA withdrawal is fast, but give the far side time to remove the route)
        broken = await wait_until(austin_cannot_reach_hq, timeout_s=45)
        if not broken:
            await fail_with_diagnostics(4)
        if not step(4, "Austin -> HQ broken after fault", broken, f"AUS-CLIENT1 -> 10.10.10.10 {'unreachable' if broken else 'still ok (BAD)'}"):
            return out

        # 5. trap: adjacency still FULL
        nb = await provider.vtysh(lab, "AUS-RTR1", ["show ip ospf neighbor json"], check=False)
        states = [
            (n.get("converged") or n.get("nbrState") or n.get("state") or "")
            for lst in json.loads(nb.stdout).get("neighbors", {}).values()
            for n in lst
        ]
        full = any(s.lower().startswith("full") for s in states)
        if not step(5, "OSPF adjacency still Full (the trap holds)", full, f"AUS-RTR1 neighbors: {states or 'none'}"):
            return out

        # 6. routing state both sides
        aus = json.loads((await provider.vtysh(lab, "AUS-RTR1", ["show ip route json"], check=False)).stdout)
        hq = json.loads((await provider.vtysh(lab, "HQ-RTR1", ["show ip route json"], check=False)).stdout)
        aus_has_hq = "10.10.10.0/24" in aus
        hq_has_aus = "10.20.10.0/24" in hq
        if not step(
            6,
            "Inspect routing on both routers",
            aus_has_hq and not hq_has_aus,
            f"AUS-RTR1 has 10.10.10.0/24: {aus_has_hq}; HQ-RTR1 has 10.20.10.0/24: {hq_has_aus} (expected True/False)",
            aus_routes=sorted(aus), hq_routes=sorted(hq),
        ):
            return out

        # baseline for ConfigUnchanged = the broken state the learner finds
        baseline = (await provider.get_state(lab)).running_configs
        ctx = VerificationContext(baseline_configs=baseline)

        # 7. repair minimally, then wait for the far side to learn the prefix
        await provider.vtysh(lab, "AUS-RTR1", ["configure terminal", "router ospf", f"network {fault.target} area {fault.area}", "end"])
        repaired = not await injector.is_applied(lab, fault)
        converged = await wait_until(austin_reaches_hq, timeout_s=90)
        if not (repaired and converged):
            await fail_with_diagnostics(7)
        if not step(7, "Repair OSPF configuration", repaired and converged, f"network {fault.target} area {fault.area} restored on AUS-RTR1; Austin -> HQ {'ok' if converged else 'still down'}"):
            return out

        # 8/9. verification
        result = await svc.verifier.run_scenario(lab, sc, ctx)
        step(8, "Run deterministic verification", True, f"passed={result.passed} failed={result.failed}", categories={k: v.model_dump() for k, v in result.categories.items()})
        if not step(
            9,
            "Resolution + regression + quality pass after correct fix",
            result.resolution_ok and result.regression_ok and result.quality_ok,
            "; ".join(f"{c.id}={'PASS' if c.passed else 'FAIL'}" for c in result.checks),
        ):
            return out

        # 10. re-break, sledgehammer, quality fails
        await injector.apply(lab, fault)
        broken_again = await wait_until(austin_cannot_reach_hq, timeout_s=45)
        await provider.vtysh(lab, "AUS-RTR1", ["configure terminal", "router ospf", "redistribute connected", "end"])
        await wait_until(austin_reaches_hq, timeout_s=90)
        r2 = await svc.verifier.run_scenario(lab, sc, ctx)
        ok10 = broken_again and r2.resolution_ok and r2.regression_ok and not r2.quality_ok
        if not step(
            10,
            "Re-break, sledgehammer fix: resolution PASS, quality FAIL",
            ok10,
            f"re-broken={broken_again} resolution={r2.resolution_ok} regression={r2.regression_ok} quality={r2.quality_ok}",
        ):
            return out
    finally:
        # 11. destroy
        await provider.destroy(lab)
        session.state = ScenarioState.DESTROYED
        store.save(session)
    step(11, "Destroy topology", True, f"lab {lab} destroyed")

    # 12. no orphans
    rep = await reconcile(provider, store, settings, dry_run=True)
    leftover = [x for x in rep.running if x == lab]
    step(12, "No orphaned containers remain", not leftover and lab not in rep.running, f"running northstar labs: {rep.running or 'none'}")
    return out
