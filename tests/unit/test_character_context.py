"""Structural spoiler defense (spec §35, §40.1): the context builder cannot see the answer."""

from __future__ import annotations

import inspect

import pytest
from app.characters import build_character_context, context_text, validate_character_output
from app.characters import context as context_module
from app.events import EventBus, EventType
from app.scenarios import load_changes, load_characters, load_wiki


@pytest.fixture
def world(scenario):
    return {
        "changes": load_changes(scenario),
        "wiki": load_wiki(scenario),
        "people": load_characters(scenario),
    }


async def _events(scenario):
    bus = EventBus()
    await bus.emit(EventType.SCENARIO_STARTED, learner_id="h", scenario_id=scenario.id, session_id="s", source="system")
    # A device-level event whose metadata contains the answer must be filtered.
    await bus.emit(EventType.CONFIG_CHANGED, learner_id="h", scenario_id=scenario.id, session_id="s", source="terminal",
                   device="AUS-RTR1", command="configure terminal ; router ospf ; network 10.20.10.0/24 area 0")
    await bus.emit(EventType.NETWORK_CONNECTIVITY_LOST, learner_id="h", scenario_id=scenario.id, session_id="s", source="prober",
                   pair="hq->aus-rtr1", src="prober@hq", dst="10.255.0.2", raw_output="network 10.20.10.0/24 area 0")
    return bus.events


@pytest.mark.parametrize("cid", ["maya", "carlos", "priya"])
async def test_context_never_contains_forbidden_strings(scenario, world, cid):
    ctx = build_character_context(world["people"][cid], scenario.public(), events=await _events(scenario), **world)
    text = context_text(ctx).lower()
    for s in scenario.hidden.forbidden_in_character_output:
        assert s.lower() not in text, f"{cid} context leaks {s!r}"
    assert scenario.hidden.root_cause[:30].lower() not in text


def test_builder_signature_only_accepts_public_view():
    sig = inspect.signature(build_character_context)
    assert sig.parameters["scenario"].annotation == "ScenarioPublicView"
    src = inspect.getsource(context_module)
    assert "hidden" not in src.replace('has no ``hidden`` field', "").replace("hidden truth", "").split("def build_character_context")[1]


async def test_carlos_knows_no_topology_or_changes(scenario, world):
    ctx = build_character_context(world["people"]["carlos"], scenario.public(), events=[], **world)
    assert ctx.known_topology == [] and ctx.known_changes == [] and ctx.known_documents == []
    assert any("internet is down" in b for b in ctx.incorrect_beliefs)


async def test_priya_knows_her_changes_but_nothing_links_them_to_the_outage(scenario, world):
    ctx = build_character_context(world["people"]["priya"], scenario.public(), events=[], **world)
    ids = {c["id"] for c in ctx.known_changes}
    assert ids == {"CHG-8815", "CHG-8816", "CHG-8817"}
    text = context_text(ctx).lower()
    for word in ("broke", "caused", "outage", "root cause", "return route"):
        assert word not in text
    assert any("nothing that would affect austin users" in b.lower() for b in ctx.incorrect_beliefs)


async def test_maya_sees_summaries_not_details(scenario, world):
    ctx = build_character_context(world["people"]["maya"], scenario.public(), events=await _events(scenario), **world)
    assert all(set(c) == {"id", "engineer", "window", "summary"} for c in ctx.known_changes)
    types = [e["type"] for e in ctx.recent_events]
    assert "CONFIG_CHANGED" not in types
    assert "NETWORK_CONNECTIVITY_LOST" in types
    assert "raw_output" not in context_text(ctx)


def test_seed_characters_have_no_forbidden_strings(scenario, world):
    for c in world["people"].values():
        text = c.model_dump_json().lower()
        for s in scenario.hidden.forbidden_in_character_output:
            assert s.lower() not in text, f"seed for {c.id} contains {s!r}"


def test_output_validator_catches_leaks(scenario):
    assert validate_character_output("Have you checked the change log from Tuesday?", scenario).ok
    assert not validate_character_output("Try adding network 10.20.10.0/24 area 0 on AUS-RTR1", scenario).ok
    assert not validate_character_output("just do redistribute connected", scenario).ok
    assert not validate_character_output("go into configure terminal and fix it", scenario).ok
