import pytest
from app.models.domain import IllegalTransition, ScenarioState, transition


def test_happy_path():
    s = ScenarioState.NOT_STARTED
    for nxt in (
        ScenarioState.PROVISIONING,
        ScenarioState.ACTIVE,
        ScenarioState.INVESTIGATING,
        ScenarioState.MITIGATED,
        ScenarioState.RESOLVED,
        ScenarioState.POSTMORTEM,
        ScenarioState.COMPLETED,
        ScenarioState.DESTROYED,
    ):
        s = transition(s, nxt)
    assert s == ScenarioState.DESTROYED


def test_cannot_skip_provisioning():
    with pytest.raises(IllegalTransition):
        transition(ScenarioState.NOT_STARTED, ScenarioState.ACTIVE)


def test_resolved_can_regress_to_investigating():
    assert transition(ScenarioState.RESOLVED, ScenarioState.INVESTIGATING) == ScenarioState.INVESTIGATING


def test_destroyed_is_terminal():
    with pytest.raises(IllegalTransition):
        transition(ScenarioState.DESTROYED, ScenarioState.ACTIVE)
