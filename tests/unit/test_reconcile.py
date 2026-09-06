from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.labs.reconcile import reconcile
from app.models.domain import ScenarioSession, ScenarioState


async def test_orphans_destroyed_live_kept(mock_provider, store, scenario, settings):
    await mock_provider.provision(scenario, lab_id="ns-live")
    await mock_provider.provision(scenario, lab_id="ns-orphan")
    store.save(ScenarioSession(id="s1", scenario_id=scenario.id, learner_id="h", state=ScenarioState.ACTIVE, lab_id="ns-live"))

    rep = await reconcile(mock_provider, store, settings, dry_run=True)
    assert rep.orphans == ["ns-orphan"] and rep.destroyed == []
    assert await mock_provider.list_labs() == ["ns-live", "ns-orphan"]

    rep = await reconcile(mock_provider, store, settings)
    assert rep.destroyed == ["ns-orphan"]
    assert await mock_provider.list_labs() == ["ns-live"]


async def test_expired_sessions_destroyed(mock_provider, store, scenario, settings):
    now = datetime.now(UTC)
    await mock_provider.provision(scenario, lab_id="ns-idle")
    await mock_provider.provision(scenario, lab_id="ns-old")
    await mock_provider.provision(scenario, lab_id="ns-fresh")
    store.save(ScenarioSession(id="idle", scenario_id=scenario.id, learner_id="h", state=ScenarioState.ACTIVE, lab_id="ns-idle",
                               started_at=now - timedelta(minutes=30), last_activity_at=now - timedelta(minutes=21)))
    store.save(ScenarioSession(id="old", scenario_id=scenario.id, learner_id="h", state=ScenarioState.ACTIVE, lab_id="ns-old",
                               started_at=now - timedelta(hours=5), last_activity_at=now))
    store.save(ScenarioSession(id="fresh", scenario_id=scenario.id, learner_id="h", state=ScenarioState.ACTIVE, lab_id="ns-fresh",
                               started_at=now, last_activity_at=now))
    rep = await reconcile(mock_provider, store, settings, now=now)
    assert sorted(rep.expired) == ["ns-idle", "ns-old"]
    assert await mock_provider.list_labs() == ["ns-fresh"]
    assert store.get("idle").state == ScenarioState.DESTROYED
    assert store.get("fresh").state == ScenarioState.ACTIVE


async def test_crashed_session_in_provisioning_is_not_treated_as_orphan(mock_provider, store, scenario, settings):
    """A session persisted before provisioning finished still owns its lab."""
    await mock_provider.provision(scenario, lab_id="ns-prov")
    store.save(ScenarioSession(id="p", scenario_id=scenario.id, learner_id="h", state=ScenarioState.PROVISIONING, lab_id="ns-prov"))
    rep = await reconcile(mock_provider, store, settings)
    assert rep.orphans == [] and rep.destroyed == []
