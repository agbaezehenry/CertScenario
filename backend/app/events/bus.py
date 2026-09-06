"""In-process event bus + append-only JSONL log.

Phase 1: JSONL per session under the state dir. Phase 2 adds a DB sink;
subscribers (consequence engine, character triggers) attach here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.models.domain import SimulationEvent

from .types import EventType

log = logging.getLogger("northstar.events")

Subscriber = Callable[[SimulationEvent], Awaitable[None] | None]


class EventBus:
    def __init__(self, log_path: Path | None = None) -> None:
        self._subscribers: list[Subscriber] = []
        self._events: list[SimulationEvent] = []
        self._log_path = log_path
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def events(self) -> list[SimulationEvent]:
        return list(self._events)

    def subscribe(self, fn: Subscriber) -> None:
        self._subscribers.append(fn)

    async def emit(
        self,
        event_type: EventType | str,
        *,
        learner_id: str,
        scenario_id: str,
        session_id: str | None,
        source: str,
        **metadata: Any,
    ) -> SimulationEvent:
        ev = SimulationEvent(
            learner_id=learner_id,
            scenario_id=scenario_id,
            session_id=session_id,
            event_type=str(event_type),
            source=source,
            metadata=metadata,
        )
        self._events.append(ev)
        self._write(ev)
        log.info(
            "event %s session=%s source=%s %s",
            ev.event_type,
            session_id,
            source,
            json.dumps(metadata, default=str)[:300],
        )
        for fn in self._subscribers:
            try:
                result = fn(ev)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("event subscriber failed for %s", ev.event_type)
        return ev

    def _write(self, ev: SimulationEvent) -> None:
        if not self._log_path:
            return
        with self._log_path.open("a", encoding="utf-8") as fh:
            fh.write(ev.model_dump_json() + "\n")

    @staticmethod
    def read_log(path: Path) -> list[SimulationEvent]:
        if not path.exists():
            return []
        out: list[SimulationEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(SimulationEvent.model_validate_json(line))
        return out
