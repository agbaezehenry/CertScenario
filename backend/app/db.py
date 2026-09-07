"""SQLAlchemy persistence (spec §6). SQLite by default, PostgreSQL via DATABASE_URL.

Runtime entities live here as rows; the Pydantic domain models remain the
in-process contract. ``SqlSessionStore`` satisfies ``labs.store.SessionStore``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.models.domain import ScenarioSession, ScenarioState, SimulationEvent


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    employee_id: Mapped[str] = mapped_column(String(64))


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(64), index=True)
    learner_id: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    lab_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    container_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    workdir: Mapped[str | None] = mapped_column(String(512), nullable=True)
    baseline_configs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    end_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


class IncidentRow(Base):
    __tablename__ = "incidents"
    key: Mapped[str] = mapped_column(String(160), primary_key=True)  # "<session>:<incident id>"
    id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    reported_at: Mapped[str] = mapped_column(String(32))
    reporter_id: Mapped[str] = mapped_column(String(64))
    affected: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    dri_id: Mapped[str] = mapped_column(String(64))
    notes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationRow(Base):
    __tablename__ = "conversations"
    key: Mapped[str] = mapped_column(String(160), primary_key=True)  # "<session>:<conv id>"
    id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(128))
    participant_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


class MessageRow(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    sender_id: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EventRow(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    learner_id: Mapped[str] = mapped_column(String(64))
    scenario_id: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PostmortemRow(Base):
    __tablename__ = "postmortems"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(64))
    impact: Mapped[str] = mapped_column(Text)
    timeline: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str] = mapped_column(Text)
    resolution: Mapped[str] = mapped_column(Text)
    contributing_factors: Mapped[str] = mapped_column(Text)
    preventative_actions: Mapped[str] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssessmentRow(Base):
    __tablename__ = "assessments"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scores: Mapped[dict[str, Any]] = mapped_column(JSON)
    overall: Mapped[float] = mapped_column(Float)
    strengths: Mapped[list[str]] = mapped_column(JSON, default=list)
    improvements: Mapped[list[str]] = mapped_column(JSON, default=list)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Database:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get("DATABASE_URL", "sqlite:///./.northstar/northstar.db")
        connect_args = {"check_same_thread": False} if self.url.startswith("sqlite") else {}
        if self.url.startswith("sqlite:///") and not self.url.startswith("sqlite:///:memory:"):
            from pathlib import Path

            Path(self.url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {"connect_args": connect_args, "future": True}
        if self.url in ("sqlite://", "sqlite:///:memory:"):
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool  # one shared in-memory DB across connections
        self.engine = create_engine(self.url, **kwargs)
        self._factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        """Add columns that exist on the models but not in an older SQLite file.

        Good enough for the MVP's single-developer databases; use Alembic once
        there is a production schema to protect.
        """
        from sqlalchemy import inspect, text

        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if not insp.has_table(table.name):
                    continue
                existing = {c["name"] for c in insp.get_columns(table.name)}
                for col in table.columns:
                    if col.name not in existing:
                        ctype = col.type.compile(dialect=self.engine.dialect)
                        conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ctype}'))

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ------------------------------------------------------------ event sink
    def event_sink(self, ev: SimulationEvent) -> None:
        with self.session() as s:
            s.add(
                EventRow(
                    session_id=ev.session_id,
                    timestamp=ev.timestamp,
                    learner_id=ev.learner_id,
                    scenario_id=ev.scenario_id,
                    event_type=ev.event_type,
                    source=ev.source,
                    meta=_jsonable(ev.metadata),
                )
            )

    def events(self, session_id: str) -> list[SimulationEvent]:
        with self.session() as s:
            rows = s.execute(select(EventRow).where(EventRow.session_id == session_id).order_by(EventRow.id)).scalars().all()
            return [
                SimulationEvent(
                    timestamp=_aware(r.timestamp),
                    learner_id=r.learner_id,
                    scenario_id=r.scenario_id,
                    session_id=r.session_id,
                    event_type=r.event_type,
                    source=r.source,
                    metadata=dict(r.meta or {}),
                )
                for r in rows
            ]


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    import json

    return json.loads(json.dumps(d, default=str))


def _aware(dt: datetime) -> datetime:

    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class SqlSessionStore:
    """labs.store.SessionStore backed by the sessions table."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _to_row(m: ScenarioSession) -> SessionRow:
        return SessionRow(
            id=m.id,
            scenario_id=m.scenario_id,
            learner_id=m.learner_id,
            state=m.state.value,
            lab_id=m.lab_id,
            started_at=m.started_at,
            last_activity_at=m.last_activity_at,
            ended_at=m.ended_at,
            container_minutes=m.container_minutes,
            workdir=m.workdir,
            baseline_configs=dict(m.baseline_configs),
            end_reason=m.end_reason,
        )

    @staticmethod
    def _to_model(r: SessionRow) -> ScenarioSession:
        return ScenarioSession(
            id=r.id,
            scenario_id=r.scenario_id,
            learner_id=r.learner_id,
            state=ScenarioState(r.state),
            lab_id=r.lab_id,
            started_at=_aware(r.started_at),
            last_activity_at=_aware(r.last_activity_at),
            ended_at=_aware(r.ended_at) if r.ended_at else None,
            container_minutes=r.container_minutes or 0.0,
            workdir=r.workdir,
            baseline_configs=dict(r.baseline_configs or {}),
            end_reason=r.end_reason,
        )

    def save(self, session: ScenarioSession) -> None:
        with self.db.session() as s:
            s.merge(self._to_row(session))

    def get(self, session_id: str) -> ScenarioSession | None:
        with self.db.session() as s:
            r = s.get(SessionRow, session_id)
            return self._to_model(r) if r else None

    def list(self) -> list[ScenarioSession]:
        with self.db.session() as s:
            return [self._to_model(r) for r in s.execute(select(SessionRow).order_by(SessionRow.started_at)).scalars()]

    def delete(self, session_id: str) -> None:
        with self.db.session() as s:
            r = s.get(SessionRow, session_id)
            if r:
                s.delete(r)
