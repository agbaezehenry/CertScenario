from __future__ import annotations

from app.events import EventType
from app.models.domain import ScenarioState
from app.services.milestone import run_milestone
from app.services.session_service import SessionService


async def test_full_session_lifecycle(settings, mock_provider, store):
    svc = SessionService(settings, mock_provider, store)
    s = await svc.start("INC-1042", "henry", start_prober=False)
    assert s.state == ScenarioState.ACTIVE and s.lab_id and s.lab_id.startswith("ns-")
    assert "HQ-RTR1" in s.baseline_configs and "access-list MGMT-SSH" in s.baseline_configs["HQ-RTR1"]

    r = await svc.verify(s.id)
    assert not r.scenario_success and svc.get(s.id).state == ScenarioState.ACTIVE

    out = await svc.exec(s.id, "AUS-RTR1", "show ip ospf neighbor")
    assert "Full/DR" in out.stdout
    assert svc.get(s.id).state == ScenarioState.INVESTIGATING
    out = await svc.exec(s.id, "aus-client1", "ping -c 1 10.10.10.10")
    assert out.exit_code == 1

    await svc.exec(s.id, "AUS-RTR1", "configure terminal; router ospf; network 10.20.10.0/24 area 0; end")
    r = await svc.verify(s.id)
    assert r.scenario_success and svc.get(s.id).state == ScenarioState.RESOLVED

    s = await svc.destroy(s.id)
    assert s.state == ScenarioState.DESTROYED and s.container_minutes >= 0
    assert await mock_provider.list_labs() == []

    types = [e.event_type for e in svc.bus(s.id).events]
    assert types[:4] == ["SCENARIO_STARTED", "LAB_PROVISIONED", "FAULT_APPLIED", "TICKET_OPENED"]
    assert EventType.COMMAND_EXECUTED in types and EventType.CONFIG_CHANGED in types
    assert EventType.VERIFICATION_PASSED in types and types[-1] == EventType.LAB_DESTROYED
    reads = [e for e in svc.bus(s.id).events if e.event_type == EventType.COMMAND_EXECUTED and e.metadata["read_only"]]
    assert len(reads) == 2
    assert (svc.session_dir(s.id) / "events.jsonl").exists()


async def test_session_starts_and_stops_prober(settings, mock_provider, store):
    svc = SessionService(settings, mock_provider, store)
    s = await svc.start("INC-1042", "henry")
    assert svc.prober(s.id) is not None
    import asyncio

    await asyncio.sleep(0.05)
    await svc.destroy(s.id)
    assert svc.prober(s.id) is None
    assert (svc.session_dir(s.id) / "prober.jsonl").exists()


async def test_milestone_passes_on_mock(settings, mock_provider, store):
    rep = await run_milestone(settings, mock_provider, store)
    assert [s.n for s in rep.steps] == list(range(1, 13))
    assert rep.ok, [(s.n, s.detail) for s in rep.steps if not s.passed]
