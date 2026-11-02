"""Seed sample client-portal users into a local tenant (dev only).

Creates a handful of enabled client-portal members (auth.users + a portal='client'
organisation_membership) so the documents surfaces that pick a client — Send, Request an upload,
Assign a task — have real people to choose from. Names mirror the mock `clients` in the platform
demo module, so the seeded data lines up with the rest of the sample content.

Idempotent: upserts on (tenant_id, email); safe to re-run (never deletes, so it won't cascade away
any documents already linked to these clients).

Run:  uv run --directory apps/api python scripts/seed_clients.py [tenant_alias]
Requires: migrations applied + a local Postgres (pnpm db:up).
"""

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

DEFAULT_TENANT_ALIAS = "graphene"

# (first name, last name, email) — mirrors the platform demo `clients`.
SAMPLES = [
    ("James", "Mitchell", "j.mitchell@example.com"),
    ("Margaret", "Cole", "m.cole@example.com"),
    ("Priya", "Nair", "p.nair@example.com"),
    ("Daniel", "Whitlock", "d.whitlock@example.com"),
    ("Eleanor", "Voss", "e.voss@example.com"),
    ("Tom", "Fletcher", "t.fletcher@example.com"),
]


async def main(tenant_alias: str) -> None:
    engine = create_async_engine(settings.migration_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))  # BYPASSRLS for seeding

        tenant_id = (
            await s.execute(
                text("SELECT id FROM auth.tenants WHERE alias = :a"), {"a": tenant_alias}
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            raise SystemExit(f"No tenant with alias '{tenant_alias}'")

        for first, last, email in SAMPLES:
            uid = f"usr_seedclient_{email.split('@')[0].replace('.', '_')}"
            user_id = (
                await s.execute(
                    text(
                        "INSERT INTO auth.users "
                        "  (uid, tenant_id, email, first_name, last_name, enabled) "
                        "VALUES (:uid, :t, :email, :first, :last, true) "
                        "ON CONFLICT (tenant_id, email) DO UPDATE "
                        "  SET first_name = EXCLUDED.first_name, "
                        "      last_name = EXCLUDED.last_name, enabled = true "
                        "RETURNING id"
                    ),
                    {"uid": uid, "t": tenant_id, "email": email, "first": first, "last": last},
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO auth.organisation_memberships "
                    "  (user_id, tenant_id, portal, status) "
                    "VALUES (:u, :t, 'client', 'active') "
                    "ON CONFLICT (user_id, tenant_id, portal) DO UPDATE SET status = 'active'"
                ),
                {"u": user_id, "t": tenant_id},
            )

    await engine.dispose()
    print(f"Seeded {len(SAMPLES)} client users into tenant '{tenant_alias}' (id {tenant_id}).")


if __name__ == "__main__":
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    alias = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TENANT_ALIAS
    asyncio.run(main(alias))
