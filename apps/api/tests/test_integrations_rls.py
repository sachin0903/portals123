"""Row-level-security proof for 0231_integrations_schema -- mirrors test_aml_rls.py's structural
half.

`integrations.*` carries ZERO RLS policies by design (docs comment in
0231_integrations_schema.sql): this
is Graphene-ops-only data, access is enforced by `require_permission` in the router, not by a
WHERE-clause. So the only thing to prove here is the negative: nothing is reachable except via
`service_role`, on all 8 relations, and nothing has quietly grown a policy or a grant that would
open a hole.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import principal_conn

TABLES: tuple[str, ...] = (
    "firms",
    "features",
    "dsrs",
    "platforms",
    "platform_features",
    "job_runs",
)

#: sei_reports' own bookkeeping/income-statement tables (0231_integrations_schema) -- deliberately
#: NOT RLS-protected (plain `GRANT SELECT, INSERT ... TO service_role`, no ENABLE/FORCE): only the
#: engine itself, as `service_role`, ever touches them, and nothing in this codebase reads them.
EXEMPT_ENGINE_TABLES: tuple[str, ...] = (
    "schema_migrations",
    "income_statement_items_sent",
    "income_statements",
    "transactions_sent",
)


@pytest.mark.parametrize("table", TABLES)
def test_every_table_is_unreachable_without_service_role(app_engine: Engine, table: str) -> None:
    """A plain SELECT as `authenticated` -- the ordinary RLS-bound request role every other
    tenant-scoped schema in this codebase grants SELECT to -- must fail on privilege here, not
    return zero rows. Note this is NOT the bare `app` login role: `app` is itself a member of
    `service_role` (bootstrap's `GRANT service_role TO app`, needed so a request session can `SET
    LOCAL ROLE service_role` for admin_session), so it inherits service_role's table grants and a
    query as plain `app` would misleadingly "succeed" with a RLS-filtered empty result rather than
    prove anything about privilege. `SET ROLE authenticated` (mirroring db.py::tenant_session and
    test_aml_rls.py's `principal_conn`) is the actual non-service_role request shape: authenticated
    holds no grant on `integrations.*` at all, so this must fail closed on privilege."""
    with principal_conn(app_engine, {}) as conn:
        with pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(text(f"SELECT * FROM integrations.{table}"))  # noqa: S608 - fixed table name from a closed tuple


def test_zero_policies_on_the_integrations_schema(super_engine: Engine) -> None:
    """The structural half: `integrations` carries ZERO rows in pg_policies. This is meant to be
    an absence, not merely an unused one -- so its ABSENCE is the thing under test, the same
    reasoning as test_aml_rls's tipping-off control."""
    with super_engine.connect() as c:
        rows = (
            c.execute(text("SELECT * FROM pg_policies WHERE schemaname = 'integrations'"))
            .mappings()
            .all()
        )
    assert rows == [], (
        f"integrations.* grew a policy: {rows}. Access here is enforced by `require_permission` "
        "in the router, not a WHERE-clause -- a policy here (even a permissive one) would open a "
        "route around that check for anyone who can otherwise reach the table."
    )


def test_no_integrations_table_carries_an_audit_trigger(super_engine: Engine) -> None:
    """`audit.record()` writes an audit.log row whose tenant_id is a foreign key to auth.tenants,
    but these tables' `tenant_id` is the Intelliflo-side tenant (a vendor id such as 12551). A
    trigger on any of them fails that foreign key and the write with it -- the tenant sync's upsert
    of a platform row. 0231 created the triggers; 0279 and 0281 remove them, and 0231 no longer
    attaches them. Nothing may bring one back."""
    with super_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT c.relname, t.tgname FROM pg_trigger t "
                    "  JOIN pg_class c ON c.oid = t.tgrelid "
                    "  JOIN pg_namespace n ON n.oid = c.relnamespace "
                    " WHERE n.nspname = 'integrations' AND NOT t.tgisinternal "
                    "   AND t.tgfoid = 'audit.record'::regproc"
                )
            )
            .mappings()
            .all()
        )
    assert rows == [], f"integrations.* carries an audit trigger: {[dict(r) for r in rows]}"


def test_only_service_role_and_the_owner_hold_grants(super_engine: Engine) -> None:
    """`information_schema.role_table_grants` for this schema should name only `service_role`
    (the migration's explicit GRANT) and the owning role (`migrator`, which always implicitly
    holds every privilege on what it created) -- nothing else, and specifically not
    `authenticated` or `app`/`PUBLIC`."""
    with super_engine.connect() as c:
        grantees = {
            r.grantee
            for r in c.execute(
                text(
                    "SELECT DISTINCT grantee FROM information_schema.role_table_grants "
                    "WHERE table_schema = 'integrations'"
                )
            )
        }
        owners = {
            r.tableowner
            for r in c.execute(
                text("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'integrations'")
            )
        }

    unexpected = grantees - owners - {"service_role"}
    assert unexpected == set(), (
        f"integrations.* granted table privileges to unexpected roles: {sorted(unexpected)}. "
        "Only service_role (explicit GRANT) and the owning role (migrator, implicit) should "
        "appear here -- a grant to `authenticated`/`app`/PUBLIC would open a hole around the "
        "router's `require_permission` gate, since there are no policies to fall back on."
    )


# -- the transactions ledger (0257): on a FRESH database, with its attempt marker ---------------

_LEDGER = "integrations.transactions_sent"
_PK_SQL = (
    "SELECT a.attname FROM pg_index i JOIN pg_attribute a "  # noqa: S608
    "  ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey) "
    f"WHERE i.indrelid = '{_LEDGER}'::regclass AND i.indisprimary ORDER BY a.attnum"
)
_COLS_SQL = (
    "SELECT column_name, column_default FROM information_schema.columns "
    "WHERE table_schema = 'integrations' AND table_name = 'transactions_sent'"
)


def _insert(key: str, status: str | None = None) -> Any:
    cols, vals = ("tenant_id, item_key", f"'t', '{key}'")
    if status:
        cols, vals = cols + ", status", vals + f", '{status}'"
    return text(f"INSERT INTO {_LEDGER} ({cols}) VALUES ({vals})")  # noqa: S608 - fixed literals


def test_the_transactions_ledger_exists_with_its_attempt_marker(super_engine: Engine) -> None:
    """integrations.transactions_sent was only ever created on a database that had run an old local
    migration, so a freshly migrated one lacked it and the Transactions feature was blocked."""
    with super_engine.connect() as conn:
        cols = {r[0]: r[1] for r in conn.execute(text(_COLS_SQL))}
        pk = [r[0] for r in conn.execute(text(_PK_SQL))]
    assert set(cols) == {"tenant_id", "item_key", "sent_at", "status", "attempted_at"}
    assert pk == ["tenant_id", "item_key"]
    assert "confirmed" in cols["status"]  # rows from before the marker existed were all confirmed


def test_the_ledger_takes_only_the_two_states(super_engine: Engine) -> None:
    with super_engine.connect() as conn:
        conn.execute(_insert("k1", "attempted"))
        status = conn.execute(text(f"{_insert('k2').text} RETURNING status")).scalar_one()
        assert status == "confirmed"
        with pytest.raises(Exception, match="transactions_sent_status_check"):
            conn.execute(_insert("k3", "sent"))
        conn.rollback()


def test_the_ledger_migration_is_safe_to_apply_over_an_existing_ledger(
    super_engine: Engine,
) -> None:
    """The dev database already had the table, with rows. Applying the SQL again (the second upgrade
    of a re-run, or a database that predates the revision) keeps the rows and changes nothing."""
    from pathlib import Path

    sql_file = Path(__file__).resolve().parent.parent / "migrations" / "sql"
    sql = (sql_file / "0276_integrations_txn_ledger.sql").read_text(encoding="utf-8")
    with super_engine.connect() as conn:
        conn.execute(_insert("keep", "attempted"))
        conn.exec_driver_sql(sql.replace("%", "%%"))
        row = conn.execute(
            text(f"SELECT status FROM {_LEDGER} WHERE tenant_id = 't' AND item_key = 'keep'")  # noqa: S608
        ).scalar_one()
        conn.rollback()
    assert row == "attempted"


# -- the income-items ledger (0259): attempt marker ------------------------------------------

_ITEMS = "integrations.income_statement_items_sent"


def _item_insert(key: str, status: str | None = None) -> Any:
    cols, vals = ("tenant_id, item_key, income_statement_id", f"'t', '{key}', 's'")
    if status:
        cols, vals = cols + ", status", vals + f", '{status}'"
    return text(f"INSERT INTO {_ITEMS} ({cols}) VALUES ({vals})")  # noqa: S608 - fixed literals


def test_the_items_ledger_has_its_attempt_marker_columns(super_engine: Engine) -> None:
    with super_engine.connect() as conn:
        cols = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'integrations' "
                    "AND table_name = 'income_statement_items_sent'"
                )
            )
        }
    assert {"status", "attempted_at", "batch_id", "fingerprint"} <= cols


def test_the_items_ledger_takes_three_states_and_defaults_to_confirmed(
    super_engine: Engine,
) -> None:
    with super_engine.connect() as conn:
        conn.execute(_item_insert("k1", "held"))
        status = conn.execute(text(f"{_item_insert('k2').text} RETURNING status")).scalar_one()
        assert status == "confirmed"
        with pytest.raises(Exception, match="income_statement_items_sent_status_check"):
            conn.execute(_item_insert("k3", "sent"))
        conn.rollback()
