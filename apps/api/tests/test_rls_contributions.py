"""RLS isolation proof for 0051_contributions_ledger.

These three tables record an HMRC-reportable decision and an instruction to move a client's
money, so getting their visibility wrong leaks more than a name. The two-layer pattern from
0016 applies: staff see the firm (tenant lineage), a client sees only their own accounts, and
nobody sees another firm — including through the expectation, which is the row that carries the
custodian payload.

Real Postgres via testcontainers (see tests/conftest.py); skipped if Docker is absent.
"""

import json

import pytest
from sqlalchemy import Engine, text

from tests.conftest import principal_conn


@pytest.fixture
def ledger_seed(super_engine: Engine) -> dict[str, object]:
    """Two firms. Firm A has a client with their own account plus another adviser's client;
    each account carries a contribution, an expectation and a transfer."""
    with super_engine.connect() as c:
        c.exec_driver_sql("TRUNCATE auth.users, auth.tenants RESTART IDENTITY CASCADE")

        def mk_tenant(uid: str, alias: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:uid, :alias, :alias) RETURNING id"
                    ),
                    {"uid": uid, "alias": alias},
                ).scalar_one()
            )

        ta, tb = mk_tenant("ten_a", "firm-a"), mk_tenant("ten_b", "firm-b")

        # App-only portal (0107): the resolve calls below classify the portal from the token's
        # application client id + an ACTIVE membership, so the firm declares its portal apps.
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  client_portal_client_id, platform_client_id) "
                "VALUES (:t, 'dev', 'env_a', 'app_client_a', 'app_platform_a')"
            ),
            {"t": ta},
        )

        def mk_user(uid: str, wos: str, tenant: int, portal: str) -> str:
            user_id = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                        "VALUES (:uid, :t, :uid || '@test', :wos, true) RETURNING id"
                    ),
                    {"uid": uid, "t": tenant, "wos": wos},
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, :p)"
                ),
                {"u": user_id, "t": tenant, "p": portal},
            )
            return user_id

        staff = mk_user("usr_staff", "wos_staff", ta, "platform")
        client_user = mk_user("usr_client", "wos_client", ta, "client")

        def mk_contact(uid: str, tenant: int, surname: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, surname, service_type) "
                        "VALUES (:t, :uid, 'individual', :s, 'advised') RETURNING id"
                    ),
                    {"t": tenant, "uid": uid, "s": surname},
                ).scalar_one()
            )

        contact_own = mk_contact("con_own", ta, "Cole")
        contact_other = mk_contact("con_other", ta, "Nair")
        contact_b = mk_contact("con_b", tb, "Fletcher")

        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                "VALUES (:t, :con, :u, 'self')"
            ),
            {"t": ta, "con": contact_own, "u": client_user},
        )

        def mk_account(uid: str, tenant: int, contact: int) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO platform.accounts (tenant_id, contact_id, uid, status) "
                        "VALUES (:t, :con, :uid, 'active') RETURNING id"
                    ),
                    {"t": tenant, "con": contact, "uid": uid},
                ).scalar_one()
            )

        acc_own = mk_account("acc_own", ta, contact_own)
        acc_other = mk_account("acc_other", ta, contact_other)
        acc_b = mk_account("acc_b", tb, contact_b)

        def mk_ledger(prefix: str, tenant: int, account: int, contact: int) -> int:
            expectation = int(
                c.execute(
                    text(
                        "INSERT INTO platform.transaction_expectations "
                        "(uid, tenant_id, account_id, contact_id, kind, amount, status, provider) "
                        "VALUES (:uid, :t, :a, :c, 'contribution', 1000, 'expected', 'sei') "
                        "RETURNING id"
                    ),
                    {"uid": f"exp_{prefix}", "t": tenant, "a": account, "c": contact},
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO platform.contributions "
                    "(uid, tenant_id, account_id, contact_id, expectation_id, category, amount, "
                    " tax_year) VALUES (:uid, :t, :a, :c, :e, 'personal_contribution', 800, "
                    " '2026/27')"
                ),
                {"uid": f"con_{prefix}", "t": tenant, "a": account, "c": contact, "e": expectation},
            )
            c.execute(
                text(
                    "INSERT INTO platform.transfers "
                    "(uid, tenant_id, account_id, contact_id, ceding_provider, total_value) "
                    "VALUES (:uid, :t, :a, :c, 'Aviva', 25000)"
                ),
                {"uid": f"trf_{prefix}", "t": tenant, "a": account, "c": contact},
            )
            return expectation

        mk_ledger("own", ta, acc_own, contact_own)
        mk_ledger("other", ta, acc_other, contact_other)
        mk_ledger("b", tb, acc_b, contact_b)

    return {
        "tenant_a": ta,
        "tenant_b": tb,
        "staff": staff,
        "client_user": client_user,
        "acc_own": acc_own,
        "acc_other": acc_other,
        "acc_b": acc_b,
    }


def _resolve(app_engine: Engine, wos_user: str, wos_org: str, client_id: str) -> dict[str, object]:
    """The minter exactly as db.py calls it: the token's application client id classifies the
    portal (0107), so the resolved claims carry `portal` + the matching scope."""
    with app_engine.connect() as conn:
        conn.execute(text("SET ROLE authenticated"))
        resolved = conn.execute(
            text("SELECT auth.resolve_claims(:u, :o, :c)"),
            {"u": wos_user, "o": wos_org, "c": client_id},
        ).scalar_one()
    return json.loads(resolved) if isinstance(resolved, str) else resolved


def _claims(app_engine: Engine, wos_user: str, role: str, perms: list[str]) -> dict[str, object]:
    # Staff sign in through the platform application, the client through the client one.
    app_id = "app_client_a" if wos_user == "wos_client" else "app_platform_a"
    resolved = _resolve(app_engine, wos_user, "org_a", app_id)
    return {**resolved, "sub": wos_user, "role": role, "permissions": perms, "org_id": "org_a"}


def _uids(conn: object, table: str) -> set[str]:
    sql = f"SELECT uid FROM {table}"  # noqa: S608 - table name is a literal from this module
    return {str(r) for r in conn.execute(text(sql)).scalars()}  # type: ignore[attr-defined]


TABLES = (
    "platform.transaction_expectations",
    "platform.contributions",
    "platform.transfers",
)


@pytest.mark.parametrize("table", TABLES)
def test_firm_staff_see_the_whole_firm_and_never_another(
    app_engine: Engine, ledger_seed: dict[str, object], table: str
) -> None:
    claims = _claims(app_engine, "wos_staff", "adviser", ["contributions:read", "transfers:read"])
    with principal_conn(app_engine, claims) as conn:
        uids = _uids(conn, table)
    assert {u.split("_", 1)[1] for u in uids} == {"own", "other"}
    assert not any(u.endswith("_b") for u in uids)


@pytest.mark.parametrize("table", TABLES)
def test_a_client_sees_only_their_own_accounts(
    app_engine: Engine, ledger_seed: dict[str, object], table: str
) -> None:
    """A client end-user reaches these rows through account_scope, not tenant lineage — so
    another client of the same firm is invisible."""
    claims = _claims(app_engine, "wos_client", "client", [])
    with principal_conn(app_engine, claims) as conn:
        uids = _uids(conn, table)
    assert {u.split("_", 1)[1] for u in uids} == {"own"}


@pytest.mark.parametrize("table", TABLES)
def test_a_client_cannot_write(
    app_engine: Engine, ledger_seed: dict[str, object], table: str
) -> None:
    """Writes are staff-only at the DB. A client's empty tenant lineage fails the WITH CHECK,
    so an INSERT is refused rather than silently landing in someone else's firm."""
    from sqlalchemy.exc import DBAPIError

    claims = _claims(app_engine, "wos_client", "client", [])
    columns = {
        "platform.transaction_expectations": (
            "(uid, tenant_id, account_id, kind, amount, status) "
            "VALUES ('exp_evil', :t, :a, 'contribution', 1, 'expected')"
        ),
        "platform.contributions": (
            "(uid, tenant_id, account_id, category, amount) "
            "VALUES ('con_evil', :t, :a, 'personal_contribution', 1)"
        ),
        "platform.transfers": (
            "(uid, tenant_id, account_id, ceding_provider) VALUES ('trf_evil', :t, :a, 'Aviva')"
        ),
    }[table]
    with principal_conn(app_engine, claims) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(f"INSERT INTO {table} {columns}"),  # noqa: S608 - literals from this module
            {"t": ledger_seed["tenant_a"], "a": ledger_seed["acc_own"]},
        )


def test_the_expectation_payload_does_not_leak_across_firms(
    app_engine: Engine, ledger_seed: dict[str, object]
) -> None:
    """The expectation carries the custodian request/response payload — the row most worth
    checking, because it holds account numbers at the custodian."""
    claims = _claims(app_engine, "wos_staff", "adviser", ["contributions:read"])
    with principal_conn(app_engine, claims) as conn:
        rows = conn.execute(
            text("SELECT count(*) FROM platform.transaction_expectations WHERE tenant_id = :t"),
            {"t": ledger_seed["tenant_b"]},
        ).scalar_one()
    assert rows == 0


def test_the_dormant_expectation_fk_is_now_wired(super_engine: Engine) -> None:
    """0002 declared transactions.expectation_id with a comment promising the FK 'in 0003';
    it never landed. Prove it exists, so the matcher can rely on referential integrity."""
    with super_engine.connect() as c:
        target = c.execute(
            text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conname = 'transactions_expectation_id_fkey'"
            )
        ).scalar_one_or_none()
    assert target == "platform.transaction_expectations"


def test_employer_contributions_cannot_be_confirmed_without_a_contributor(
    super_engine: Engine, ledger_seed: dict[str, object]
) -> None:
    """Morgan Lloyd requires the employer's name for a non-personal contribution. The CHECK
    lets a half-classified row sit in the queue but refuses to let it reach 'confirmed'
    nameless — otherwise the failure only surfaces at the vendor call."""
    from sqlalchemy.exc import DBAPIError

    with super_engine.connect() as c:
        # Allowed while still being worked on.
        c.execute(
            text(
                "INSERT INTO platform.contributions "
                "(uid, tenant_id, account_id, category, amount, status) "
                "VALUES ('con_wip', :t, :a, 'employer_contribution', 500, "
                " 'awaiting_confirmation')"
            ),
            {"t": ledger_seed["tenant_a"], "a": ledger_seed["acc_own"]},
        )
        with pytest.raises(DBAPIError):
            c.execute(
                text(
                    "INSERT INTO platform.contributions "
                    "(uid, tenant_id, account_id, category, amount, status) "
                    "VALUES ('con_bad', :t, :a, 'employer_contribution', 500, 'confirmed')"
                ),
                {"t": ledger_seed["tenant_a"], "a": ledger_seed["acc_own"]},
            )
