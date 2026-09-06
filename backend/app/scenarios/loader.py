"""Load scenario directories into ScenarioDefinition objects."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.models.domain import ChangeLogEntry, Character, WikiPage

from .schema import ScenarioDefinition


class ScenarioNotFound(Exception):
    pass


def load_scenario(path: Path, *, max_nodes: int | None = None) -> ScenarioDefinition:
    """``path`` is the scenario directory (or its scenario.yaml)."""
    if path.is_file():
        path = path.parent
    yaml_path = path / "scenario.yaml"
    if not yaml_path.exists():
        raise ScenarioNotFound(f"no scenario.yaml under {path}")
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    scenario = ScenarioDefinition.model_validate(raw)
    scenario.root_dir = path
    if not scenario.template_path.exists():
        raise ScenarioNotFound(f"topology template missing: {scenario.template_path}")
    if not scenario.configs_path.is_dir():
        raise ScenarioNotFound(f"configs dir missing: {scenario.configs_path}")
    if max_nodes is not None and len(scenario.topology.countable_nodes) > max_nodes:
        raise ValueError(
            f"scenario {scenario.id} has {len(scenario.topology.countable_nodes)} nodes, cap is {max_nodes}"
        )
    return scenario


def find_scenario(scenarios_dir: Path, scenario_id: str, **kw: object) -> ScenarioDefinition:
    wanted = scenario_id.lower()
    for candidate in sorted(scenarios_dir.iterdir()):
        if not candidate.is_dir() or not (candidate / "scenario.yaml").exists():
            continue
        if candidate.name.lower() == wanted:
            return load_scenario(candidate, **kw)  # type: ignore[arg-type]
        sc = load_scenario(candidate, **kw)  # type: ignore[arg-type]
        if sc.id.lower() == wanted:
            return sc
    raise ScenarioNotFound(f"scenario {scenario_id!r} not found under {scenarios_dir}")


def load_wiki(scenario: ScenarioDefinition) -> list[WikiPage]:
    assert scenario.root_dir is not None
    pages: list[WikiPage] = []
    for md in sorted((scenario.root_dir / "wiki").glob("*.md")):
        text = md.read_text(encoding="utf-8")
        meta: dict[str, str] = {}
        body = text
        if text.startswith("---"):
            _, fm, body = text.split("---", 2)
            meta = yaml.safe_load(fm) or {}
        pages.append(
            WikiPage(
                id=str(meta.get("id", md.stem)),
                title=str(meta.get("title", md.stem)),
                body=body.strip(),
                owner=meta.get("owner"),
                last_reviewed=str(meta.get("last_reviewed")) if meta.get("last_reviewed") else None,
            )
        )
    return pages


def load_changes(scenario: ScenarioDefinition) -> list[ChangeLogEntry]:
    assert scenario.root_dir is not None
    p = scenario.root_dir / "seed" / "changes.yaml"
    if not p.exists():
        return []
    return [ChangeLogEntry.model_validate(e) for e in yaml.safe_load(p.read_text(encoding="utf-8"))]


def load_characters(scenario: ScenarioDefinition) -> dict[str, Character]:
    assert scenario.root_dir is not None
    p = scenario.root_dir / "seed" / "characters.yaml"
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    return {cid: Character.model_validate({"id": cid, **body}) for cid, body in raw.items()}
