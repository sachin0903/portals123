"""Every API transaction runs with Postgres JIT off, and only that transaction (app.db._jit_off).

JIT compiled the documents library's query for ~1.4s on every load because RLS inflates the
planner's cost estimate past `jit_above_cost`. The fix is transaction-scoped on purpose, so the
second half of each test is the part that matters under a pooler: the setting must not outlive
the transaction that set it.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import db as db_mod
from app.db import ApiSession


async def test_api_transactions_run_with_jit_off_and_do_not_leak_it(db: dict[str, str]) -> None:
    engine = create_async_engine(db["app"], pool_size=1, max_overflow=0)
    maker = async_sessionmaker(
        engine, class_=AsyncSession, sync_session_class=ApiSession, expire_on_commit=False
    )
    try:
        async with maker() as session, session.begin():
            assert (await session.execute(text("SHOW jit"))).scalar_one() == "off"
        # One pooled connection, so this is the same backend the session just used.
        async with engine.connect() as conn:
            assert (await conn.execute(text("SHOW jit"))).scalar_one() == "on"
    finally:
        await engine.dispose()


async def test_an_autobegun_session_runs_with_jit_off_too(db: dict[str, str]) -> None:
    """`aml.py` and others open `SessionLocal()` without `session.begin()`; autobegin still fires
    `after_begin`, so they get the same setting."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(
        engine, class_=AsyncSession, sync_session_class=ApiSession, expire_on_commit=False
    )
    try:
        async with maker() as session:
            assert (await session.execute(text("SHOW jit"))).scalar_one() == "off"
    finally:
        await engine.dispose()


def test_the_app_session_maker_uses_the_jit_off_session() -> None:
    assert db_mod.SessionLocal.kw["sync_session_class"] is ApiSession
