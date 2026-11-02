"""Queue and execute the same non-production tenant data job exposed in Operations.

Run:
  uv run --directory apps/api python scripts/reset_tenant_data.py ALIAS
  uv run --directory apps/api python scripts/reset_tenant_data.py ALIAS --seed-demo
  uv run --directory apps/api python scripts/reset_tenant_data.py ALIAS --seed-book
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import tenant_data
from app.config import settings


async def main(alias: str, kind: tenant_data.JobKind) -> None:
    engine = create_async_engine(settings.migration_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        tenant_id = (
            await session.execute(
                text("SELECT id FROM auth.tenants WHERE alias=:alias"), {"alias": alias}
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            raise SystemExit(f"No tenant with alias {alias!r}")
        await tenant_data.enqueue(
            session,
            tenant_id=int(tenant_id),
            kind=kind,
            confirm_alias=alias,
            requested_by=None,
        )
    await tenant_data.handle_once(maker)
    async with maker() as session:
        result = await tenant_data.latest(session, int(tenant_id))
    await engine.dispose()
    if result is None or result["status"] != "completed":
        raise SystemExit(result["error"] if result else "job disappeared")
    print(f"Tenant {alias!r} data job completed; provider refreshes remain paused.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("alias")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--seed-demo", action="store_true", help="full reset, then seed the demo book"
    )
    mode.add_argument(
        "--seed-book",
        action="store_true",
        help="replace only the demo clients, accounts, transactions and holdings",
    )
    args = parser.parse_args()
    kind: tenant_data.JobKind = (
        "seed_book" if args.seed_book else "reset_and_seed" if args.seed_demo else "reset"
    )
    asyncio.run(main(args.alias, kind))
