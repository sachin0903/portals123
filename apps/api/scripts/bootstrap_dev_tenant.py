"""Provision the local dev tenant + your user, so login resolves against a real DB mirror (dev).

The DB mirror (auth.tenants + auth.users + membership + role) is normally created by the runtime
provisioning flow; a `pnpm db:reset` wipes it, which leaves login unable to resolve you (see
`auth.resolve_claims`). This recreates the `graphene` dev tenant and the platform-staff users
(Mark Alford and Gio Matonti) so either can log in as before, and gives the other seed scripts
the tenant they assume exists.

Re-running is the repair for a local revoke: `auth.resolve_claims` only grants a portal when the
matching membership is ``status='active'``, and the WorkOS poller will not promote an ``inactive``
row back to active (it treats that as a deliberate suspend). A `pnpm db:reset` is the other reason
to re-run; follow it with `bootstrap_dev_environment.py` (or `moon run api:bootstrap-dev`).

Permissions are minted from this mirror (`auth.membership_roles` → `auth.resolve_claims`), not from
the WorkOS access token. Granting the role every permission — including `system` — is what makes
the operations portal admit you.

Run:  PYTHONPATH=. uv run --directory apps/api python scripts/bootstrap_dev_tenant.py
      moon run api:bootstrap-dev   # tenant + WorkOS host routing
Env overrides: DEV_TENANT_ALIAS, DEV_TENANT_NAME, DEV_WORKOS_USER_ID,
               DEV_USER_EMAIL, DEV_USER_FIRST, DEV_USER_LAST, DEV_ROLE_SLUG, DEV_ROLE_NAME.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.ids import new_id, tenant_uid
from app.provisioning import clone_model_roles, fold_email

# Defaults for the local dev firm + owner (override via env). These are ids, not secrets.
TENANT_ALIAS = os.environ.get("DEV_TENANT_ALIAS", "graphene")
TENANT_NAME = os.environ.get("DEV_TENANT_NAME", "Graphene")
WORKOS_USER_ID = os.environ.get("DEV_WORKOS_USER_ID", "user_01KX8KQRD9RVG624DT8NHWXPR6")
USER_EMAIL = os.environ.get("DEV_USER_EMAIL", "mark@grapheneplatforms.com")
USER_FIRST = os.environ.get("DEV_USER_FIRST", "Mark")
USER_LAST = os.environ.get("DEV_USER_LAST", "Alford")
ROLE_SLUG = os.environ.get("DEV_ROLE_SLUG", "admin")
ROLE_NAME = os.environ.get("DEV_ROLE_NAME", "Administrator")
# Further local logins on the same tenant and owner role. Not env-overridden: the primary user
# above is the one a machine can retarget, and these are the other Graphene accounts that log in
# locally. Skipped in main() when DEV_WORKOS_USER_ID already names one of them.
EXTRA_USERS: tuple[dict[str, str], ...] = (
    {
        "workos_user_id": "user_01KWF1SDX8WDJV4ZXWX2H1N3M1",
        "email": "gio@grapheneplatforms.com",
        "first_name": "Gio",
        "last_name": "Matonti",
    },
)


async def ensure_staff_user(
    s: AsyncSession,
    *,
    tenant_id: int,
    role_id: int,
    workos_user_id: str,
    email: str,
    first_name: str,
    last_name: str,
) -> str:
    """Staff user plus active platform and operations memberships. Idempotent on workos_user_id;
    a re-run sets membership status back to active (the login repair)."""
    email = fold_email(email)
    user_id = (
        await s.execute(
            text(
                "INSERT INTO auth.users "
                "  (uid, tenant_id, email, first_name, last_name, workos_user_id, "
                "   enabled, type) "
                "VALUES (:uid, :t, :email, :first, :last, :wos, true, "
                "        ARRAY['staff']::text[]) "
                "ON CONFLICT (workos_user_id) DO UPDATE SET "
                "  tenant_id = EXCLUDED.tenant_id, email = EXCLUDED.email, "
                "  first_name = EXCLUDED.first_name, last_name = EXCLUDED.last_name, "
                "  enabled = true, type = ARRAY['staff']::text[] "
                "RETURNING id"
            ),
            {
                "uid": new_id(),
                "t": tenant_id,
                "email": email,
                "first": first_name,
                "last": last_name,
                "wos": workos_user_id,
            },
        )
    ).scalar_one()
    for portal in ("platform", "operations"):
        await s.execute(
            text(
                "INSERT INTO auth.organisation_memberships "
                "  (user_id, tenant_id, role_id, portal, status) "
                "VALUES (:u, :t, :r, :p, 'active') "
                "ON CONFLICT (user_id, tenant_id, portal) DO UPDATE SET "
                "  role_id = EXCLUDED.role_id, status = 'active'"
            ),
            {"u": user_id, "t": tenant_id, "r": role_id, "p": portal},
        )
    return str(user_id)


async def bootstrap(
    s: AsyncSession,
    *,
    tenant_alias: str = TENANT_ALIAS,
    tenant_name: str = TENANT_NAME,
    workos_user_id: str = WORKOS_USER_ID,
    email: str = USER_EMAIL,
    first_name: str = USER_FIRST,
    last_name: str = USER_LAST,
    role_slug: str = ROLE_SLUG,
    role_name: str = ROLE_NAME,
) -> dict[str, Any]:
    """Idempotent identity mirror. Caller must already be `service_role` (BYPASSRLS)."""
    email = fold_email(email)

    # 1. Tenant (idempotent on alias; lineage is set by the auth.lineage* triggers on insert).
    tenant_id = (
        await s.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name, enabled) "
                "VALUES (:uid, :alias, :name, true) "
                "ON CONFLICT (alias) DO UPDATE SET name = EXCLUDED.name "
                "RETURNING id"
            ),
            {
                "uid": tenant_uid(),
                "alias": tenant_alias,
                "name": tenant_name,
            },
        )
    ).scalar_one()

    # 2.5. Clone the model tenant's roles (Platform + Client) — matches what real tenant
    # provisioning does, so the dev tenant has the same role set (e.g. "Advised Client") the
    # team/[client] pages expect, instead of only the one ad-hoc role step 3 adds below.
    cloned = await clone_model_roles(s, tenant_id)

    # 3. Platform-staff role (idempotent on (tenant_id, slug)).
    role_id = (
        await s.execute(
            text(
                "INSERT INTO auth.roles (tenant_id, slug, name, type, portal, description) "
                "VALUES (:t, :slug, :name, 'organisation', 'platform', :desc) "
                "ON CONFLICT (tenant_id, slug) DO UPDATE SET name = EXCLUDED.name "
                "RETURNING id"
            ),
            {
                "t": tenant_id,
                "slug": role_slug,
                "name": role_name,
                "desc": "Dev owner role — mirrors all permissions.",
            },
        )
    ).scalar_one()

    # 4. Grant the role every permission (including `system`). The operations slugs
    # (tenants, platforms, aml, system, …) are not all inserted by migrations;
    # `sync_permissions` writes them on API startup. A reset that bootstraps before
    # that startup would otherwise grant only the SQL seed, and the admin sidebar
    # would be missing every operations-gated row. Sync first.
    from app.permissions import sync_permissions

    await sync_permissions(s)
    # Live session permissions are minted from this mirror via auth.membership_roles.
    granted = (
        await s.execute(
            text(
                "INSERT INTO auth.role_permissions (role_id, permission_id) "
                "SELECT :r, p.id FROM auth.permissions p "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            ),
            {"r": role_id},
        )
    ).rowcount

    # 5. The owner. App-only portal (0105): a membership row per portal IS the access — platform
    # AND operations, so both the platform portal and the admin portal admit them.
    user_id = await ensure_staff_user(
        s,
        tenant_id=int(tenant_id),
        role_id=int(role_id),
        workos_user_id=workos_user_id,
        email=email,
        first_name=first_name,
        last_name=last_name,
    )

    return {
        "tenant_id": int(tenant_id),
        "user_id": user_id,
        "role_id": int(role_id),
        "email": email,
        "granted": int(granted or 0),
        "cloned": int(cloned),
    }


async def main() -> None:
    engine = create_async_engine(settings.migration_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))  # BYPASSRLS for provisioning
        result = await bootstrap(s)
        extras = [user for user in EXTRA_USERS if user["workos_user_id"] != WORKOS_USER_ID]
        for user in extras:
            await ensure_staff_user(
                s,
                tenant_id=result["tenant_id"],
                role_id=result["role_id"],
                **user,
            )

    await engine.dispose()
    also = "".join(
        f", user {fold_email(user['email'])} ({user['workos_user_id']})" for user in extras
    )
    print(
        f"Bootstrapped tenant '{TENANT_ALIAS}' (id {result['tenant_id']}), user {result['email']} "
        f"({WORKOS_USER_ID}){also}, role '{ROLE_SLUG}' (+{result['granted']} permission grants). "
        f"Cloned {result['cloned']} role(s) from the model tenant"
        f"{' (none configured -- see /admin/model)' if result['cloned'] == 0 else ''}."
    )


if __name__ == "__main__":
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
