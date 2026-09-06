"""Session orchestration (spec §29 lifecycle).

    start -> provision -> capture baseline -> inject fault -> capture baseline
          -> start prober -> ACTIVE ... verify ... destroy

Everything the learner does goes through ``exec``/``vtysh`` here so it is
recorded as events and bumps the idle clock.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.events import EventBus, EventType
from app.events.types import CONFIG_COMMAND_MARKERS, READ_COMMAND_PREFIXES
from app.labs.base import LabProvider
from app.labs.faults import FaultInjector
from app.labs.naming import new_lab_id
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
    def __init__(self, settings: Settings, provider: LabProvider, store: SessionStore) -> None:
        self.settings = settings
        self.provider = provider
        self.store = store
        self.faults = FaultInjector(provider)
        self.verifier = Verifier(provider)
        self._buses: dict[str, EventBus] = {}
        self._probers: dict[str, Prober] = {}
        self._scenarios: dict[str, ScenarioDefinition] = {}

    # ------------------------------------------------------------- plumbing
    def session_dir(self, session_id: str) -> Path:
        return self.settings.state_dir / "sessions" / session_id

    def bus(self, session_id: str) -> EventBus:
        if session_id not in self._buses:
            self._buses[session_id] = EventBus(self.session_dir(session_id) / "events.jsonl")
        return self._buses[session_id]

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

    async def destroy(self, session_id: str) -> ScenarioSession:
        session = self.get(session_id)
        await self.stop_prober(session_id)
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

    # ---------------------------------------------------------- learner I/O
    async def exec(self, session_id: str, device: str, command: str) -> CommandResult:
        """Run a learner command on a device and record telemetry (spec §22)."""
        session = self.get(session_id)
        assert session.lab_id
        scenario = self.scenario_for(session)
        dev = scenario.topology.device(device)
        if dev.kind == DeviceKind.ROUTER:
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
