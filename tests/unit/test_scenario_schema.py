from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from app.labs.frrconfig import config_lines_diff
from app.models.domain import AssertionCategory
from app.scenarios import ScenarioPublicView, load_scenario
from app.scenarios.schema import ScenarioDefinition


def test_loads_and_has_three_distinct_categories(scenario):
    cats = {a.category for a in scenario.verification}
    assert cats == {AssertionCategory.RESOLUTION, AssertionCategory.REGRESSION, AssertionCategory.QUALITY}
    assert {a.id for a in scenario.by_category(AssertionCategory.RESOLUTION)} == {"austin-to-app", "hq-has-austin-prefix"}


def test_resolution_route_assertion_targets_hq_side(scenario):
    """Spec correction #2: the missing prefix is Austin's, on HQ-RTR1."""
    route = next(a for a in scenario.by_category(AssertionCategory.RESOLUTION) if a.check == "route_exists")
    assert route.params["device"] == "HQ-RTR1"
    assert route.params["prefix"] == "10.20.10.0/24"


def test_ospf_neighbor_is_regression_not_resolution(scenario):
    """Spec correction #3: adjacency stays Full; it is the trap, not the fix."""
    nb = next(a for a in scenario.verification if a.check == "ospf_neighbor")
    assert nb.category == AssertionCategory.REGRESSION


def test_node_cap_counts_routers_and_hosts_only(scenario):
    assert len(scenario.topology.countable_nodes) == 6
    with pytest.raises(ValueError, match="cap"):
        load_scenario(scenario.root_dir, max_nodes=5)


def test_public_view_has_no_hidden_field(scenario):
    view = scenario.public()
    assert isinstance(view, ScenarioPublicView)
    assert "hidden" not in ScenarioPublicView.model_fields
    assert not hasattr(view, "hidden")
    dumped = view.model_dump_json()
    for s in scenario.hidden.forbidden_in_character_output:
        assert s.lower() not in dumped.lower(), f"public view leaks {s!r}"


def test_broken_configs_are_healthy_minus_the_fault(scenario):
    """configs/healthy vs configs/broken is the scenario's ground truth (spec §31)."""
    root: Path = scenario.root_dir
    fault = scenario.faults[0]
    for dev in scenario.topology.devices:
        if dev.kind != "router":
            continue
        healthy = (root / "configs/healthy" / dev.node / "frr.conf").read_text()
        broken = (root / "configs/broken" / dev.node / "frr.conf").read_text()
        added, removed = config_lines_diff(healthy, broken)
        if dev.name == fault.device:
            assert added == []
            assert removed == [f"network {fault.target} area {fault.area}"]
        else:
            assert (added, removed) == ([], [])


def test_schema_rejects_scenario_without_regression(scenario):
    raw = yaml.safe_load((scenario.root_dir / "scenario.yaml").read_text())
    raw["verification"] = [a for a in raw["verification"] if a["category"] != "regression"]
    with pytest.raises(ValueError, match="regression"):
        ScenarioDefinition.model_validate(raw)


def test_schema_rejects_unknown_device_reference(scenario):
    raw = yaml.safe_load((scenario.root_dir / "scenario.yaml").read_text())
    raw["faults"][0]["device"] = "DAL-RTR9"
    with pytest.raises(Exception, match="DAL-RTR9"):
        ScenarioDefinition.model_validate(raw)


def test_schema_rejects_unknown_prober_vantage(scenario):
    raw = yaml.safe_load((scenario.root_dir / "scenario.yaml").read_text())
    raw["prober_matrix"][0]["vantage"] = "moon"
    with pytest.raises(ValueError, match="vantage"):
        ScenarioDefinition.model_validate(raw)


def test_decoy_present(scenario):
    assert [d.id for d in scenario.decoys] == ["CHG-8817"]
    assert scenario.decoys[0].device == "HQ-RTR1"
