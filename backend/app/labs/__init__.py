from .base import LabError, LabNotFound, LabProvider, UnknownDevice
from .faults import FaultInjector
from .naming import is_northstar_lab, new_lab_id
from .store import InMemorySessionStore, JsonSessionStore, SessionStore

__all__ = [
    "FaultInjector",
    "InMemorySessionStore",
    "JsonSessionStore",
    "LabError",
    "LabNotFound",
    "LabProvider",
    "SessionStore",
    "UnknownDevice",
    "is_northstar_lab",
    "make_provider",
    "new_lab_id",
]


def make_provider(settings):  # type: ignore[no-untyped-def]
    """Provider factory (LAB_PROVIDER=mock|containerlab)."""
    if settings.lab_provider == "mock":
        from .mock import MockLabProvider

        return MockLabProvider(state_dir=settings.state_dir)
    if settings.lab_provider == "containerlab":
        from .containerlab import ContainerlabProvider

        return ContainerlabProvider(settings)
    raise ValueError(f"unknown LAB_PROVIDER {settings.lab_provider!r}")
