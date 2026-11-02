"""Shared fixtures for the unit suite: a fresh in-memory engine per test, actions on one
registry. The Postgres suite has its own conftest under tests/pg/."""

from __future__ import annotations

import pytest

from baton import Engine
from baton.registry import Registry
from baton.store.memory import FakeClock, MemoryStore


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def registry() -> Registry:
    return Registry()


@pytest.fixture
def store(clock: FakeClock) -> MemoryStore:
    return MemoryStore(clock=clock)


@pytest.fixture
def engine(store: MemoryStore, registry: Registry) -> Engine:
    return Engine(store, registry, lease_seconds=60, default_budget_seconds=1000)
