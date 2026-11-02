import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db import Base

config = context.config
if config.config_file_name:
    # `disable_existing_loggers=False`, against the default: a migration has no business silencing
    # the application's loggers. The DB-backed tests bootstrap their schema by running Alembic IN
    # THIS PROCESS, so the default leaves every already-imported `app.*` logger `disabled` for the
    # rest of the run — and a disabled logger emits nothing, so any later assertion about what was
    # logged captures an empty list. It failed only when the test order put a DB test first, which
    # is why it read as flakiness. `tests/logcapture.py` works around it from the other side.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# SQL-first migrations (migrations/sql/*.sql); no autogenerate, so this stays empty.
target_metadata = Base.metadata


def _db_url() -> str:
    # Allow a caller (e.g. the test harness) to inject the URL via the Alembic config;
    # otherwise use the elevated migrator role from settings.
    return config.get_main_option("sqlalchemy.url") or settings.migration_database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # Migrations run as the elevated role, never the RLS-bound app role.
    engine = create_async_engine(_db_url())
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # async psycopg can't use Windows' default ProactorEventLoop (mirrors conftest.py + the seed
    # scripts); pick the selector policy before asyncio.run so `alembic upgrade` works on Windows.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_migrations_online())
