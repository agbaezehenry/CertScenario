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

    def step(n: int, title: str, passed: bool, detail: str = "", **evidence: object) -> bool:
        s = Step(n=n, title=title, passed=passed, detail=detail, evidence=evidence)
        out.steps.append(s)
        if report:
            report(s)
        return passed

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

        # 2. healthy connectivity
        p = await provider.ping(lab, "AUS-CLIENT1", "10.10.10.10", count=2)
        if not step(2, "Austin -> HQ works when healthy", p.success, f"AUS-CLIENT1 -> 10.10.10.10 {'ok' if p.success else 'FAIL'}"):
            return out

        # 3. inject fault
        changed = await injector.apply(lab, fault)
        applied = await injector.is_applied(lab, fault)
        if not step(3, "Inject fault", applied, f"{fault.action} {fault.target} on {fault.device} (changed={changed})"):
            return out

        # 4. broken
        p = await provider.ping(lab, "AUS-CLIENT1", "10.10.10.10", count=2)
        if not step(4, "Austin -> HQ broken after fault", not p.success, f"AUS-CLIENT1 -> 10.10.10.10 {'still ok (BAD)' if p.success else 'unreachable'}"):
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

        # 7. repair minimally
        await provider.vtysh(lab, "AUS-RTR1", ["configure terminal", "router ospf", f"network {fault.target} area {fault.area}", "end"])
        if not step(7, "Repair OSPF configuration", not await injector.is_applied(lab, fault), f"network {fault.target} area {fault.area} restored on AUS-RTR1"):
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
        broken_again = not (await provider.ping(lab, "AUS-CLIENT1", "10.10.10.10")).success
        await provider.vtysh(lab, "AUS-RTR1", ["configure terminal", "router ospf", "redistribute connected", "end"])
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
