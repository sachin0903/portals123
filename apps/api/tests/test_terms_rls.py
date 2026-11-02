"""Cross-tenant isolation for the terms catalogue and acceptances.

Does not truncate auth.tenants — same reason as test_content_rls.py.
"""

from uuid import uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import principal_conn


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, object]:
    run = uuid4().hex[:8]
    with super_engine.connect() as c:

        def mk_tenant(label: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:uid, :alias, :name) RETURNING id"
                    ),
                    {
                        "uid": f"ten_{uuid4().hex[:12]}",
                        "alias": f"firm-{label}-{run}",
                        "name": f"Firm {label.upper()} {run}",
                    },
                ).scalar_one()
            )

        ta = mk_tenant("a")
        tb = mk_tenant("b")

        def mk_user(uid: str, email: str, wos: str, ten: int) -> str:
            return str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                        "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                    ),
                    {"uid": uid, "t": ten, "email": email, "wos": wos},
                ).scalar_one()
            )

        staff_a = mk_user(f"usr_sa_{run}", f"sa-{run}@a.test", f"wos_sa_{run}", ta)
        staff_b = mk_user(f"usr_sb_{run}", f"sb-{run}@b.test", f"wos_sb_{run}", tb)
        client_a = mk_user(f"usr_ca_{run}", f"ca-{run}@a.test", f"wos_ca_{run}", ta)
        for uid_, ten, portal in (
            (staff_a, ta, "platform"),
            (staff_b, tb, "platform"),
            (client_a, ta, "client"),
        ):
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships "
                    "(user_id, tenant_id, portal, status) "
                    "VALUES (:u, :t, :p, 'active')"
                ),
                {"u": uid_, "t": ten, "p": portal},
            )
        term_a = str(
            c.execute(
                text(
                    "INSERT INTO terms.terms "
                    "(uid, owner, tenant_id, code, title, audience, required_at, status, "
                    " body_kind, body_md) "
                    "VALUES (:u, 'tenant', :t, 'general_declaration', 'General', 'client', "
                    " ARRAY['onboarding']::text[], 'published', 'html', '<p>A</p>') "
                    "RETURNING uid"
                ),
                {"u": f"trma{run}", "t": ta},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO terms.terms "
                "(uid, owner, tenant_id, code, title, audience, required_at, status, "
                " body_kind, body_md) "
                "VALUES (:u, 'tenant', :t, 'general_declaration', 'Other firm', 'client', "
                " ARRAY['onboarding']::text[], 'published', 'html', '<p>B</p>')"
            ),
            {"u": f"trmb{run}", "t": tb},
        )
        draft_a = str(
            c.execute(
                text(
                    "INSERT INTO terms.terms "
                    "(uid, owner, tenant_id, code, title, audience, required_at, status, "
                    " body_kind, body_md) "
                    "VALUES (:u, 'tenant', :t, 'draft_decl', 'Draft', 'client', "
                    " ARRAY['onboarding']::text[], 'draft', 'html', '<p>D</p>') "
                    "RETURNING uid"
                ),
                {"u": f"trmd{run}", "t": ta},
            ).scalar_one()
        )
        # Graphene's own platform-wide terms: `tenant_id` NULL, and readable by every firm and every
        # client once published (`terms_graphene_published_read`). The unpublished one is the half
        # that makes the pair a test — the policy is gated on `status`, so a firm must not see a
        # platform term Graphene is still drafting.
        for code, title, term_status in (
            ("platform_privacy", "Privacy policy", "published"),
            ("platform_next", "Unreleased platform terms", "draft"),
        ):
            c.execute(
                text(
                    "INSERT INTO terms.terms "
                    "(uid, owner, tenant_id, code, title, audience, required_at, status, "
                    " body_kind, body_md) "
                    "VALUES (:u, 'graphene', NULL, :code, :title, 'all', "
                    " ARRAY['login']::text[], CAST(:st AS terms.status), 'html', '<p>G</p>')"
                ),
                {
                    "u": f"trmg{uuid4().hex[:8]}",
                    "code": f"{code}_{run}",
                    "title": title,
                    "st": term_status,
                },
            )
        c.commit()
        return {
            "ta": ta,
            "tb": tb,
            "staff_a": staff_a,
            "staff_b": staff_b,
            "client_a": client_a,
            "term_a": term_a,
            "draft_a": draft_a,
        }


def _claims(
    user_uuid: str,
    tenant_id: int,
    *,
    portal: str,
    lineage: list[int],
    contact_ids: list[int],
) -> dict[str, object]:
    return {
        "sub": "wos",
        "user_uuid": user_uuid,
        "tenant_id": tenant_id,
        "tenant_ids": lineage,
        "contact_ids": contact_ids,
        "account_ids": [],
        "portal": portal,
        "permissions": ["terms:write"],
    }


def test_staff_cannot_see_another_firm(seed: dict[str, object], app_engine: Engine) -> None:
    claims = _claims(
        str(seed["staff_a"]),
        int(seed["ta"]),
        portal="platform",
        lineage=[int(seed["ta"])],
        contact_ids=[],
    )
    with principal_conn(app_engine, claims) as conn:
        titles = [r[0] for r in conn.execute(text("SELECT title FROM terms.terms")).all()]
    assert "General" in titles
    assert "Draft" in titles
    assert "Other firm" not in titles
    assert "Privacy policy" in titles
    assert "Unreleased platform terms" not in titles


def test_client_sees_published_only(seed: dict[str, object], app_engine: Engine) -> None:
    claims = _claims(
        str(seed["client_a"]),
        int(seed["ta"]),
        portal="client",
        lineage=[],
        contact_ids=[],
    )
    with principal_conn(app_engine, claims) as conn:
        rows = conn.execute(text("SELECT title, status::text FROM terms.terms")).all()
    titles = {r[0] for r in rows}
    assert "General" in titles
    assert "Privacy policy" in titles
    assert "Draft" not in titles
    assert "Unreleased platform terms" not in titles
    assert "Other firm" not in titles


def test_client_cannot_write_catalogue(
    seed: dict[str, object], app_engine: Engine, super_engine: Engine
) -> None:
    claims = _claims(
        str(seed["client_a"]),
        int(seed["ta"]),
        portal="client",
        lineage=[],
        contact_ids=[],
    )
    with principal_conn(app_engine, claims) as conn:
        conn.execute(
            text("UPDATE terms.terms SET title = 'hacked' WHERE uid = :u"),
            {"u": seed["term_a"]},
        )
        conn.commit()
    with super_engine.connect() as c:
        title = c.execute(
            text("SELECT title FROM terms.terms WHERE uid = :u"), {"u": seed["term_a"]}
        ).scalar_one()
    assert title == "General"


def test_versions_reject_update(seed: dict[str, object], super_engine: Engine) -> None:
    with super_engine.connect() as c:
        term_id = c.execute(
            text("SELECT id FROM terms.terms WHERE uid = :u"), {"u": seed["term_a"]}
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO terms.term_versions "
                "(uid, tenant_id, term_id, version_number, title, body_kind, body_html, "
                " content_hash) "
                "VALUES (:u, :t, :id, 1, 'General', 'html', '<p>A</p>', 'abc')"
            ),
            {"u": f"ver{uuid4().hex[:8]}", "t": seed["ta"], "id": term_id},
        )
        c.commit()
        with pytest.raises(IntegrityError, match="immutable"):
            c.execute(
                text("UPDATE terms.term_versions SET title = 'nope' WHERE term_id = :id"),
                {"id": term_id},
            )
