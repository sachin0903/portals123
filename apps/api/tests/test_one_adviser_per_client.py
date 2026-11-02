"""0245 — a client has ONE adviser and any number of other staff, against a real Postgres.

Each case runs on the MIGRATOR connection (the role ``api:migrate`` runs as) inside a transaction
that is always rolled back. It steps the schema back with the revision's own ``downgrade()``, lays
down the free-text rows the estate held before 0245 (written as service_role: the table is FORCE
RLS, so the migrator itself reads nothing), then runs the revision's own ``upgrade()`` — loaded by
path, so a copy of its SQL cannot drift from it.

What it pins: the re-labelling picks exactly one adviser per client and never deletes anybody, and
afterwards the database refuses a second adviser and any word outside the list.
"""

from __future__ import annotations

import importlib.util
import types
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0245_one_adviser_per_client.py"
)


def _migration(conn: Connection) -> types.ModuleType:
    """The revision, loaded by path (its name starts with a digit), with Alembic's ``op`` bound to
    this connection."""
    spec = importlib.util.spec_from_file_location("_rev_0245", MIGRATION)
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


def _scalar(conn: Connection, sql: str, **params: object) -> object:
    return conn.execute(text(sql), params).scalar_one()


def _firm(conn: Connection) -> int:
    suffix = uuid.uuid4().hex[:10]
    return int(
        _scalar(  # type: ignore[call-overload]
            conn,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:uid, :alias, 'One Adviser') "
            "RETURNING id",
            uid=f"ten_{suffix}",
            alias=f"one-adviser-{suffix}",
        )
    )


def _login(conn: Connection, tenant: int, *, adviser: bool, deleted: bool = False) -> str:
    return str(
        _scalar(
            conn,
            "INSERT INTO auth.users (tenant_id, email, first_name, enabled, type, is_adviser, "
            "                        deleted_at) "
            "VALUES (:t, :e, 'Test', true, '{staff}', :adv, "
            "        CASE WHEN :deleted THEN now() END) RETURNING id",
            t=tenant,
            e=f"{uuid.uuid4().hex[:10]}@one-adviser.test",
            adv=adviser,
            deleted=deleted,
        )
    )


def _client(conn: Connection, tenant: int) -> int:
    return int(
        _scalar(  # type: ignore[call-overload]
            conn,
            "INSERT INTO crm.contacts (tenant_id, type) VALUES (:t, 'individual') RETURNING id",
            t=tenant,
        )
    )


def _on_team(conn: Connection, tenant: int, client: int, person: str, role: str | None) -> None:
    conn.execute(
        text(
            "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
            "VALUES (:t, :c, CAST(:a AS uuid), :r)"
        ),
        {"t": tenant, "c": client, "a": person, "r": role},
    )


def _team(conn: Connection, client: int) -> dict[str, str]:
    rows = conn.execute(
        text("SELECT adviser_id::text, role FROM crm.client_advisers WHERE contact_id = :c"),
        {"c": client},
    ).all()
    return {str(person): str(role) for person, role in rows}


def test_the_legacy_words_become_one_adviser_and_a_team(migrator: Connection) -> None:
    rev = _migration(migrator)
    rev.downgrade()  # the table as it stood before 0245: free text, NULL allowed, no limit

    migrator.execute(text("SET LOCAL ROLE service_role"))
    t = _firm(migrator)
    lead = _login(migrator, t, adviser=True)
    second = _login(migrator, t, adviser=True)
    gone = _login(migrator, t, adviser=True, deleted=True)
    planner = _login(migrator, t, adviser=False)
    admin = _login(migrator, t, adviser=False)
    reviewer = _login(migrator, t, adviser=False)

    # Two advisers, one called lead in the estate's own spelling, plus three staff in free text.
    both = _client(migrator, t)
    for person, word in (
        (lead, " Lead "),
        (second, None),
        (planner, "Paraplanner"),
        (admin, "admin"),
        (reviewer, "reviewer"),
    ):
        _on_team(migrator, t, both, person, word)
    # No lead at all: the oldest adviser row is the one.
    unnamed = _client(migrator, t)
    _on_team(migrator, t, unnamed, second, None)
    _on_team(migrator, t, unnamed, lead, "support")
    # The only `lead` is not an adviser — 0158 and the creator path could both write that.
    nobody = _client(migrator, t)
    _on_team(migrator, t, nobody, planner, "lead")
    # A deleted lead gives way to a live adviser.
    departed = _client(migrator, t)
    _on_team(migrator, t, departed, gone, "lead")
    _on_team(migrator, t, departed, second, None)
    # An adviser who was named as the paraplanner here stays the paraplanner.
    planning = _client(migrator, t)
    _on_team(migrator, t, planning, lead, "paraplanner")
    migrator.execute(text("RESET ROLE"))

    rev.upgrade()

    migrator.execute(text("SET LOCAL ROLE service_role"))
    assert _team(migrator, both) == {
        lead: "adviser",
        second: "support",
        planner: "paraplanner",
        admin: "administrator",
        reviewer: "support",
    }
    assert _team(migrator, unnamed) == {second: "adviser", lead: "support"}
    assert _team(migrator, nobody) == {planner: "support"}
    assert _team(migrator, departed) == {gone: "support", second: "adviser"}
    assert _team(migrator, planning) == {lead: "paraplanner"}

    # Afterwards the database is the floor under `contacts.assign_adviser`.
    with pytest.raises(IntegrityError, match="client_advisers_one_adviser_key"):
        with migrator.begin_nested():
            migrator.execute(
                text("UPDATE crm.client_advisers SET role = 'adviser' WHERE contact_id = :c"),
                {"c": both},
            )
    with pytest.raises(IntegrityError, match="client_advisers_role_known"):
        with migrator.begin_nested():
            _on_team(migrator, t, planning, second, "lead")
    with pytest.raises(IntegrityError, match="role"):
        with migrator.begin_nested():
            _on_team(migrator, t, planning, second, None)
    migrator.execute(text("RESET ROLE"))


def test_the_downgrade_lifts_the_rule_and_keeps_the_rows(migrator: Connection) -> None:
    rev = _migration(migrator)
    rev.downgrade()

    migrator.execute(text("SET LOCAL ROLE service_role"))
    t = _firm(migrator)
    first = _login(migrator, t, adviser=True)
    other = _login(migrator, t, adviser=True)
    client = _client(migrator, t)
    _on_team(migrator, t, client, first, "adviser")
    _on_team(migrator, t, client, other, "adviser")  # a second adviser: allowed again
    _on_team(migrator, t, _client(migrator, t), first, None)
    migrator.execute(text("RESET ROLE"))

    # And a database that went down and back up comes out with one adviser per client.
    rev.upgrade()
    migrator.execute(text("SET LOCAL ROLE service_role"))
    assert _team(migrator, client) == {first: "adviser", other: "support"}
    migrator.execute(text("RESET ROLE"))
