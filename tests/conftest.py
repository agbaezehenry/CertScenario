from __future__ import annotations

import os
from pathlib import Path

import pytest
from app.config import Settings
from app.labs import InMemorySessionStore, make_provider
from app.labs.faults import FaultInjector
from app.labs.mock import MockLabProvider
from app.scenarios import find_scenario, load_scenario

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "scenarios"


@pytest.fixture(scope="session")
def scenario():
    return load_scenario(SCENARIOS / "inc-1042", max_nodes=6)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(lab_provider="mock", state_dir=tmp_path / "state", scenarios_dir=SCENARIOS, prober_interval_seconds=0.01)


@pytest.fixture
def mock_provider() -> MockLabProvider:
    return MockLabProvider()


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
async def healthy_lab(mock_provider, scenario):
    """A provisioned mock lab in the healthy state. Yields (provider, lab_id)."""
    lab_id = "ns-test0001"
    await mock_provider.provision(scenario, lab_id=lab_id)
    yield mock_provider, lab_id
    await mock_provider.destroy(lab_id)


@pytest.fixture
async def broken_lab(healthy_lab, scenario):
    provider, lab_id = healthy_lab
    await FaultInjector(provider).apply(lab_id, scenario.faults[0])
    return provider, lab_id


# ---------------------------------------------------------------- integration
def _real_available() -> bool:
    import shutil

    return os.environ.get("LAB_PROVIDER") == "containerlab" and shutil.which("containerlab") is not None and shutil.which("docker") is not None


requires_real_lab = pytest.mark.skipif(
    not _real_available(),
    reason="integration tests need LAB_PROVIDER=containerlab plus docker + containerlab on PATH",
)


@pytest.fixture
def real_settings(tmp_path: Path) -> Settings:
    return Settings(lab_provider="containerlab", state_dir=tmp_path / "state", scenarios_dir=SCENARIOS, prober_interval_seconds=1.0)


@pytest.fixture
def real_provider(real_settings):
    return make_provider(real_settings)


@pytest.fixture
def real_scenario(real_settings):
    return find_scenario(real_settings.scenarios_dir, "INC-1042", max_nodes=6)
