"""Session orchestration (spec §29 lifecycle).

    start -> provision -> capture baseline -> inject fault -> capture baseline
          -> start prober -> ACTIVE ... verify ... destroy

Everything the learner does goes through ``exec``/``vtysh`` here so it is
recorded as events and bumps the idle clock.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.events import EventBus, EventType
from app.events.bus import Subscriber
from app.events.types import CONFIG_COMMAND_MARKERS, READ_COMMAND_PREFIXES
from app.labs.base import LabProvider
from app.labs.faults import FaultInjector
from app.labs.naming import new_lab_id
from app.labs.reconcile import LIVE_STATES, session_expired
from app.labs.store import SessionStore
from app.models.domain import (
    CommandResult,
    DeviceKind,
    ScenarioSession,
    ScenarioState,
    VerificationResult,
    transition,
)
from app.probing import Prober
from app.scenarios import ScenarioDefinition, find_scenario
from app.verification import VerificationContext, Verifier

log = logging.getLogger("northstar.session")


class SessionService:
    def __init__(
        self,
        settings: Settings,
        provider: LabProvider,
        store: SessionStore,
        *,
        event_sinks: list[Subscriber] | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.store = store
        self.faults = FaultInjector(provider)
        self.verifier = Verifier(provider)
        self._buses: dict[str, EventBus] = {}
        self._probers: dict[str, Prober] = {}
        self._scenarios: dict[str, ScenarioDefinition] = {}
        self._event_sinks: list[Subscriber] = list(event_sinks or [])
        self.on_bus_created: list[Callable[[str, EventBus], None]] = []

    # ------------------------------------------------------------- plumbing
    def session_dir(self, session_id: str) -> Path:
        return self.settings.state_dir / "sessions" / session_id

    def bus(self, session_id: str) -> EventBus:
        if session_id not in self._buses:
            bus = EventBus(self.session_dir(session_id) / "events.jsonl")
            for sink in self._event_sinks:
                bus.subscribe(sink)
            self._buses[session_id] = bus
            for hook in self.on_bus_created:
                hook(session_id, bus)
        return self._buses[session_id]

    def live_sessions(self) -> list[ScenarioSession]:
        return [s for s in self.store.list() if s.state in LIVE_STATES]

    async def sweep_expired(self, now: datetime | None = None) -> list[tuple[str, str]]:
        """Destroy sessions past their idle/absolute timeouts (spec §7.3). Returns (id, reason)."""
        out: list[tuple[str, str]] = []
        for s in self.live_sessions():
            why = session_expired(s, self.settings, now)
            if why:
                await self._emit(
                    s,
                    EventType.SESSION_IDLE_EXPIRED if why == "idle" else EventType.SESSION_TIMED_OUT,
                    "system",
                    reason=why,
                )
                await self.destroy(s.id)
                out.append((s.id, why))
        return out

    def prober(self, session_id: str) -> Prober | None:
        return self._probers.get(session_id)

    def scenario_for(self, session: ScenarioSession) -> ScenarioDefinition:
        if session.scenario_id not in self._scenarios:
            self._scenarios[session.scenario_id] = find_scenario(
                self.settings.scenarios_dir, session.scenario_id, max_nodes=self.settings.max_nodes
            )
        return self._scenarios[session.scenario_id]

    def get(self, session_id: str) -> ScenarioSession:
        s = self.store.get(session_id)
        if not s:
            raise KeyError(f"unknown session {session_id}")
        return s

    def _touch(self, session: ScenarioSession) -> None:
        session.last_activity_at = datetime.now(UTC)
        self.store.save(session)

    async def _emit(self, session: ScenarioSession, event_type: EventType, source: str, **meta: object) -> None:
        await self.bus(session.id).emit(
            event_type,
            learner_id=session.learner_id,
            scenario_id=session.scenario_id,
            session_id=session.id,
            source=source,
            **meta,
        )

    # -------------------------------------------------------------- lifecycle
    async def start(
        self, scenario_id: str, learner_id: str, *, start_prober: bool = True, session_id: str | None = None
    ) -> ScenarioSession:
        scenario = find_scenario(self.settings.scenarios_dir, scenario_id, max_nodes=self.settings.max_nodes)
        self._scenarios[scenario.id] = scenario
        session = ScenarioSession(
            id=session_id or uuid.uuid4().hex[:12],
            scenario_id=scenario.id,
            learner_id=learner_id,
            lab_id=new_lab_id(),
        )
        session.state = transition(session.state, ScenarioState.PROVISIONING)
        self.store.save(session)  # persisted before provisioning so a crash can be reconciled
        await self._emit(session, EventType.SCENARIO_STARTED, "system", lab_id=session.lab_id)
        assert session.lab_id
        try:
            inst = await self.provider.provision(scenario, lab_id=session.lab_id)
            session.workdir = inst.workdir
            await self._emit(session, EventType.LAB_PROVISIONED, "lab", devices=[d.name for d in inst.devices])
            for fault in scenario.faults:
                changed = await self.faults.apply(session.lab_id, fault)
                await self._emit(session, EventType.FAULT_APPLIED, "scenario", fault=fault.id, device=fault.device, changed=changed)
            # Baseline = the world as the learner finds it (fault applied). ConfigUnchanged
            # compares against this, so untouched sections must stay identical.
            state = await self.provider.get_state(session.lab_id)
            session.baseline_configs = dict(state.running_configs)
            session.state = transition(session.state, ScenarioState.ACTIVE)
            self.store.save(session)
            await self._emit(session, EventType.TICKET_OPENED, "helpdesk", incident=scenario.id, severity=scenario.incident.severity)
            if start_prober and scenario.prober_matrix:
                await self.start_prober(session.id)
        except Exception:
            session.state = ScenarioState.FAILED
            self.store.save(session)
            raise
        return session

    async def start_prober(self, session_id: str) -> Prober:
        session = self.get(session_id)
        assert session.lab_id
        if session_id in self._probers:
            return self._probers[session_id]
        scenario = self.scenario_for(session)
        prober = Prober(
            self.provider,
            scenario,
            session.lab_id,
            bus=self.bus(session.id),
            learner_id=session.learner_id,
            session_id=session.id,
            log_path=self.session_dir(session.id) / "prober.jsonl",
            interval_s=self.settings.prober_interval_seconds,
        )
        await prober.start()
        self._probers[session_id] = prober
        return prober

    async def stop_prober(self, session_id: str) -> None:
        p = self._probers.pop(session_id, None)
        if p:
            await p.stop()

    async def verify(self, session_id: str) -> VerificationResult:
        session = self.get(session_id)
        assert session.lab_id
        scenario = self.scenario_for(session)
        ctx = VerificationContext(baseline_configs=session.baseline_configs)
        result = await self.verifier.run_scenario(session.lab_id, scenario, ctx, session_id=session.id)
        (self.session_dir(session.id) / "verification").mkdir(parents=True, exist_ok=True)
        (self.session_dir(session.id) / "verification" / f"{result.run_at.strftime('%H%M%S')}.json").write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        (self.session_dir(session.id) / "verification" / "latest.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        await self._emit(
            session,
            EventType.VERIFICATION_RUN,
            "verifier",
            passed=result.passed,
            failed=result.failed,
            categories={k: v.model_dump() for k, v in result.categories.items()},
            success=result.scenario_success,
        )
        if result.scenario_success and session.state in (
            ScenarioState.ACTIVE,
            ScenarioState.INVESTIGATING,
            ScenarioState.MITIGATED,
        ):
            session.state = ScenarioState.RESOLVED
            await self._emit(session, EventType.VERIFICATION_PASSED, "verifier", quality_ok=result.quality_ok)
        elif not result.scenario_success and session.state == ScenarioState.RESOLVED:
            session.state = transition(session.state, ScenarioState.INVESTIGATING)
        self._touch(session)
        return result

    async def capture_final_state(self, session_id: str) -> dict[str, str]:
        """Snapshot running configs before teardown; the grader compares them to the baseline."""
        session = self.get(session_id)
        path = self.session_dir(session_id) / "final_configs.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        configs: dict[str, str] = {}
        if session.lab_id:
            try:
                configs = dict((await self.provider.get_state(session.lab_id)).running_configs)
            except Exception:  # noqa: BLE001 — lab may already be gone
                log.warning("could not capture final state for %s", session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(configs, indent=2), encoding="utf-8")
        return configs

    def latest_verification(self, session_id: str) -> VerificationResult | None:
        path = self.session_dir(session_id) / "verification" / "latest.json"
        if not path.exists():
            return None
        return VerificationResult.model_validate_json(path.read_text(encoding="utf-8"))

    async def destroy(self, session_id: str) -> ScenarioSession:
        session = self.get(session_id)
        await self.stop_prober(session_id)
        await self.capture_final_state(session_id)
        if session.lab_id:
            await self.provider.destroy(session.lab_id)
        session.ended_at = datetime.now(UTC)
        scenario = self.scenario_for(session)
        node_count = len(scenario.topology.devices) + (1 if scenario.topology.prober else 0)
        session.container_minutes = round(
            (session.ended_at - session.started_at).total_seconds() / 60 * node_count, 2
        )
        session.state = ScenarioState.DESTROYED
        self.store.save(session)
        await self._emit(session, EventType.LAB_DESTROYED, "lab", container_minutes=session.container_minutes)
        return session

    async def complete(self, session_id: str) -> ScenarioSession:
        """RESOLVED/POSTMORTEM -> COMPLETED, then tear the lab down."""
        session = self.get(session_id)
        if session.state == ScenarioState.RESOLVED:
            session.state = transition(session.state, ScenarioState.POSTMORTEM)
        session.state = transition(session.state, ScenarioState.COMPLETED)
        self.store.save(session)
        await self._emit(session, EventType.SCENARIO_COMPLETED, "system")
        await self.stop_prober(session_id)
        await self.capture_final_state(session_id)
        if session.lab_id:
            await self.provider.destroy(session.lab_id)
        session = self.get(session_id)
        session.ended_at = datetime.now(UTC)
        scenario = self.scenario_for(session)
        node_count = len(scenario.topology.devices) + (1 if scenario.topology.prober else 0)
        session.container_minutes = round((session.ended_at - session.started_at).total_seconds() / 60 * node_count, 2)
        self.store.save(session)
        await self._emit(session, EventType.LAB_DESTROYED, "lab", container_minutes=session.container_minutes)
        return session

    # ---------------------------------------------------------- learner I/O
    async def exec(self, session_id: str, device: str, command: str) -> CommandResult:
        """Run a learner command on a device and record telemetry (spec §22)."""
        session = self.get(session_id)
        assert session.lab_id
        scenario = self.scenario_for(session)
        dev = scenario.topology.device(device)
        if dev.kind == DeviceKind.ROUTER and not command.strip().lower().startswith(("ping", "traceroute")):
            lines = [ln.strip() for ln in command.split(";") if ln.strip()]
            result = await self.provider.vtysh(session.lab_id, dev.name, lines, check=False)
        else:
            result = await self.provider.exec(session.lab_id, dev.name, command)
        is_config = any(m in command.lower() for m in CONFIG_COMMAND_MARKERS)
        is_read = command.strip().lower().startswith(READ_COMMAND_PREFIXES) and not is_config
        await self._emit(
            session,
            EventType.COMMAND_EXECUTED,
            "terminal",
            device=dev.name,
            command=command,
            exit_code=result.exit_code,
            read_only=is_read,
        )
        if is_config:
            await self._emit(session, EventType.CONFIG_CHANGED, "terminal", device=dev.name, command=command)
            if session.state == ScenarioState.ACTIVE:
                session.state = transition(session.state, ScenarioState.INVESTIGATING)
        elif session.state == ScenarioState.ACTIVE:
            session.state = transition(session.state, ScenarioState.INVESTIGATING)
        self._touch(session)
        return result
