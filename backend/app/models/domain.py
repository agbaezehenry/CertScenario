"""Domain model (spec §27).

Phase 1 keeps these as Pydantic models persisted through a small JSON store.
Phase 2 maps the same shapes onto SQLAlchemy; the shapes are the contract.
Scenario-specific *definitions* stay declarative in YAML (see scenarios/schema.py);
these are the runtime entities.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


# ----------------------------------------------------------------------------
# World engine: company, people
# ----------------------------------------------------------------------------


class Company(BaseModel):
    id: str = "northstar"
    name: str = "Northstar Technologies"


class Team(BaseModel):
    id: str
    name: str
    manager_id: str | None = None


class Employee(BaseModel):
    """A person inside the fictional company; may be the learner or a character."""

    id: str
    name: str
    title: str
    team_id: str
    manager_id: str | None = None
    is_ai_character: bool = False


class User(BaseModel):
    id: str
    email: str
    display_name: str
    employee_id: str


class Character(BaseModel):
    id: str
    name: str
    role: str
    persona: str
    knows: list[str] = Field(default_factory=list)
    does_not_know: list[str] = Field(default_factory=list)
    incorrect_beliefs: list[str] = Field(default_factory=list)
    behavior: str = ""
    triggers: list[dict[str, Any]] = Field(default_factory=list)


class CharacterContext(BaseModel):
    """Explicit per-character knowledge boundary (spec §13).

    Constructed by characters/context.py from a ScenarioPublicView. It can
    never contain the hidden truth because the builder is never handed it.
    """

    character_id: str
    known_incidents: list[dict[str, Any]] = Field(default_factory=list)
    known_changes: list[dict[str, Any]] = Field(default_factory=list)
    known_topology: list[str] = Field(default_factory=list)
    known_people: list[str] = Field(default_factory=list)
    known_documents: list[str] = Field(default_factory=list)
    incorrect_beliefs: list[str] = Field(default_factory=list)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)


# ----------------------------------------------------------------------------
# Scenario engine
# ----------------------------------------------------------------------------


class ScenarioState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    PROVISIONING = "PROVISIONING"
    ACTIVE = "ACTIVE"
    INVESTIGATING = "INVESTIGATING"
    MITIGATED = "MITIGATED"
    RESOLVED = "RESOLVED"
    POSTMORTEM = "POSTMORTEM"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DESTROYED = "DESTROYED"


# Legal transitions. FAILED/DESTROYED reachable from anywhere active.
SCENARIO_TRANSITIONS: dict[ScenarioState, set[ScenarioState]] = {
    ScenarioState.NOT_STARTED: {ScenarioState.PROVISIONING},
    ScenarioState.PROVISIONING: {ScenarioState.ACTIVE, ScenarioState.FAILED},
    ScenarioState.ACTIVE: {ScenarioState.INVESTIGATING, ScenarioState.MITIGATED, ScenarioState.FAILED, ScenarioState.DESTROYED},
    ScenarioState.INVESTIGATING: {ScenarioState.MITIGATED, ScenarioState.FAILED, ScenarioState.DESTROYED},
    ScenarioState.MITIGATED: {ScenarioState.INVESTIGATING, ScenarioState.RESOLVED, ScenarioState.FAILED, ScenarioState.DESTROYED},
    ScenarioState.RESOLVED: {ScenarioState.POSTMORTEM, ScenarioState.INVESTIGATING, ScenarioState.DESTROYED},
    ScenarioState.POSTMORTEM: {ScenarioState.COMPLETED, ScenarioState.DESTROYED},
    ScenarioState.COMPLETED: {ScenarioState.DESTROYED},
    ScenarioState.FAILED: {ScenarioState.DESTROYED},
    ScenarioState.DESTROYED: set(),
}


class IllegalTransition(Exception):
    pass


def transition(current: ScenarioState, new: ScenarioState) -> ScenarioState:
    if new not in SCENARIO_TRANSITIONS[current]:
        raise IllegalTransition(f"{current} -> {new} is not allowed")
    return new


class ScenarioSession(BaseModel):
    id: str
    scenario_id: str
    learner_id: str
    state: ScenarioState = ScenarioState.NOT_STARTED
    lab_id: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    last_activity_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
    container_minutes: float = 0.0
    workdir: str | None = None
    baseline_configs: dict[str, str] = Field(default_factory=dict)


# ----------------------------------------------------------------------------
# Tickets, changes, chat, wiki
# ----------------------------------------------------------------------------


class Incident(BaseModel):
    id: str
    title: str
    severity: str
    status: str = "Assigned"
    reported_at: str
    reporter_id: str
    affected: str
    description: str
    dri_id: str
    notes: list[dict[str, Any]] = Field(default_factory=list)  # {"at": iso, "body": str}
    resolution: str | None = None
    impact: str | None = None
    root_cause: str | None = None


class Ticket(Incident):
    """Alias kept for the ticketing UI; incidents are the only ticket type in MVP."""


class ChangeLogEntry(BaseModel):
    id: str
    device: str
    engineer: str
    window: str
    applied_at: str
    status: str
    category: str
    summary: str
    description: str


class ChatConversation(BaseModel):
    id: str
    kind: str  # "channel" | "dm"
    name: str
    participant_ids: list[str] = Field(default_factory=list)


class ChatMessage(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    body: str
    sent_at: datetime = Field(default_factory=utcnow)


class WikiPage(BaseModel):
    id: str
    title: str
    body: str
    owner: str | None = None
    last_reviewed: str | None = None


# ----------------------------------------------------------------------------
# Simulation engine
# ----------------------------------------------------------------------------


class DeviceKind(StrEnum):
    ROUTER = "router"
    SWITCH = "switch"
    HOST = "host"
    INFRA = "infra"


class Device(BaseModel):
    name: str  # learner-facing, e.g. AUS-RTR1
    node: str  # containerlab node name, e.g. aus-rtr1
    kind: DeviceKind
    site: str = ""
    address: str | None = None


class LabInstance(BaseModel):
    id: str
    scenario_id: str
    provider: str
    devices: list[Device]
    workdir: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class LabState(BaseModel):
    lab_id: str
    devices: list[Device]
    running_configs: dict[str, str] = Field(default_factory=dict)
    captured_at: datetime = Field(default_factory=utcnow)


class CommandResult(BaseModel):
    device: str
    command: str
    stdout: str
    stderr: str = ""
    exit_code: int = 0
    duration_ms: float = 0.0
    truncated: bool = False


class PingResult(BaseModel):
    source: str
    destination: str
    success: bool
    rtt_ms: float | None = None
    raw: str = ""


class Fault(BaseModel):
    id: str
    type: str
    device: str
    action: str
    target: str
    area: int = 0


class Decoy(BaseModel):
    type: str
    id: str
    device: str
    description: str


# ----------------------------------------------------------------------------
# Verification / assessment
# ----------------------------------------------------------------------------


class AssertionCategory(StrEnum):
    RESOLUTION = "resolution"
    REGRESSION = "regression"
    QUALITY = "quality"


class VerificationAssertion(BaseModel):
    id: str
    check: str
    category: AssertionCategory
    params: dict[str, Any] = Field(default_factory=dict)
    on_fail: str | None = None


class CheckResult(BaseModel):
    id: str
    check: str
    category: AssertionCategory
    passed: bool
    detail: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0


class CategorySummary(BaseModel):
    passed: int = 0
    failed: int = 0


class VerificationResult(BaseModel):
    session_id: str | None = None
    lab_id: str
    run_at: datetime = Field(default_factory=utcnow)
    passed: int
    failed: int
    categories: dict[str, CategorySummary]
    checks: list[CheckResult]

    @property
    def resolution_ok(self) -> bool:
        return self.categories.get("resolution", CategorySummary()).failed == 0

    @property
    def regression_ok(self) -> bool:
        return self.categories.get("regression", CategorySummary()).failed == 0

    @property
    def quality_ok(self) -> bool:
        return self.categories.get("quality", CategorySummary()).failed == 0

    @property
    def scenario_success(self) -> bool:
        """Spec §20: resolution AND regression. Quality only affects score."""
        return self.resolution_ok and self.regression_ok


class ProberSample(BaseModel):
    ts: datetime
    src: str
    dst: str
    result: str  # "ok" | "fail"
    rtt_ms: float | None = None
    pair_id: str | None = None


class SimulationEvent(BaseModel):
    timestamp: datetime = Field(default_factory=utcnow)
    learner_id: str
    scenario_id: str
    session_id: str | None = None
    event_type: str
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Skill(BaseModel):
    id: str
    name: str
    domain: str


class CertificationObjective(BaseModel):
    id: str
    certification: str
    domain: str
    description: str


class SkillWeight(BaseModel):
    id: str
    weight: float


class AssessmentDimension(StrEnum):
    TECHNICAL = "technical_resolution"
    REGRESSION = "regression_safety"
    METHOD = "troubleshooting_method"
    INCIDENT = "incident_management"
    COMMUNICATION = "communication"
    POSTMORTEM = "postmortem"


class Assessment(BaseModel):
    session_id: str
    scores: dict[AssessmentDimension, float]
    overall: float
    strengths: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list)


class Postmortem(BaseModel):
    session_id: str
    incident_id: str
    impact: str
    timeline: str
    root_cause: str
    resolution: str
    contributing_factors: str
    preventative_actions: str
    submitted_at: datetime = Field(default_factory=utcnow)


# ----------------------------------------------------------------------------
# Career progression abstractions (spec §26) — data only for now
# ----------------------------------------------------------------------------


class CareerLevel(BaseModel):
    id: str
    name: str
    order: int


class Responsibility(BaseModel):
    id: str
    name: str
    level_id: str


class Permission(BaseModel):
    id: str
    name: str


class ScenarioRequirement(BaseModel):
    scenario_id: str
    min_overall_score: float


class SkillRequirement(BaseModel):
    skill_id: str
    min_score: float


__all__ = [n for n in dir() if n[0].isupper() or n in {"utcnow", "transition"}]
