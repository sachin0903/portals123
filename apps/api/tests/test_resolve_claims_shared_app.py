"""A token's application proposes a portal; the membership disposes.

When the same WorkOS application client id is stored on more than one portal (a local
environment that only has the default application), ``auth.resolve_claims`` must follow the
membership the person holds. A distinct application still names one portal, and a token for
an application the person has no membership on stays NULL.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine


def _portal(
    engine: Engine, workos_user_id: str, client_id: str, named: str | None = None
) -> str | None:
    with engine.connect() as c:
        if named is None:
            statement = "SELECT auth.resolve_claims(:w, NULL, :app) ->> 'portal'"
        else:
            # One statement, so the transaction-local setting is visible to the function
            # even on an autocommit connection.
            statement = (
                "WITH cfg AS (SELECT set_config('request.portal', :named, true)) "
                "SELECT auth.resolve_claims(:w, NULL, :app) ->> 'portal' FROM cfg"
            )
        portal = c.execute(
            text(statement),
            {"w": workos_user_id, "app": client_id, "named": named},
        ).scalar_one()
    return portal if isinstance(portal, str) else None


def test_shared_application_follows_the_membership(super_engine: Engine) -> None:
    s = uuid.uuid4().hex[:8]
    shared = f"app_shared_{s}"
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"sapten_{s}", "a": f"sapfirm-{s}", "n": f"Shared app {s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments "
                "  (tenant_id, stage, client_portal_client_id, platform_client_id) "
                "VALUES (:t, 'dev', :app, :app)"
            ),
            {"t": tenant, "app": shared},
        )

        def login(tag: str, portal: str) -> str:
            wos = f"sapwos_{tag}_{s}"
            uid = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                        "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                    ),
                    {
                        "uid": f"sapusr_{tag}_{s}",
                        "t": tenant,
                        "email": f"{tag}_{s}@shared-app.test",
                        "wos": wos,
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, :p)"
                ),
                {"u": uid, "t": tenant, "p": portal},
            )
            return wos

        client_user = login("client", "client")
        platform_user = login("platform", "platform")

    assert _portal(super_engine, client_user, shared) == "client"
    assert _portal(super_engine, platform_user, shared) == "platform"


def test_distinct_application_does_not_cross_grant(super_engine: Engine) -> None:
    s = uuid.uuid4().hex[:8]
    client_app = f"app_client_{s}"
    platform_app = f"app_platform_{s}"
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"dapten_{s}", "a": f"dapfirm-{s}", "n": f"Distinct app {s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments "
                "  (tenant_id, stage, client_portal_client_id, platform_client_id) "
                "VALUES (:t, 'dev', :c, :p)"
            ),
            {"t": tenant, "c": client_app, "p": platform_app},
        )
        wos = f"dapwos_{s}"
        uid = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                ),
                {
                    "uid": f"dapusr_{s}",
                    "t": tenant,
                    "email": f"staff_{s}@distinct-app.test",
                    "wos": wos,
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": uid, "t": tenant},
        )

    assert _portal(super_engine, wos, platform_app) == "platform"
    assert _portal(super_engine, wos, client_app) is None
    # Naming operations does not make a platform token into an operations session.
    assert _portal(super_engine, wos, platform_app, named="operations") == "platform"


def test_named_portal_breaks_a_shared_application_tie(super_engine: Engine) -> None:
    s = uuid.uuid4().hex[:8]
    shared = f"app_both_{s}"
    other = f"app_other_{s}"
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"npten_{s}", "a": f"npfirm-{s}", "n": f"Named portal {s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments "
                "  (tenant_id, stage, client_portal_client_id, platform_client_id, "
                "   operations_client_id) "
                "VALUES (:t, 'dev', :other, :app, :app)"
            ),
            {"t": tenant, "app": shared, "other": other},
        )
        wos = f"npwos_{s}"
        uid = str(
            c.execute(
                text(
                    "INSERT INTO auth.users "
                    "  (uid, tenant_id, email, workos_user_id, enabled, type) "
                    "VALUES (:uid, :t, :email, :wos, true, ARRAY['staff']::text[]) RETURNING id"
                ),
                {
                    "uid": f"npusr_{s}",
                    "t": tenant,
                    "email": f"staff_{s}@named-portal.test",
                    "wos": wos,
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform'), (:u, :t, 'operations')"
            ),
            {"u": uid, "t": tenant},
        )

    # Unnamed, a staff login that holds both stays on the platform.
    assert _portal(super_engine, wos, shared) == "platform"
    assert _portal(super_engine, wos, shared, named="operations") == "operations"
    assert _portal(super_engine, wos, shared, named="platform") == "platform"
    # No client membership, and the client column is a different application.
    assert _portal(super_engine, wos, shared, named="client") == "platform"
    assert _portal(super_engine, wos, other, named="operations") is None
