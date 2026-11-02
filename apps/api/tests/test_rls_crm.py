"""Client-scoped RLS isolation proof for 0002_platform_crm.

Seeds one firm with three personas -- a read-all adviser, a scope-limited adviser (own
book only), and a client end-user -- plus a second firm, then proves the two-layer RLS
on the domain tables:

  * auth.resolve_claims now FILLS contact_ids/account_ids from crm.contact_users +
    crm.client_advisers (0001 left them empty);
  * a `contacts:read_all` adviser sees the whole firm's contacts;
  * a scope-limited adviser sees ONLY their assigned book;
  * a client sees ONLY their own contact/account/transactions;
  * contact_notes are staff-internal (a client never sees notes about themselves);
  * cross-tenant rows are invisible throughout.

Real Postgres via testcontainers (see tests/conftest.py); skipped if Docker is absent.
"""

import json

import pytest
from sqlalchemy import Engine, text

from tests.conftest import principal_conn


@pytest.fixture
def crm_seed(super_engine: Engine) -> dict[str, object]:
    """One firm (A) with three personas + contacts/accounts/txns, and a second firm (B)."""
    with super_engine.connect() as c:
        c.exec_driver_sql("TRUNCATE auth.users, auth.tenants RESTART IDENTITY CASCADE")
        ta = c.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES ('ten_a','firm-a','Firm A') RETURNING id"
            )
        ).scalar_one()
        tb = c.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES ('ten_b','firm-b','Firm B') RETURNING id"
            )
        ).scalar_one()

        # App-only portal (0107): resolve_claims classifies the portal from the token's
        # application client id + an ACTIVE membership, so Firm A declares its portal apps.
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  client_portal_client_id, platform_client_id) "
                "VALUES (:t, 'dev', 'env_a', 'app_client_a', 'app_platform_a')"
            ),
            {"t": ta},
        )

        def mk_user(uid: str, email: str, wos: str, tenant: int, portal: str) -> str:
            user_id = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                        "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                    ),
                    {"uid": uid, "t": tenant, "email": email, "wos": wos},
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

        staff_all = mk_user("usr_all", "all@firm-a.test", "wos_all", ta, "platform")
        staff_scoped = mk_user("usr_scoped", "scoped@firm-a.test", "wos_scoped", ta, "platform")
        client_user = mk_user("usr_client", "client@firm-a.test", "wos_client", ta, "client")

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

        contact_1 = mk_contact("con_1", ta, "Cole")  # the client_user's own party
        contact_2 = mk_contact("con_2", ta, "Osei")  # staff_scoped's assigned book
        contact_3 = mk_contact("con_3", ta, "Nair")  # another adviser's client
        contact_b = mk_contact("con_b", tb, "Fletcher")  # Firm B

        # login <-> party: the client end-user acts for contact_1 (self).
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                "VALUES (:t, :con, :u, 'self')"
            ),
            {"t": ta, "con": contact_1, "u": client_user},
        )
        # staff assignment: staff_scoped's book is contact_2 only.
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :con, :u, 'adviser')"
            ),
            {"t": ta, "con": contact_2, "u": staff_scoped},
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

        account_1 = mk_account("acc_1", ta, contact_1)
        account_2 = mk_account("acc_2", ta, contact_2)
        account_3 = mk_account("acc_3", ta, contact_3)
        mk_account("acc_b", tb, contact_b)

        # provider mapping (links) for each account -> its Seccl id.
        for aid, ext in [
            (account_1, "SECCL1"),
            (account_2, "SECCL2"),
            (account_3, "SECCL3"),
        ]:
            c.execute(
                text(
                    "INSERT INTO platform.links "
                    "(tenant_id, entity_type, entity_id, provider, external_id) "
                    "VALUES (:t, 'account', :eid, 'seccl', :ext)"
                ),
                {"t": ta, "eid": aid, "ext": ext},
            )

        def mk_txn(src: str, tenant: int, account: int, contact: int) -> None:
            c.execute(
                text(
                    "INSERT INTO platform.transactions "
                    "(tenant_id, account_id, contact_id, source_system, source_transaction_id, "
                    " type, amount) VALUES (:t, :acc, :con, 'seccl', :src, 'buy', 100.00)"
                ),
                {"t": tenant, "acc": account, "con": contact, "src": src},
            )

        mk_txn("tx_1", ta, account_1, contact_1)
        mk_txn("tx_2", ta, account_2, contact_2)

        # a staff note on contact_1 (must stay invisible to the client end-user)
        c.execute(
            text(
                "INSERT INTO crm.contact_notes (tenant_id, contact_id, author_id, body) "
                "VALUES (:t, :con, :u, 'Adviser-only note')"
            ),
            {"t": ta, "con": contact_1, "u": staff_all},
        )

    return {
        "tenant_a": ta,
        "tenant_b": tb,
        "staff_all": staff_all,
        "staff_scoped": staff_scoped,
        "client_user": client_user,
        "contact_1": contact_1,
        "contact_2": contact_2,
        "contact_3": contact_3,
        "contact_b": contact_b,
        "account_1": account_1,
        "account_2": account_2,
        "account_3": account_3,
    }


def _resolve(app_engine: Engine, wos_user: str, wos_org: str, client_id: str) -> dict[str, object]:
    """Call the SECURITY DEFINER minter exactly as db.py::tenant_session does — the token's
    application client id (arg 3) is what classifies the `portal` claim under 0107."""
    with app_engine.connect() as conn:
        conn.execute(text("SET ROLE authenticated"))
        resolved = conn.execute(
            text("SELECT auth.resolve_claims(:u, :o, :c)"),
            {"u": wos_user, "o": wos_org, "c": client_id},
        ).scalar_one()
    return json.loads(resolved) if isinstance(resolved, str) else resolved


def _claims(app_engine: Engine, wos_user: str, role: str, perms: list[str]) -> dict[str, object]:
    """Mint the full request claims: the DB-resolved half + the JWT-derived half. Staff sign in
    through the platform application, the client end-user through the client one."""
    app_id = "app_client_a" if wos_user == "wos_client" else "app_platform_a"
    resolved = _resolve(app_engine, wos_user, "org_a", app_id)
    return {**resolved, "sub": wos_user, "role": role, "permissions": perms, "org_id": "org_a"}


def _ids(conn: object, sql: str) -> set[int]:
    return {int(r) for r in conn.execute(text(sql)).scalars()}  # type: ignore[attr-defined]


def test_resolve_claims_fills_scope(app_engine: Engine, crm_seed: dict[str, object]) -> None:
    """The minter derives the OWN scope: a client from contact_users, an adviser from
    client_advisers, plus those parties' accounts."""
    client = _resolve(app_engine, "wos_client", "org_a", "app_client_a")
    assert client["portal"] == "client"
    assert client["contact_ids"] == [crm_seed["contact_1"]]
    assert client["account_ids"] == [crm_seed["account_1"]]

    scoped = _resolve(app_engine, "wos_scoped", "org_a", "app_platform_a")
    assert scoped["portal"] == "platform"
    assert scoped["contact_ids"] == [crm_seed["contact_2"]]
    assert scoped["account_ids"] == [crm_seed["account_2"]]

    # A read-all adviser has no explicit links -> empty own scope (read_all covers them).
    alla = _resolve(app_engine, "wos_all", "org_a", "app_platform_a")
    assert alla["contact_ids"] == [] and alla["account_ids"] == []


def test_registered_contact_login_sees_the_child_and_jisa(
    app_engine: Engine, super_engine: Engine, crm_seed: dict[str, object]
) -> None:
    """The adult logs in as themselves, but their client scope includes the JISA child and account.

    This is what keeps `/applications/me` available during onboarding and the account visible
    afterwards without falsely linking the child's CRM contact to the adult login as `self`.
    """
    with super_engine.begin() as conn:
        application = conn.execute(
            text(
                "INSERT INTO crm.applications (tenant_id, uid, status) "
                "VALUES (:t, 'app_jisa_scope', 'in_progress') RETURNING id"
            ),
            {"t": crm_seed["tenant_a"]},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO crm.account_contacts "
                "  (tenant_id, application_id, account_id, contact_id, role) "
                "VALUES (:t, :application, :account, :parent, 'registered_contact')"
            ),
            {
                "t": crm_seed["tenant_a"],
                "application": application,
                "account": crm_seed["account_3"],
                "parent": crm_seed["contact_1"],
            },
        )

    claims = _resolve(app_engine, "wos_client", "org_a", "app_client_a")
    assert set(claims["contact_ids"]) == {crm_seed["contact_1"], crm_seed["contact_3"]}
    assert set(claims["account_ids"]) == {crm_seed["account_1"], crm_seed["account_3"]}


def test_read_all_adviser_sees_whole_firm(app_engine: Engine, crm_seed: dict[str, object]) -> None:
    claims = _claims(app_engine, "wos_all", "adviser", ["contacts:read_all"])
    with principal_conn(app_engine, claims) as conn:
        contacts = _ids(conn, "SELECT id FROM crm.contacts")
    assert contacts == {crm_seed["contact_1"], crm_seed["contact_2"], crm_seed["contact_3"]}
    assert crm_seed["contact_b"] not in contacts  # never another firm


def test_scoped_adviser_sees_only_book(app_engine: Engine, crm_seed: dict[str, object]) -> None:
    claims = _claims(app_engine, "wos_scoped", "adviser", [])
    with principal_conn(app_engine, claims) as conn:
        contacts = _ids(conn, "SELECT id FROM crm.contacts")
        accounts = _ids(conn, "SELECT id FROM platform.accounts")
        txns = _ids(conn, "SELECT account_id FROM platform.transactions")
    assert contacts == {crm_seed["contact_2"]}  # not 1, not 3, not B
    assert accounts == {crm_seed["account_2"]}
    assert txns == {crm_seed["account_2"]}


def test_client_sees_only_self(app_engine: Engine, crm_seed: dict[str, object]) -> None:
    claims = _claims(app_engine, "wos_client", "client", [])
    with principal_conn(app_engine, claims) as conn:
        contacts = _ids(conn, "SELECT id FROM crm.contacts")
        accounts = _ids(conn, "SELECT id FROM platform.accounts")
        txn_accounts = _ids(conn, "SELECT account_id FROM platform.transactions")
        notes = conn.execute(text("SELECT count(*) FROM crm.contact_notes")).scalar_one()
    assert contacts == {crm_seed["contact_1"]}
    assert accounts == {crm_seed["account_1"]}
    assert txn_accounts == {crm_seed["account_1"]}
    assert notes == 0  # staff-internal: a client never reads notes about themselves


def test_notes_visible_to_read_all_staff(app_engine: Engine, crm_seed: dict[str, object]) -> None:
    claims = _claims(app_engine, "wos_all", "adviser", ["contacts:read_all"])
    with principal_conn(app_engine, claims) as conn:
        notes = conn.execute(text("SELECT count(*) FROM crm.contact_notes")).scalar_one()
    assert notes == 1


def test_client_portal_is_a_hard_boundary(app_engine: Engine, crm_seed: dict[str, object]) -> None:
    """A client-portal session (portal='client') is confined to its OWN contact scope even
    when the underlying identity carries contacts:read_all and firm tenant_ids -- read_all is
    suppressed and lineage is empty. This is what stops a staff/break-glass identity seeing
    the whole firm through their client login."""
    claims = {
        "sub": "wos_client",
        "role": "advised-client",  # NB: not literally 'client' -- the gate is the portal
        "permissions": ["contacts:read_all"],
        "portal": "client",
        "org_id": "org_a",
        "user_uuid": crm_seed["client_user"],
        "tenant_id": crm_seed["tenant_a"],
        "tenant_ids": [crm_seed["tenant_a"]],
        "contact_ids": [crm_seed["contact_1"]],
        "account_ids": [crm_seed["account_1"]],
    }
    with principal_conn(app_engine, claims) as conn:
        contacts = _ids(conn, "SELECT id FROM crm.contacts")
        read_all = conn.execute(text("SELECT auth.can_read_all()")).scalar_one()
        lineage = conn.execute(text("SELECT auth.tenant_lineage()")).scalar_one()
    assert contacts == {crm_seed["contact_1"]}  # NOT the whole firm
    assert read_all is False
    assert lineage == []  # empty despite tenant_ids being present


def test_platform_links_scoped_like_the_entity(
    app_engine: Engine, crm_seed: dict[str, object]
) -> None:
    """The provider bridge is the authz boundary for lake reads: a client resolves the
    Seccl id for their OWN account only; a scoped adviser only their book's; a read-all
    adviser the whole firm's."""
    q = "SELECT external_id FROM platform.links WHERE entity_type='account'"

    with principal_conn(app_engine, _claims(app_engine, "wos_client", "client", [])) as conn:
        assert set(conn.execute(text(q)).scalars()) == {"SECCL1"}

    with principal_conn(app_engine, _claims(app_engine, "wos_scoped", "adviser", [])) as conn:
        assert set(conn.execute(text(q)).scalars()) == {"SECCL2"}

    with principal_conn(
        app_engine, _claims(app_engine, "wos_all", "adviser", ["contacts:read_all"])
    ) as conn:
        assert set(conn.execute(text(q)).scalars()) == {"SECCL1", "SECCL2", "SECCL3"}


def test_client_can_read_their_firm_product_types(
    app_engine: Engine, super_engine: Engine, crm_seed: dict[str, object]
) -> None:
    """The home-card badge is `products.product_type`. Lineage-only RLS hid the firm's shelf
    from a client session, so every wrapper labelled itself "Account"."""
    with super_engine.begin() as conn:
        own = conn.execute(
            text(
                "INSERT INTO platform.products (tenant_id, product_type, name, code) "
                "VALUES (:t, 'ISA', 'ISA', 'ISA-OWN') RETURNING id"
            ),
            {"t": crm_seed["tenant_a"]},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO platform.products (tenant_id, product_type, name, code) "
                "VALUES (:t, 'GIA', 'GIA', 'GIA-OTHER')"
            ),
            {"t": crm_seed["tenant_b"]},
        )
        conn.execute(
            text("UPDATE platform.accounts SET product_id = :p WHERE id = :a"),
            {"p": own, "a": crm_seed["account_1"]},
        )

    q = (
        "SELECT p.product_type::text FROM platform.accounts a "
        "JOIN platform.products p ON p.id = a.product_id"
    )
    with principal_conn(app_engine, _claims(app_engine, "wos_client", "client", [])) as conn:
        assert set(conn.execute(text(q)).scalars()) == {"ISA"}
        assert set(conn.execute(text("SELECT code FROM platform.products")).scalars()) == {
            "ISA-OWN"
        }


# The read policies on these three tables as they stood before 0230 wrapped each helper in a
# scalar subquery. Evaluated below as service_role (no RLS), with the same claims published, so the
# WHERE is the only filter: the row set it admits is what the old policy admitted.
_PRE_0230 = {
    "platform.transactions": (
        "(auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage())) "
        "OR account_id = ANY (auth.account_scope()) "
        "OR contact_id = ANY (auth.contact_scope())"
    ),
    "platform.accounts": (
        "(auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage())) "
        "OR id = ANY (auth.account_scope()) "
        "OR contact_id = ANY (auth.contact_scope())"
    ),
    "platform.links": (
        "(auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage())) "
        "OR (entity_type = 'account' AND entity_id = ANY (auth.account_scope())) "
        "OR (entity_type = 'contact' AND entity_id = ANY (auth.contact_scope()))"
    ),
}


def test_wrapped_policies_admit_the_rows_the_old_ones_did(
    app_engine: Engine, super_engine: Engine, crm_seed: dict[str, object]
) -> None:
    """0230 changed WHEN the helpers run (once per statement, not per row), never WHAT a principal
    may read. For every persona, each table's visible rows must equal the rows the pre-0230
    expression admits — the per-role check docs/gotchas.md asks of any rewrite of this shape.
    Contact links are added so the contact arm of `links_read` is exercised too."""
    with super_engine.connect() as c:
        for contact, ext in ((crm_seed["contact_1"], "C1"), (crm_seed["contact_3"], "C3")):
            c.execute(
                text(
                    "INSERT INTO platform.links (tenant_id, entity_type, entity_id, provider, "
                    "  external_id) VALUES (:t, 'contact', :eid, 'seccl', :ext)"
                ),
                {"t": crm_seed["tenant_a"], "eid": contact, "ext": ext},
            )

    personas = {
        "read-all adviser": _claims(app_engine, "wos_all", "adviser", ["contacts:read_all"]),
        "book-scoped adviser": _claims(app_engine, "wos_scoped", "adviser", []),
        "client": _claims(app_engine, "wos_client", "client", []),
        "client portal holding read_all": {
            "sub": "wos_client",
            "role": "advised-client",
            "permissions": ["contacts:read_all"],
            "portal": "client",
            "org_id": "org_a",
            "user_uuid": crm_seed["client_user"],
            "tenant_id": crm_seed["tenant_a"],
            "tenant_ids": [crm_seed["tenant_a"]],
            "contact_ids": [crm_seed["contact_1"]],
            "account_ids": [crm_seed["account_1"]],
        },
        "no claims": {},
    }
    for persona, claims in personas.items():
        for table, old in _PRE_0230.items():
            with principal_conn(app_engine, claims) as conn:
                now = _ids(conn, f"SELECT id FROM {table}")  # noqa: S608 - fixed table names
            with app_engine.connect() as conn:
                conn.execute(text("SET ROLE service_role"))
                conn.execute(
                    text("SELECT set_config('request.jwt.claims', :c, false)"),
                    {"c": json.dumps(claims)},
                )
                before = _ids(conn, f"SELECT id FROM {table} WHERE {old}")  # noqa: S608
            assert now == before, f"{persona} on {table}: {sorted(now)} != {sorted(before)}"

    # Not vacuous: the read-all adviser sees all of firm A's rows and the scoped adviser fewer.
    with principal_conn(app_engine, personas["read-all adviser"]) as conn:
        assert len(_ids(conn, "SELECT id FROM platform.links")) == 5
        assert len(_ids(conn, "SELECT id FROM platform.transactions")) == 2
    with principal_conn(app_engine, personas["book-scoped adviser"]) as conn:
        assert len(_ids(conn, "SELECT id FROM platform.links")) == 1
