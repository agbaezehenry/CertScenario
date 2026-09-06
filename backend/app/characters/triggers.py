"""System-triggered character messages (spec §33–34, consequence engine §21).

Deterministic rules decide *that* a character speaks and with which intent;
the CharacterEngine decides the words. Rules:

* NETWORK_CONNECTIVITY_LOST  -> Maya: manager_notices_new_outage (debounced 2 min)
* VERIFICATION_PASSED        -> Maya: request_postmortem (once)
* 20 min without learner activity -> Maya: request_status_update (once per quiet period)
* each 30-min SEV-2 window without an update -> Maya: cadence_missed (once per window)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.events import EventBus, EventType
from app.models.domain import SimulationEvent
from app.services.chat_service import ChatService
from app.services.session_service import SessionService

log = logging.getLogger("northstar.triggers")

IDLE_MINUTES = 20
CADENCE_MINUTES = 30
OUTAGE_DEBOUNCE_MINUTES = 2


@dataclass
class TriggerState:
    last_outage_msg: datetime | None = None
    postmortem_requested: bool = False
    idle_nagged_for: datetime | None = None  # last_activity_at value we nagged about
    cadence_windows_flagged: set[int] = field(default_factory=set)


def is_manager_update(e: SimulationEvent) -> bool:
    if e.event_type == EventType.INCIDENT_UPDATED:
        return True
    if e.event_type == EventType.CHAT_MESSAGE_SENT:
        to = e.metadata.get("to") or []
        conv = e.metadata.get("conversation")
        return ("maya" in to or conv == "network-ops") and bool(e.metadata.get("status_update"))
    return False


class TriggerEngine:
    def __init__(self, sessions: SessionService, chat: ChatService) -> None:
        self.sessions = sessions
        self.chat = chat
        self._state: dict[str, TriggerState] = {}
        sessions.on_bus_created.append(self.attach)

    def state(self, session_id: str) -> TriggerState:
        return self._state.setdefault(session_id, TriggerState())

    def attach(self, session_id: str, bus: EventBus) -> None:
        async def handler(e: SimulationEvent) -> None:
            if e.session_id != session_id:
                return
            await self.on_event(e)

        bus.subscribe(handler)

    async def on_event(self, e: SimulationEvent) -> None:
        sid = e.session_id
        if not sid:
            return
        st = self.state(sid)
        now = e.timestamp
        if e.event_type == EventType.NETWORK_CONNECTIVITY_LOST:
            if st.last_outage_msg and now - st.last_outage_msg < timedelta(minutes=OUTAGE_DEBOUNCE_MINUTES):
                return
            st.last_outage_msg = now
            await self._say(sid, "maya", "manager_notices_new_outage")
        elif e.event_type == EventType.VERIFICATION_PASSED and not st.postmortem_requested:
            st.postmortem_requested = True
            await self._say(sid, "maya", "request_postmortem")
            await self._say(sid, "carlos", "users_confirm_restored")

    async def _say(self, sid: str, cid: str, intent: str) -> None:
        try:
            await self.chat.system_message(sid, cid, intent)
        except Exception:
            log.exception("trigger %s/%s failed", cid, intent)

    async def tick(self, now: datetime | None = None) -> list[tuple[str, str]]:
        """Evaluate elapsed-time rules for every live session. Returns fired (session, intent)."""
        now = now or datetime.now(UTC)
        fired: list[tuple[str, str]] = []
        for s in self.sessions.live_sessions():
            if s.state.value in ("PROVISIONING", "RESOLVED", "POSTMORTEM"):
                continue
            st = self.state(s.id)
            events = self.sessions.bus(s.id).events
            # idle
            if now - s.last_activity_at >= timedelta(minutes=IDLE_MINUTES) and st.idle_nagged_for != s.last_activity_at:
                st.idle_nagged_for = s.last_activity_at
                await self._say(s.id, "maya", "request_status_update")
                fired.append((s.id, "request_status_update"))
            # cadence
            elapsed = now - s.started_at
            window = int(elapsed.total_seconds() // (CADENCE_MINUTES * 60))
            if window >= 1:
                for w in range(1, window + 1):
                    if w in st.cadence_windows_flagged:
                        continue
                    w_start = s.started_at + timedelta(minutes=CADENCE_MINUTES * (w - 1))
                    w_end = s.started_at + timedelta(minutes=CADENCE_MINUTES * w)
                    updated = any(is_manager_update(e) and w_start <= e.timestamp < w_end for e in events)
                    st.cadence_windows_flagged.add(w)
                    if not updated:
                        await self._say(s.id, "maya", "cadence_missed")
                        fired.append((s.id, "cadence_missed"))
        return fired
