"""Make every EMPTY state reachable on a dev box, without emptying anything real (dev only).

An empty state is the one part of the UI that a seeded database hides. Seeding exists to make the
product look alive, so the surfaces a firm meets on its FIRST DAY — and the ones it meets whenever a
filter matches nothing, a permission is missing or a read fails — are exactly the ones nobody sees
while building. That is why they rot: 22 of this estate's 93 empty states shipped with no
description and no action at all, and four listings told a firm with 379 accounts that it had none.

This is `seed_ui_edge_cases.py`'s sibling. That one makes the UGLY cases reproducible on demand
(89-character fund names, four advisers, 24 uncategorised credits); this one makes the ABSENT cases
reproducible, on rows that exist only for that purpose, so neither has to be produced by breaking
the seeded book.

WHAT IT CREATES, all idempotent and all additive:

1. **An empty FIRM** (`empty-firm`). Zero clients, accounts, documents, tasks, messages — zero of
   everything. Every LISTING's "nothing yet" at once, and reachable without provisioning WorkOS for
   it, because the operations portal reads any tenant's book by uid (`/tenants/{uid}/book/...`).
   Signing IN as that firm would need its own WorkOS project, which this cannot mint.

2. **A bare CLIENT** on the dev tenant. No accounts, no documents, no tasks, no messages, no
   adviser, no beneficiaries, no screening. One client record with every panel in its empty state
   on one screen — which is the view that shows whether the panels agree with each other.

3. **A bare ACCOUNT** on that client. No transactions, no fees, no bank details, no regular
   payments in or out, no beneficiaries, and no product parent — the last being the real state of
   98 of 379 accounts on the dev book, not an invented one.

WHAT IT DELIBERATELY DOES NOT CREATE: the `unavailable` kind. A read that FAILED cannot be seeded,
because it is the absence of an answer rather than an absence in the data — and on a dev box
`crm._firm_read` degrades a failed lake read to the committed fixture on purpose, so it cannot even
be provoked by deleting a schema. That kind is covered by tests, and by pointing a loader at a dead
URL for as long as it takes to look.

Run:  PYTHONPATH=. uv run --directory apps/api python scripts/seed_empty_states.py
Env:  DEV_TENANT_ALIAS (default `graphene`), EMPTY_FIRM_NAME, KEEP (=1 to report only).
"""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.getcwd())

from app import contacts as contacts_mod  # noqa: E402
from app import provisioning  # noqa: E402
from app.config import settings  # noqa: E402
from app.ids import new_id, tenant_uid  # noqa: E402

TENANT_ALIAS = os.getenv("DEV_TENANT_ALIAS", "graphene")
EMPTY_ALIAS = "empty-firm"
EMPTY_NAME = os.getenv("EMPTY_FIRM_NAME", "Empty Firm Ltd")
REPORT_ONLY = os.getenv("KEEP") == "1"

#: The bare client's name. Recognisable on a listing, and says why it is there.
BARE_CLIENT = ("Nothing", "Yet")
BARE_ACCOUNT = "Bare account (no data)"


async def main() -> int:
    engine = create_async_engine(settings.migration_database_url or settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as s, s.begin():
        # Every table here is FORCE-RLS and this session carries no claims, so without the role it
        # would see no tenants, create nothing, and report success — the failure mode the estate
        # documents for migrations and which applies just as well to a seed.
        await s.execute(text("SET LOCAL ROLE service_role"))

        dev = (
            await s.execute(
                text("SELECT id, uid, name FROM auth.tenants WHERE alias = :a"),
                {"a": TENANT_ALIAS},
            )
        ).one_or_none()
        if dev is None:
            print(
                f"no tenant with alias {TENANT_ALIAS!r} — run scripts/bootstrap_dev_tenant.py first"
            )
            await engine.dispose()
            return 1

        # ── 1. the empty firm ────────────────────────────────────────────────────────────────────
        empty = (
            await s.execute(
                text("SELECT id, uid FROM auth.tenants WHERE alias = :a"), {"a": EMPTY_ALIAS}
            )
        ).one_or_none()
        if empty is None and not REPORT_ONLY:
            empty = (
                await s.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name, enabled) "
                        "VALUES (:uid, :alias, :name, true) RETURNING id, uid"
                    ),
                    {"uid": tenant_uid(), "alias": EMPTY_ALIAS, "name": EMPTY_NAME},
                )
            ).one()
            print(f"created the empty firm: {EMPTY_NAME} ({empty.uid})")
        elif empty is not None:
            print(f"empty firm already present: {empty.uid}")

        # AND ITS ROLES, because a real new firm has them. `create_tenant` clones the model tenant's
        # roles on creation; this script writes the row directly (it must not reach WorkOS), so
        # without this the "empty firm" is emptier than any firm can actually be — and the roles
        # page would show an empty state no real firm ever meets, which is worse than useless for
        # reviewing empty states: it is a state the product does not have.
        if empty is not None and not REPORT_ONLY:
            existing = (
                await s.execute(
                    text("SELECT count(*) FROM auth.roles WHERE tenant_id = :t"), {"t": empty.id}
                )
            ).scalar_one()
            if existing == 0:
                cloned = await provisioning.clone_model_roles(s, empty.id)
                print(f"cloned {cloned} role(s) from the model tenant, as tenant creation does")

        # ── 2. the bare client ───────────────────────────────────────────────────────────────────
        bare = (
            await s.execute(
                text(
                    "SELECT id, uid FROM crm.contacts "
                    "WHERE tenant_id = :t AND first_name = :f AND surname = :l"
                ),
                {"t": dev.id, "f": BARE_CLIENT[0], "l": BARE_CLIENT[1]},
            )
        ).one_or_none()
        if bare is None and not REPORT_ONLY:
            # THE SERVICE, not an INSERT: it is what the product runs, so the row carries whatever
            # a real client carries today and cannot drift from it. `created_by_workos` is left out
            # deliberately — no adviser on the book is one of the empty panels being tested.
            created = await contacts_mod.create_contact(
                s,
                tenant_id=dev.id,
                type="individual",
                first_name=BARE_CLIENT[0],
                surname=BARE_CLIENT[1],
            )
            bare = (
                await s.execute(
                    text("SELECT id, uid FROM crm.contacts WHERE uid = :u"),
                    {"u": created["uid"]},
                )
            ).one()
            print(f"created the bare client: {' '.join(BARE_CLIENT)} ({bare.uid})")
        elif bare is not None:
            print(f"bare client already present: {bare.uid}")

        # ── 3. the bare account ──────────────────────────────────────────────────────────────────
        if bare is not None:
            acct = (
                await s.execute(
                    text(
                        "SELECT id, uid FROM platform.accounts "
                        "WHERE tenant_id = :t AND contact_id = :c AND name = :n"
                    ),
                    {"t": dev.id, "c": bare.id, "n": BARE_ACCOUNT},
                )
            ).one_or_none()
            if acct is None and not REPORT_ONLY:
                # No product parent, no model, no fee package, no value: every optional column left
                # as it arrives for an account the pipeline has discovered and not yet resolved.
                acct = (
                    await s.execute(
                        text(
                            "INSERT INTO platform.accounts "
                            "  (tenant_id, contact_id, uid, name, status, currency) "
                            "VALUES (:t, :c, :uid, :n, 'active', 'GBP') RETURNING id, uid"
                        ),
                        {"t": dev.id, "c": bare.id, "uid": new_id(), "n": BARE_ACCOUNT},
                    )
                ).one()
                print(f"created the bare account: {BARE_ACCOUNT} ({acct.uid})")
            elif acct is not None:
                print(f"bare account already present: {acct.uid}")

        # ── the checklist ────────────────────────────────────────────────────────────────────────
        empty_uid = empty.uid if empty else "(not created)"
        bare_uid = bare.uid if bare else "(not created)"
        print(
            f"""
WHAT TO LOOK AT (platform :5185, operations :5184, both on portals2.localhost)

  NOTHING YET — every listing, on a firm that has nothing:
    operations  /tenants/{empty_uid}/book/clients
                /tenants/{empty_uid}/book/accounts
                /tenants/{empty_uid}/book/transactions
    Each should name what is missing and offer the one action that fixes it — not "No rows."

  NOTHING YET — every panel of one client record at once:
    platform    /clients/{bare_uid}
    Accounts, documents, tasks, messages, beneficiaries, advisers, screening: all empty, on one
    screen. They should agree with each other in voice and in shape.

  NOTHING YET — an account with no movements and no product:
    platform    /accounts/{acct.uid if bare and acct else "(not created)"}
    Overview (bank details, fees, regular payments), holdings, transactions — and the product line,
    which is the state 98 of 379 dev accounts are really in.

  NOTHING MATCHES — needs no data, only a filter:
    platform    /accounts, /transactions, /clients: type a string that matches nothing.
    The message must mention the FILTER and offer to clear it. Saying "none yet" here is the bug
    this seed exists to make visible.

  ALREADY EMPTY ON THIS BOX, no seeding needed:
    contributions, transfers, journeys, flows, document requests, products, fee packages,
    content, AML profiles, beneficiaries, provider enablements.

  NOT SEEDABLE — the read that FAILED. `crm._firm_read` degrades to the committed lake fixture on a
  dev box by design, so this kind cannot be provoked with data. Point a loader at a dead URL for as
  long as it takes to look, and rely on the tests otherwise."""
        )

    await engine.dispose()
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
