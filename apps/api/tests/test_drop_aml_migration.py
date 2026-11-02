"""0225 drops schema aml only when it is empty, against a real Postgres.

The rows of the Creditsafe mirror are regulated screening records. The migration counts every
aml.* table first and raises while any holds a row, so a deploy stops rather than losing them.

Each case builds a stand-in schema aml inside a transaction on the MIGRATOR connection (the role
``api:migrate`` runs as) and rolls it back. The tables are FORCE ROW LEVEL SECURITY, as 0037 made
them: that is what makes a migrator-run count read zero, and why the guard counts as service_role.
The migration's own ``upgrade()`` runs, loaded by path, so a copy of its SQL cannot drift from it.
"""

from __future__ import annotations

import importlib.util
import types
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0225_drop_aml_schema.py"
)


def _migration(conn: Connection) -> types.ModuleType:
    """The revision, loaded by path (its name starts with a digit), with Alembic's ``op`` bound to
    this connection."""
    spec = importlib.util.spec_from_file_location("_rev_0225", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = types.SimpleNamespace(get_bind=lambda: conn)  # type: ignore[attr-defined]
    return module


@pytest.fixture
def migrator(db: dict[str, str]) -> Iterator[Connection]:
    """A migrator connection inside a transaction that is always rolled back."""
    engine = create_engine(db["migrator"])
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            yield conn
        finally:
            trans.rollback()
    engine.dispose()


def _stand_in(conn: Connection, *, rows: int) -> None:
    """Schema aml as 0037 left it, in miniature: FORCE RLS tables granted to service_role, the
    sync engine's seeded singleton, and ``rows`` hits (written as service_role, the mirror's only
    writer)."""
    assert conn.execute(text("SELECT to_regnamespace('aml')")).scalar_one() is None
    conn.exec_driver_sql(
        """
        CREATE SCHEMA aml;
        GRANT USAGE ON SCHEMA aml TO service_role;
        CREATE TABLE aml.hits (id int PRIMARY KEY, name text);
        CREATE TABLE aml.sync_state (id int PRIMARY KEY);
        INSERT INTO aml.sync_state (id) VALUES (1);
        ALTER TABLE aml.hits ENABLE ROW LEVEL SECURITY;
        ALTER TABLE aml.hits FORCE ROW LEVEL SECURITY;
        ALTER TABLE aml.sync_state ENABLE ROW LEVEL SECURITY;
        ALTER TABLE aml.sync_state FORCE ROW LEVEL SECURITY;
        GRANT ALL ON aml.hits, aml.sync_state TO service_role;
        """
    )
    conn.execute(text("SET LOCAL ROLE service_role"))
    for i in range(rows):
        conn.execute(text("INSERT INTO aml.hits (id, name) VALUES (:i, 'Subject')"), {"i": i + 1})
    conn.execute(text("RESET ROLE"))


def test_a_held_row_stops_the_drop(migrator: Connection) -> None:
    _stand_in(migrator, rows=2)
    # The trap the guard exists for: the migrator itself reads nothing through FORCE RLS.
    assert migrator.execute(text("SELECT count(*) FROM aml.hits")).scalar_one() == 0

    with pytest.raises(DBAPIError) as excinfo:
        _migration(migrator).upgrade()
    message = str(excinfo.value.orig)
    assert "Refusing to drop schema aml: aml.hits (2 rows)" in message
    assert "aml.sync_state" not in message
    diag = excinfo.value.orig.diag  # type: ignore[union-attr]
    assert "object storage" in str(diag.message_hint)
    assert "five years" in str(diag.message_hint)


def test_an_empty_schema_is_dropped(migrator: Connection) -> None:
    """Empty but for the sync engine's singleton, which every database has: that row is
    bookkeeping, not a screening record, and does not hold the drop."""
    _stand_in(migrator, rows=0)
    _migration(migrator).upgrade()
    assert migrator.execute(text("SELECT to_regnamespace('aml')")).scalar_one() is None
    # The migrator's role is its own again for whatever runs after.
    assert migrator.execute(text("SELECT current_user")).scalar_one() == "migrator"


def test_a_database_without_the_schema_passes(migrator: Connection) -> None:
    _migration(migrator).upgrade()
    assert migrator.execute(text("SELECT to_regnamespace('aml')")).scalar_one() is None


def test_the_downgrade_is_refused() -> None:
    with pytest.raises(NotImplementedError, match="one-way"):
        _migration(cast(Connection, None)).downgrade()
