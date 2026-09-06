"""Session persistence for Phase 1 — a JSON file per session under the state dir.

The interface (``SessionStore``) is what Phase 2 replaces with SQLAlchemy.
Anything that must survive a backend restart (which labs are ours, which
workdir they live in, baseline configs) goes through here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from app.models.domain import ScenarioSession


class SessionStore(Protocol):
    def save(self, session: ScenarioSession) -> None: ...
    def get(self, session_id: str) -> ScenarioSession | None: ...
    def list(self) -> list[ScenarioSession]: ...
    def delete(self, session_id: str) -> None: ...


class JsonSessionStore:
    def __init__(self, state_dir: Path) -> None:
        self.dir = state_dir / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.dir / f"{session_id}.json"

    def save(self, session: ScenarioSession) -> None:
        tmp = self._path(session.id).with_suffix(".tmp")
        tmp.write_text(session.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self._path(session.id))

    def get(self, session_id: str) -> ScenarioSession | None:
        p = self._path(session_id)
        if not p.exists():
            return None
        return ScenarioSession.model_validate_json(p.read_text(encoding="utf-8"))

    def list(self) -> list[ScenarioSession]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                out.append(ScenarioSession.model_validate_json(p.read_text(encoding="utf-8")))
            except (ValueError, json.JSONDecodeError):
                continue
        return out

    def delete(self, session_id: str) -> None:
        p = self._path(session_id)
        if p.exists():
            p.unlink()


class InMemorySessionStore:
    def __init__(self) -> None:
        self._data: dict[str, ScenarioSession] = {}

    def save(self, session: ScenarioSession) -> None:
        self._data[session.id] = session.model_copy(deep=True)

    def get(self, session_id: str) -> ScenarioSession | None:
        s = self._data.get(session_id)
        return s.model_copy(deep=True) if s else None

    def list(self) -> list[ScenarioSession]:
        return [s.model_copy(deep=True) for s in self._data.values()]

    def delete(self, session_id: str) -> None:
        self._data.pop(session_id, None)
