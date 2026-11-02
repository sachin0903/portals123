"""The tipping-off control on ``compliance.*`` (0186, 0221), against a real Postgres.

A client-portal principal reads NOTHING about its own screening, even though it is the subject of
the rows and its ``auth.contact_scope()`` contains that contact. Telling someone they are being
screened against sanctions, PEP or adverse-media lists — or merely that a check exists — is tipping
off, an offence under POCA 2002 s.333A.

The mechanism is one OMITTED clause. Every ``compliance.*`` read policy is
``tenant_id = ANY (auth.tenant_lineage())`` and does NOT add
``OR contact_id = ANY (auth.contact_scope())``, which ``crm.kyc_checks`` (0026) does.
``auth.tenant_lineage()`` is ``'{}'`` for a client-portal principal (0030), so the lineage predicate
is false for every row. ``compliance.matches`` and ``compliance.events`` hold no grant to
``authenticated`` at all (0221), so a read of them is refused outright.

The control is an absence, so it is invisible to a reader "tidying up for symmetry with
crm.kyc_checks". This file attacks it from two directions: behaviourally (a real client principal
reads zero rows) and structurally (no policy on ``compliance.*`` mentions ``contact_scope``, so the
guard holds even if a fixture stops exercising the client path).

Also pinned: the DML trail of the dropped ``aml`` schema stays withheld from a firm. Its
``audit.log`` rows outlive the tables, and they carry the raw before/after of every hit.

The seed runs as superuser (RLS-bypassing), as tests/test_rls.py does, and deletes only what it
created.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import principal_conn

# Written out per table rather than interpolated, so the SQL a reviewer reads is the SQL that runs.
# `uid` for compliance.checks, because a firm holds a COLUMN grant there and `count(*)` would test
# the grant shape rather than the policy.
READABLE_SQL = {
    "compliance.clients": "SELECT count(*) FROM compliance.clients",
    "compliance.addresses": "SELECT count(*) FROM compliance.addresses",
    "compliance.documents": "SELECT count(*) FROM compliance.documents",
    "compliance.live_photos": "SELECT count(*) FROM compliance.live_photos",
    "compliance.checks": "SELECT count(uid) FROM compliance.checks",
}
#: No grant to `authenticated` (0221): the read is refused before any policy is evaluated.
UNGRANTED_SQL = {
    "compliance.matches": "SELECT count(*) FROM compliance.matches",
    "compliance.events": "SELECT count(*) FROM compliance.events",
}


@pytest.fixture
def seed(super_engine: Engine) -> Iterator[dict[str, object]]:
    """One firm whose client has a portal login, and one screening of that client in every
    ``compliance.*`` table. The login is the point: it is what makes the client principal's
    ``contact_scope()`` cover the subject, i.e. what a ``contact_scope`` clause WOULD match."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"crl_{s}", "a": f"crl-{s}", "n": f"Tipping {s}"},
            ).scalar_one()
        )

        def mk_user(tag: str) -> str:
            return str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                        "VALUES (:u, :t, :e, :w, true) RETURNING id"
                    ),
                    {
                        "u": f"usr_{tag}_{s}",
                        "t": tenant,
                        "e": f"{tag}-{s}@firm.test",
                        "w": f"wos_{tag}_{s}",
                    },
                ).scalar_one()
            )

        staff = mk_user("staff")
        login = mk_user("client")
        contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, surname, service_type) "
                    "VALUES (:t, :u, 'individual', 'Okafor', 'advised') RETURNING id"
                ),
                {"t": tenant, "u": f"crl{s}"},
            ).scalar_one()
        )
        # The login IS the subject: auth.resolve_claims reads this to fill `contact_ids`.
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                "VALUES (:t, :c, :u, 'self')"
            ),
            {"t": tenant, "c": contact, "u": login},
        )
        client = int(
            c.execute(
                text(
                    "INSERT INTO compliance.clients (tenant_id, contact_id, provider_client_id) "
                    "VALUES (:t, :c, :p) RETURNING id"
                ),
                {"t": tenant, "c": contact, "p": f"cli_crl_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO compliance.addresses (tenant_id, client_id, line, city, country) "
                "VALUES (:t, :c, '1 High Street', 'Leeds', 'GB')"
            ),
            {"t": tenant, "c": client},
        )
        document = int(
            c.execute(
                text(
                    "INSERT INTO compliance.documents (tenant_id, client_id, provider_document_id, "
                    "  classification, object_key) "
                    "VALUES (:t, :c, :p, 'proof_of_identity', 'idv/front.jpg') RETURNING id"
                ),
                {"t": tenant, "c": client, "p": f"doc_crl_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO compliance.live_photos (tenant_id, client_id, provider_photo_id, "
                "  object_key) VALUES (:t, :c, :p, 'idv/selfie.jpg')"
            ),
            {"t": tenant, "c": client, "p": f"lp_crl_{s}"},
        )
        # A referred AML screen with an undecided PEP match: the most damaging row to disclose.
        check = int(
            c.execute(
                text(
                    "INSERT INTO compliance.checks (tenant_id, client_id, document_id, kind, "
                    "  status, origin, provider_check_id, outcome) "
                    "VALUES (:t, :c, :d, 'aml', 'refer', 'portal', :p, 'attention') RETURNING id"
                ),
                {"t": tenant, "c": client, "d": document, "p": f"chk_crl_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO compliance.matches (tenant_id, check_id, provider_match_id, name, "
                "  match_types) VALUES (:t, :c, 'm_1', 'Okafor', ARRAY['pep'])"
            ),
            {"t": tenant, "c": check},
        )
        c.execute(
            text(
                "INSERT INTO compliance.events (tenant_id, provider_event_id, event_type, payload) "
                "VALUES (:t, :e, 'check.completed', '{\"outcome\": \"attention\"}')"
            ),
            {"t": tenant, "e": f"evt_crl_{s}"},
        )
        # The contrast row (0026), whose policy DOES carry contact_scope: the positive control that
        # proves the client principal below is wired, so a zero count means "denied", not "inert".
        kyc_uid = f"kyc{s}"
        c.execute(
            text(
                "INSERT INTO crm.kyc_checks (uid, tenant_id, contact_id, kind, status, provider) "
                "VALUES (:u, :t, :c, 'aml', 'passed', 'complycube')"
            ),
            {"u": kyc_uid, "t": tenant, "c": contact},
        )
        # A row of the dropped aml schema's DML trail, as audit.record() wrote it: stamped with the
        # firm's tenant and holding a hit's raw before/after.
        c.execute(
            text(
                "INSERT INTO audit.log (tenant_id, action, entity_type, entity_id, detail) "
                "VALUES (:t, 'UPDATE', 'aml.hits', 1, "
                '  \'{"new": {"name": "Okafor", "decision": "undecided"}}\')'
            ),
            {"t": tenant},
        )

    yield {
        "tenant": tenant,
        "staff": staff,
        "login": login,
        "contact": contact,
        "kyc_uid": kyc_uid,
        "nonce": s,
    }

    # Children before the tenant: each table is audited, and audit.record() writes an audit.log row
    # that references the tenant (the shape test_aml_rls.py recorded).
    with super_engine.connect() as c:
        for stmt in (
            "DELETE FROM compliance.matches WHERE tenant_id = :t",
            "DELETE FROM compliance.checks WHERE tenant_id = :t",
            "DELETE FROM compliance.events WHERE tenant_id = :t",
            "DELETE FROM compliance.live_photos WHERE tenant_id = :t",
            "DELETE FROM compliance.documents WHERE tenant_id = :t",
            "DELETE FROM compliance.addresses WHERE tenant_id = :t",
            "DELETE FROM compliance.clients WHERE tenant_id = :t",
            "DELETE FROM crm.kyc_checks WHERE tenant_id = :t",
            "DELETE FROM crm.contact_users WHERE tenant_id = :t",
            "DELETE FROM crm.contacts WHERE tenant_id = :t",
            "DELETE FROM auth.users WHERE tenant_id = :t",
            "DELETE FROM auth.tenants WHERE id = :t",
        ):
            c.execute(text(stmt), {"t": tenant})


def _staff_claims(seed: dict[str, object]) -> dict[str, object]:
    """The firm's own staff: the audience the lineage policy is written for."""
    return {
        "sub": f"wos_staff_{seed['nonce']}",
        "role": "adviser",
        "portal": "platform",
        "permissions": ["contacts:read_all"],
        "user_uuid": seed["staff"],
        "tenant_id": seed["tenant"],
        "tenant_ids": [seed["tenant"]],
        "contact_ids": [],
        "account_ids": [],
    }


def _client_claims(seed: dict[str, object]) -> dict[str, object]:
    """The subject's own login, with `tenant_ids` populated as a real client token is, so the test
    proves the suppression in auth.tenant_lineage() rather than an accidentally empty claim."""
    return {
        "sub": f"wos_client_{seed['nonce']}",
        "role": "advised-client",
        "portal": "client",
        "permissions": ["contacts:read_all"],  # suppressed for a client portal; hostile on purpose
        "user_uuid": seed["login"],
        "tenant_id": seed["tenant"],
        "tenant_ids": [seed["tenant"]],
        "contact_ids": [seed["contact"]],
        "account_ids": [],
    }


def _count(conn: Connection, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar_one())


def test_the_client_principal_is_live_and_scoped(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """Anti-vacuity guard for the tests below: the client's lineage is empty (the mechanism), its
    contact_scope() holds the subject (what a contact_scope clause would match), and it reads its
    own crm.kyc_checks row (0026's policy carries contact_scope). If this fails, fix the fixture
    before trusting the zero counts."""
    with principal_conn(app_engine, _client_claims(seed)) as conn:
        lineage = conn.execute(text("SELECT auth.tenant_lineage()")).scalar_one()
        scope = conn.execute(text("SELECT auth.contact_scope()")).scalar_one()
        kyc = set(conn.execute(text("SELECT uid FROM crm.kyc_checks")).scalars())
    assert lineage == []
    assert seed["contact"] in scope, f"the fixture is inert: contact_scope() = {scope}"
    assert kyc == {seed["kyc_uid"]}


@pytest.mark.parametrize("table", sorted(READABLE_SQL))
def test_the_firm_reads_the_screening_rows_the_client_cannot(
    app_engine: Engine, seed: dict[str, object], table: str
) -> None:
    """The second positive control: every seeded row is really there for the firm's staff, so the
    client's zero below is the policy and not an empty table."""
    with principal_conn(app_engine, _staff_claims(seed)) as conn:
        assert _count(conn, READABLE_SQL[table]) == 1, table


@pytest.mark.parametrize("table", sorted(READABLE_SQL))
def test_a_client_cannot_read_their_own_screening(
    app_engine: Engine, seed: dict[str, object], table: str
) -> None:
    """THE control, table by table: the offence is committed by disclosing ANY of them — that a
    client record exists at the provider, which documents were checked, or how the check came out.
    A "fix" for symmetry with crm.kyc_checks typically touches one table first."""
    with principal_conn(app_engine, _client_claims(seed)) as conn:
        count = _count(conn, READABLE_SQL[table])
    assert count == 0, (
        f"TIPPING OFF: a client-portal principal read {count} row(s) from {table} about itself. "
        "A client must never learn that they are being screened. Almost certainly "
        "`OR contact_id = ANY (auth.contact_scope())` has been added to a compliance read policy "
        "for symmetry with crm.kyc_checks. The OMISSION is the control (POCA 2002 s.333A)."
    )


@pytest.mark.parametrize("table", sorted(UNGRANTED_SQL))
def test_a_client_cannot_read_matches_or_the_provider_inbox(
    app_engine: Engine, seed: dict[str, object], table: str
) -> None:
    """Matches and the provider's raw events hold no grant to `authenticated`: the read fails on
    privilege, for the client as for the firm, rather than returning zero rows."""
    with principal_conn(app_engine, _client_claims(seed)) as conn:
        with pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(text(UNGRANTED_SQL[table]))


def test_a_client_cannot_read_screening_via_a_join(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The direction a feature would take: the client's own visible contact row joined to its
    screening. RLS applies per relation, so the join resolves nothing."""
    sql = (
        "SELECT count(*) FROM crm.contacts c "
        "JOIN compliance.clients cl ON cl.contact_id = c.id "
        "JOIN compliance.checks ch ON ch.client_id = cl.id"
    )
    with principal_conn(app_engine, _client_claims(seed)) as conn:
        contacts = _count(conn, "SELECT count(*) FROM crm.contacts")
        joined = _count(conn, sql)
    assert contacts == 1, "precondition: the client sees its own contact row"
    assert joined == 0, f"TIPPING OFF via a join: {joined} screening row(s) resolved"


def test_no_compliance_policy_mentions_contact_scope(super_engine: Engine) -> None:
    """The structural half, read from pg_policies. It holds even if the fixtures above stop
    exercising the client path."""
    with super_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT tablename, policyname, cmd, roles::text AS roles, "
                    "       coalesce(qual, '') || ' ' || coalesce(with_check, '') AS expr "
                    "  FROM pg_policies WHERE schemaname = 'compliance'"
                )
            )
            .mappings()
            .all()
        )
    # A guard that matched nothing would pass silently.
    assert {r["tablename"] for r in rows} >= {
        "clients",
        "addresses",
        "documents",
        "live_photos",
        "checks",
        "events",
    }, sorted(r["tablename"] for r in rows)
    offenders = sorted(
        f"compliance.{r['tablename']}.{r['policyname']}"
        for r in rows
        if "contact_scope" in r["expr"]
    )
    assert offenders == [], (
        f"TIPPING OFF: {offenders} now reference auth.contact_scope(). That grants a screening "
        "subject read access to their own sanctions/PEP/adverse-media result (POCA 2002 s.333A). "
        "crm.kyc_checks includes contact_scope on purpose and compliance.* omits it on purpose. "
        "Revert the clause; do not 'fix' this test."
    )
    # SELECT-only, for the request role only: no request path writes a screening record.
    assert {r["cmd"] for r in rows} == {"SELECT"}, sorted(
        f"{r['tablename']}:{r['cmd']}" for r in rows
    )
    assert {r["roles"] for r in rows} == {"{authenticated}"}, sorted(r["roles"] for r in rows)


def test_a_firm_cannot_read_the_dropped_aml_schemas_trail(
    app_engine: Engine, seed: dict[str, object]
) -> None:
    """The ``aml`` tables are gone, their audit.log rows are not: the carve-out on aml.% stays. The
    firm still reads its own non-screening trail, which proves the carve-out is not over-broad."""
    with principal_conn(app_engine, _staff_claims(seed)) as conn:
        aml_rows = _count(conn, "SELECT count(*) FROM audit.log WHERE entity_type LIKE 'aml.%'")
        own_contacts = _count(
            conn, "SELECT count(*) FROM audit.log WHERE entity_type = 'crm.contacts'"
        )
    assert own_contacts > 0
    assert aml_rows == 0, "TIPPING OFF: a firm principal read the aml.* DML trail"
