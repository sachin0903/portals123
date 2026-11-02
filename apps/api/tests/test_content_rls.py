"""Cross-tenant isolation for the content CMS (staff lineage; clients cannot write).

This file deliberately does NOT `TRUNCATE auth.tenants`, though tests/test_rls.py and its
siblings do. That truncate takes the migration-seeded MODEL TENANT with it, and two files —
test_document_types.py (0037's backfill) and test_model_email_templates.py — read it back
without re-seeding it, so they only pass because the truncating files collect after them.
`test_content_rls` sorts BEFORE both, so a truncate here fails all three of their tests with
"no row was found", pointing at migrations that are fine. Isolation is what is under test, so
the fixture mints its own tenants with random uids and every assertion is scoped to them; RLS
does the narrowing, which is the point.
"""

from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

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
        item_a = str(
            c.execute(
                text(
                    "INSERT INTO content.items (tenant_id, kind, title, slug, created_by, status) "
                    "VALUES (:t, 'article', 'A piece', 'a-piece', CAST(:u AS uuid), 'published') "
                    "RETURNING uid"
                ),
                {"t": ta, "u": staff_a},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO content.items (tenant_id, kind, title, slug, created_by, status) "
                "VALUES (:t, 'article', 'B piece', 'b-piece', CAST(:u AS uuid), 'published')"
            ),
            {"t": tb, "u": staff_b},
        )
        draft_a = str(
            c.execute(
                text(
                    "INSERT INTO content.items (tenant_id, kind, title, slug, created_by, status) "
                    "VALUES (:t, 'news', 'Draft', 'draft', CAST(:u AS uuid), 'draft') "
                    "RETURNING uid"
                ),
                {"t": ta, "u": staff_a},
            ).scalar_one()
        )
        return {
            "ta": ta,
            "tb": tb,
            "staff_a": staff_a,
            "staff_b": staff_b,
            "client_a": client_a,
            "item_a": item_a,
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
        "permissions": ["content:write"],
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
        uids = [r[0] for r in conn.execute(text("SELECT uid FROM content.items")).all()]
    assert seed["item_a"] in uids
    assert "b-piece" not in uids
    assert any(u == seed["draft_a"] for u in uids)


def test_client_sees_published_only_for_own_tenant(
    seed: dict[str, object], app_engine: Engine
) -> None:
    claims = _claims(
        str(seed["client_a"]),
        int(seed["ta"]),
        portal="client",
        lineage=[],
        contact_ids=[],
    )
    with principal_conn(app_engine, claims) as conn:
        rows = conn.execute(text("SELECT uid, status::text FROM content.items")).all()
        other = conn.execute(text("SELECT title FROM content.items WHERE title = 'B piece'")).all()
    assert {(r[0], r[1]) for r in rows} == {(seed["item_a"], "published")}
    assert other == []


def test_client_write_does_not_stick(
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
            text("UPDATE content.items SET title = 'hacked' WHERE uid = :u"),
            {"u": seed["item_a"]},
        )
        conn.commit()
    with super_engine.connect() as c:
        title = c.execute(
            text("SELECT title FROM content.items WHERE uid = :u"), {"u": seed["item_a"]}
        ).scalar_one()
    assert title == "A piece"
