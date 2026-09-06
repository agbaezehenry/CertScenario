"""Composition root. Everything the API, CLI and tests need, wired once."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.assessments import AssessmentInputs, assess
from app.characters.engine import CharacterEngine
from app.characters.llm import LLMProvider, provider_from_env
from app.characters.triggers import TriggerEngine
from app.config import Settings, load_settings
from app.db import AssessmentRow, Database, PostmortemRow, SqlSessionStore
from app.events import EventType
from app.labs import make_provider
from app.labs.base import LabProvider
from app.labs.reconcile import reconcile
from app.models.domain import Assessment, Postmortem, ScenarioSession, ScenarioState
from app.probing import Prober
from app.services.chat_service import ChatService
from app.services.session_service import SessionService
from app.services.world import DEMO_USERS, World

log = logging.getLogger("northstar.platform")

COMPLETION_CHECKS = ("incident_acknowledged", "network_repaired", "regression_passed", "resolution_submitted", "postmortem_completed")


class Platform:
    def __init__(self, settings: Settings | None = None, *, provider: LabProvider | None = None, db: Database | None = None, llm: LLMProvider | None = None) -> None:
        self.settings = settings or load_settings()
        self.db = db or Database(f"sqlite:///{self.settings.state_dir / 'northstar.db'}")
        self.provider = provider or make_provider(self.settings)
        self.store = SqlSessionStore(self.db)
        self.sessions = SessionService(self.settings, self.provider, self.store, event_sinks=[self.db.event_sink])
        self.world = World(self.db, self.settings)
        self.llm = llm if llm is not None else provider_from_env()
        self.chat = ChatService(self.world, self.sessions, self._engine_for)
        self.triggers = TriggerEngine(self.sessions, self.chat)

    def _engine_for(self, scenario_id: str) -> CharacterEngine:
        sc = self.world.scenario(scenario_id)
        return CharacterEngine(sc, self.world.characters(scenario_id), self.world.changes(scenario_id), self.world.wiki(scenario_id), self.llm)

    # ------------------------------------------------------------- lifecycle
    async def startup(self) -> dict[str, Any]:
        rep = await reconcile(self.provider, self.store, self.settings)
        # Probers do not survive a restart; restart them for live sessions.
        for s in self.sessions.live_sessions():
            if s.state not in (ScenarioState.PROVISIONING,):
                try:
                    await self.sessions.start_prober(s.id)
                except Exception:
                    log.exception("could not restart prober for %s", s.id)
        return {"orphans_destroyed": rep.destroyed, "expired": rep.expired}

    async def shutdown(self) -> None:
        for s in self.sessions.live_sessions():
            await self.sessions.stop_prober(s.id)

    async def housekeeping(self) -> None:
        """Called periodically: timeouts + elapsed-time character triggers."""
        await self.sessions.sweep_expired()
        await self.triggers.tick()

    # -------------------------------------------------------------- learner
    def active_session(self, learner_id: str) -> ScenarioSession | None:
        live = [s for s in self.sessions.live_sessions() if s.learner_id == learner_id]
        return live[-1] if live else None

    async def start_scenario(self, scenario_id: str, learner_id: str) -> ScenarioSession:
        if self.active_session(learner_id):
            raise RuntimeError("learner already has a live session")
        session = await self.sessions.start(scenario_id, learner_id)
        scenario = self.world.scenario(session.scenario_id)
        self.world.create_incident(session, scenario)
        self.world.seed_conversations(session, scenario)
        return session

    async def update_incident(self, session_id: str, incident_id: str, patch: dict[str, Any]) -> Any:
        session = self.sessions.get(session_id)
        incident, changed = self.world.update_incident(session_id, incident_id, patch)
        if changed:
            await self.sessions.bus(session_id).emit(
                EventType.INCIDENT_UPDATED,
                learner_id=session.learner_id,
                scenario_id=session.scenario_id,
                session_id=session_id,
                source="ticket",
                incident=incident.id,
                **{k: (v if k != "note" else True) for k, v in changed.items()},
            )
            self.sessions._touch(session)
        return incident

    async def record_view(self, session_id: str, event_type: EventType, **meta: Any) -> None:
        session = self.sessions.get(session_id)
        await self.sessions.bus(session_id).emit(event_type, learner_id=session.learner_id, scenario_id=session.scenario_id, session_id=session_id, source="ui", **meta)
        self.sessions._touch(session)

    # ------------------------------------------------------------ monitoring
    def monitoring(self, session_id: str) -> dict[str, Any]:
        session = self.sessions.get(session_id)
        scenario = self.world.scenario(session.scenario_id)
        prober = self.sessions.prober(session_id)
        snap = prober.snapshot() if prober else {}
        tiles = []
        for t in scenario.monitoring:
            if t.source == "static":
                tiles.append({"id": t.id, "label": t.label, "state": t.value, "value": None})
            elif t.source == "prober":
                p = snap.get(t.pair or "", {})
                tiles.append({"id": t.id, "label": t.label, "state": p.get("state", "NO DATA"), "value": None})
            else:
                p = snap.get(t.pair or "", {})
                rtt = p.get("rtt_ms")
                tiles.append({"id": t.id, "label": t.label, "state": "OK" if rtt is not None else "NO DATA", "value": f"{rtt:.1f} ms" if rtt is not None else None})
        outages = []
        if prober:
            for o in prober.stats.outages[-20:]:
                outages.append({"pair": o.pair_id, "started_at": o.started_at.isoformat(), "ended_at": o.ended_at.isoformat() if o.ended_at else None, "duration_s": o.duration_s})
        return {"tiles": tiles, "pairs": snap, "outages": outages, "prober_running": prober is not None, "captured_at": datetime.now(UTC).isoformat()}

    # ------------------------------------------------------------ postmortem
    def save_postmortem(self, session_id: str, pm: Postmortem) -> Postmortem:
        with self.db.session() as s:
            s.merge(
                PostmortemRow(
                    session_id=session_id,
                    incident_id=pm.incident_id,
                    impact=pm.impact,
                    timeline=pm.timeline,
                    root_cause=pm.root_cause,
                    resolution=pm.resolution,
                    contributing_factors=pm.contributing_factors,
                    preventative_actions=pm.preventative_actions,
                    submitted_at=pm.submitted_at,
                )
            )
        return pm

    def postmortem(self, session_id: str) -> Postmortem | None:
        with self.db.session() as s:
            r = s.get(PostmortemRow, session_id)
            if not r:
                return None
            return Postmortem(
                session_id=r.session_id,
                incident_id=r.incident_id,
                impact=r.impact,
                timeline=r.timeline,
                root_cause=r.root_cause,
                resolution=r.resolution,
                contributing_factors=r.contributing_factors,
                preventative_actions=r.preventative_actions,
                submitted_at=r.submitted_at if r.submitted_at.tzinfo else r.submitted_at.replace(tzinfo=UTC),
            )

    # ------------------------------------------------------------ completion
    def completion_status(self, session_id: str) -> dict[str, bool]:
        session = self.sessions.get(session_id)
        inc = self.world.incident(session_id, session.scenario_id)
        v = self.sessions.latest_verification(session_id)
        return {
            "incident_acknowledged": bool(inc and inc.status.lower() != "assigned"),
            "network_repaired": bool(v and v.resolution_ok),
            "regression_passed": bool(v and v.regression_ok),
            "resolution_submitted": bool(inc and (inc.resolution or inc.status.lower() == "resolved")),
            "postmortem_completed": self.postmortem(session_id) is not None,
        }

    def _inputs(self, session: ScenarioSession) -> AssessmentInputs:
        sid = session.id
        prober_log = self.sessions.session_dir(sid) / "prober.jsonl"
        final_path = self.sessions.session_dir(sid) / "final_configs.json"
        import json

        final_configs = json.loads(final_path.read_text()) if final_path.exists() else {}
        return AssessmentInputs(
            scenario=self.world.scenario(session.scenario_id),
            events=self.db.events(sid) or self.sessions.bus(sid).events,
            verification=self.sessions.latest_verification(sid),
            prober_samples=Prober.read_log(prober_log),
            incident=self.world.incident(sid, session.scenario_id),
            messages=self.world.all_messages(sid),
            postmortem=self.postmortem(sid),
            baseline_configs=session.baseline_configs,
            final_configs=final_configs,
            started_at=session.started_at,
            learner_id=session.learner_id,
        )

    async def complete(self, session_id: str) -> tuple[Assessment, dict[str, Any]]:
        status = self.completion_status(session_id)
        missing = [k for k, ok in status.items() if not ok]
        if missing:
            raise IncompleteScenario(missing)
        await self.sessions.capture_final_state(session_id)
        await self.sessions.complete(session_id)
        session = self.sessions.get(session_id)
        assessment, detail = assess(session_id, self._inputs(session))
        with self.db.session() as s:
            s.merge(
                AssessmentRow(
                    session_id=session_id,
                    scores={k.value: v for k, v in assessment.scores.items()},
                    overall=assessment.overall,
                    strengths=assessment.strengths,
                    improvements=assessment.improvements,
                    detail=detail,
                    created_at=datetime.now(UTC),
                )
            )
        return assessment, detail

    def scorecard(self, session_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            r = s.get(AssessmentRow, session_id)
            if not r:
                return None
            return {"session_id": r.session_id, "scores": r.scores, "overall": r.overall, "strengths": r.strengths, "improvements": r.improvements, "detail": r.detail, "created_at": r.created_at.isoformat()}

    def preview_assessment(self, session_id: str) -> dict[str, Any]:
        """Assessment on the current state without completing (for the UI's progress view)."""
        session = self.sessions.get(session_id)
        a, detail = assess(session_id, self._inputs(session))
        return {"scores": {k.value: v for k, v in a.scores.items()}, "overall": a.overall, "strengths": a.strengths, "improvements": a.improvements, "detail": detail}


class IncompleteScenario(Exception):
    def __init__(self, missing: list[str]) -> None:
        super().__init__(", ".join(missing))
        self.missing = missing


def demo_user(user_id: str) -> Any:
    return DEMO_USERS.get(user_id)


def state_dir_for(settings: Settings) -> Path:
    return settings.state_dir
