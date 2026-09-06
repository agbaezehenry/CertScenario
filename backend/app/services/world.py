"""World engine (spec §46): the fictional company and its persistent state.

People, teams, incidents, conversations, wiki, change log. Everything here is
organisational; nothing here touches devices.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.config import Settings
from app.db import ConversationRow, Database, IncidentRow, MessageRow
from app.models.domain import (
    ChangeLogEntry,
    Character,
    ChatConversation,
    ChatMessage,
    Employee,
    Incident,
    ScenarioSession,
    Team,
    User,
    WikiPage,
)
from app.scenarios import (
    ScenarioDefinition,
    find_scenario,
    load_changes,
    load_characters,
    load_wiki,
)

TEAMS = {
    "infra": Team(id="infra", name="Infrastructure Engineering", manager_id="maya"),
    "helpdesk": Team(id="helpdesk", name="IT Help Desk", manager_id=None),
}

EMPLOYEES = {
    "henry": Employee(id="henry", name="Henry", title="Associate Network Engineer", team_id="infra", manager_id="maya"),
    "maya": Employee(id="maya", name="Maya Chen", title="Network Engineering Manager", team_id="infra", is_ai_character=True),
    "priya": Employee(id="priya", name="Priya Shah", title="Senior Network Engineer", team_id="infra", manager_id="maya", is_ai_character=True),
    "carlos": Employee(id="carlos", name="Carlos Ramirez", title="Help Desk Technician", team_id="helpdesk", is_ai_character=True),
}

DEMO_USERS = {
    "henry": User(id="henry", email="henry@northstar.example", display_name="Henry", employee_id="henry"),
}

OPENING_MESSAGES: dict[str, list[tuple[str, str]]] = {
    "dm-maya": [
        (
            "maya",
            (
                "Morning. Austin is reporting that users cannot reach internal services. "
                "INC-1042 has been assigned to you. You're DRI. Please investigate and keep me updated."
            ),
        )
    ],
    "network-ops": [
        ("carlos", "hey netops — getting a bunch of calls from Austin, internet's down over there? users can't get to anything"),
        ("maya", "Thanks Carlos. Henry has INC-1042, he'll take it from here."),
    ],
}


class World:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._scenarios: dict[str, ScenarioDefinition] = {}
        self._wiki: dict[str, list[WikiPage]] = {}
        self._changes: dict[str, list[ChangeLogEntry]] = {}
        self._characters: dict[str, dict[str, Character]] = {}

    # ------------------------------------------------------------ scenario data
    def scenario(self, scenario_id: str) -> ScenarioDefinition:
        if scenario_id not in self._scenarios:
            sc = find_scenario(self.settings.scenarios_dir, scenario_id, max_nodes=self.settings.max_nodes)
            self._scenarios[scenario_id] = sc
            self._wiki[scenario_id] = load_wiki(sc)
            self._changes[scenario_id] = load_changes(sc)
            self._characters[scenario_id] = load_characters(sc)
        return self._scenarios[scenario_id]

    def wiki(self, scenario_id: str) -> list[WikiPage]:
        self.scenario(scenario_id)
        return self._wiki[scenario_id]

    def wiki_page(self, scenario_id: str, page_id: str) -> WikiPage | None:
        return next((p for p in self.wiki(scenario_id) if p.id == page_id), None)

    def changes(self, scenario_id: str, q: str | None = None) -> list[ChangeLogEntry]:
        self.scenario(scenario_id)
        items = self._changes[scenario_id]
        if q:
            ql = q.lower()
            items = [c for c in items if ql in f"{c.id} {c.device} {c.engineer} {c.summary} {c.description} {c.category}".lower()]
        return sorted(items, key=lambda c: c.applied_at, reverse=True)

    def change(self, scenario_id: str, change_id: str) -> ChangeLogEntry | None:
        return next((c for c in self.changes(scenario_id) if c.id.upper() == change_id.upper()), None)

    def characters(self, scenario_id: str) -> dict[str, Character]:
        self.scenario(scenario_id)
        return self._characters[scenario_id]

    # ------------------------------------------------------------------ people
    def me(self, user_id: str) -> dict[str, Any]:
        user = DEMO_USERS[user_id]
        emp = EMPLOYEES[user.employee_id]
        team = TEAMS[emp.team_id]
        manager = EMPLOYEES[emp.manager_id] if emp.manager_id else None
        return {
            "user": user.model_dump(),
            "employee": emp.model_dump(),
            "team": team.model_dump(),
            "manager": manager.model_dump() if manager else None,
            "company": {"id": "northstar", "name": "Northstar Technologies"},
            "responsibility_level": "Associate Network Engineer",
        }

    def people(self) -> list[Employee]:
        return list(EMPLOYEES.values())

    # --------------------------------------------------------------- incidents
    @staticmethod
    def _incident_model(r: IncidentRow) -> Incident:
        return Incident(
            id=r.id,
            title=r.title,
            severity=r.severity,
            status=r.status,
            reported_at=r.reported_at,
            reporter_id=r.reporter_id,
            affected=r.affected,
            description=r.description,
            dri_id=r.dri_id,
            notes=list(r.notes or []),
            resolution=r.resolution,
            impact=r.impact,
            root_cause=r.root_cause,
        )

    def create_incident(self, session: ScenarioSession, scenario: ScenarioDefinition) -> Incident:
        row = IncidentRow(
            key=f"{session.id}:{scenario.id}",
            id=scenario.id,
            session_id=session.id,
            title=scenario.title,
            severity=scenario.incident.severity,
            status="Assigned",
            reported_at=scenario.incident.reported_at,
            reporter_id=scenario.incident.reported_by,
            affected=scenario.incident.affected,
            description=scenario.incident.description.strip(),
            dri_id=session.learner_id,
            notes=[],
        )
        with self.db.session() as s:
            s.merge(row)
        return self._incident_model(row)

    def incidents(self, session_id: str) -> list[Incident]:
        with self.db.session() as s:
            rows = s.execute(select(IncidentRow).where(IncidentRow.session_id == session_id)).scalars().all()
            return [self._incident_model(r) for r in rows]

    def incident(self, session_id: str, incident_id: str) -> Incident | None:
        with self.db.session() as s:
            r = s.get(IncidentRow, f"{session_id}:{incident_id.upper()}")
            return self._incident_model(r) if r else None

    def incident_row(self, session_id: str, incident_id: str) -> IncidentRow | None:
        with self.db.session() as s:
            return s.get(IncidentRow, f"{session_id}:{incident_id.upper()}")

    def update_incident(self, session_id: str, incident_id: str, patch: dict[str, Any]) -> tuple[Incident, dict[str, Any]]:
        """Apply a PATCH. Returns (incident, changed fields)."""
        changed: dict[str, Any] = {}
        with self.db.session() as s:
            r = s.get(IncidentRow, f"{session_id}:{incident_id.upper()}")
            if r is None:
                raise KeyError(incident_id)
            now = datetime.now(UTC)
            for field in ("status", "resolution", "impact", "root_cause"):
                if field in patch and patch[field] is not None and patch[field] != getattr(r, field):
                    setattr(r, field, patch[field])
                    changed[field] = patch[field]
            if patch.get("note"):
                notes = list(r.notes or [])
                notes.append({"at": now.isoformat(timespec="seconds"), "body": str(patch["note"])})
                r.notes = notes
                changed["note"] = patch["note"]
            if changed:
                r.updated_at = now
                if "status" in changed and r.acknowledged_at is None and changed["status"].lower() != "assigned":
                    r.acknowledged_at = now
                    changed["acknowledged"] = True
            s.flush()
            model = self._incident_model(r)
        return model, changed

    # ----------------------------------------------------------- conversations
    def seed_conversations(self, session: ScenarioSession, scenario: ScenarioDefinition) -> list[ChatConversation]:
        convs = [ChatConversation(id="network-ops", kind="channel", name="#network-ops", participant_ids=[session.learner_id, "maya", "priya", "carlos"])]
        for cid in scenario.characters:
            convs.append(ChatConversation(id=f"dm-{cid}", kind="dm", name=EMPLOYEES[cid].name, participant_ids=[session.learner_id, cid]))
        with self.db.session() as s:
            for c in convs:
                s.merge(ConversationRow(key=f"{session.id}:{c.id}", id=c.id, session_id=session.id, kind=c.kind, name=c.name, participant_ids=c.participant_ids))
        for conv_id, msgs in OPENING_MESSAGES.items():
            for sender, body in msgs:
                self.add_message(session.id, conv_id, sender, body, {"opening": True})
        return convs

    def conversations(self, session_id: str) -> list[dict[str, Any]]:
        with self.db.session() as s:
            rows = s.execute(select(ConversationRow).where(ConversationRow.session_id == session_id)).scalars().all()
            out = []
            for r in rows:
                last = s.execute(
                    select(MessageRow).where(MessageRow.session_id == session_id, MessageRow.conversation_id == r.id).order_by(MessageRow.sent_at.desc()).limit(1)
                ).scalar_one_or_none()
                out.append(
                    {
                        **ChatConversation(id=r.id, kind=r.kind, name=r.name, participant_ids=list(r.participant_ids or [])).model_dump(),
                        "last_message": self._message_model(last).model_dump(mode="json") if last else None,
                    }
                )
            order = {"network-ops": 0, "dm-maya": 1, "dm-carlos": 2, "dm-priya": 3}
            return sorted(out, key=lambda c: order.get(c["id"], 9))

    def conversation(self, session_id: str, conv_id: str) -> ChatConversation | None:
        with self.db.session() as s:
            r = s.get(ConversationRow, f"{session_id}:{conv_id}")
            return ChatConversation(id=r.id, kind=r.kind, name=r.name, participant_ids=list(r.participant_ids or [])) if r else None

    @staticmethod
    def _message_model(r: MessageRow) -> ChatMessage:
        sent = r.sent_at if r.sent_at.tzinfo else r.sent_at.replace(tzinfo=UTC)
        return ChatMessage(id=r.id, conversation_id=r.conversation_id, sender_id=r.sender_id, body=r.body, sent_at=sent)

    def messages(self, session_id: str, conv_id: str) -> list[ChatMessage]:
        with self.db.session() as s:
            rows = s.execute(
                select(MessageRow).where(MessageRow.session_id == session_id, MessageRow.conversation_id == conv_id).order_by(MessageRow.sent_at, MessageRow.id)
            ).scalars().all()
            return [self._message_model(r) for r in rows]

    def all_messages(self, session_id: str) -> list[ChatMessage]:
        with self.db.session() as s:
            rows = s.execute(select(MessageRow).where(MessageRow.session_id == session_id).order_by(MessageRow.sent_at, MessageRow.id)).scalars().all()
            return [self._message_model(r) for r in rows]

    def add_message(self, session_id: str, conv_id: str, sender_id: str, body: str, meta: dict[str, Any] | None = None) -> ChatMessage:
        row = MessageRow(
            id=uuid.uuid4().hex[:16],
            session_id=session_id,
            conversation_id=conv_id,
            sender_id=sender_id,
            body=body,
            sent_at=datetime.now(UTC),
            meta=meta or {},
        )
        with self.db.session() as s:
            s.add(row)
        return self._message_model(row)
