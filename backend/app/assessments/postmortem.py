"""Postmortem grading (spec §36). Technical claims are checked against ground
truth and the event log; only prose quality is left to an optional LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.labs.frrconfig import config_lines_diff
from app.models.domain import Postmortem, SimulationEvent
from app.scenarios import ScenarioDefinition

TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


@dataclass
class PostmortemGrade:
    score: float
    parts: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _words(t: str) -> int:
    return len(t.split())


def grade_postmortem(
    pm: Postmortem,
    scenario: ScenarioDefinition,
    events: list[SimulationEvent],
    baseline_configs: dict[str, str],
    final_configs: dict[str, str],
    prober_outage_pairs: list[str],
) -> PostmortemGrade:
    g = PostmortemGrade(score=0.0)
    fault = scenario.faults[0]
    truth = scenario.hidden

    # --- root cause (40): device, mechanism, prefix
    rc = pm.root_cause.lower()
    device_hit = fault.device.lower() in rc or "austin router" in rc or "branch router" in rc
    mechanism_hit = any(w in rc for w in ("network statement", "network command", "advertis", "not advertised", "no longer advertised", "ospf network", "removed from ospf", "missing from ospf", "not in ospf"))
    prefix_hit = any(w in rc for w in (fault.target, fault.target.split("/")[0], "austin subnet", "austin user subnet", "user subnet", "10.20.10"))
    hits = sum([device_hit, mechanism_hit, prefix_hit])
    g.parts["root_cause"] = round(40 * hits / 3, 1)
    if hits < 3:
        missing = [n for n, ok in (("device", device_hit), ("mechanism", mechanism_hit), ("prefix", prefix_hit)) if not ok]
        symptom_only = any(w in rc for w in ("unreachable", "could not reach", "couldn't reach", "down", "no connectivity")) and not mechanism_hit
        g.notes.append(
            ("Root cause described the symptom but not the cause. " if symptom_only else "Root cause was incomplete. ")
            + f"Missing: {', '.join(missing)}. Ground truth: {truth.root_cause.strip()}"
        )

    # --- impact (15)
    im = pm.impact.lower()
    site = "austin" in im
    what = any(w in im for w in ("internal", "application", "app", "service", "portal", "users"))
    scope_ok = not any(w in im for w in ("internet", "external")) or "not the internet" in im or "internet was fine" in im or "only internal" in im
    g.parts["impact"] = round(15 * (site + what + scope_ok) / 3, 1)
    if not scope_ok:
        g.notes.append("Impact repeats Carlos's framing ('internet down'); only internal services were affected.")

    # --- timeline (15): times mentioned should match real events (±5 min)
    key_events = [e for e in events if e.event_type in ("SCENARIO_STARTED", "CONFIG_CHANGED", "VERIFICATION_PASSED", "NETWORK_CONNECTIVITY_RESTORED", "INCIDENT_UPDATED")]
    mentioned = [(int(h), int(m)) for h, m in TIME_RE.findall(pm.timeline)]
    matched = 0
    for h, m in mentioned:
        for e in key_events:
            for candidate in (e.timestamp, e.timestamp.astimezone()):
                ev = candidate.hour * 60 + candidate.minute
                if abs(ev - (h * 60 + m)) <= 5:
                    matched += 1
                    break
            else:
                continue
            break
    if not mentioned:
        g.parts["timeline"] = 5.0 if _words(pm.timeline) >= 15 else 0.0
        g.notes.append("Timeline has no timestamps; the event log has them.")
    else:
        g.parts["timeline"] = round(15 * min(1.0, matched / min(len(mentioned), 3)), 1)
        if matched < len(mentioned):
            g.notes.append(f"{len(mentioned) - matched} of {len(mentioned)} timeline timestamps do not match the event log.")

    # --- resolution (15): should describe what actually changed on the device
    added: list[str] = []
    for dev, final in final_configs.items():
        a, _ = config_lines_diff(baseline_configs.get(dev, ""), final)
        added += a
    res = pm.resolution.lower()
    describes_fix = any(w in res for w in ("network statement", "network 10.20", "network command", "re-added", "readded", "added the", "added back", "restored the", "advertis"))
    if any("redistribute" in ln for ln in added) or any(ln.startswith("ip route") for ln in added):
        describes_fix = describes_fix or "redistribute" in res or "static route" in res
    g.parts["resolution"] = 15.0 if describes_fix else (5.0 if _words(pm.resolution) >= 10 else 0.0)
    if not describes_fix:
        g.notes.append("Resolution does not say what configuration was changed. Final config diff: " + ("; ".join(added) if added else "none"))

    # --- contributing factors / prevention (7.5 each): prose quality only
    g.parts["contributing_factors"] = 7.5 if _words(pm.contributing_factors) >= 15 else round(7.5 * min(1.0, _words(pm.contributing_factors) / 15), 1)
    g.parts["preventative_actions"] = 7.5 if _words(pm.preventative_actions) >= 15 else round(7.5 * min(1.0, _words(pm.preventative_actions) / 15), 1)
    if _words(pm.preventative_actions) < 15:
        g.notes.append("Preventative actions are thin. What would have caught a removed advertisement before users did?")

    g.score = round(sum(g.parts.values()), 1)
    return g


def event_at(events: list[SimulationEvent], event_type: str, **match: object) -> datetime | None:
    for e in events:
        if e.event_type == event_type and all(e.metadata.get(k) == v for k, v in match.items()):
            return e.timestamp
    return None


def within(a: datetime | None, b: datetime | None, minutes: int) -> bool:
    return a is not None and b is not None and abs(a - b) <= timedelta(minutes=minutes)
