"""Integration suite against the REAL containerlab provider (spec §40.2).

Run with:  LAB_PROVIDER=containerlab pytest -m integration tests/integration
Skipped automatically when docker/containerlab are not on PATH.
"""

from __future__ import annotations

import pytest
from app.labs.faults import FaultInjector
from app.labs.reconcile import reconcile
from app.labs.store import InMemorySessionStore
from app.services.milestone import run_milestone

from tests.conftest import requires_real_lab

pytestmark = [pytest.mark.integration, requires_real_lab]


async def test_milestone_on_real_frr(real_settings, real_provider):
    store = InMemorySessionStore()
    rep = await run_milestone(real_settings, real_provider, store, report=lambda s: print(f"{s.n:>2} {'PASS' if s.passed else 'FAIL'} {s.title}: {s.detail}"))
    assert rep.ok, [(s.n, s.title, s.detail) for s in rep.steps if not s.passed]


async def test_prober_detects_transient_outage_on_real_lab(real_settings, real_provider, real_scenario):
    import asyncio

    from app.events import EventBus, EventType
    from app.probing import Prober

    lab = "ns-itprobe"
    await real_provider.provision(real_scenario, lab_id=lab)
    try:
        bus = EventBus()
        prober = Prober(real_provider, real_scenario, lab, bus=bus, learner_id="ci", session_id="ci", interval_s=1.0)
        await prober.start()
        await asyncio.sleep(3)
        await real_provider.vtysh(lab, "AUS-RTR1", ["configure terminal", "interface eth1", "shutdown", "end"])
        await asyncio.sleep(4)
        await real_provider.vtysh(lab, "AUS-RTR1", ["configure terminal", "interface eth1", "no shutdown", "end"])
        await asyncio.sleep(45)  # OSPF re-adjacency + SPF
        await prober.stop()
        types = [e.event_type for e in bus.events]
        assert EventType.NETWORK_CONNECTIVITY_LOST in types
        assert EventType.NETWORK_CONNECTIVITY_RESTORED in types
    finally:
        await real_provider.destroy(lab)


async def test_orphan_reconciliation_after_simulated_crash(real_settings, real_provider, real_scenario):
    store = InMemorySessionStore()
    lab = "ns-itorphan"
    await real_provider.provision(real_scenario, lab_id=lab)
    # "crash": provider object forgets the lab; store never heard of it.
    fresh = type(real_provider)(real_settings)
    rep = await reconcile(fresh, store, real_settings)
    assert lab in rep.orphans and lab in rep.destroyed
    assert lab not in await fresh.list_labs()


async def test_fault_broken_fixed_broken_without_reprovision(real_settings, real_provider, real_scenario):
    import asyncio

    lab = "ns-itfault"
    inj = FaultInjector(real_provider)
    fault = real_scenario.faults[0]
    await real_provider.provision(real_scenario, lab_id=lab)
    try:
        await asyncio.sleep(30)  # initial OSPF convergence
        assert (await real_provider.ping(lab, "AUS-CLIENT1", "10.10.10.10", count=2)).success
        assert await inj.apply(lab, fault)
        await asyncio.sleep(5)
        assert not (await real_provider.ping(lab, "AUS-CLIENT1", "10.10.10.10", count=2)).success
        assert await inj.revert(lab, fault)
        await asyncio.sleep(10)
        assert (await real_provider.ping(lab, "AUS-CLIENT1", "10.10.10.10", count=2)).success
        assert await inj.apply(lab, fault)
        assert not await inj.apply(lab, fault)
    finally:
        await real_provider.destroy(lab)
