"""Apply the advice fact find (the three forms + the ``fact_find`` flow) to one tenant.

    PYTHONPATH=. uv run python scripts/seed_advice_journey.py --tenant-uid ten_ab12
    PYTHONPATH=. uv run python scripts/seed_advice_journey.py --sub user_01ABC...
    PYTHONPATH=. uv run python scripts/seed_advice_journey.py                      # default dev login
    PYTHONPATH=. uv run python scripts/seed_advice_journey.py --list
    PYTHONPATH=. uv run python scripts/seed_advice_journey.py --reset             # drop + reseed

This is the only way the fact find is written. It was built for one firm, so provisioning does
not call it and a new tenant does not get it. Same resolution and flags as
``scripts/seed_onboarding.py``. Idempotent: re-running is a no-op once the flow exists and its
definition is unchanged, and publishes a NEW version when the definition has moved on. In-flight
journeys stay pinned to the version they started on. ``--reset`` rebuilds from scratch and is for
a tenant with nothing in flight. A ``pension_transfer`` flow an earlier seed published is archived
on every run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.config import settings
from app.flows.advice_seed import FACT_FIND_FLOW, FORMS, seed_advice_journey

#: A local dev login (gio's → Chester Rose). Only a default for convenience; on any other
#: deployment pass --tenant-uid or --sub, because a sub that does not exist here is a hard failure.
DEFAULT_SUB = "user_01KWF1SDX8WDJV4ZXWX2H1N3M1"


async def _reset(conn: AsyncConnection, tenant: int) -> None:
    await conn.execute(
        text("DELETE FROM flows.flows WHERE tenant_id = :t AND code = :c"),
        {"t": tenant, "c": FACT_FIND_FLOW},
    )
    await conn.execute(
        text("DELETE FROM documents.forms WHERE tenant_id = :t AND code = ANY(:c)"),
        {"t": tenant, "c": list(FORMS.keys())},
    )
    print(f"reset advice-journey forms/flow on tenant {tenant}")


async def _list(conn: AsyncConnection) -> None:
    """Every tenant and whether the journey is published — so nobody seeds the wrong firm."""
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT t.uid, t.name, "
                    "       (ff.current_published_version_id IS NOT NULL) AS fact_find "
                    "FROM auth.tenants t "
                    "LEFT JOIN flows.flows ff ON ff.tenant_id = t.id AND ff.code = :f "
                    "ORDER BY t.name"
                ),
                {"f": FACT_FIND_FLOW},
            )
        )
        .mappings()
        .all()
    )
    for r in rows:
        flag = "published" if r["fact_find"] else "not seeded"
        print(f"{r['uid']:<14} fact find {flag:<12} {r['name']}")


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
        result = await seed_advice_journey(conn, tenant_id=tenant)
        await conn.commit()
        print(
            f"fact_find_uid={result['fact_find_uid']} created={result['created']} "
            f"updated={result['updated']} retired_pension_transfer={result['retired']}"
        )
        print(f"forms={result['forms']}")
    await engine.dispose()


if __name__ == "__main__":
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-uid", help="seed this tenant (auth.tenants.uid)")
    parser.add_argument("--sub", default=DEFAULT_SUB, help="resolve the tenant from a WorkOS login")
    parser.add_argument("--reset", action="store_true", help="drop the flow/forms first")
    parser.add_argument("--list", action="store_true", help="list tenants and exit")
    asyncio.run(main(parser.parse_args()))
