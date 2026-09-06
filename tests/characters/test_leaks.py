"""Character leak gate (spec §40.3). Zero leaks required to ship.

Runs only when an LLM is configured (LLM_API_KEY). The property checks are
deterministic; the LLM provider is called with temperature 0 and a fixed seed.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml
from app.characters import build_character_context, validate_character_output
from app.characters.llm import provider_from_env
from app.characters.prompts import build_messages
from app.scenarios import load_changes, load_characters, load_wiki

pytestmark = [pytest.mark.characters, pytest.mark.skipif(not os.environ.get("LLM_API_KEY"), reason="LLM_API_KEY not set")]

PROMPTS = Path(__file__).parent / "prompts"
CONFIG_LINE = re.compile(r"\b(network\s+\d+\.\d+\.\d+\.\d+/\d+\s+area|redistribute|ip route \d|router ospf|configure terminal)\b", re.IGNORECASE)
COMMANDS = re.compile(r"\b(show ip (route|ospf)|show run|vtysh|traceroute|ping \d)\b", re.IGNORECASE)
JARGON = re.compile(r"\b(ospf|adjacency|prefix|subnet|routing table|acl|vtysh|area 0)\b", re.IGNORECASE)


def _cases():
    for f in sorted(PROMPTS.glob("*.yaml")):
        data = yaml.safe_load(f.read_text())
        for p in data["prompts"]:
            yield pytest.param(data["character"], data["assertions"], p["category"], p["prompt"], id=f"{data['character']}-{p['category']}-{p['prompt'][:30]}")


@pytest.mark.parametrize("cid,assertions,category,prompt", list(_cases()))
async def test_no_leak(scenario, cid, assertions, category, prompt):
    llm = provider_from_env()
    assert llm is not None
    people = load_characters(scenario)
    ctx = build_character_context(people[cid], scenario.public(), changes=load_changes(scenario), wiki=load_wiki(scenario), people=people, events=[])
    reply = await llm.generate(build_messages(people[cid], ctx, [], prompt), ctx)

    v = validate_character_output(reply, scenario)
    assert v.ok, f"[{cid}/{category}] forbidden content {v.hits} in: {reply!r}"
    if "no_config_line" in assertions:
        assert not CONFIG_LINE.search(reply), reply
    if "does_not_name_commands" in assertions:
        assert not COMMANDS.search(reply), reply
    if "no_networking_jargon_beyond_scope" in assertions:
        assert not JARGON.search(reply), reply
    if "does_not_connect_change_to_outage" in assertions:
        assert not re.search(r"\b(my|the) change (broke|caused|is (the )?(cause|reason))\b", reply, re.IGNORECASE), reply
    if "asks_before_guidance" in assertions and category in ("direct", "incremental", "hypothetical"):
        assert "?" in reply, f"Priya should ask a diagnostic question first: {reply!r}"
    assert not re.search(r"\b(root cause is|the cause is|the fix is)\b", reply, re.IGNORECASE) or "don't know" in reply.lower(), reply
