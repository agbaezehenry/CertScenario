"""Character context construction (spec §13, §35).

Structural spoiler defense: this module's only view of a scenario is
``ScenarioPublicView``, a type that has no ``hidden`` field. If the builder
cannot read the answer, no prompt injection can extract it. Prompt wording
(Phase 4) and the output validator are the second and third layers.
"""

from __future__ import annotations

from typing import Any

from app.models.domain import ChangeLogEntry, Character, CharacterContext, SimulationEvent, WikiPage
from app.scenarios.schema import ScenarioPublicView

# Event types characters may be told about, per role. Nothing that exposes
# device-level truth (COMMAND_EXECUTED output, CONFIG_CHANGED lines).
_VISIBLE_EVENTS: dict[str, set[str]] = {
    "maya": {
        "SCENARIO_STARTED",
        "INCIDENT_UPDATED",
        "ESCALATION_REQUESTED",
        "NETWORK_CONNECTIVITY_LOST",
        "NETWORK_CONNECTIVITY_RESTORED",
        "VERIFICATION_PASSED",
        "POSTMORTEM_SUBMITTED",
    },
    "carlos": {"SCENARIO_STARTED", "INCIDENT_UPDATED", "NETWORK_CONNECTIVITY_RESTORED"},
    "priya": {"SCENARIO_STARTED", "ESCALATION_REQUESTED", "INCIDENT_UPDATED"},
}


def build_character_context(
    character: Character,
    scenario: ScenarioPublicView,
    *,
    changes: list[ChangeLogEntry],
    wiki: list[WikiPage],
    people: dict[str, Character],
    events: list[SimulationEvent],
    max_events: int = 20,
) -> CharacterContext:
    ctx = CharacterContext(character_id=character.id)
    ctx.incorrect_beliefs = list(character.incorrect_beliefs)
    ctx.known_people = [f"{p.name} ({p.role})" for pid, p in people.items() if pid != character.id]

    incident = {
        "id": scenario.id,
        "title": scenario.title,
        "severity": scenario.incident.severity,
        "reported_at": scenario.incident.reported_at,
        "affected": scenario.incident.affected,
        "description": scenario.incident.description,
        "dri": scenario.role.title,
    }
    ctx.known_incidents = [incident]

    if character.id == "priya":
        # She ran the window; she knows her own tickets as she wrote them.
        ctx.known_changes = [c.model_dump() for c in changes if c.engineer == "priya"]
        ctx.known_topology = [f"{d.name} ({d.kind}, {d.site})" for d in scenario.devices]
        ctx.known_documents = [p.title for p in wiki]
    elif character.id == "maya":
        ctx.known_changes = [{"id": c.id, "engineer": c.engineer, "window": c.window, "summary": c.summary} for c in changes]
        ctx.known_topology = sorted({d.site for d in scenario.devices})
        ctx.known_documents = [p.title for p in wiki if "incident" in p.id]
    else:  # carlos and any future non-technical role
        ctx.known_changes = []
        ctx.known_topology = []
        ctx.known_documents = []

    visible = _VISIBLE_EVENTS.get(character.id, {"SCENARIO_STARTED"})
    ctx.recent_events = [
        _sanitize_event(e) for e in events if e.event_type in visible
    ][-max_events:]
    return ctx


def _sanitize_event(e: SimulationEvent) -> dict[str, Any]:
    """Only pass fields that describe *that something happened*, never device output."""
    allowed = {"pair", "src", "dst", "status", "note", "severity"}
    return {
        "type": e.event_type,
        "at": e.timestamp.isoformat(timespec="seconds"),
        **{k: v for k, v in e.metadata.items() if k in allowed},
    }


def context_text(ctx: CharacterContext) -> str:
    """Flatten a context for prompt building or leak scanning."""
    return ctx.model_dump_json()
