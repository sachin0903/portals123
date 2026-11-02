"""Real-Postgres fixtures: one container for the session, baton's DDL in a test schema,
truncated between tests. Skipped cleanly when Docker is not available."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from baton import Engine, ddl
from baton.registry import Registry
from baton.store.memory import FakeClock
from baton.store.postgres import PostgresStore

SCHEMA = "baton_test"


def _unavailable(reason: str) -> None:
    pytest.skip(reason, allow_module_level=True)


@pytest.fixture(scope="session")
def _container() -> Iterator[object]:
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError as exc:  # pragma: no cover
        _unavailable(f"testcontainers not installed: {exc}")
    container = PostgresContainer(
        "postgres:18", username="postgres", password="postgres", dbname="baton"
    )
    try:
        container.start()
    except Exception as exc:  # pragma: no cover - Docker not running
        _unavailable(f"Postgres testcontainer unavailable: {exc}")
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def dsn(_container: object) -> str:
    host = _container.get_container_host_ip()  # type: ignore[attr-defined]
    port = _container.get_exposed_port(5432)  # type: ignore[attr-defined]
    return f"postgresql+psycopg://postgres:postgres@{host}:{port}/baton"


@pytest.fixture(scope="session")
def _schema_ready(dsn: str) -> bool:
    async def _apply() -> None:
        engine = create_async_engine(dsn)
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            await conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
            for stmt in ddl(SCHEMA).split(";\n"):
                s = stmt.strip()
                if s:
                    await conn.execute(text(s))
        await engine.dispose()

    asyncio.run(_apply())
    return True


@pytest_asyncio.fixture
async def pg_engine(dsn: str, _schema_ready: bool) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(dsn)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"TRUNCATE {SCHEMA}.runs, {SCHEMA}.attempts, {SCHEMA}.events, "
                f"{SCHEMA}.subjects RESTART IDENTITY CASCADE"
            )
        )
    yield engine
    await engine.dispose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def registry() -> Registry:
    return Registry()


@pytest.fixture
def pg_store(pg_engine: AsyncEngine, clock: FakeClock) -> PostgresStore:
    return PostgresStore(pg_engine, schema=SCHEMA, clock=clock)


@pytest.fixture
def pg_engine_obj(pg_store: PostgresStore, registry: Registry) -> Engine:
    return Engine(pg_store, registry, lease_seconds=60, default_budget_seconds=1000)
