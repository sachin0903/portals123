"""Seed a tenant's CRM + accounts from the Seccl analytical file, recording the provider
mapping in links. This is a thin, idempotent stand-in for the eventual inbound
sync (data-architecture.md §8.2) — enough to give the portals real, linked entities to
resolve lake holdings against.

    uv run python scripts/ingest_seccl.py --tenant 1 [--path docs/seccl.duckdb] [--limit N]

Idempotent: re-running upserts by the Seccl external id (via links), so it never
duplicates. Runs as service_role (BYPASSRLS). The raw Seccl structure is read ONLY here and
in app/lake.py — nothing else depends on its columns.

**This is a hand-run snapshot, and nothing refreshes it.** ``platform.accounts.value`` is
whatever this script last copied in; there is no poller, no cron and no webhook behind it
(``app/poller.py`` only drains the event queue). Because the portals show that value as the
firm's assets under advice, each account records ``data.valued_at`` + ``data.value_source``
here so the read path can state the figure's real as-at (``routers/crm.py`` prefers it over
``updated_at``, which any unrelated write to the row would bump — making a months-old value
look like today's). Replacing this with a real inbound Seccl sync is the outstanding
integration.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

# Make `app` importable when run as a bare script (its dir, not apps/api, is sys.path[0]).
_API_ROOT = Path(__file__).resolve().parents[1]  # apps/api
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from sqlalchemy import text  # noqa: E402  (after the sys.path shim above)

from app.db import system_transaction  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]

_ACCOUNT_STATUS = {"active": "active", "closed": "closed"}  # else -> 'draft'


def _read_seccl(path: Path, limit: int | None) -> tuple[list[dict], list[dict]]:
    import duckdb

    con = duckdb.connect(str(path), read_only=True)
    try:

        def rows(sql: str) -> list[dict]:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]

        cap = f" LIMIT {int(limit)}" if limit else ""  # int-cast, so safe to interpolate
        clients = rows(
            "SELECT id, client_type, title, first_name, surname, name, date_of_birth, "  # noqa: S608
            "       email, nationality FROM seccl.seccl.clients" + cap
        )
        accounts = rows(
            "SELECT id, name, client_id, status, currency, current_value, product_type, "  # noqa: S608
            "       advised FROM seccl.seccl.accounts" + cap
        )
        return clients, accounts
    finally:
        con.close()


async def _link_entity_id(session: Any, entity_type: str, external_id: str) -> int | None:
    return (
        await session.execute(
            text(
                "SELECT entity_id FROM platform.links "
                "WHERE provider='seccl' AND entity_type=:et AND external_id=:ext "
                "AND link_type='primary'"
            ),
            {"et": entity_type, "ext": external_id},
        )
    ).scalar_one_or_none()


async def _upsert_link(
    session: Any, tenant_id: int, entity_type: str, entity_id: int, ext: str
) -> None:
    await session.execute(
        text(
            "INSERT INTO platform.links "
            "(tenant_id, entity_type, entity_id, provider, link_type, external_id) "
            "VALUES (:t, :et, :eid, 'seccl', 'primary', :ext) "
            "ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type) "
            "DO UPDATE SET external_id = EXCLUDED.external_id"
        ),
        {"t": tenant_id, "et": entity_type, "eid": entity_id, "ext": ext},
    )


async def _purge_seccl_entities(session: Any, tenant_id: int) -> None:
    """Delete this tenant's Seccl-ingested contacts + accounts (everything this
    script creates carries uid 'seccl_<ext>'). Accounts go first — a contact is
    ON DELETE RESTRICT from platform.accounts — and deleting contacts cascades to
    contact_notes / client_advisers / contact_users / relationships. Use before a
    re-seed when the source ids changed (e.g. swapping the real file for the
    anonymised fixture, whose ids are pseudonymised)."""
    for label, sql in (
        ("accounts", "DELETE FROM platform.accounts WHERE tenant_id=:t AND uid LIKE 'seccl\\_%'"),
        ("contacts", "DELETE FROM crm.contacts WHERE tenant_id=:t AND uid LIKE 'seccl\\_%'"),
        ("links", "DELETE FROM platform.links WHERE tenant_id=:t AND provider='seccl'"),
    ):
        res = await session.execute(text(sql), {"t": tenant_id})
        print(f"  purged {res.rowcount} {label}")


async def ingest(tenant_id: int, path: Path, limit: int | None, reset: bool = False) -> None:
    clients, accounts = _read_seccl(path, limit)
    print(f"read {len(clients)} clients, {len(accounts)} accounts from {path}")

    contact_by_seccl: dict[str, int] = {}
    async with system_transaction() as s:
        # tenant must exist
        if not (
            await s.execute(text("SELECT 1 FROM auth.tenants WHERE id=:t"), {"t": tenant_id})
        ).scalar_one_or_none():
            raise SystemExit(f"tenant {tenant_id} not found")

        if reset:
            print(f"reset: purging existing Seccl entities for tenant {tenant_id}")
            await _purge_seccl_entities(s, tenant_id)

        for c in clients:
            ext = str(c["id"])
            is_indiv = "indiv" in str(c.get("client_type") or "").lower()
            ctype = "individual" if is_indiv else "organisation"
            fields = {
                "type": ctype,
                "title": c.get("title"),
                "first_name": c.get("first_name"),
                "surname": c.get("surname"),
                "name": c.get("name"),
                "dob": c.get("date_of_birth"),
                "email": c.get("email"),
                "nationality": [c["nationality"]] if c.get("nationality") else None,
            }
            existing = await _link_entity_id(s, "contact", ext)
            if existing is None:
                cid = (
                    await s.execute(
                        text(
                            "INSERT INTO crm.contacts "
                            "(tenant_id, uid, type, title, first_name, surname, name, dob, "
                            " email, nationality) "
                            "VALUES (:t, :uid, :type, :title, :first_name, :surname, :name, "
                            " :dob, :email, :nationality) RETURNING id"
                        ),
                        {"t": tenant_id, "uid": f"seccl_{ext}", **fields},
                    )
                ).scalar_one()
                await _upsert_link(s, tenant_id, "contact", cid, ext)
            else:
                cid = existing
                await s.execute(
                    text(
                        "UPDATE crm.contacts SET title=:title, first_name=:first_name, "
                        "surname=:surname, name=:name, dob=:dob, email=:email, "
                        "nationality=:nationality, type=:type WHERE id=:id AND tenant_id=:t"
                    ),
                    {"id": cid, "t": tenant_id, **fields},
                )
            contact_by_seccl[ext] = cid

        n_acc = 0
        # One stamp for the whole run: every value written below was true as of this moment,
        # and the portals quote it as the figure's as-at.
        valued_at = dt.datetime.now(dt.UTC).isoformat()
        for a in accounts:
            ext = str(a["id"])
            contact_id = contact_by_seccl.get(str(a.get("client_id")))
            status = _ACCOUNT_STATUS.get(str(a.get("status") or "").lower(), "draft")
            data = {
                "seccl_product_type": a.get("product_type"),
                "advised": a.get("advised"),
                "value_source": "seccl",
                "valued_at": valued_at,
            }
            fields = {
                "name": a.get("name"),
                "status": status,
                "currency": a.get("currency"),
                "value": a.get("current_value"),
                "contact_id": contact_id,
                "platform_identifier": ext,
                "data": json.dumps(data),
            }
            existing = await _link_entity_id(s, "account", ext)
            if existing is None:
                aid = (
                    await s.execute(
                        text(
                            "INSERT INTO platform.accounts "
                            "(tenant_id, uid, name, status, currency, value, contact_id, "
                            " platform_identifier, data) "
                            "VALUES (:t, :uid, :name, :status, :currency, :value, :contact_id, "
                            " :platform_identifier, CAST(:data AS jsonb)) RETURNING id"
                        ),
                        {"t": tenant_id, "uid": f"seccl_{ext}", **fields},
                    )
                ).scalar_one()
                await _upsert_link(s, tenant_id, "account", aid, ext)
            else:
                aid = existing
                await s.execute(
                    text(
                        "UPDATE platform.accounts SET name=:name, status=:status, "
                        "currency=:currency, value=:value, contact_id=:contact_id, "
                        "platform_identifier=:platform_identifier, data=CAST(:data AS jsonb) "
                        "WHERE id=:id AND tenant_id=:t"
                    ),
                    {"id": aid, "t": tenant_id, **fields},
                )
            n_acc += 1
        print(
            f"ingested {len(contact_by_seccl)} contacts, {n_acc} accounts into tenant {tenant_id}"
        )
        print(
            f"  account values stamped valued_at={valued_at} — nothing refreshes them, so the "
            "portals will show that date until this script is run again"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, required=True, help="target auth.tenants.id")
    ap.add_argument("--path", type=str, default=str(_REPO_ROOT / "docs" / "seccl.duckdb"))
    ap.add_argument("--limit", type=int, default=None, help="cap rows (dev/testing)")
    ap.add_argument(
        "--reset",
        action="store_true",
        help="purge this tenant's existing Seccl contacts/accounts before ingesting "
        "(needed when re-seeding from the anonymised fixture, whose ids differ)",
    )
    args = ap.parse_args()
    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"file not found: {path}")
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(ingest(args.tenant, path, args.limit, args.reset))


if __name__ == "__main__":
    main()
