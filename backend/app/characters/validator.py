"""Outgoing character-message validator (spec §35, third layer).

Unlike the context builder, the validator is *allowed* to read the hidden
truth: it is a filter on output, not a source of knowledge for characters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.scenarios.schema import ScenarioDefinition

# Config-line shapes that must never appear in character text, regardless of scenario.
_GENERIC_LEAKS = [
    r"\bnetwork\s+\d+\.\d+\.\d+\.\d+/\d+\s+area\s+\d+",
    r"\bno\s+network\s+\d+\.\d+",
    r"\bredistribute\s+\w+",
    r"\bip\s+route\s+\d+\.\d+\.\d+\.\d+",
    r"\bconfigure\s+terminal\b",
    r"\brouter\s+ospf\b",
]


@dataclass
class ValidationResult:
    ok: bool
    hits: list[str] = field(default_factory=list)


def validate_character_output(text: str, scenario: ScenarioDefinition) -> ValidationResult:
    hits: list[str] = []
    low = text.lower()
    for s in scenario.hidden.forbidden_in_character_output:
        if s.lower() in low:
            hits.append(s)
    for pat in _GENERIC_LEAKS:
        if re.search(pat, text, re.IGNORECASE):
            hits.append(pat)
    return ValidationResult(ok=not hits, hits=hits)
