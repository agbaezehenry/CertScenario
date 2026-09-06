"""Continuous background prober (spec §20.1).

Pings every pair in the scenario's prober matrix from the dedicated prober
container at a fixed interval, appends JSONL samples, and emits
NETWORK_CONNECTIVITY_LOST / RESTORED events on state change. The JSONL log is
the ground truth for blast-radius scoring and the postmortem timeline.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.events import EventBus, EventType
from app.labs.base import LabProvider
from app.models.domain import ProberSample
from app.scenarios.schema import ProberPairSpec, ScenarioDefinition

log = logging.getLogger("northstar.prober")


@dataclass
class Outage:
    pair_id: str
    started_at: datetime
    ended_at: datetime | None = None

    @property
    def duration_s(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds()


@dataclass
class ProberStats:
    cycles: int = 0
    samples: int = 0
    outages: list[Outage] = field(default_factory=list)
    last_state: dict[str, bool] = field(default_factory=dict)
    last_rtt: dict[str, float | None] = field(default_factory=dict)


class Prober:
    def __init__(
        self,
        provider: LabProvider,
        scenario: ScenarioDefinition,
        lab_id: str,
        *,
        bus: EventBus,
        learner_id: str,
        session_id: str | None,
        log_path: Path | None = None,
        interval_s: float = 1.0,
    ) -> None:
        if scenario.topology.prober is None:
            raise ValueError("scenario has no prober definition")
        self.provider = provider
        self.scenario = scenario
        self.lab_id = lab_id
        self.bus = bus
        self.learner_id = learner_id
        self.session_id = session_id
        self.log_path = log_path
        self.interval_s = interval_s
        self.stats = ProberStats()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._prober_node = scenario.topology.prober.node
        self._vantages = scenario.topology.prober.vantages
        self.pairs: list[ProberPairSpec] = list(scenario.prober_matrix)
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._task:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name=f"prober-{self.lab_id}")
        await self.bus.emit(
            EventType.PROBER_STARTED,
            learner_id=self.learner_id,
            scenario_id=self.scenario.id,
            session_id=self.session_id,
            source="prober",
            pairs=[p.id for p in self.pairs],
            interval_s=self.interval_s,
        )

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=self.interval_s + 5)
        except TimeoutError:
            self._task.cancel()
        self._task = None
        await self.bus.emit(
            EventType.PROBER_STOPPED,
            learner_id=self.learner_id,
            scenario_id=self.scenario.id,
            session_id=self.session_id,
            source="prober",
            cycles=self.stats.cycles,
            outages=[
                {"pair": o.pair_id, "started_at": o.started_at.isoformat(), "duration_s": o.duration_s}
                for o in self.stats.outages
            ],
        )

    async def _loop(self) -> None:
        while not self._stop.is_set():
            started = asyncio.get_running_loop().time()
            try:
                await self.cycle()
            except Exception:
                log.exception("prober cycle failed")
            elapsed = asyncio.get_running_loop().time() - started
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(0.0, self.interval_s - elapsed))
            except TimeoutError:
                pass

    # ---------------------------------------------------------------- cycle
    async def cycle(self) -> list[ProberSample]:
        """One pass over the matrix. Public so tests can drive it deterministically."""
        self.stats.cycles += 1
        samples = await asyncio.gather(*(self._probe(p) for p in self.pairs))
        for s in samples:
            self._record(s)
            await self._track(s)
        return list(samples)

    async def _probe(self, pair: ProberPairSpec) -> ProberSample:
        v = self._vantages[pair.vantage]
        res = await self.provider.ping(
            self.lab_id, self._prober_node, pair.destination, source=v.address, count=1, timeout_s=1.0
        )
        return ProberSample(
            ts=datetime.now(UTC),
            src=f"prober@{pair.vantage}",
            dst=pair.destination,
            result="ok" if res.success else "fail",
            rtt_ms=res.rtt_ms if res.success else None,
            pair_id=pair.id,
        )

    def _record(self, s: ProberSample) -> None:
        self.stats.samples += 1
        self.stats.last_rtt[s.pair_id or ""] = s.rtt_ms
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(s.model_dump_json() + "\n")

    async def _track(self, s: ProberSample) -> None:
        pid = s.pair_id or f"{s.src}->{s.dst}"
        ok = s.result == "ok"
        prev = self.stats.last_state.get(pid)
        self.stats.last_state[pid] = ok
        if prev is None:
            # First observation. A pair that starts broken is the scenario's own
            # symptom; record it as an open outage so duration is still measured.
            if not ok:
                self.stats.outages.append(Outage(pair_id=pid, started_at=s.ts))
            return
        if prev and not ok:
            self.stats.outages.append(Outage(pair_id=pid, started_at=s.ts))
            await self.bus.emit(
                EventType.NETWORK_CONNECTIVITY_LOST,
                learner_id=self.learner_id,
                scenario_id=self.scenario.id,
                session_id=self.session_id,
                source="prober",
                pair=pid,
                src=s.src,
                dst=s.dst,
            )
        elif not prev and ok:
            for o in reversed(self.stats.outages):
                if o.pair_id == pid and o.ended_at is None:
                    o.ended_at = s.ts
                    break
            await self.bus.emit(
                EventType.NETWORK_CONNECTIVITY_RESTORED,
                learner_id=self.learner_id,
                scenario_id=self.scenario.id,
                session_id=self.session_id,
                source="prober",
                pair=pid,
                src=s.src,
                dst=s.dst,
            )

    # --------------------------------------------------------------- reading
    def snapshot(self) -> dict[str, dict[str, object]]:
        """Current view for the monitoring board."""
        out: dict[str, dict[str, object]] = {}
        for p in self.pairs:
            st = self.stats.last_state.get(p.id)
            out[p.id] = {
                "label": p.label,
                "state": "NO DATA" if st is None else ("UP" if st else "CRITICAL"),
                "rtt_ms": self.stats.last_rtt.get(p.id),
            }
        return out

    @staticmethod
    def read_log(path: Path) -> list[ProberSample]:
        if not path.exists():
            return []
        return [ProberSample.model_validate_json(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
