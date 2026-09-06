from __future__ import annotations

from pathlib import Path

from app.events import EventBus, EventType
from app.labs.faults import FaultInjector
from app.probing import Prober


async def test_prober_detects_transient_outage_and_restoration(healthy_lab, scenario, tmp_path: Path):
    p, lab = healthy_lab
    bus = EventBus()
    prober = Prober(p, scenario, lab, bus=bus, learner_id="t", session_id="s", log_path=tmp_path / "prober.jsonl", interval_s=0.01)

    await prober.cycle()
    assert all(v["state"] == "UP" for v in prober.snapshot().values())

    await FaultInjector(p).apply(lab, scenario.faults[0])
    await prober.cycle()
    snap = prober.snapshot()
    assert snap["austin->app"]["state"] == "CRITICAL"
    assert snap["hq->app"]["state"] == "UP"
    assert snap["hq->aus-rtr1"]["state"] == "UP"  # WAN up, routers up, HQ up: the §14 clue shape

    lost = [e for e in bus.events if e.event_type == EventType.NETWORK_CONNECTIVITY_LOST]
    assert [e.metadata["pair"] for e in lost] == ["austin->app"]

    await FaultInjector(p).revert(lab, scenario.faults[0])
    await prober.cycle()
    restored = [e for e in bus.events if e.event_type == EventType.NETWORK_CONNECTIVITY_RESTORED]
    assert [e.metadata["pair"] for e in restored] == ["austin->app"]
    assert len(prober.stats.outages) == 1 and prober.stats.outages[0].duration_s is not None

    samples = Prober.read_log(tmp_path / "prober.jsonl")
    assert len(samples) == 3 * len(scenario.prober_matrix)
    assert {s.src for s in samples} == {"prober@austin", "prober@hq"}


async def test_prober_blast_radius_when_learner_shuts_wan(broken_lab, scenario, tmp_path: Path):
    p, lab = broken_lab
    bus = EventBus()
    prober = Prober(p, scenario, lab, bus=bus, learner_id="t", session_id="s", interval_s=0.01)
    await prober.cycle()  # austin->app already down at start: recorded, no event
    assert bus.events == []
    await p.vtysh(lab, "AUS-RTR1", ["configure terminal", "interface eth1", "shutdown", "end"])
    await prober.cycle()
    lost = sorted(e.metadata["pair"] for e in bus.events if e.event_type == EventType.NETWORK_CONNECTIVITY_LOST)
    assert lost == ["hq->aus-rtr1"]


async def test_prober_start_stop_runs_in_background(healthy_lab, scenario):
    import asyncio

    p, lab = healthy_lab
    bus = EventBus()
    prober = Prober(p, scenario, lab, bus=bus, learner_id="t", session_id="s", interval_s=0.01)
    await prober.start()
    await asyncio.sleep(0.15)
    await prober.stop()
    assert prober.stats.cycles >= 3
    types = [e.event_type for e in bus.events]
    assert types[0] == EventType.PROBER_STARTED and types[-1] == EventType.PROBER_STOPPED
