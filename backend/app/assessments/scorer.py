"""Scorecard (spec §23–24). Every claim cites an event; nothing is invented.

Dimensions and weights:
    technical_resolution   0.25   verification (resolution) + sledgehammer/line-count penalties
    regression_safety      0.20   regression checks + prober outages the learner caused
    troubleshooting_method 0.20   command telemetry signals
    incident_management    0.15   ack, cadence, escalation, documentation
    communication          0.10   the resolution update (heuristics; LLM optional)
    postmortem             0.10   grade_postmortem
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.assessments.postmortem import grade_postmortem
from app.characters.triggers import is_manager_update
from app.labs.frrconfig import config_lines_diff
from app.models.domain import (
    Assessment,
    AssessmentDimension,
    ChatMessage,
    Incident,
    Postmortem,
    ProberSample,
    SimulationEvent,
    VerificationResult,
)
from app.scenarios import ScenarioDefinition

WEIGHTS = {
    AssessmentDimension.TECHNICAL: 0.25,
    AssessmentDimension.REGRESSION: 0.20,
    AssessmentDimension.METHOD: 0.20,
    AssessmentDimension.INCIDENT: 0.15,
    AssessmentDimension.COMMUNICATION: 0.10,
    AssessmentDimension.POSTMORTEM: 0.10,
}


@dataclass
class AssessmentInputs:
    scenario: ScenarioDefinition
    events: list[SimulationEvent]
    verification: VerificationResult | None
    prober_samples: list[ProberSample]
    incident: Incident | None
    messages: list[ChatMessage]
    postmortem: Postmortem | None
    baseline_configs: dict[str, str]
    final_configs: dict[str, str]
    started_at: datetime
    learner_id: str


@dataclass
class Dimension:
    score: float
    strengths: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


def _t(dt: datetime) -> str:
    return dt.astimezone().strftime("%H:%M")


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, round(x, 1)))


# ---------------------------------------------------------------- dimensions
def score_technical(i: AssessmentInputs) -> Dimension:
    d = Dimension(score=0.0)
    v = i.verification
    if v is None:
        d.improvements.append("No verification was run.")
        return d
    res = [c for c in v.checks if c.category.value == "resolution"]
    passed = sum(1 for c in res if c.passed)
    d.score = 100.0 * passed / max(1, len(res))
    d.detail["resolution_checks"] = {c.id: c.passed for c in res}
    if passed == len(res):
        d.strengths.append(f"All resolution checks passed at {_t(v.run_at)}: " + ", ".join(c.detail for c in res if c.detail))
    quality_fail = [c for c in v.checks if c.category.value == "quality" and not c.passed]
    if quality_fail:
        d.score = min(d.score, 70.0)
        d.improvements.append(
            "Connectivity was restored with a broad change rather than the minimal one: " + "; ".join(c.detail for c in quality_fail) + ". Partial credit."
        )
    # config lines changed vs minimum
    changed = 0
    for dev, final in i.final_configs.items():
        a, r = config_lines_diff(i.baseline_configs.get(dev, ""), final)
        changed += len(a) + len(r)
    minimum = i.scenario.hidden.minimum_config_lines_changed
    d.detail["config_lines_changed"] = changed
    if changed > minimum + 1 and passed == len(res):
        penalty = min(30.0, 5.0 * (changed - minimum))
        d.score -= penalty
        d.improvements.append(f"{changed} configuration lines changed to fix a {minimum}-line problem.")
    elif changed and changed <= minimum + 1 and passed == len(res):
        d.strengths.append(f"Fixed with {changed} configuration line{'s' if changed != 1 else ''} (minimum {minimum}).")
    d.score = _clamp(d.score)
    return d


def score_regression(i: AssessmentInputs) -> Dimension:
    d = Dimension(score=100.0)
    v = i.verification
    if v:
        reg_fail = [c for c in v.checks if c.category.value == "regression" and not c.passed]
        for c in reg_fail:
            d.score -= 40
            d.improvements.append(f"Regression check failed at completion: {c.detail}")
        if not reg_fail:
            d.strengths.append("Every regression check held at completion (OSPF adjacency, HQ connectivity, decoy ACL untouched).")
    # prober: outages that started after the first learner config change
    first_change = next((e.timestamp for e in i.events if e.event_type == "CONFIG_CHANGED"), None)
    caused: dict[str, list[tuple[datetime, datetime | None]]] = {}
    state: dict[str, bool] = {}
    open_since: dict[str, datetime] = {}
    for s in sorted(i.prober_samples, key=lambda x: x.ts):
        pid = s.pair_id or f"{s.src}->{s.dst}"
        ok = s.result == "ok"
        prev = state.get(pid)
        state[pid] = ok
        if prev is True and not ok:
            open_since[pid] = s.ts
        elif prev is False and ok and pid in open_since:
            start = open_since.pop(pid)
            if first_change and start >= first_change:
                caused.setdefault(pid, []).append((start, s.ts))
    for pid, start in open_since.items():
        if first_change and start >= first_change:
            caused.setdefault(pid, []).append((start, None))
    d.detail["caused_outages"] = {k: [(a.isoformat(), b.isoformat() if b else None) for a, b in v_] for k, v_ in caused.items()}
    for pid, spans in caused.items():
        if pid == "austin->app":
            continue  # the incident itself flapping during the fix is expected
        total = sum(((b or a) - a).total_seconds() for a, b in spans)
        d.score -= min(30.0, 15.0 + total / 10)
        d.improvements.append(f"Prober saw {pid} lose connectivity for {int(total)}s after a configuration change at {_t(spans[0][0])} (NETWORK_CONNECTIVITY_LOST).")
    if not any(p != "austin->app" for p in caused) and i.prober_samples:
        d.strengths.append("No unrelated path lost connectivity at any point during the session (continuous prober).")
    d.score = _clamp(d.score)
    return d


def score_method(i: AssessmentInputs) -> Dimension:
    d = Dimension(score=0.0)
    cmds = [e for e in i.events if e.event_type == "COMMAND_EXECUTED"]
    first_change = next((e for e in i.events if e.event_type == "CONFIG_CHANGED"), None)
    reads_before = [e for e in cmds if e.metadata.get("read_only") and (first_change is None or e.timestamp < first_change.timestamp)]
    d.detail["read_commands_before_first_change"] = len(reads_before)
    # 1. evidence before change (25)
    if len(reads_before) >= 4:
        d.score += 25
        span = (first_change.timestamp - reads_before[0].timestamp) if first_change else timedelta(0)
        d.strengths.append(f"Ran {len(reads_before)} read commands over {int(span.total_seconds() // 60)} min before the first configuration change.")
    elif reads_before:
        d.score += 25 * len(reads_before) / 4
        d.improvements.append(f"Only {len(reads_before)} read command(s) before the first configuration change.")
    elif first_change:
        d.improvements.append(f"First configuration change at {_t(first_change.timestamp)} came before any read command.")
    # 2. looked at the far side (25)
    hq_cmds = [e for e in cmds if e.metadata.get("device") == "HQ-RTR1"]
    hq_route = [e for e in hq_cmds if "route" in str(e.metadata.get("command", "")).lower()]
    if hq_route and (first_change is None or hq_route[0].timestamp < first_change.timestamp):
        d.score += 25
        d.strengths.append(f"Checked the routing table on HQ-RTR1 at {_t(hq_route[0].timestamp)}, before changing anything (the far side of the path).")
    elif hq_cmds:
        d.score += 10
        d.improvements.append("Connected to HQ-RTR1 but did not inspect its routing table before making changes.")
    else:
        d.improvements.append("Never looked at HQ-RTR1. The missing route was on the far side of the WAN link.")
    d.detail["time_to_first_hq_command_s"] = (hq_cmds[0].timestamp - i.started_at).total_seconds() if hq_cmds else None
    # 3. did not stop at neighbor check (20)
    nbr = [e for e in cmds if "neighbor" in str(e.metadata.get("command", "")).lower()]
    routes_after_nbr = [e for e in cmds if "route" in str(e.metadata.get("command", "")).lower() and nbr and e.timestamp > nbr[0].timestamp]
    if nbr and routes_after_nbr:
        d.score += 20
        d.strengths.append(f"Did not stop at 'show ip ospf neighbor' ({_t(nbr[0].timestamp)}); went on to inspect routes.")
    elif nbr:
        d.improvements.append("Checked OSPF neighbors but never followed up with the routing tables; a Full adjacency says nothing about which prefixes are exchanged.")
    elif routes_after_nbr or any("route" in str(e.metadata.get("command", "")).lower() for e in cmds):
        d.score += 15
    # 4. verified after remediation (20)
    last_change = next((e for e in reversed(i.events) if e.event_type == "CONFIG_CHANGED"), None)
    verified = [e for e in i.events if e.event_type in ("VERIFICATION_RUN",) and last_change and e.timestamp > last_change.timestamp]
    ping_after = [e for e in cmds if str(e.metadata.get("command", "")).startswith("ping") and last_change and e.timestamp > last_change.timestamp]
    if verified or ping_after:
        d.score += 20
        d.strengths.append(f"Verified connectivity after the last change ({'verification run' if verified else 'ping'} at {_t((verified or ping_after)[0].timestamp)}).")
    elif last_change:
        d.improvements.append("No verification after the last configuration change.")
    # 5. decoy chase (10): time on CHG-8817 / HQ ACL
    decoy = [e for e in i.events if e.event_type == "CHANGE_LOG_VIEWED" and str(e.metadata.get("change", "")).upper() == "CHG-8817"]
    acl_cmds = [e for e in cmds if "access-list" in str(e.metadata.get("command", "")).lower()]
    if acl_cmds:
        d.improvements.append(f"Spent time on the HQ-RTR1 access-list (CHG-8817) at {_t(acl_cmds[0].timestamp)}; it was unrelated. Two changes that night, only one touched routing.")
    else:
        d.score += 10
        if decoy:
            d.strengths.append("Read CHG-8817 but did not act on it; correctly correlated the change log with the symptom.")
    d.score = _clamp(d.score)
    return d


def score_incident(i: AssessmentInputs) -> Dimension:
    d = Dimension(score=0.0)
    inc = i.incident
    ack = next((e for e in i.events if e.event_type == "INCIDENT_UPDATED" and e.metadata.get("acknowledged")), None)
    if ack:
        d.score += 25
        d.strengths.append(f"Acknowledged INC-1042 at {_t(ack.timestamp)} ({int((ack.timestamp - i.started_at).total_seconds() // 60)} min after assignment).")
    else:
        d.improvements.append("Incident status was never moved off 'Assigned'.")
    updates = [e for e in i.events if is_manager_update(e)]
    end = next((e.timestamp for e in i.events if e.event_type == "VERIFICATION_PASSED"), i.events[-1].timestamp if i.events else i.started_at)
    windows = max(1, int((end - i.started_at).total_seconds() // 1800) + 1)
    covered = {int((u.timestamp - i.started_at).total_seconds() // 1800) for u in updates if u.timestamp <= end}
    cadence = len(covered & set(range(windows))) / windows
    d.score += 35 * cadence
    d.detail["cadence_windows"] = windows
    d.detail["cadence_covered"] = sorted(covered)
    if updates:
        first = updates[0].timestamp
        minutes = int((first - i.started_at).total_seconds() // 60)
        if minutes <= 30:
            d.strengths.append(f"First manager update at {_t(first)}, {minutes} min in, inside the SEV-2 cadence.")
        else:
            d.improvements.append(f"First manager update came at {_t(first)}, {minutes} min in, past the SEV-2 30-minute cadence.")
        if cadence < 1:
            d.improvements.append(f"Status updates covered {len(covered & set(range(windows)))} of {windows} 30-minute windows.")
    else:
        d.improvements.append("No status updates reached Maya (chat or ticket) during the incident.")
    if inc and inc.resolution:
        d.score += 25
        d.strengths.append("Resolution documented in the ticket.")
    else:
        d.improvements.append("Ticket has no resolution recorded.")
    if inc and inc.status.lower() == "resolved":
        d.score += 15
    d.score = _clamp(d.score)
    return d


def score_communication(i: AssessmentInputs) -> Dimension:
    d = Dimension(score=0.0)
    candidates = [m for m in i.messages if m.sender_id == i.learner_id and m.conversation_id in ("dm-maya", "network-ops")]
    resolution_msgs = [m for m in candidates if any(w in m.body.lower() for w in ("resolved", "fixed", "restored", "back up", "working again"))]
    text = (resolution_msgs[-1].body if resolution_msgs else (i.incident.resolution if i.incident and i.incident.resolution else "")) or ""
    if not text:
        d.improvements.append("No resolution update was sent to Maya or recorded in the ticket.")
        return d
    low = text.lower()
    parts = {
        "impact": any(w in low for w in ("austin", "users", "site")),
        "cause": any(w in low for w in ("route", "ospf", "advertis", "config", "network statement", "prefix")),
        "verification": any(w in low for w in ("verified", "confirmed", "ping", "checked", "tested", "monitoring")),
        "concise": 15 <= len(text.split()) <= 120,
    }
    d.score = 25.0 * sum(parts.values())
    d.detail["parts"] = parts
    if all(parts.values()):
        d.strengths.append("Resolution update stated impact, cause, and how it was verified, concisely.")
    for k, ok in parts.items():
        if not ok:
            d.improvements.append({"impact": "Resolution update did not state who was affected.", "cause": "Resolution update did not state the cause.", "verification": "Resolution update did not say how the fix was verified.", "concise": "Resolution update was outside 15–120 words."}[k])
    return d


def score_postmortem(i: AssessmentInputs) -> Dimension:
    d = Dimension(score=0.0)
    if i.postmortem is None:
        d.improvements.append("No postmortem submitted.")
        return d
    outage_pairs = sorted({s.pair_id or "" for s in i.prober_samples if s.result == "fail"})
    g = grade_postmortem(i.postmortem, i.scenario, i.events, i.baseline_configs, i.final_configs, outage_pairs)
    d.score = _clamp(g.score)
    d.detail = g.parts
    if g.parts.get("root_cause", 0) >= 40:
        d.strengths.append("Postmortem identified the removed OSPF advertisement on AUS-RTR1 as the root cause.")
    d.improvements.extend(g.notes)
    return d


# -------------------------------------------------------------------- overall
def assess(session_id: str, i: AssessmentInputs) -> tuple[Assessment, dict[str, Any]]:
    dims = {
        AssessmentDimension.TECHNICAL: score_technical(i),
        AssessmentDimension.REGRESSION: score_regression(i),
        AssessmentDimension.METHOD: score_method(i),
        AssessmentDimension.INCIDENT: score_incident(i),
        AssessmentDimension.COMMUNICATION: score_communication(i),
        AssessmentDimension.POSTMORTEM: score_postmortem(i),
    }
    overall = round(sum(dims[k].score * w for k, w in WEIGHTS.items()), 1)
    a = Assessment(
        session_id=session_id,
        scores={k: v.score for k, v in dims.items()},
        overall=overall,
        strengths=[s for v in dims.values() for s in v.strengths],
        improvements=[s for v in dims.values() for s in v.improvements],
    )
    detail = {k.value: v.detail for k, v in dims.items()}
    return a, detail
