"""Seed the full legacy onboarding (forms + terms + tiered flow) onto a tenant.

    PYTHONPATH=. uv run python scripts/seed_onboarding.py                      # default dev login
    PYTHONPATH=. uv run python scripts/seed_onboarding.py --tenant-uid ten_ab12
    PYTHONPATH=. uv run python scripts/seed_onboarding.py --sub user_01ABC...
    PYTHONPATH=. uv run python scripts/seed_onboarding.py --list
    PYTHONPATH=. uv run python scripts/seed_onboarding.py --reset             # drop + reseed

Idempotent: re-running is a no-op once the flow exists and its definition is unchanged. When the
definition HAS changed -- which is what happens after configuring ``COMPLYCUBE_API_KEY``, since it
decides the identity/AML provider and who the step is assigned to -- it publishes a NEW version. In-flight journeys stay pinned to the version they started on, so start a fresh journey to
see the change. ``--reset`` rebuilds from scratch and is for a tenant with nothing in flight.

The tenant is named explicitly (``--tenant-uid``) or resolved from a WorkOS login (``--sub``); the
default sub is a local dev login and will not exist on a deployed environment, hence ``--list``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.config import settings
from app.flows.onboarding_seed import FLOW_CODE, FORMS, seed_onboarding

#: A local dev login (gio's → Chester Rose). Only a default for convenience; on any other
#: deployment pass --tenant-uid or --sub, because a sub that does not exist here is a hard failure.
DEFAULT_SUB = "user_01KWF1SDX8WDJV4ZXWX2H1N3M1"


async def _reset(conn: AsyncConnection, tenant: int) -> None:
    codes = list(FORMS.keys())
    await conn.execute(
        text("DELETE FROM flows.flows WHERE tenant_id = :t AND code = :c"),
        {"t": tenant, "c": FLOW_CODE},
    )
    await conn.execute(
        text("DELETE FROM documents.forms WHERE tenant_id = :t AND code = ANY(:c)"),
        {"t": tenant, "c": codes},
    )
    await conn.execute(
        text("DELETE FROM documents.documents WHERE tenant_id = :t AND document_type = 'terms'"),
        {"t": tenant},
    )
    print(f"reset onboarding forms/terms/flow on tenant {tenant}")


async def _list(conn: AsyncConnection) -> None:
    """Every tenant and whether onboarding is published — so nobody seeds the wrong firm."""
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT t.uid, t.name, "
                    "       (f.current_published_version_id IS NOT NULL) AS published "
                    "FROM auth.tenants t "
                    "LEFT JOIN flows.flows f ON f.tenant_id = t.id AND f.code = :c "
                    "ORDER BY t.name"
                ),
                {"c": FLOW_CODE},
            )
        )
        .mappings()
        .all()
    )
    for r in rows:
        state = "published" if r["published"] else "not seeded"
        print(f"{r['uid']:<14} {state:<12} {r['name']}")


async def _resolve(conn: AsyncConnection, *, tenant_uid: str | None, sub: str) -> tuple[int, str]:
    if tenant_uid:
        row = (
            (
                await conn.execute(
                    text("SELECT id, name FROM auth.tenants WHERE uid = :u"),
                    {"u": tenant_uid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise SystemExit(f"no tenant with uid {tenant_uid!r} — run with --list")
        return int(row["id"]), str(row["name"])
    row = (
        (
            await conn.execute(
                text(
                    "SELECT t.id, t.name FROM auth.users u "
                    "JOIN auth.tenants t ON t.id = u.tenant_id "
                    "WHERE u.workos_user_id = :s LIMIT 1"
                ),
                {"s": sub},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise SystemExit(
            f"no user with workos_user_id {sub!r} on this deployment — "
            "pass --tenant-uid instead (see --list)"
        )
    return int(row["id"]), str(row["name"])


async def main(args: argparse.Namespace) -> None:
    engine = create_async_engine(settings.migration_database_url)
    async with engine.connect() as conn:
        await conn.begin()
        await conn.execute(text("SET ROLE service_role"))
        if args.list:
            await _list(conn)
            await engine.dispose()
            return
        tenant, name = await _resolve(conn, tenant_uid=args.tenant_uid, sub=args.sub)
        print(f"tenant {tenant} ({name})")
        if args.reset:
            await _reset(conn, tenant)
        result = await seed_onboarding(conn, tenant_id=tenant)
        await conn.commit()
        print(f"flow_uid={result['flow_uid']} created={result['created']}")
        print(f"forms={result['forms']}")
        print(f"terms={result['terms']}")
    await engine.dispose()


if __name__ == "__main__":
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-uid", help="seed this tenant (auth.tenants.uid)")
    parser.add_argument("--sub", default=DEFAULT_SUB, help="resolve the tenant from a WorkOS login")
    parser.add_argument("--reset", action="store_true", help="drop the flow/forms/terms first")
    parser.add_argument("--list", action="store_true", help="list tenants and exit")
    asyncio.run(main(parser.parse_args()))
