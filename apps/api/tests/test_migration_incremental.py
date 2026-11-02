"""Head must be reachable from a COMMITTED database, not only from nothing.

THIS IS THE ONE THING THE REST OF THE SUITE STRUCTURALLY CANNOT SEE. Every database-backed test
builds its schema from scratch in ONE Alembic transaction (``conftest.db``), where a migration's
predecessors are uncommitted work in the same transaction. A deployment is the opposite: everything
up to its current revision committed months ago, and only the new migrations run together.

The difference is not academic — it decides whether an enum member exists yet. PostgreSQL refuses
``ALTER TYPE ... ADD VALUE`` followed by a USE of that value in the same transaction, and exempts a
type CREATED in the current transaction. So ``0168``'s original ``ADD VALUE`` + ``0169``'s backfill
passed every test here (0002 creates ``crm.relationship_type`` in the same transaction, so the new
member was exempt) and failed on the first real database, where 0002 had long since committed. The
error reads ``unsafe use of new value``, arrives mid-deploy, and rolls the whole upgrade back.

So this walks the graph the way a deployment does: base, COMMIT, then the rest in one transaction.
Two ``command.upgrade`` calls, because ``env.py`` opens its own engine and transaction per run.

The base is deliberately near the bottom rather than at ``head~1``: the whole class of bug is one
migration using what an EARLIER UNAPPLIED migration created, and the distance between them is not
knowable in advance — ``0169`` also named a ``crm.account_role`` member that ``0155`` had added,
which a ``head~1`` split would have missed entirely. A legitimate future migration that genuinely
needs a value added by an older one has two answers that keep this green: compare the column as
``::text`` (naming no enum value), or replace the type rather than extend it, as 0168 now does.
"""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from tests.conftest import API_ROOT

#: The oldest revision worth claiming a deployment could be sitting at. Anything after it is
#: exercised as "new migrations, run together, against a committed database".
BASE_REVISION = "0002_platform_crm"

_SCRATCH_DB = "portals_incremental"


def _config(url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _swap_database(url: str, name: str) -> str:
    return url.rsplit("/", 1)[0] + f"/{name}"


def test_head_is_reachable_from_a_committed_base(db: dict[str, str]) -> None:
    """Upgrade to the base, commit, then upgrade the rest — exactly what ``api:migrate`` does."""
    # A second database in the same container: the roles from `01-roles.sql` are cluster-wide, so
    # only the database-specific grants `02-grants.sql` makes have to be repeated.
    admin = create_engine(db["super"], isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {_SCRATCH_DB} WITH (FORCE)")
        conn.exec_driver_sql(f"CREATE DATABASE {_SCRATCH_DB}")
        conn.exec_driver_sql(f"GRANT CREATE ON DATABASE {_SCRATCH_DB} TO migrator")
    admin.dispose()

    scratch_admin = create_engine(
        _swap_database(db["super"], _SCRATCH_DB), isolation_level="AUTOCOMMIT"
    )
    with scratch_admin.connect() as conn:
        conn.exec_driver_sql("GRANT CREATE, USAGE ON SCHEMA public TO migrator")
    scratch_admin.dispose()

    migrator_url = _swap_database(db["migrator"], _SCRATCH_DB)
    try:
        command.upgrade(_config(migrator_url), BASE_REVISION)
        # The second call is the point: a fresh engine, a fresh transaction, and everything the
        # base left undone applied together. A migration that needs an enum member one of its
        # unapplied predecessors added fails HERE and nowhere else in the suite.
        command.upgrade(_config(migrator_url), "head")

        engine = create_engine(migrator_url)
        with engine.connect() as conn:
            revision = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
        engine.dispose()
        assert revision, "the incremental upgrade left no stamped revision"
    finally:
        admin = create_engine(db["super"], isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {_SCRATCH_DB} WITH (FORCE)")
        admin.dispose()
