"""Seed sample notifications into a local tenant (dev only).

Fills the three notification surfaces with enough shape to actually look at them:

* **Spread over days**, because the all-notifications page groups by day and a pile of rows all
  stamped "now" renders one heading and proves nothing. The stamps below deliberately land in
  today, yesterday, the middle of this week (the weekday headings, "Wednesday, 9 September") and
  a fortnight back (the absolute-date heading).
* **Mixed read state**, so the bell's badge has a number, the unread rows carry their weight, and
  "Mark all read" has something to do.
* **Some already dismissed**, which is the whole point of the split this seeds for: a dismissed
  row is absent from the bell and present on the all-notifications page. Seeding a few means you
  can see that difference without first clicking anything.
* **Every category the UI labels** (`describe.ts::CATEGORY_META` — tasks, documents, clients,
  accounts, messages) plus one deliberately unknown category, so the neutral "Update" fallback
  gets looked at too rather than only ever appearing in production.

Staff rows (portal='platform', recipient_kind='user') go to every enabled PLATFORM login; client
rows (portal='client', recipient_kind='contact') go to the tenant's contacts.

Deliberately NOT to operations logins. These samples are adviser copy stamped 'platform', and
since 0173 a session only reads notifications raised for the portal it is signed into — so an
ops-only login seeded this way would hold rows it can never see. Nothing writes an 'operations'
notification yet; when something does, it belongs in its own sample list rather than borrowing
the adviser one.

Idempotent: seeded rows carry `source_kind='domain'` with a `source_ref` prefixed `seed:`, and
every one of those is deleted before re-seeding, so re-running resets the picture rather than
stacking a second copy of it.

Run:  uv run --directory apps/api python scripts/seed_notifications.py [tenant_alias]
Requires: migrations applied + a local Postgres (pnpm db:up).
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.ids import new_id

DEFAULT_TENANT_ALIAS = "graphene"


class Sample(NamedTuple):
    """One seeded notification.

    A NamedTuple rather than a bare tuple because the formatter splits a row of this width over
    one line per value, and nine bare positionals stacked like that are unreadable — you cannot
    tell the priority from the state without counting. Defaults keep the common row short.
    """

    #: How many days back it was raised. This is what produces the day headings, so the values
    #: below hit every branch of `formatArchiveDayHeading`: 0 (Today), 1 (Yesterday), 2-6 (a
    #: weekday), 14 (an absolute date).
    days_ago: int
    hour: int
    category: str
    event_type: str
    title: str
    body: str
    link: str | None = None
    priority: str = "normal"
    #: "unread", "read" or "dismissed" — the three states the surfaces render differently.
    state: str = "unread"


STAFF: list[Sample] = [
    # -- Today: the unread work at the top of the bell ------------------------------------------
    Sample(
        0,
        9,
        "tasks",
        "task.assigned",
        "New task: Chase the ATR for Margaret Cole",
        "Due Friday. Raised by the annual review flow.",
        "/tasks",
        "high",
    ),
    Sample(
        0,
        8,
        "messages",
        "message.received",
        "New message from James Mitchell",
        "\u201cCould you confirm the transfer went through?\u201d",
        "/chats",
    ),
    Sample(
        0,
        7,
        "accounts",
        "account.reconciliation_break",
        "Reconciliation break on SIPP 4471",
        "Custody is \u00a31,204.18 below the expected settlement.",
        "/accounts",
        "high",
    ),
    # -- Yesterday: a mix, so the heading has more than one row under it -------------------------
    Sample(
        1,
        16,
        "documents",
        "document.signed",
        "Client agreement signed by Priya Nair",
        "Countersigned copy filed to the client record.",
        "/documents",
    ),
    Sample(
        1,
        11,
        "clients",
        "client.onboarded",
        "Daniel Whitlock finished onboarding",
        "All checks passed. The account is ready to fund.",
        "/clients",
        state="read",
    ),
    Sample(
        1,
        9,
        "tasks",
        "task.completed",
        "Task completed: Send the suitability report",
        "Completed by Gio Matonti.",
        "/tasks",
        "low",
        "read",
    ),
    # -- Earlier this week: the weekday headings -------------------------------------------------
    Sample(
        3,
        15,
        "documents",
        "document.requested",
        "Upload requested from Eleanor Voss",
        "Proof of address. Raised by the onboarding flow.",
        "/documents",
        state="read",
    ),
    Sample(
        3,
        10,
        "messages",
        "message.received",
        "New message from Gio Matonti",
        "\u201cAye, that one is with compliance now.\u201d",
        "/chats",
        state="dismissed",
    ),
    Sample(
        5,
        14,
        "accounts",
        "account.opened",
        "New account opened for Tom Fletcher",
        "ISA 9920 is live and awaiting its first contribution.",
        "/accounts",
        state="read",
    ),
    # No `link`, and no category the UI labels: the neutral "Update" bell and a row that cannot be
    # clicked through are both real states, and both are otherwise never seen outside production.
    Sample(
        5,
        9,
        "billing",
        "fee.run_completed",
        "Quarterly fee run completed",
        "241 accounts charged. Nothing failed.",
        None,
        "low",
        "read",
    ),
    # -- A fortnight back: the absolute-date heading, and the archive keeping what the bell hid --
    Sample(
        14,
        12,
        "clients",
        "client.archived",
        "Margaret Cole was archived",
        "Archived by Craig Currie.",
        "/clients",
        "low",
        "dismissed",
    ),
    Sample(
        14,
        9,
        "tasks",
        "task.overdue",
        "Task overdue: Confirm the ceding scheme",
        "Was due 12 days ago.",
        "/tasks",
        "high",
        "read",
    ),
]

CLIENT: list[Sample] = [
    Sample(
        0,
        10,
        "messages",
        "message.received",
        "New message from Craig Currie",
        "You have a new message from your adviser. Open your portal to reply.",
        "/messages",
    ),
    Sample(
        0,
        9,
        "documents",
        "document.shared",
        "Your annual review is ready",
        "Your adviser has shared a document with you.",
        "/documents",
    ),
    Sample(
        1,
        14,
        "clients",
        "client.adviser_changed",
        "Your adviser has been updated",
        "Your adviser has changed. Open your portal to see who is looking after you.",
        state="read",
    ),
    Sample(
        4,
        11,
        "accounts",
        "account.statement_ready",
        "Your quarterly statement is ready",
        "Covering 1 April to 30 June.",
        "/accounts",
        "low",
        "read",
    ),
    Sample(
        14,
        10,
        "documents",
        "document.shared",
        "Illustration shared with you",
        "A pension transfer illustration is on your portal.",
        "/documents",
        "low",
        "dismissed",
    ),
]


def _stamp(days_ago: int, hour: int, now: datetime) -> datetime:
    """A timestamp `days_ago` days back at `hour` LOCAL-ish time.

    Built off the reader's own day rather than a fixed UTC hour: the archive groups by local
    calendar day, and a row seeded at 00:30 UTC would land under the wrong heading for anyone
    west of it — exactly the bug the grouping helper takes care to avoid.
    """
    day = (now - timedelta(days=days_ago)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return day


async def main(tenant_alias: str) -> None:
    engine = create_async_engine(settings.migration_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    seeded = 0
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))  # BYPASSRLS for seeding

        tenant_id = (
            await s.execute(
                text("SELECT id FROM auth.tenants WHERE alias = :a"), {"a": tenant_alias}
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            raise SystemExit(f"No tenant with alias '{tenant_alias}'")

        # Clear the previous seed first. Scoped to this tenant AND the `seed:` source_ref, so a
        # re-run never touches a notification the app itself produced.
        # `RETURNING` rather than `.rowcount`: SQLAlchemy types `execute` as `Result`, which does
        # not carry rowcount, and counting the returned rows needs no cast to say the same thing.
        removed = len(
            (
                await s.execute(
                    text(
                        "DELETE FROM notifications.notifications "
                        "WHERE tenant_id = :t AND source_ref LIKE 'seed:%' RETURNING 1"
                    ),
                    {"t": tenant_id},
                )
            ).all()
        )

        staff = (
            (
                await s.execute(
                    text(
                        "SELECT u.id FROM auth.users u "
                        "JOIN auth.organisation_memberships m ON m.user_id = u.id "
                        "WHERE u.tenant_id = :t AND u.deleted_at IS NULL AND u.enabled "
                        "  AND m.portal = 'platform' AND m.status = 'active' "
                        "ORDER BY u.id"
                    ),
                    {"t": tenant_id},
                )
            )
            .scalars()
            .all()
        )
        contacts = (
            (
                await s.execute(
                    text(
                        "SELECT id FROM crm.contacts "
                        "WHERE tenant_id = :t AND deleted_at IS NULL ORDER BY id LIMIT 5"
                    ),
                    {"t": tenant_id},
                )
            )
            .scalars()
            .all()
        )
        if not staff and not contacts:
            raise SystemExit(
                f"Tenant '{tenant_alias}' has no staff logins and no contacts to notify — "
                "run scripts/seed_clients.py (and bootstrap_dev_tenant.py) first."
            )

        now = datetime.now(UTC)

        async def insert(rows: list[Sample], portal: str, kind: str, recipient: int) -> int:
            written = 0
            # OLDEST FIRST, and that is not cosmetic. The feed orders by `id DESC` — a real
            # notification's id rises with its timestamp, so the newest row has the highest id.
            # Inserting these newest-first inverted that: ids climbed as the dates fell, and the
            # archive rendered 28 August at the top and Yesterday at the bottom. The data was
            # lying about the ordering, not the page.
            # `-days_ago` then `hour` ascending, so the sort runs oldest to newest THROUGH
            # the day as well as across days. Sorting on the day alone left each day's own
            # rows in list order, which put 09:00 before 07:00 once the ids were read back
            # descending.
            for i, row in enumerate(sorted(rows, key=lambda r: (-r.days_ago, r.hour))):
                created = _stamp(row.days_ago, row.hour, now)
                # Read and dismissed rows carry plausible stamps AFTER their creation, not `now` —
                # "read three hours later" is what real data looks like, and the row's own age is
                # what the archive's heading reads.
                done = row.state in ("read", "dismissed")
                read_at = created + timedelta(hours=2) if done else None
                dismissed = row.state == "dismissed"
                dismissed_at = created + timedelta(hours=3) if dismissed else None
                await s.execute(
                    text(
                        "INSERT INTO notifications.notifications "
                        "  (uid, tenant_id, recipient_kind, recipient_user_id, "
                        "   recipient_contact_id, portal, event_type, category, title, body, "
                        "   link, priority, source_kind, source_ref, read_at, dismissed_at, "
                        "   created_at) "
                        "VALUES (:uid, :t, :kind, :uid_user, :uid_contact, :portal, :event, "
                        "        :category, :title, :body, :link, :prio, 'domain', :ref, "
                        "        :read_at, :dismissed_at, :created) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {
                        "uid": new_id(),
                        "t": tenant_id,
                        "kind": kind,
                        "uid_user": recipient if kind == "user" else None,
                        "uid_contact": recipient if kind == "contact" else None,
                        "portal": portal,
                        "event": row.event_type,
                        "category": row.category,
                        "title": row.title,
                        "body": row.body,
                        "link": row.link,
                        "prio": row.priority,
                        # Unique per recipient AND per row: the table has a uniqueness index on
                        # (recipient, source_kind, source_ref), so a shared ref would silently
                        # drop every row after the first.
                        "ref": f"seed:{kind}:{recipient}:{i}",
                        "read_at": read_at,
                        "dismissed_at": dismissed_at,
                        "created": created,
                    },
                )
                written += 1
            return written

        for user_id in staff:
            seeded += await insert(STAFF, "platform", "user", user_id)
        for contact_id in contacts:
            seeded += await insert(CLIENT, "client", "contact", contact_id)

    await engine.dispose()
    unread = sum(1 for r in STAFF if r.state == "unread")
    dismissed_count = sum(1 for r in STAFF if r.state == "dismissed")
    print(
        f"Cleared {removed} previously seeded rows.\n"
        f"Seeded {seeded} notifications into '{tenant_alias}' "
        f"({len(staff)} staff x {len(STAFF)}, {len(contacts)} contacts x {len(CLIENT)}).\n"
        f"Each staff login: {unread} unread on the bell, {dismissed_count} dismissed "
        f"(hidden from the bell, still on /notifications), spread over today, yesterday, "
        f"this week and a fortnight ago so the day headings all render."
    )


if __name__ == "__main__":
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    alias = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TENANT_ALIAS
    asyncio.run(main(alias))
