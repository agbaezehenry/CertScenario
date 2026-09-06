"""Declarative scenario schema (spec §10).

Two models are exposed on purpose:

* ``ScenarioDefinition`` — the full file, including ``hidden``.
* ``ScenarioPublicView`` — everything a character may ever see. It has no
  ``hidden`` attribute. The character context builder accepts only this type.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.domain import AssertionCategory, Decoy, Device, DeviceKind, Fault, SkillWeight


class RoleSpec(BaseModel):
    title: str
    team: str
    manager: str


class IncidentSpec(BaseModel):
    severity: str
    reported_by: str
    reported_at: str
    affected: str
    description: str


class VantageSpec(BaseModel):
    interface: str
    address: str


class ProberSpec(BaseModel):
    node: str
    vantages: dict[str, VantageSpec]


class TopologySpec(BaseModel):
    template: str
    configs: str = "configs/healthy"
    devices: list[Device]
    prober: ProberSpec | None = None

    def device(self, name: str) -> Device:
        for d in self.devices:
            if d.name.upper() == name.upper() or d.node == name.lower():
                return d
        raise KeyError(f"unknown device {name!r}")

    @property
    def countable_nodes(self) -> list[Device]:
        """Nodes counted against the per-topology cap (spec §7.3).

        Switches and infra (prober) are plumbing, not scenario devices, and are
        excluded, matching the spec's "six nodes plus the switch".
        """
        return [d for d in self.devices if d.kind in (DeviceKind.ROUTER, DeviceKind.HOST)]


class AssertionSpec(BaseModel):
    id: str
    check: str
    category: AssertionCategory
    on_fail: Literal["partial_credit", "fail"] | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _collect_params(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        known = {"id", "check", "category", "on_fail", "params"}
        params = dict(data.get("params", {}))
        for k, v in data.items():
            if k not in known:
                params[k] = v
        return {**{k: v for k, v in data.items() if k in known}, "params": params}


class ProberPairSpec(BaseModel):
    id: str
    vantage: str
    destination: str
    label: str = ""


class MonitoringTileSpec(BaseModel):
    id: str
    label: str
    source: Literal["prober", "prober_latency", "static"]
    pair: str | None = None
    value: str | None = None


class MinimalFixSpec(BaseModel):
    device: str
    lines: list[str]


class SledgehammerSpec(BaseModel):
    pattern: str
    score: Literal["partial", "none"] = "partial"


class HiddenTruth(BaseModel):
    """Ground truth. Read by the verifier/grader only. Never by characters."""

    root_cause: str
    minimal_fix: MinimalFixSpec
    minimum_config_lines_changed: int = 1
    forbidden_in_character_output: list[str] = Field(default_factory=list)
    sledgehammers: list[SledgehammerSpec] = Field(default_factory=list)


class ScenarioPublicView(BaseModel):
    """Everything about a scenario that is safe for any character to know.

    Deliberately constructed field-by-field in ``ScenarioDefinition.public()``
    so that adding a field to the definition never leaks by default.
    """

    id: str
    title: str
    difficulty: str
    role: RoleSpec
    incident: IncidentSpec
    devices: list[Device]
    decoys: list[Decoy]
    characters: list[str]
    objectives: list[str]


class ScenarioDefinition(BaseModel):
    id: str
    title: str
    difficulty: str
    role: RoleSpec
    incident: IncidentSpec
    topology: TopologySpec
    faults: list[Fault]
    decoys: list[Decoy] = Field(default_factory=list)
    characters: list[str]
    objectives: list[str] = Field(default_factory=list)
    skills: list[SkillWeight] = Field(default_factory=list)
    verification: list[AssertionSpec]
    prober_matrix: list[ProberPairSpec] = Field(default_factory=list)
    monitoring: list[MonitoringTileSpec] = Field(default_factory=list)
    hidden: HiddenTruth

    # populated by the loader
    root_dir: Path | None = Field(default=None, exclude=True)

    @field_validator("verification")
    @classmethod
    def _needs_resolution_and_regression(cls, v: list[AssertionSpec]) -> list[AssertionSpec]:
        cats = {a.category for a in v}
        if AssertionCategory.RESOLUTION not in cats:
            raise ValueError("scenario needs at least one resolution assertion")
        if AssertionCategory.REGRESSION not in cats:
            raise ValueError("scenario needs at least one regression assertion")
        ids = [a.id for a in v]
        if len(ids) != len(set(ids)):
            raise ValueError("assertion ids must be unique")
        return v

    @model_validator(mode="after")
    def _prober_refs(self) -> ScenarioDefinition:
        if self.prober_matrix:
            if self.topology.prober is None:
                raise ValueError("prober_matrix given but topology.prober is missing")
            for pair in self.prober_matrix:
                if pair.vantage not in self.topology.prober.vantages:
                    raise ValueError(f"prober pair {pair.id} uses unknown vantage {pair.vantage}")
        pair_ids = {p.id for p in self.prober_matrix}
        for tile in self.monitoring:
            if tile.source != "static" and tile.pair not in pair_ids:
                raise ValueError(f"monitoring tile {tile.id} references unknown pair {tile.pair}")
        for f in self.faults:
            self.topology.device(f.device)
        for a in self.verification:
            for key in ("device", "from"):
                if key in a.params:
                    self.topology.device(a.params[key])
        return self

    def by_category(self, category: AssertionCategory) -> list[AssertionSpec]:
        return [a for a in self.verification if a.category == category]

    def public(self) -> ScenarioPublicView:
        return ScenarioPublicView(
            id=self.id,
            title=self.title,
            difficulty=self.difficulty,
            role=self.role,
            incident=self.incident,
            devices=list(self.topology.devices),
            decoys=list(self.decoys),
            characters=list(self.characters),
            objectives=list(self.objectives),
        )

    @property
    def template_path(self) -> Path:
        assert self.root_dir is not None
        return self.root_dir / self.topology.template

    @property
    def configs_path(self) -> Path:
        assert self.root_dir is not None
        return self.root_dir / self.topology.configs
