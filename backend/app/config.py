"""Structured runtime configuration (env-driven, spec §44)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    lab_provider: str = field(default_factory=lambda: _env("LAB_PROVIDER", "mock"))
    state_dir: Path = field(default_factory=lambda: Path(_env("NORTHSTAR_STATE_DIR", ".northstar")))
    scenarios_dir: Path = field(
        default_factory=lambda: Path(_env("NORTHSTAR_SCENARIOS_DIR", "scenarios"))
    )
    max_nodes: int = field(default_factory=lambda: int(_env("NORTHSTAR_MAX_NODES", "6")))
    idle_timeout_minutes: int = field(
        default_factory=lambda: int(_env("NORTHSTAR_IDLE_TIMEOUT_MINUTES", "20"))
    )
    session_timeout_hours: int = field(
        default_factory=lambda: int(_env("NORTHSTAR_SESSION_TIMEOUT_HOURS", "4"))
    )
    exec_timeout_seconds: float = field(
        default_factory=lambda: float(_env("NORTHSTAR_EXEC_TIMEOUT_SECONDS", "15"))
    )
    exec_max_output_bytes: int = field(
        default_factory=lambda: int(_env("NORTHSTAR_EXEC_MAX_OUTPUT_BYTES", "262144"))
    )
    prober_interval_seconds: float = field(
        default_factory=lambda: float(_env("NORTHSTAR_PROBER_INTERVAL_SECONDS", "1.0"))
    )
    containerlab_bin: str = field(default_factory=lambda: _env("CONTAINERLAB_BIN", "containerlab"))
    docker_bin: str = field(default_factory=lambda: _env("DOCKER_BIN", "docker"))

    def resolved(self, root: Path) -> Settings:
        """Return a copy with relative paths resolved against *root*."""
        return Settings(
            lab_provider=self.lab_provider,
            state_dir=(root / self.state_dir).resolve()
            if not self.state_dir.is_absolute()
            else self.state_dir,
            scenarios_dir=(root / self.scenarios_dir).resolve()
            if not self.scenarios_dir.is_absolute()
            else self.scenarios_dir,
            max_nodes=self.max_nodes,
            idle_timeout_minutes=self.idle_timeout_minutes,
            session_timeout_hours=self.session_timeout_hours,
            exec_timeout_seconds=self.exec_timeout_seconds,
            exec_max_output_bytes=self.exec_max_output_bytes,
            prober_interval_seconds=self.prober_interval_seconds,
            containerlab_bin=self.containerlab_bin,
            docker_bin=self.docker_bin,
        )


def find_repo_root(start: Path | None = None) -> Path:
    """Walk upwards until a directory containing ``scenarios/`` and ``backend/`` is found."""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "scenarios").is_dir() and (candidate / "backend").is_dir():
            return candidate
    return here


def load_settings(root: Path | None = None) -> Settings:
    return Settings().resolved(root or find_repo_root())
