"""Orphan reconciliation (spec §29).

On backend start, and via ``northstar cleanup-labs``: list every lab the
provider is running, compare with sessions the store considers live, and
destroy the difference. Also destroys labs for sessions that exceeded their
absolute or idle timeouts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.models.domain import ScenarioSession, ScenarioState

from .base import LabProvider
from .store import SessionStore

log = logging.getLogger("northstar.labs.reconcile")

LIVE_STATES = {
    ScenarioState.PROVISIONING,
    ScenarioState.ACTIVE,
    ScenarioState.INVESTIGATING,
    ScenarioState.MITIGATED,
    ScenarioState.RESOLVED,
    ScenarioState.POSTMORTEM,
}


@dataclass
class ReconcileReport:
    running: list[str] = field(default_factory=list)
    live_sessions: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    destroyed: list[str] = field(default_factory=list)
    dry_run: bool = False


def session_expired(session: ScenarioSession, settings: Settings, now: datetime | None = None) -> str | None:
    now = now or datetime.now(UTC)
    if now - session.started_at > timedelta(hours=settings.session_timeout_hours):
        return "absolute"
    if now - session.last_activity_at > timedelta(minutes=settings.idle_timeout_minutes):
        return "idle"
    return None


async def reconcile(
    provider: LabProvider,
    store: SessionStore,
    settings: Settings,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> ReconcileReport:
    report = ReconcileReport(dry_run=dry_run)
    report.running = await provider.list_labs()
    sessions = store.list()
    live = {s.lab_id: s for s in sessions if s.state in LIVE_STATES and s.lab_id}
    report.live_sessions = sorted(live)

    to_destroy: dict[str, str] = {}
    for lab in report.running:
        if lab not in live:
            report.orphans.append(lab)
            to_destroy[lab] = "orphan"
    for lab, sess in live.items():
        why = session_expired(sess, settings, now)
        if why:
            report.expired.append(lab)
            to_destroy[lab] = why

    for lab, why in to_destroy.items():
        log.warning("reconcile: destroying lab %s (%s)%s", lab, why, " [dry-run]" if dry_run else "")
        if dry_run:
            continue
        try:
            await provider.destroy(lab)
        except Exception:
            log.exception("failed destroying %s", lab)
            continue
        report.destroyed.append(lab)
        sess = live.get(lab)
        if sess:
            sess.state = ScenarioState.DESTROYED
            sess.ended_at = datetime.now(UTC)
            store.save(sess)
    return report
