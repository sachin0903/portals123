"""Seed the ADVERSARIAL cases the book's UI is supposed to survive (dev only).

Every one of these is a defect we actually shipped and then fixed, and each was invisible on the
seeded book because the seeded book is polite: short names, one adviser, a handful of rows. The
containment work in particular (`pkgs/ui/src/lib/text-shell.ts`) is only exercised by content long
enough to overflow, so a table that clips correctly and a table with no ceiling at all look
identical until someone's fund name is 89 characters.

So this makes the ugly cases reproducible on demand, on ONE deliberately-awful client, rather than
waiting for a real firm to surface them:

  1. LONG STRINGS IN TABLES — a trust with a 74-character name, an account name longer than its
     column, a fund name at the real 89-character ceiling, and a client with four advisers (the
     `advisers.join(', ')` that text-shell.ts names as the widest thing in several listings).
  2. A CROWDED ACTION-REQUIRED STRIP — 24 uncategorised pension credits on one client, so the
     record's strip caps at six and offers "+18 more" instead of burying the record.
  3. THE LONG STATUS — contributions at `awaiting_confirmation`, which renders "Awaiting
     classification": 23 characters in a nowrap badge, the one that forced the contributions
     workbench wider than its container.
  4. A PHONE THAT IS AN OBJECT — Seccl publishes `{"number", "isMobile", "locale"}` and the text
     column took it whole on any book merged before that was fixed. `app.contacts.phone_expr`
     extracts it on read; this is the row that proves it, and proves a normal number beside it is
     returned untouched.
  5. MANY NOTES — 14 on one client, past the notes card's page of five, one of them a wall of text
     with no line breaks.
  6. LOADS OF OPEN DOCUMENT REQUESTS — 18 pending, several with long titles, feeding both the
     `/documents/requests` listing and the DASHBOARD's action-items panel, which merges tasks and
     requests into one urgency-ordered queue and renders only its first few. A queue reliably
     longer than its panel is what proves the cap and its overflow line work.

Idempotent: every insert is keyed and re-runnable, and `--clear` removes exactly what this wrote
(everything hangs off the one contact, found by its uid).

Run:  uv run --directory apps/api python scripts/seed_ui_edge_cases.py [tenant_alias] [--clear]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

DEFAULT_TENANT_ALIAS = "graphene"

#: Stable uid so the whole fixture can be found and removed again. Not a nanoid on purpose — it is
#: recognisable in a listing, which is the point of a fixture you go looking for.
CONTACT_UID = "uiedgecase01"

#: 74 characters. Real organisations are named like this; `CELL_NAME`'s 14rem ceiling and the
#: identity column's `CELL_SPINE` are what stop one of them setting every row's width.
ORG_NAME = "The Wetherington-Smythe Family Discretionary Settlement Trust 2018 (No. 2)"

#: 89 characters — the longest fund name measured in the committed Seccl extract, which is why
#: `CELL_INSTRUMENT` exists and is described as the one ceiling that earns its keep.
FUND_NAME = (
    "Vanguard FTSE Developed World ex-UK Equity Index Fund GBP Accumulation Hedged Share Class"
)

#: The stale Seccl shape, stored verbatim in a text column exactly as a pre-fix merge left it.
PHONE_OBJECT = '{"number": "07700900123", "isMobile": true, "locale": "en-GB"}'

#: Long enough to be clipped by the notes card's 2-line clamp, with no line breaks to help it.
WALL_OF_TEXT = (
    "Client called about the transfer and we went through the whole history again: the ceding "
    "scheme has asked for the discharge forms a second time, the trustees want the valuation "
    "restated as at the end of the month rather than the date of instruction, and the adviser "
    "has agreed to hold the rebalance until the cash lands so the model is not applied twice. "
    "Follow up on Monday and confirm in writing."
)

ADVISER_NAMES = ("Craig Currie", "Gio Maxwell", "Sachin Sharma", "Persephone Wainwright-Hollis")


async def _tenant(s: AsyncSession, alias: str) -> int:
    row = (
        await s.execute(text("SELECT id FROM auth.tenants WHERE alias = :a"), {"a": alias})
    ).scalar_one_or_none()
    if row is None:
        raise SystemExit(f"No tenant with alias '{alias}'")
    return int(row)


async def _clear(s: AsyncSession, tenant: int) -> None:
    """Remove exactly what this script wrote. Everything hangs off the one contact."""
    contact = (
        await s.execute(
            text("SELECT id FROM crm.contacts WHERE tenant_id = :t AND uid = :u"),
            {"t": tenant, "u": CONTACT_UID},
        )
    ).scalar_one_or_none()
    if contact is None:
        print("nothing to clear.")
        return
    # Request ITEMS reference the request, so they go first.
    await s.execute(
        text(
            "DELETE FROM documents.request_items WHERE request_id IN "
            "(SELECT id FROM documents.requests WHERE contact_id = :c)"
        ),
        {"c": contact},
    )
    for table, column in (
        ("documents.requests", "contact_id"),
        ("platform.contributions", "contact_id"),
        ("crm.contact_notes", "contact_id"),
        ("crm.client_advisers", "contact_id"),
    ):
        await s.execute(
            text(f"DELETE FROM {table} WHERE {column} = :c"),  # noqa: S608 - fixed names
            {"c": contact},
        )
    # Transactions reference the account, so they go before it.
    await s.execute(
        text(
            "DELETE FROM platform.transactions WHERE account_id IN "
            "(SELECT id FROM platform.accounts WHERE contact_id = :c)"
        ),
        {"c": contact},
    )
    await s.execute(text("DELETE FROM platform.accounts WHERE contact_id = :c"), {"c": contact})
    await s.execute(text("DELETE FROM crm.contacts WHERE id = :c"), {"c": contact})
    await s.execute(text("DELETE FROM platform.instruments WHERE name = :n"), {"n": FUND_NAME})
    print("cleared the edge-case fixture.")


async def seed(s: AsyncSession, tenant: int) -> None:
    # --- 1. the client: an organisation with a very long name, and a number that is an object ---
    contact = (
        await s.execute(
            text(
                "INSERT INTO crm.contacts "
                "  (uid, tenant_id, type, service_type, name, email, mobile, enabled) "
                "VALUES (:uid, :t, 'organisation', 'advised', :name, :email, :phone, true) "
                "ON CONFLICT (uid) DO UPDATE "
                "  SET name = EXCLUDED.name, mobile = EXCLUDED.mobile, "
                "      email = EXCLUDED.email "
                "RETURNING id"
            ),
            {
                "uid": CONTACT_UID,
                "t": tenant,
                "name": ORG_NAME,
                "email": "trustees.correspondence.department@wetherington-smythe-trustees.example",
                # The stale provider object. `phone_expr` must read 07700900123 out of this, and
                # must leave every ordinary number on the book untouched.
                "phone": PHONE_OBJECT,
            },
        )
    ).scalar_one()

    # --- 2. a team of four, so the client record's team list has no upper bound ----------------
    # One adviser per client (0245): the first is the adviser if they may be one, everyone else is
    # support. An `is_adviser` login sorts first so the client does get an adviser.
    advisers = (
        (
            await s.execute(
                text(
                    "SELECT id, name, is_adviser FROM auth.users "
                    "WHERE tenant_id = :t AND name IS NOT NULL "
                    "ORDER BY is_adviser DESC, id LIMIT 4"
                ),
                {"t": tenant},
            )
        )
        .mappings()
        .all()
    )
    for i, adviser in enumerate(advisers):
        await s.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :c, :a, :role) ON CONFLICT DO NOTHING"
            ),
            {
                "t": tenant,
                "c": contact,
                "a": adviser["id"],
                "role": "adviser" if i == 0 and adviser["is_adviser"] else "support",
            },
        )
    print(f"team assigned: {len(advisers)} (the team list is what grows)")

    # --- 3. the account: a name longer than its column, on a pension so credits need classifying
    # The account MUST carry a pension product or the 24 credits below are invisible: the API derives
    # `needs_categorisation` from the product type, so with no product there is no Action-required
    # strip at all — the fixture looks like it did not seed. Learned the hard way.
    #
    # `tenant_id IS NULL` is in the filter because a tenant-scoped filter alone finds nothing here:
    # products only became tenant-scoped in 0042, and this database still holds the legacy GLOBAL
    # "Seccl SIPP" with a null tenant. Prefer the firm's own, fall back to the legacy row.
    product = (
        await s.execute(
            text(
                "SELECT id FROM platform.products "
                " WHERE (tenant_id = :t OR tenant_id IS NULL) "
                "   AND UPPER(product_type::text) IN ('SIPP','PENSION') "
                " ORDER BY (tenant_id IS NULL) LIMIT 1"
            ),
            {"t": tenant},
        )
    ).scalar_one_or_none()
    if product is None:
        raise SystemExit(
            "No SIPP/PENSION product on this database — the credits would seed but never surface "
            "as Action required, because that flag is derived from the account's product type."
        )
    account = (
        await s.execute(
            text(
                "INSERT INTO platform.accounts "
                "  (uid, tenant_id, contact_id, product_id, name, status, currency, value) "
                "VALUES (:uid, :t, :c, :p, :name, 'active', 'GBP', 1875500.42) "
                # `product_id` in the DO UPDATE, not just the name: the account already existed from an
                # earlier run with a NULL product, so refreshing only the name left it that way and
                # the 24 credits stayed invisible. An upsert that does not update the column you
                # just fixed is the same shape of bug as a discarded return map.
                "ON CONFLICT (uid) DO UPDATE "
                "  SET name = EXCLUDED.name, product_id = EXCLUDED.product_id "
                "RETURNING id"
            ),
            {
                "uid": "uiedgeacct01",
                "t": tenant,
                "c": contact,
                "p": product,
                "name": f"{ORG_NAME} — Self-Invested Personal Pension (Drawdown Sub-Account 2)",
            },
        )
    ).scalar_one()

    # --- 4. a fund name at the real 89-character ceiling ---------------------------------------
    await s.execute(
        text(
            "INSERT INTO platform.instruments (isin, name, asset_type, currency) "
            "VALUES ('GB00UIEDGE01', :n, 'Fund', 'GBP') "
            # `instruments_isin_key` is a PARTIAL unique index (`WHERE isin IS NOT NULL`), and a
            # partial index is only inferred if the predicate is repeated here — the trap CLAUDE.md
            # records for the merge's own upserts. Without the WHERE this is "no unique or exclusion
            # constraint matching the ON CONFLICT specification", which reads like a missing index.
            "ON CONFLICT (isin) WHERE isin IS NOT NULL DO UPDATE SET name = EXCLUDED.name"
        ),
        {"n": FUND_NAME},
    )

    # --- 5. 24 uncategorised pension credits: the Action-required strip's crowding -------------
    # `needs_categorisation` is DERIVED by the API (a deposit on a pension with no contribution
    # and no expectation), so the shape is what matters, not a flag: 24 of them on one client is
    # what made the record's strip render 21 rows and bury everything below it.
    today = dt.date.today()
    for n in range(24):
        when = today - dt.timedelta(days=30 * n)
        await s.execute(
            text(
                "INSERT INTO platform.transactions "
                "  (tenant_id, account_id, contact_id, source_system, source_transaction_id, "
                "   type, source_type, source_subtype, amount, currency, transaction_date, "
                "   status, data) "
                "VALUES (:t, :a, :c, 'uiedge', :sid, 'deposit', 'Payment', 'Employer', "
                "        :amount, 'GBP', :when, 'completed', CAST(:data AS jsonb)) "
                "ON CONFLICT (tenant_id, source_system, source_transaction_id) DO UPDATE "
                "  SET transaction_date = EXCLUDED.transaction_date"
            ),
            {
                "t": tenant,
                "a": account,
                "c": contact,
                "sid": f"uiedge-credit-{n:02d}",
                "amount": 250 + n * 25,
                "when": when,
                "data": json.dumps({"seeded_by": "seed_ui_edge_cases"}),
            },
        )
    print("uncategorised pension credits: 24 (the strip should cap at 6 and offer '+18 more')")

    # --- 6. the long status: 'awaiting_confirmation' renders "To classify" ----------
    for n in range(6):
        await s.execute(
            text(
                "INSERT INTO platform.contributions "
                "  (uid, tenant_id, account_id, contact_id, category, amount, currency, "
                "   frequency, paid_by, contribution_date, status, employer_name) "
                "VALUES (:uid, :t, :a, :c, 'employer_contribution', :amount, 'GBP', "
                "        'monthly', 'employer', :when, 'awaiting_confirmation', :employer) "
                "ON CONFLICT (uid) DO UPDATE SET status = EXCLUDED.status"
            ),
            {
                "uid": f"uiedgectrb{n:02d}",
                "t": tenant,
                "a": account,
                "c": contact,
                "amount": 1500 + n * 100,
                "when": today - dt.timedelta(days=14 * n),
                # Long enough to test the workbench's employer column too.
                "employer": "Wetherington-Smythe Group Holdings (Trustee Services) Limited",
            },
        )
    print("contributions awaiting classification: 6 (the 23-character status badge)")

    # --- 7. many notes, one of them a wall of text ---------------------------------------------
    author = (
        await s.execute(
            text("SELECT id FROM auth.users WHERE tenant_id = :t ORDER BY id LIMIT 1"),
            {"t": tenant},
        )
    ).scalar_one_or_none()
    await s.execute(text("DELETE FROM crm.contact_notes WHERE contact_id = :c"), {"c": contact})
    for n in range(14):
        await s.execute(
            text(
                "INSERT INTO crm.contact_notes (tenant_id, contact_id, author_id, body, created_at) "
                "VALUES (:t, :c, :a, :body, :when)"
            ),
            {
                "t": tenant,
                "c": contact,
                "a": author,
                "body": WALL_OF_TEXT
                if n % 5 == 0
                else f"Review note {n + 1}: nothing outstanding.",
                "when": dt.datetime.now(dt.UTC) - dt.timedelta(days=n),
            },
        )
    print("notes: 14 (past the card's page of five; every 5th is a wall of text)")

    # --- 8. loads of OPEN document requests -------------------------------------------------
    # Two surfaces at once. `/documents/requests` lists them, and the DASHBOARD's action-items panel
    # merges tasks and requests into one urgency-ordered queue that renders only its first few -- so
    # a queue comfortably longer than the panel is what proves the cap and its overflow line work.
    #
    # Mixed priority and due dates on purpose: the queue orders by urgency, and rows all sharing one
    # priority would fall back to insertion order and prove nothing. A third are already overdue.
    asks = (
        ("upload", "Photographic identification (passport or driving licence)"),
        ("upload", "Proof of address dated within the last three months"),
        ("agree", "Terms of business and our conflicts-of-interest policy"),
        ("acknowledge", "Confirmation you have read the key features illustration"),
        ("complete_form", "Attitude-to-risk questionnaire for the trustees"),
        (
            "upload",
            "Trust deed and any subsequent deeds of variation, appointment or retirement of "
            "trustees (long on purpose: this is the title that overflows its column)",
        ),
    )
    priorities = ("urgent", "high", "normal", "low")
    await s.execute(
        text(
            "DELETE FROM documents.request_items WHERE request_id IN "
            "(SELECT id FROM documents.requests WHERE contact_id = :c)"
        ),
        {"c": contact},
    )
    await s.execute(text("DELETE FROM documents.requests WHERE contact_id = :c"), {"c": contact})
    for n in range(18):
        action, title = asks[n % len(asks)]
        request = (
            await s.execute(
                text(
                    "INSERT INTO documents.requests "
                    "  (uid, tenant_id, contact_id, requested_by, priority, message, due_date, "
                    "   status, created_at) "
                    "VALUES (:uid, :t, :c, :by, :priority, :message, :due, 'pending', :made) "
                    "RETURNING id"
                ),
                {
                    "uid": f"uiedgereq{n:03d}",
                    "t": tenant,
                    "c": contact,
                    "by": author,
                    "priority": priorities[n % len(priorities)],
                    "message": "Needed before we can proceed with the transfer.",
                    "due": today + dt.timedelta(days=(n % 3) * 7 - 7),
                    "made": dt.datetime.now(dt.UTC) - dt.timedelta(days=n),
                },
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO documents.request_items "
                "  (uid, request_id, tenant_id, action, title, status) "
                "VALUES (:uid, :r, :t, :action, :title, 'pending')"
            ),
            {
                "uid": f"uiedgeitem{n:03d}",
                "r": request,
                "t": tenant,
                "action": action,
                "title": title,
            },
        )
    print("open document requests: 18 (the dashboard panel shows a few and says how many remain)")

    print(f"\nThe client is '{ORG_NAME[:40]}…' — uid {CONTACT_UID}.")


async def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Seed the book's adversarial UI cases.")
    parser.add_argument("tenant_alias", nargs="?", default=DEFAULT_TENANT_ALIAS)
    parser.add_argument("--clear", action="store_true", help="remove the fixture and exit")
    args = parser.parse_args(argv)

    if settings.app_stage == "production":
        raise SystemExit("Refusing to seed a production deployment.")

    engine = create_async_engine(settings.migration_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))  # BYPASSRLS for seeding
        tenant = await _tenant(s, args.tenant_alias)
        if args.clear:
            await _clear(s, tenant)
        else:
            await seed(s, tenant)
    await engine.dispose()


if __name__ == "__main__":
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(sys.argv[1:]))
