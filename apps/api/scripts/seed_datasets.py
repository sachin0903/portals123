"""Seed a few sample datasets into a local tenant (dev only).

Gives the datasets listing (and the template link dialog / create wizard) real, RLS-backed rows to
browse: three ``inline`` datasets carrying sample rows, so the detail page's data preview is real
and the template editor's "smart" tables have a source to bind to. Idempotent: seeded rows are
keyed by code and cleared before re-seeding.

It seeds no per-client dataset and needs none — the provided entries in
``app/render/catalogue.py`` are code-side, so every tenant has them with no row at all.

Run:  uv run --directory apps/api python scripts/seed_datasets.py [tenant_alias]
Requires: migrations applied. No object storage needed.
"""

import asyncio
import json
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.ids import new_id
from app.render import catalogue

DEFAULT_TENANT_ALIAS = "graphene"

# (code, name, format, scope, description, inline_data | catalogue_ref)
#   - inline    → literal rows shown on the detail page
#   - catalogue → a Graphene-provided query with the firm's chosen settings
#
# NOTHING HERE SEEDS A PER-CLIENT DATASET, and it does not need to: the provided entries
# (`book_valuations`, `book_transactions`, …) are code-side, so every tenant already has them with
# no row at all. The one sample that used to be per-client was a `sql` dataset reading a
# `ledger.holdings` table this script created itself — and it granted `authenticated` SELECT on
# that table, putting a readable relation outside the RLS model into every dev database purely to
# give a fixture something to query.
SAMPLES: list[dict[str, object]] = [
    {
        "code": "isa_allowances",
        "name": "ISA Annual Allowances",
        "format": "inline",
        "scope": "global",
        "description": "HMRC ISA subscription limits by tax year.",
        "inline_data": {
            "columns": ["Tax year", "ISA limit", "JISA limit"],
            "rows": [
                ["2024/25", "£20,000", "£9,000"],
                ["2025/26", "£20,000", "£9,000"],
                ["2026/27", "£20,000", "£9,000"],
            ],
        },
    },
    {
        "code": "projection_assumptions",
        "name": "Projection Assumptions",
        "format": "inline",
        "scope": "global",
        "description": "FCA-aligned projection rates and ongoing charge assumptions for COBS 13 "
        "illustrations. Reviewed annually.",
        "inline_data": {
            "columns": ["Scenario", "Growth rate", "Charge"],
            "rows": [
                ["Low", "2.0%", "0.75%"],
                ["Mid", "5.0%", "0.75%"],
                ["High", "8.0%", "0.75%"],
            ],
        },
    },
    {
        # PLACEHOLDER projection figures until the projection engine (app/projections.py) is wired —
        # CTC only returns a PDF, so no real projected values are query-able yet. The column KEYS
        # (age/lower/mid/higher) match the template editor's Projection smart-table exprs (row.age …)
        # so binding it renders these rows; the pretty headers come from the smart-table itself.
        "code": "projections",
        "name": "Retirement Projection (sample)",
        "format": "inline",
        "scope": "global",
        "description": "Sample projected fund value at retirement by illustrative growth rate. "
        "Placeholder figures — not a real illustration — until the projection engine is wired.",
        "inline_data": {
            "columns": ["age", "lower", "mid", "higher"],
            "rows": [
                ["60", "£142,000", "£168,000", "£198,000"],
                ["65", "£158,000", "£214,000", "£292,000"],
                ["67", "£165,000", "£236,000", "£340,000"],
            ],
        },
    },
]


async def main(tenant_alias: str) -> None:
    engine = create_async_engine(settings.migration_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))  # BYPASSRLS for seeding

        row = (
            await s.execute(
                text("SELECT id FROM auth.tenants WHERE alias = :a"), {"a": tenant_alias}
            )
        ).one_or_none()
        if row is None:
            raise SystemExit(f"No tenant with alias '{tenant_alias}'")
        tenant_id = int(row[0])
        actor = (
            await s.execute(
                text("SELECT id FROM auth.users WHERE tenant_id = :t ORDER BY id LIMIT 1"),
                {"t": tenant_id},
            )
        ).scalar_one_or_none()
        if actor is None:
            raise SystemExit(f"Tenant '{tenant_alias}' has no users to attribute the seed to.")

        codes = [d["code"] for d in SAMPLES]
        # Idempotent: cascade-drop any prior seed of these codes (removes their versions too).
        await s.execute(
            text("DELETE FROM documents.datasets WHERE tenant_id = :t AND code = ANY(:codes)"),
            {"t": tenant_id, "codes": codes},
        )

        for d in SAMPLES:
            dataset_id = (
                await s.execute(
                    text(
                        "INSERT INTO documents.datasets "
                        "  (uid, tenant_id, code, name, description, format, scope, created_by) "
                        "VALUES (:uid, :t, :code, :name, :desc, "
                        # text() can't parse a bind followed by a `::` cast, so use CAST().
                        "  CAST(:fmt AS documents.dataset_format), "
                        "  CAST(:scope AS documents.dataset_scope), :by) "
                        "RETURNING id"
                    ),
                    {
                        "uid": new_id(),
                        "t": tenant_id,
                        "code": d["code"],
                        "name": d["name"],
                        "desc": d.get("description"),
                        "fmt": d["format"],
                        "scope": d["scope"],
                        "by": actor,
                    },
                )
            ).scalar_one()

            await s.execute(
                text(
                    "INSERT INTO documents.dataset_versions "
                    "  (uid, tenant_id, dataset_id, version_number, status, format, "
                    "   inline_data, catalogue_ref, created_by, published_at) "
                    "VALUES (:uid, :t, :did, 1, 'published', "
                    "  CAST(:fmt AS documents.dataset_format), "
                    "  CAST(:inline AS jsonb), CAST(:ref AS jsonb), :by, now())"
                ),
                {
                    "uid": new_id(),
                    "t": tenant_id,
                    "did": dataset_id,
                    "fmt": d["format"],
                    "inline": json.dumps(d["inline_data"]) if d.get("inline_data") else None,
                    "ref": json.dumps(d["catalogue_ref"]) if d.get("catalogue_ref") else None,
                    "by": actor,
                },
            )

    await engine.dispose()
    print(
        f"Seeded {len(SAMPLES)} datasets into tenant '{tenant_alias}' (id {tenant_id}). "
        f"The provided catalogue ({', '.join(sorted(catalogue.keys()))}) needs no seeding — "
        "entries are code-side and every tenant already has them."
    )


if __name__ == "__main__":
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    alias = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TENANT_ALIAS
    asyncio.run(main(alias))
