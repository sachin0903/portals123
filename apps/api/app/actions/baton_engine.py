"""The process-wide baton engine, bound to the app database and the firm policy hooks.

Runtime state lives in schema ``baton`` (migration 0261); firm policy stays in
``actions.policies``. Actions register onto baton's default registry — importing
``app.actions.defs`` populates it. The engine is built once, lazily.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from baton import Engine
from baton.registry import default_registry
from baton.store.postgres import PostgresStore

from app.actions.hooks import GrapheneHooks
from app.actions.redact import firm_visible
from app.db import engine as _db_engine

BATON_SCHEMA = "baton"
#: The per-firm advisory-lock key for RV-{n} case numbers, kept from the old engine.
CASE_LOCK_KEY = 179


def build_engine(sa_engine: Any, *, default_budget_seconds: float = 5.0) -> Engine:
    """A baton engine bound to a given SQLAlchemy async engine. The API uses the app engine
    (get_engine); the worker passes its own, which connects on migration_database_url."""
    import app.actions.defs  # noqa: F401 - register the action catalogue onto default_registry

    hooks = GrapheneHooks()
    store = PostgresStore(sa_engine, schema=BATON_SCHEMA, hooks=hooks, case_lock_key=CASE_LOCK_KEY)
    return Engine(
        store,
        default_registry,
        hooks=hooks,
        redact=firm_visible,
        lease_seconds=60.0,
        default_budget_seconds=default_budget_seconds,
    )


@cache
def get_engine() -> Engine:
    return build_engine(_db_engine)
