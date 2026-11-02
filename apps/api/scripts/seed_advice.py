"""Seed richer advice data for demos (dev only).

Fills the gaps that make the platform screens look empty: a Seccl platform + products,
a few discretionary models, client service types, adviser assignments, and account →
product/model/risk linkage. Portfolio values/holdings/transactions still come from the
lake (unchanged) — this only populates the OLTP entities the UI reads alongside them.

`ingest_seccl.py` lands every account as a GIA, so ``PRODUCT_MIX`` retypes named demo clients
onto a realistic wrapper spread (largest account first) and renames the account to match.
That is what gives the listings variety and gives the pension features (illustrations,
drawdown) a SIPP with a fund worth projecting.

Idempotent: check-then-insert for the reference rows (identity PKs, no natural unique key),
ON CONFLICT DO NOTHING for client_advisers, and plain UPDATEs for the contact/account columns. Safe to
re-run; never deletes.

Run:  PYTHONPATH=. uv run --directory apps/api python scripts/seed_advice.py [tenant_alias]
"""

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

DEFAULT_TENANT_ALIAS = "graphene"

# Advisers are staff logins (auth.users). Resolved by email so ids stay stable across envs.
ADVISERS = {
    "andy": "andy@grapheneplatforms.com",
    "craig": "craig@grapheneplatforms.com",
    "mark": "mark@grapheneplatforms.com",
}

# contact surname -> (service_type, adviser key). Drives service pills + the adviser column.
CLIENTS = {
    "Theo Caldwell": ("advised", "andy"),
    "Isla Fenwick": ("execution_only", "craig"),
    "Oscar Kingsley": ("advised", "craig"),
    "Max Oakden": ("advised", "andy"),
    "Freddie Elphick": ("execution_only", "mark"),
    "Leo Quennell": ("advised", "craig"),
    "Sophie Halloway": ("advised", "andy"),
    "Freddie Salcombe": ("execution_only", "mark"),
    "Rosie Vellacott": ("execution_only", "mark"),
}

# Discretionary models (name, risk_level). Assigned to accounts round-robin by risk.
MODELS = [
    ("Graphene Cautious", "cautious"),
    ("Graphene Balanced", "balanced"),
    ("Graphene Growth", "growth"),
]

# Wrapper mix per client, so the listings show a real spread of product types instead of a wall
# of GIAs. Applied to the client's accounts ordered by value DESCENDING, so the first wrapper
# listed lands on their largest account: a SIPP first means the pension features that hang off it
# (illustrations, drawdown) have a fund worth projecting. Accounts beyond the list stay GIA.
# Identity details Morgan Lloyd's `cam_createcontact` requires but the anonymised Seccl fixture
# doesn't carry, so a demo client can actually be pushed to ML (and then drawn down).
#
# DELIBERATELY FICTIONAL. `QQ` is the HMRC test prefix and is never issued to a real person, and
# the address is not a real one — this data reaches Clifton's DEV Dynamics, so it must be
# recognisable as test data by anyone who sees it there.
ML_IDENTITY: dict[str, dict[str, str]] = {
    "Halloway": {
        "ni": "QQ123456C",
        "line1": "1 Test House, Example Street",
        "city": "Bristol",
        "postcode": "BS1 1AA",
    },
}

PRODUCT_MIX: dict[str, list[str]] = {
    "Halloway": ["SIPP", "GIA"],
    "Oakden": ["SIPP", "ISA"],
    "Caldwell": ["ISA", "GIA"],
    "Quennell": ["SIPP"],
    "Fenwick": ["ISA"],
    "Kingsley": ["ISA", "GIA"],
    "Salcombe": ["SIPP"],
}


async def _get_or_create(s, select_sql, insert_sql, params):
    found = (await s.execute(text(select_sql), params)).scalar_one_or_none()
    if found is not None:
        return found
    return (await s.execute(text(insert_sql), params)).scalar_one()


async def main(tenant_alias: str) -> None:
    engine = create_async_engine(settings.migration_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))  # BYPASSRLS for seeding

        tenant_id = (
            await s.execute(text("SELECT id FROM auth.tenants WHERE alias=:a"), {"a": tenant_alias})
        ).scalar_one_or_none()
        if tenant_id is None:
            raise SystemExit(f"No tenant with alias '{tenant_alias}'")

        # 1) The custodian --------------------------------------------------
        # A VALUE, not a row. `platform.platforms` was dropped by 0138: it existed to be a
        # foreign-key target for a member of a closed vocabulary, and the vocabulary is
        # `platform.provider_code`. There is nothing to seed and nothing to resolve — the id every
        # caller keyed on IS the provider code.
        provider = "seccl"

        # 2) Products --------------------------------------------------------
        # TENANT-SCOPED, and the mapping is a `product_platforms` row (0044/0130). This used to
        # write `platform_id` on the product and no tenant at all, which made a GLOBAL shelf item:
        # `products_read`'s first branch exposes `tenant_id IS NULL` to every firm, so seeding a
        # dev book put three products on everybody's shelf.
        product_ids: dict[str, int] = {}
        for pt in ("GIA", "ISA", "SIPP"):
            product_ids[pt] = await _get_or_create(
                s,
                "SELECT id FROM platform.products WHERE tenant_id=:tid AND product_type=:t LIMIT 1",
                # `is_pension` seeded from the wrapper, not left to the column default (false): a
                # SIPP seeded unflagged reads as a non-pension, which hides the contribution,
                # crystallisation and safeguarding UI that gates on it. It stays editable on the
                # product for the wrapper whose answer differs (a TPPENSION we do not administer).
                "INSERT INTO platform.products "
                "  (tenant_id, product_type, name, code, enabled, is_pension) "
                "VALUES (:tid, CAST(:t AS platform.product_type), :n, CAST(:t AS text), "
                "        true, :pension) "
                "RETURNING id",
                {
                    "tid": tenant_id,
                    "t": pt,
                    "n": f"Seccl {pt}",
                    "pension": pt in {"SIPP", "SSAS", "PENSION"},
                },
            )
            await s.execute(
                text(
                    "INSERT INTO platform.product_platforms "
                    "  (tenant_id, product_id, provider, external_id, external_name, role) "
                    "SELECT :tid, :pr, CAST(:p AS platform.provider_code), :t, :t, 'custodian' "
                    # TWO unique indexes to respect, not one. `(product_id, provider)` is the
                    # upsert target, but 0130 also added a partial unique on
                    # `(product_id, role)` — one provider per role per product, because a shelf
                    # item custodied in two places makes "which account number do I send this to"
                    # ambiguous. A firm whose GIA is already custodied elsewhere is a real
                    # configuration, so this SKIPS rather than fights it: a seed must not decide
                    # who custodies a product that already has a custodian.
                    " WHERE NOT EXISTS ("
                    "   SELECT 1 FROM platform.product_platforms x "
                    "    WHERE x.product_id = :pr AND x.role = 'custodian') "
                    "ON CONFLICT (product_id, provider) DO NOTHING"
                ),
                {"tid": tenant_id, "pr": product_ids[pt], "p": provider, "t": pt},
            )

        # 3) Models ----------------------------------------------------------
        model_ids: dict[str, int] = {}
        for name, risk in MODELS:
            model_ids[risk] = await _get_or_create(
                s,
                "SELECT id FROM platform.models WHERE tenant_id=:t AND name=:n LIMIT 1",
                "INSERT INTO platform.models (provider, tenant_id, name, enabled) "
                "VALUES (CAST(:p AS platform.provider_code), :t, :n, true) RETURNING id",
                {"p": provider, "t": tenant_id, "n": name},
            )

        # 4) Advisers (resolve staff logins) --------------------------------
        adviser_ids: dict[str, str] = {}
        for key, email in ADVISERS.items():
            uid = (
                (
                    await s.execute(
                        text(
                            "SELECT id FROM auth.users WHERE tenant_id=:t AND email=:e "
                            "ORDER BY enabled DESC, created_at ASC LIMIT 1"
                        ),
                        {"t": tenant_id, "e": email},
                    )
                )
                .scalars()
                .first()
            )
            if uid is not None:
                adviser_ids[key] = uid

        # 5) Per-client: service_type + adviser assignment ------------------
        assigned = 0
        for name, (service, adviser_key) in CLIENTS.items():
            rows = (
                (
                    await s.execute(
                        text("SELECT id FROM crm.contacts WHERE tenant_id=:t AND name=:n"),
                        {"t": tenant_id, "n": name},
                    )
                )
                .scalars()
                .all()
            )
            for contact_id in rows:
                await s.execute(
                    text(
                        "UPDATE crm.contacts SET service_type=CAST(:s AS crm.service_type) "
                        "WHERE id=:c"
                    ),
                    {"s": service, "c": contact_id},
                )
                adviser_id = adviser_ids.get(adviser_key)
                if adviser_id is not None:
                    await s.execute(
                        text(
                            "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                            "VALUES (:t, :c, :a, 'adviser') "
                            # No conflict target: a client that already has a different adviser
                            # trips `client_advisers_one_adviser_key` (0245), and a re-run keeps
                            # whoever holds the role.
                            "ON CONFLICT DO NOTHING"
                        ),
                        {"t": tenant_id, "c": contact_id, "a": adviser_id},
                    )
                    assigned += 1

        # 5.5) ML-required identity details (fictional — see ML_IDENTITY) ----
        # Only fills blanks: never overwrites a real NI number or address that has been entered.
        identified = 0
        for surname, details in ML_IDENTITY.items():
            identified += (
                await s.execute(
                    text(
                        "UPDATE crm.contacts SET "
                        "  national_identifier = COALESCE(NULLIF(national_identifier, ''), :ni), "
                        "  national_identifier_type = "
                        "    COALESCE(NULLIF(national_identifier_type, ''), 'nino'), "
                        # Casts are required: Postgres can't infer a parameter's type inside
                        # jsonb_build_object, so it errors with "could not determine data type".
                        "  address = COALESCE(address, jsonb_build_object("
                        "    'line1', CAST(:line1 AS text), 'city', CAST(:city AS text), "
                        "    'postcode', CAST(:postcode AS text), 'country', 'GB')) "
                        "WHERE tenant_id = :t AND surname = :s AND deleted_at IS NULL"
                    ),
                    {"t": tenant_id, "s": surname, **details},
                )
            ).rowcount

        # 6) Accounts: product + model + risk --------------------------------
        # Deterministic model spread by contact_id parity so the demo shows variety.
        risks = ["cautious", "balanced", "growth"]
        await s.execute(
            text(
                "UPDATE platform.accounts SET product_id=:gia WHERE tenant_id=:t AND product_id IS NULL"
            ),
            {"gia": product_ids["GIA"], "t": tenant_id},
        )

        # Wrapper mix (PRODUCT_MIX). The account NAME is rewritten with the product too —
        # ingest_seccl names accounts "<client> GIA", so converting the product without the name
        # would leave a SIPP called "Sophie Halloway GIA".
        retyped = 0
        for surname, wrappers in PRODUCT_MIX.items():
            accounts = (
                (
                    await s.execute(
                        text(
                            "SELECT a.id, "
                            "       trim(concat_ws(' ', c.first_name, c.surname)) AS client_name "
                            "FROM platform.accounts a "
                            "JOIN crm.contacts c ON c.id = a.contact_id "
                            "WHERE a.tenant_id = :t AND c.surname = :s AND c.deleted_at IS NULL "
                            "ORDER BY a.value DESC NULLS LAST, a.id"
                        ),
                        {"t": tenant_id, "s": surname},
                    )
                )
                .mappings()
                .all()
            )
            for row, wrapper in zip(accounts, wrappers, strict=False):
                await s.execute(
                    text("UPDATE platform.accounts SET product_id = :p, name = :n WHERE id = :a"),
                    {
                        "p": product_ids[wrapper],
                        "n": f"{row['client_name'] or surname} {wrapper}".strip(),
                        "a": row["id"],
                    },
                )
                retyped += 1
        for i, risk in enumerate(risks):
            await s.execute(
                text(
                    "UPDATE platform.accounts SET model_id=:m, risk_level=:r "
                    "WHERE tenant_id=:t AND mod(contact_id, 3) = :i"
                ),
                {"m": model_ids[risk], "r": risk, "t": tenant_id, "i": i},
            )

    await engine.dispose()
    print(
        f"Seeded advice data into '{tenant_alias}' (tenant {tenant_id}): "
        f"platform={provider}, products={len(product_ids)}, models={len(model_ids)}, "
        f"adviser assignments={assigned}, wrappers retyped={retyped}, "
        f"ML identity filled={identified}."
    )


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    alias = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TENANT_ALIAS
    asyncio.run(main(alias))
