"""Behaviour + RLS tests for in-app notifications (notifications.* — the notification centre).

Against a real ``authenticated`` session (claims minted by ``auth.resolve_claims`` for staff;
hand-crafted client-portal claims for a client, as the tasks suite does). Proves:

- ``service.notify`` inserts one row per recipient, respects the per-category preference, and is
  idempotent on (recipient, source_kind, source_ref).
- RLS: a staff user sees ONLY notifications addressed to their own uid; a client sees ONLY those
  addressed to their contact scope; cross-user and cross-tenant are invisible.
- The tail consumer turns an ``audit.actions`` row into notifications per the registry, advances
  its cursor, and a re-run produces no duplicate.
- The router feed / unread-count / mark-read behave and stay RLS-scoped; preferences round-trip.
"""

import json
import re
import types
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, get_args

import psycopg
import pytest
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import live_push
from app.auth import Principal
from app.comms import stream
from app.config import settings
from app.email import transport as email_mod
from app.flows import notifications as flows_notifications
from app.notifications import (
    catalogue,
    consumer,
    email_consumer,
    realtime,
    registry,
    rules_service,
    service,
)
from app.notifications import config as notif_config
from app.notifications.registry import Recipient
from app.routers import notifications as notif_router
from tests import email_support
from tests.conftest import assert_public_id


def _principal(perms: list[str] | None = None) -> Principal:
    return Principal(user_id="x", organisation_id="x", role="adviser", permissions=perms or [])


@asynccontextmanager
async def _staff(app_url: str, workos_user: str, client_id: str) -> AsyncIterator[AsyncSession]:
    """A staff session — SET ROLE authenticated + claims minted by auth.resolve_claims. App-only
    portal (0107): the application client id (arg 3) classifies the portal claim, and lineage is
    a whitelist on it — resolving without one would leave staff with no reach at all."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        resolved = (
            await session.execute(
                text("SELECT auth.resolve_claims(:w, NULL, :cid)"),
                {"w": workos_user, "cid": client_id},
            )
        ).scalar_one()
        claims = {"sub": workos_user, "role": "adviser", "permissions": [], **resolved}
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)}
        )
        yield session
    await engine.dispose()


@asynccontextmanager
async def _client(
    app_url: str, user_uuid: str, tenant: int, contact_ids: list[int]
) -> AsyncIterator[AsyncSession]:
    """A CLIENT-portal session — portal='client' empties lineage, so only contact_scope matches."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        claims = {
            "sub": "client",
            "role": "authenticated",
            "permissions": [],
            "user_uuid": user_uuid,
            "tenant_id": tenant,
            "tenant_ids": [tenant],
            "portal": "client",
            "contact_ids": contact_ids,
            "account_ids": [],
            "role_ids": [],
        }
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)}
        )
        yield session
    await engine.dispose()


@asynccontextmanager
async def _ops(app_url: str, workos_user: str, tenant: int) -> AsyncIterator[AsyncSession]:
    """An INTERNAL OPS session — staff reach (non-empty lineage), portal='operations'.

    Hand-crafted rather than resolved through an app client id, like `_client` above: the one
    variable under test is the portal claim, and registering an operations application for the
    fixture tenant would add a second. Everything else matches a platform staff session, which is
    the point — before 0173 these two were indistinguishable to the policy."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        claims = {
            "sub": workos_user,
            "role": "adviser",
            "permissions": [],
            "user_uuid": workos_user,
            "tenant_id": tenant,
            "tenant_ids": [tenant],
            "portal": "operations",
            "contact_ids": [],
            "account_ids": [],
            "role_ids": [],
        }
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)}
        )
        yield session
    await engine.dispose()


@asynccontextmanager
async def _service(app_url: str) -> AsyncIterator[AsyncSession]:
    """A service_role session — what the consumer and domain producers hold (can INSERT)."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        yield session
    await engine.dispose()


@pytest.fixture
def notifbed(super_engine: Engine) -> dict[str, object]:
    """Tenant A: a compliance role, a staff login holding it, two client contacts (c1 with a
    portal login). Tenant B: a staff login (the cross-tenant negative)."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        ten_a = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"nta_{s}", "a": f"notiffirm-{s}", "n": f"NotifFirm {s}"},
            ).scalar_one()
        )
        ten_b = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"ntb_{s}", "a": f"otherfirm-{s}", "n": f"OtherFirm {s}"},
            ).scalar_one()
        )
        role = int(
            c.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                    "VALUES (:t, 'compliance', 'Compliance', 'platform') RETURNING id"
                ),
                {"t": ten_a},
            ).scalar_one()
        )
        wos_staff = f"nwos_{s}"
        staff = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, first_name, "
                    "  last_name, enabled) VALUES (:uid, :t, :email, :wos, 'Nadia', 'Staff', true) "
                    "RETURNING id"
                ),
                {
                    "uid": f"nusr_{s}",
                    "t": ten_a,
                    "email": f"staff_{s}@notiffirm.test",
                    "wos": wos_staff,
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, role_id, "
                "  status) VALUES (:u, :t, 'platform', :r, 'active')"
            ),
            {"u": staff, "t": ten_a, "r": role},
        )
        wos_staff_b = f"nwosb_{s}"
        staff_b = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                ),
                {
                    "uid": f"nusrb_{s}",
                    "t": ten_b,
                    "email": f"b_{s}@otherfirm.test",
                    "wos": wos_staff_b,
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, status) "
                "VALUES (:u, :t, 'platform', 'active')"
            ),
            {"u": staff_b, "t": ten_b},
        )
        # App-only portal (0107): resolve_claims grants a portal only by naming the tenant's app.
        for ten, tag in ((ten_a, "a"), (ten_b, "b")):
            c.execute(
                text(
                    "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                    "  platform_client_id) VALUES (:t, 'dev', :env, :app)"
                ),
                {"t": ten, "env": f"env_n{tag}_{s}", "app": f"app_n{tag}_platform_{s}"},
            )
        c1 = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :u, 'individual', 'Cleo Client', :e) RETURNING id"
                ),
                {"t": ten_a, "u": f"nc1_{s}", "e": f"cleo_{s}@client.test"},
            ).scalar_one()
        )
        client_login = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                ),
                {
                    "uid": f"nclu_{s}",
                    "t": ten_a,
                    "email": f"cleo_{s}@client.test",
                    "wos": f"nwoc_{s}",
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id) VALUES (:t, :c, :u)"
            ),
            {"t": ten_a, "c": c1, "u": client_login},
        )
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :c, :u, 'adviser')"
            ),
            {"t": ten_a, "c": c1, "u": staff},
        )
        c.commit()
    return {
        "tenant_a": ten_a,
        "tenant_b": ten_b,
        "role": role,
        "staff": staff,
        "wos_staff": wos_staff,
        "wos_staff_b": wos_staff_b,
        "app_a": f"app_na_platform_{s}",
        "app_b": f"app_nb_platform_{s}",
        "c1": c1,
        "c1_uid": f"nc1_{s}",
        "client_login": client_login,
        "staff_email": f"staff_{s}@notiffirm.test",
        "client_email": f"cleo_{s}@client.test",
    }


# ---- service.notify --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_inserts_and_respects_preference(
    db: dict[str, str], notifbed: dict[str, object]
) -> None:
    ten = int(notifbed["tenant_a"])
    staff = str(notifbed["staff"])
    async with _service(db["app"]) as ses:
        n = await service.notify(
            ses,
            tenant_id=ten,
            recipients=[Recipient(kind="user", user_id=staff)],
            category="tasks",
            portal="platform",
            title="A task was assigned to you",
            event_type="task.created",
            source_kind="action",
            source_ref="action:1:tasks",
        )
        assert n == 1

    async with _service(db["app"]) as ses:
        await ses.execute(
            text(
                "INSERT INTO notifications.preferences (tenant_id, recipient_kind, "
                "  recipient_user_id, category, in_app) VALUES (:t, 'user', :u, 'tasks', false)"
            ),
            {"t": ten, "u": staff},
        )
    async with _service(db["app"]) as ses:
        n = await service.notify(
            ses,
            tenant_id=ten,
            recipients=[Recipient(kind="user", user_id=staff)],
            category="tasks",
            portal="platform",
            title="Another task",
            event_type="task.created",
            source_kind="action",
            source_ref="action:2:tasks",
        )
        assert n == 0


@pytest.mark.asyncio
async def test_notify_is_idempotent_on_source_ref(
    db: dict[str, str], notifbed: dict[str, object]
) -> None:
    ten = int(notifbed["tenant_a"])
    staff = str(notifbed["staff"])
    ref = f"action:{uuid.uuid4().hex}:tasks"
    for _ in range(3):
        async with _service(db["app"]) as ses:
            await service.notify(
                ses,
                tenant_id=ten,
                recipients=[Recipient(kind="user", user_id=staff)],
                category="tasks",
                portal="platform",
                title="Same task",
                event_type="task.created",
                source_kind="action",
                source_ref=ref,
            )
    async with _service(db["app"]) as ses:
        count = (
            await ses.execute(
                text("SELECT count(*) FROM notifications.notifications WHERE source_ref = :r"),
                {"r": ref},
            )
        ).scalar_one()
    assert count == 1


# ---- RLS -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rls_scopes_to_the_recipient(db: dict[str, str], notifbed: dict[str, object]) -> None:
    ten = int(notifbed["tenant_a"])
    staff = str(notifbed["staff"])
    c1 = int(notifbed["c1"])
    async with _service(db["app"]) as ses:
        await service.notify(
            ses,
            tenant_id=ten,
            recipients=[Recipient(kind="user", user_id=staff)],
            category="tasks",
            portal="platform",
            title="For staff",
            event_type="task.created",
            source_kind="action",
            source_ref=f"s:{uuid.uuid4().hex}",
        )
        await service.notify(
            ses,
            tenant_id=ten,
            recipients=[Recipient(kind="contact", contact_id=c1)],
            category="documents",
            portal="client",
            title="For the client",
            event_type="document.sent",
            source_kind="action",
            source_ref=f"c:{uuid.uuid4().hex}",
        )

    async with _staff(db["app"], str(notifbed["wos_staff"]), str(notifbed["app_a"])) as ses:
        rows = (
            (await ses.execute(text("SELECT title FROM notifications.notifications")))
            .scalars()
            .all()
        )
        assert rows == ["For staff"]

    async with _client(db["app"], str(notifbed["client_login"]), ten, [c1]) as ses:
        rows = (
            (await ses.execute(text("SELECT title FROM notifications.notifications")))
            .scalars()
            .all()
        )
        assert rows == ["For the client"]

    async with _staff(db["app"], str(notifbed["wos_staff_b"]), str(notifbed["app_b"])) as ses:
        count = (
            await ses.execute(text("SELECT count(*) FROM notifications.notifications"))
        ).scalar_one()
        assert count == 0

    # 0067: the user branch is staff-only, so a client sharing the uid sees only the contact's row.
    async with _client(db["app"], staff, ten, [c1]) as ses:
        result = await ses.execute(text("SELECT title FROM notifications.notifications"))
        titles = result.scalars().all()
        assert titles == ["For the client"]  # the contact's row only, never the staff "For staff"


# ---- the tail consumer -----------------------------------------------------------------------


async def _seed_cursor_at_head(super_engine: Engine, consumer: str = "notifications") -> int:
    """Park a consumer's cursor at the current head so only rows inserted after are read."""
    with super_engine.connect() as c:
        head = int(c.execute(text("SELECT COALESCE(max(id), 0) FROM audit.actions")).scalar_one())
        c.execute(
            text(
                "INSERT INTO notifications.cursor (consumer, position) VALUES (:c, :p) "
                "ON CONFLICT (consumer) DO UPDATE SET position = :p"
            ),
            {"c": consumer, "p": head},
        )
        c.commit()
    return head


def _insert_action(
    super_engine: Engine,
    *,
    tenant_id: int,
    event_type: str,
    subject: dict,
    path_params: dict | None = None,
    actor_id: str | None = None,
) -> int:
    """Insert one audit.actions row, BACKDATED past the LAG cutoff so the tail reads it.
    ``actor_id`` (an auth.users uuid) is the handle a resolver keys on when the recipient depends on
    WHO acted — e.g. a client's chat message resolves the adviser via the sender's login."""
    backdated = datetime.now(UTC) - timedelta(seconds=30)
    with super_engine.connect() as c:
        aid = int(
            c.execute(
                text(
                    "INSERT INTO audit.actions (event_type, tenant_id, actor_id, actor_sub, "
                    "  method, route, status, metadata, path_params, created_at) "
                    "VALUES (:et, :t, CAST(:actor AS uuid), 'wos_actor', 'POST', :route, 200, "
                    "  CAST(:meta AS jsonb), CAST(:pp AS jsonb), :ts) RETURNING id"
                ),
                {
                    "et": event_type,
                    "t": tenant_id,
                    "actor": actor_id,
                    "route": "/x",
                    "meta": json.dumps({"subject": subject}),
                    "pp": json.dumps(path_params or {}),
                    "ts": backdated,
                },
            ).scalar_one()
        )
        c.commit()
    return aid


def _enable_resend(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A configured transport whose POST is captured — the worker drain's real send path, not a
    stub of ``send_email``. The suite's autouse blanks the Resend key and silences the ledger;
    callers that need a delivered row also request ``live_comms_ledger``."""
    payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(settings, "email_nonprod_guard", False)
    monkeypatch.setattr(
        email_mod, "httpx", types.SimpleNamespace(AsyncClient=email_support.FakeClient(payloads))
    )
    return payloads


def _queued_payload(super_engine: Engine, tenant_id: int, event_type: str) -> dict[str, Any]:
    """The outbox row the email subscriber just wrote for this tenant — the worker's input."""
    with super_engine.connect() as c:
        payload = c.execute(
            text(
                "SELECT payload FROM flows.outbox "
                "WHERE tenant_id = :t AND event_type = :e AND status = 'queued' "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"t": tenant_id, "e": event_type},
        ).scalar_one()
    assert isinstance(payload, dict)
    return payload


@pytest.mark.asyncio
async def test_consumer_fans_document_send_to_the_contact(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document.sent",
        subject={"contact_ids": [c1]},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
        await consumer.consume_once(maker)  # re-run: must not duplicate
    finally:
        await engine.dispose()

    with super_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT category, title FROM notifications.notifications "
                    "WHERE recipient_contact_id = :c "
                    "  AND event_type = 'document.sent'"
                ),
                {"c": c1},
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    assert rows[0]["category"] == "documents"


@pytest.mark.asyncio
async def test_expanded_catalogue_offers_all_triggers_and_fires_a_stored_one(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """The builder now offers every action-sourced trigger (not just the 3 built-ins), and an
    OFFERED trigger fires once a firm authors a rule for it — here 'a client's bank details change'
    → the client's adviser, resolving the subject generically from the event's path params."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    staff = str(notifbed["staff"])  # advises c1 via client_advisers

    keys = {t["key"] for t in rules_service.catalogue_json()}
    assert {"contact.bank_details_changed", "account.model_changed", "task.completed"} <= keys
    assert "document.shared" in keys  # the built-ins remain

    async with _service(db["app"]) as ses:
        await rules_service.create_rule(
            ses,
            ten,
            authored_by="firm",
            trigger_key="contact.bank_details_changed",
            audience="adviser",
            title="A client's bank details changed",
            default_in_app=True,
        )

    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="client.bank_account_updated",
        subject={},
        path_params={"contact_id": c1},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
    finally:
        await engine.dispose()

    with super_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT category, recipient_kind, recipient_user_id::text AS uid "
                    "FROM notifications.notifications "
                    "WHERE event_type = 'client.bank_account_updated'"
                )
            )
            .mappings()
            .all()
        )
    assert any(
        r["recipient_kind"] == "user" and r["uid"] == staff and r["category"] == "clients"
        for r in rows
    )


@pytest.mark.asyncio
async def test_a_staff_notice_deep_links_by_uid_and_stores_the_serial(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A staff notice with no authored link deep-links to `/clients/{uid}`.

    `_subject_of` answers in internal ids; interpolating those into a portal path lands on a
    record that does not exist. The stored `subject_id` stays the serial — `isOnScreen` parses
    it as the contact's id — and only the click destination is uid-addressed. A miss resolves
    to no link rather than falling back to the serial: a notice with no link still marks read,
    where one pointing at a 404 reads as the record having been deleted.
    """
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    c1_uid = str(notifbed["c1_uid"])
    staff = str(notifbed["staff"])

    async with _service(db["app"]) as ses:
        await rules_service.create_rule(
            ses,
            ten,
            authored_by="firm",
            trigger_key="contact.bank_details_changed",
            audience="adviser",
            title="A client's bank details changed",
            default_in_app=True,
        )

    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="client.bank_account_updated",
        subject={},
        path_params={"contact_id": c1},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
    finally:
        await engine.dispose()

    with super_engine.connect() as c:
        row = (
            c.execute(
                text(
                    "SELECT link, subject_type, subject_id FROM notifications.notifications "
                    "WHERE event_type = 'client.bank_account_updated' "
                    "  AND recipient_kind = 'user' AND recipient_user_id = CAST(:u AS uuid)"
                ),
                {"u": staff},
            )
            .mappings()
            .one()
        )
    assert row["link"] == f"/clients/{c1_uid}", (
        f"{row['link']} does not name this client's uid — a serial in a URL is the bug this pins"
    )
    assert row["subject_type"] == "contact"
    assert row["subject_id"] == str(c1)


def test_catalogue_params_match_subject_kind() -> None:
    """The builder's merge-field menu is derived from each trigger's subject, and served on the
    catalogue JSON. Pins the derivation so a copy author is only offered params we can resolve."""
    by_key = {t["key"]: t for t in rules_service.catalogue_json()}

    def param_keys(trigger_key: str) -> set[str]:
        return {p["key"] for p in by_key[trigger_key]["params"]}

    # Universal (F2): actor, timestamp and firm resolve for every action.
    base = {"actor.name", "actor.email", "event.when", "firm.name"}
    assert param_keys("contact.bank_details_changed") == base | {"client.name", "adviser.name"}
    assert param_keys("account.model_changed") == base | {
        "client.name",
        "adviser.name",
        "account.name",
        "account.number",
    }
    assert param_keys("task.assigned") == base | {
        "task.title",
        "task.due_date",
        "task.description",
    }
    assert {"document.name", "document.type"} <= param_keys("document.shared")
    assert "document.name" not in param_keys("document.request")
    assert "document.name" not in param_keys("document.generated")

    # The MESSAGING triggers have no flows-trigger entry, so the generic subject resolver finds
    # nothing — but both message events now capture `contact_id` off the response, which
    # `context._fallback_contact_id` reads, so {client.name} resolves for the ADVISER audience too.
    # That is the whole point of capturing it: the adviser's notice has to name the CLIENT, and
    # {actor.name} is the LOGIN, which is a different value even at capacity 'self' (an auth.users
    # name against a crm.contacts one) and a different PERSON when a login acts for several
    # contacts. {adviser.name} stays withheld: it would name the adviser back to themselves, and a
    # group send has no single contact to resolve one from.
    assert param_keys("message.received_by_adviser") == base | {"client.name"}
    assert param_keys("message.received_by_client") == base | {"client.name"}
    assert "adviser.name" not in param_keys("message.received_by_adviser")


@pytest.mark.asyncio
async def test_client_chat_notifies_only_the_thread_s_own_adviser(
    db: dict[str, str], notifbed: dict[str, object], super_engine: Engine
) -> None:
    """A delegate/POA login can be linked to SEVERAL clients' contacts (`crm.contact_users.capacity`
    allows authorised/power_of_attorney/delegate), but its chat thread belongs to exactly ONE
    contact. Resolving the sender's advisers from every linked contact therefore notified the
    advisers of every client that login can act for — advisers with no connection to the message.

    Seeds: `client_login` linked to c1 (advised by `staff`) AND to a second contact c9 (advised by a
    DIFFERENT staff user), with the client's conversation + message on c1's thread only. Only c1's
    adviser may be returned."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    staff = str(notifbed["staff"])
    login = str(notifbed["client_login"])
    s = uuid.uuid4().hex[:8]

    with super_engine.connect() as c:
        other_adviser = str(
            c.execute(
                text(
                    # `name` is a GENERATED column — supply the parts, not the whole.
                    "INSERT INTO auth.users "
                    "  (uid, tenant_id, email, first_name, last_name, workos_user_id) "
                    "VALUES (:uid, :t, :e, 'Other', 'Adviser', :w) RETURNING id"
                ),
                {
                    "uid": f"usr_{s}",
                    "t": ten,
                    "e": f"other-adv-{s}@firm.test",
                    "w": f"wos_oadv_{s}",
                },
            ).scalar_one()
        )
        c9 = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :u, 'individual', 'Poa Client', :e) RETURNING id"
                ),
                {"t": ten, "u": f"npoa_{s}", "e": f"poa_{s}@client.test"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                "VALUES (:t, :c, :u, 'power_of_attorney')"
            ),
            {"t": ten, "c": c9, "u": login},
        )
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :c, :u, 'adviser')"
            ),
            {"t": ten, "c": c9, "u": other_adviser},
        )
        conv_row = c.execute(
            text(
                "INSERT INTO messaging.conversations (tenant_id, contact_id, adviser_user_id) "
                "VALUES (:t, :c, :a) RETURNING id, uid"
            ),
            {"t": ten, "c": c1, "a": staff},
        ).one()
        conv = int(conv_row.id)
        # `sender_user_id` is deliberately NOT set, because `messaging.send_client_message` does not
        # set it — it passes None, so every client message in the real table has it NULL. Seeding it
        # here is what hid the client-to-adviser bug: the resolver matched on that column, the
        # fixture supplied it, the test passed, and no client message ever notified anyone.
        c.execute(
            text(
                "INSERT INTO messaging.messages "
                "  (tenant_id, conversation_id, contact_id, sender_kind, body) "
                "VALUES (:t, :v, :c, 'client', 'hello')"
            ),
            {"t": ten, "v": conv, "c": c1},
        )
        c.commit()

    action = {
        "event_type": "message.sent",
        "actor_id": login,
        "path_params": {},
        "metadata": {},
    }
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as sess, sess.begin():
            await sess.execute(text("SET LOCAL ROLE service_role"))
            got = await catalogue._resolve_message_thread_advisers(sess, action, ten)
    finally:
        await engine.dispose()

    ids = {r.user_id for r in got}
    assert ids == {staff}, "only the adviser of the thread's own client may be notified"
    assert other_adviser not in ids, "the other client's adviser must not hear about this message"


def test_no_merge_field_is_aml_or_screening_shaped() -> None:
    """Guardrail (docs/aml.md tipping-off): the merge-field vocabulary must NEVER expose an
    AML / screening value. No advertised Param may be named for screening, so a firm's authored copy
    can't leak a client's sanctions / PEP / adverse-media position into a notification or email."""
    banned = ("aml", "screen", "sanction", "pep", "adverse", "kyc", "watchlist")
    for trig in rules_service.catalogue_json():
        for param in trig["params"]:
            low = str(param["key"]).lower()
            hits = [b for b in banned if b in low]
            assert not hits, f"merge field {param['key']!r} is AML/screening-shaped ({hits})"


@pytest.mark.asyncio
async def test_build_context_resolves_the_widened_merge_fields(
    db: dict[str, str], notifbed: dict[str, object]
) -> None:
    """F2: build_context resolves the widened set — the actor's email, the event timestamp, and the
    client's book adviser — that the notification picker now advertises."""
    from app.notifications import context as ctx_mod

    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    staff = str(notifbed["staff"])  # 'Nadia Staff', and c1's book adviser
    action = {
        "event_type": "document.sent",  # subject-fallback contact resolution
        "actor_id": staff,
        "path_params": {},
        "metadata": {"subject": {"contact_ids": [c1]}},
        "created_at": datetime(2026, 1, 15, tzinfo=UTC),
    }
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            ctx = await ctx_mod.build_context(s, action, ten)
    finally:
        await engine.dispose()
    assert ctx["actor"]["name"] == "Nadia Staff"
    assert "@" in ctx["actor"]["email"]
    assert ctx["event"]["when"] == "15 Jan 2026"
    assert ctx["adviser"]["name"] == "Nadia Staff"  # c1's book adviser
    assert ctx["client"]["name"] == "Cleo Client"
    assert ctx["contact"]["id"] == str(c1)
    assert ctx["contact"]["uid"] == str(notifbed["c1_uid"])


def test_the_task_notices_render_their_own_email_and_ship_email_on() -> None:
    """Task email is rule-driven now: the bespoke worker sender is gone, so the two triggers must
    carry the codes it used to resolve AND ship email ON — otherwise the switch-over would silently
    stop every assignment and chase email a firm relies on today. Not mandatory: turning them off is
    deliberately the firm's to make."""
    by_key = {t.key: t for t in catalogue.CATALOGUE}
    for key, code in (("task.assigned", "task_assigned"), ("task.due_soon", "task_reminder")):
        trig = by_key[key]
        assert trig.subject_kind == "task"
        (aud,) = trig.audiences
        assert aud.key == "assignee"
        assert aud.email_code == code
        assert aud.default_email and aud.default_in_app and aud.builtin
        assert not aud.mandatory

    # And they fire on the WORKER's events, not on the create route's `task.created` — which named
    # no resolvable subject, so `{task.title}` rendered blank on every notice.
    assert by_key["task.assigned"].event_types == ("task.assigned",)
    assert by_key["task.due_soon"].event_types == ("task.due_soon",)
    assert not catalogue.defaults_for("task.created")


@pytest.mark.asyncio
async def test_build_context_names_the_task_a_worker_notice_is_about(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """The task notices sit on no route, so the flows subject resolver names nothing for them and
    `{task.title}` would render blank — the exact defect `task.created` had. The producer puts
    `task_id` on the event and the fallback reads the task off it."""
    from app.notifications import context as ctx_mod

    ten = int(notifbed["tenant_a"])
    with super_engine.connect() as c:
        task = (
            c.execute(
                text(
                    "INSERT INTO tasks.tasks (uid, tenant_id, assignee_kind, assignee_user_id, "
                    "  category, title, description, due_date) "
                    "VALUES (:u, :t, 'user', CAST(:a AS uuid), 'generic', 'Complete the fact-find',"
                    "  'Check the ATR answers', DATE '2026-01-22') RETURNING id, uid"
                ),
                {"u": f"tsk{uuid.uuid4().hex[:9]}", "t": ten, "a": str(notifbed["staff"])},
            )
            .mappings()
            .one()
        )
        c.commit()
    action = {
        "event_type": "task.assigned",
        "actor_id": None,
        "path_params": {"uid": task["uid"]},
        "metadata": {"subject": {"task_id": task["id"], "assignee_kind": "user"}},
        "created_at": datetime(2026, 1, 15, tzinfo=UTC),
    }
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            ctx = await ctx_mod.build_context(s, action, ten)
            # The chase notice resolves the same scope off the same key.
            action["event_type"] = "task.due_soon"
            due_ctx = await ctx_mod.build_context(s, action, ten)
    finally:
        await engine.dispose()
    # Everything the shipped copy names, and the uid the /tasks/{task.uid} target renders.
    assert ctx["task"]["title"] == "Complete the fact-find"
    assert ctx["task"]["description"] == "Check the ATR answers"
    assert ctx["task"]["due_date"] == "22 Jan 2026"
    assert ctx["task"]["uid"] == task["uid"]
    assert due_ctx["task"] == ctx["task"]


@pytest.mark.asyncio
async def test_a_task_notice_reaches_the_assignee_on_both_channels(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """The whole point of the switch-over: the worker emits ONE event and both tails act on it — the
    bell gets a notice naming the task, and the email tail enqueues the `task_assigned` template
    (email ships on, so today's behaviour survives)."""
    ten = int(notifbed["tenant_a"])
    staff = str(notifbed["staff"])
    with super_engine.connect() as c:
        task = (
            c.execute(
                text(
                    "INSERT INTO tasks.tasks (uid, tenant_id, assignee_kind, assignee_user_id, "
                    "  category, title) VALUES (:u, :t, 'user', CAST(:a AS uuid), 'generic', "
                    "  'Chase the ATR') RETURNING id, uid"
                ),
                {"u": f"tsk{uuid.uuid4().hex[:9]}", "t": ten, "a": staff},
            )
            .mappings()
            .one()
        )
        c.commit()
    await _seed_cursor_at_head(super_engine)
    await _seed_cursor_at_head(super_engine, "email")
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="task.assigned",
        subject={
            "task_id": task["id"],
            "task_uid": task["uid"],
            "assignee_kind": "user",
            "assignee_user_id": staff,
        },
        path_params={"uid": task["uid"]},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
        await email_consumer.consume_once(maker)
    finally:
        await engine.dispose()

    with super_engine.connect() as c:
        notices = (
            c.execute(
                text(
                    "SELECT category, title, link FROM notifications.notifications "
                    "WHERE recipient_user_id = CAST(:u AS uuid) AND event_type = 'task.assigned'"
                ),
                {"u": staff},
            )
            .mappings()
            .all()
        )
        emails = (
            c.execute(
                text(
                    "SELECT event_type, payload FROM flows.outbox "
                    "WHERE tenant_id = :t AND event_type = 'notify.tasks'"
                ),
                {"t": ten},
            )
            .mappings()
            .all()
        )
    assert len(notices) == 1
    assert notices[0]["category"] == "tasks"
    assert notices[0]["title"] == "New task: Chase the ATR"  # {task.title} resolved
    assert len(emails) == 1
    assert "Chase the ATR" in emails[0]["payload"]["subject"]  # the task_assigned template


@pytest.mark.asyncio
async def test_a_task_notice_lands_in_the_inbox_and_activity_via_the_worker(
    db: dict[str, str],
    super_engine: Engine,
    notifbed: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    live_comms_ledger: None,
) -> None:
    """The path a staffer actually takes: an action event, both tails, then the dispatcher
    calling ``send_email``. The bell is the in-app row the assignee's feed returns; Activity is
    the ledger row ``_send`` recorded — not an INSERT into ``comms.messages``.

    A password-reset is NOT this path (it never fans into ``notifications.notifications``); this
    is the one that does. ``_send`` is what the dispatcher calls with this payload; we invoke it
    on this tenant's row so a sibling test's leftover outbox cannot steal a ``dispatch_due`` batch.
    """
    ten = int(notifbed["tenant_a"])
    staff = str(notifbed["staff"])
    payloads = _enable_resend(monkeypatch)
    async with _service(db["app"]) as ses:
        await rules_service.load_default_rules(ses, ten)
    with super_engine.connect() as c:
        task = (
            c.execute(
                text(
                    "INSERT INTO tasks.tasks (uid, tenant_id, assignee_kind, assignee_user_id, "
                    "  category, title) VALUES (:u, :t, 'user', CAST(:a AS uuid), 'generic', "
                    "  'Chase the ATR') RETURNING id, uid"
                ),
                {"u": f"tsk{uuid.uuid4().hex[:9]}", "t": ten, "a": staff},
            )
            .mappings()
            .one()
        )
        c.commit()
    await _seed_cursor_at_head(super_engine)
    await _seed_cursor_at_head(super_engine, "email")
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="task.assigned",
        subject={
            "task_id": task["id"],
            "task_uid": task["uid"],
            "assignee_kind": "user",
            "assignee_user_id": staff,
        },
        path_params={"uid": task["uid"]},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
        await email_consumer.consume_once(maker)
    finally:
        await engine.dispose()

    assert await flows_notifications._send(ten, _queued_payload(super_engine, ten, "notify.tasks"))
    assert len(payloads) == 1
    assert payloads[0]["to"] == [notifbed["staff_email"]]

    async with _staff(db["app"], str(notifbed["wos_staff"]), str(notifbed["app_a"])) as ses:
        page = await notif_router.list_notifications(
            before=None, unread=False, limit=20, session=ses, _=_principal()
        )
    assert [item.event_type for item in page.items] == ["task.assigned"]
    assert page.items[0].title == "New task: Chase the ATR"

    async with _service(db["app"]) as ses:
        activity = await stream.fetch_messages(ses, tenant_id=ten)
    assert len(activity["items"]) == 1
    row = activity["items"][0]
    assert row["template_code"] == "task_assigned"
    assert row["event_type"] == "task.assigned"
    assert row["source"] == "worker"
    assert row["recipient_kind"] == "user"
    assert row["to_address"] == notifbed["staff_email"]
    assert row["status"] == "sent"
    assert_public_id(row["rule_uid"])


@pytest.mark.asyncio
async def test_a_document_send_lands_in_the_client_inbox_and_activity_via_the_worker(
    db: dict[str, str],
    super_engine: Engine,
    notifbed: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    live_comms_ledger: None,
) -> None:
    """The client-facing twin: ``document.sent`` fans to the contact's bell AND the email
    subscriber, then the dispatcher posts through ``send_email``. Activity names the contact,
    not an invented external address.
    """
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    payloads = _enable_resend(monkeypatch)
    async with _service(db["app"]) as ses:
        await rules_service.load_default_rules(ses, ten)
    await _seed_cursor_at_head(super_engine)
    await _seed_cursor_at_head(super_engine, "email")
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document.sent",
        subject={"contact_ids": [c1], "document_uid": "docabcdefgh"},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
        await email_consumer.consume_once(maker)
    finally:
        await engine.dispose()

    assert await flows_notifications._send(
        ten, _queued_payload(super_engine, ten, "notify.documents")
    )
    assert payloads[0]["to"] == [notifbed["client_email"]]

    async with _client(db["app"], str(notifbed["client_login"]), ten, [c1]) as ses:
        page = await notif_router.list_notifications(
            before=None, unread=False, limit=20, session=ses, _=_principal()
        )
    assert [item.event_type for item in page.items] == ["document.sent"]

    async with _service(db["app"]) as ses:
        activity = await stream.fetch_messages(ses, tenant_id=ten)
    assert len(activity["items"]) == 1
    row = activity["items"][0]
    assert row["template_code"] == "document_sent"
    assert row["event_type"] == "document.sent"
    assert row["source"] == "worker"
    assert row["recipient_kind"] == "contact"
    assert row["recipient_contact_id"] == c1
    assert row["to_address"] == notifbed["client_email"]
    assert row["status"] == "sent"
    assert_public_id(row["rule_uid"])


def test_catalogue_carries_raw_event_metadata() -> None:
    """The builder shows an author the underlying event a trigger sits on, so the catalogue JSON
    carries the raw audit.actions event type(s) and the subject kind alongside the friendly name."""
    by_key = {t["key"]: t for t in rules_service.catalogue_json()}
    shared = by_key["document.shared"]
    assert shared["event_types"] == ["document.sent"]
    assert shared["subject_kind"] == "contact"
    assert by_key["task.assigned"]["subject_kind"] == "task"
    assert all(t["event_types"] for t in by_key.values())
    assert shared["link_targets"] == [
        {
            "key": "document",
            "label": "The document",
            "href": "/documents/{document.uid}",
            "client": True,
        },
        {
            "key": "contact",
            "label": "The client",
            "href": "/clients/{contact.uid}",
            "client": False,
        },
    ]
    assert by_key["task.assigned"]["link_targets"] == [
        {"key": "task", "label": "The task", "href": "/tasks/{task.uid}", "client": False}
    ]
    assert by_key["account.model_changed"]["link_targets"] == [
        {
            "key": "account",
            "label": "The account",
            "href": "/accounts/{account.uid}",
            "client": True,
        }
    ]


@pytest.mark.asyncio
async def test_consumer_renders_merge_fields_in_authored_copy(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A firm authors adviser copy with ``{placeholders}``; the tail resolves the event's context
    and the delivered notification carries the substituted values (client name here), with an
    unresolved token dropped — the whole point of the dynamic-message feature."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])  # 'Cleo Client', advised by staff
    staff = str(notifbed["staff"])

    async with _service(db["app"]) as ses:
        await rules_service.create_rule(
            ses,
            ten,
            authored_by="firm",
            trigger_key="contact.bank_details_changed",
            audience="adviser",
            title="{client.name}'s bank details changed",
            # {document.name} is unresolvable for this event → dropped, no dangling token.
            body="Updated by {actor.name}.{document.name}",
            default_in_app=True,
        )

    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="client.bank_account_updated",
        subject={},
        path_params={"contact_id": c1},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
    finally:
        await engine.dispose()

    with super_engine.connect() as c:
        row = (
            c.execute(
                text(
                    "SELECT title, body FROM notifications.notifications "
                    "WHERE recipient_user_id = :u "
                    "  AND event_type = 'client.bank_account_updated'"
                ),
                {"u": staff},
            )
            .mappings()
            .one()
        )
    assert row["title"] == "Cleo Client's bank details changed"
    assert "{" not in row["body"]
    assert row["body"].startswith("Updated by ")


@pytest.mark.asyncio
async def test_document_send_renders_each_recipients_own_name(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A document sent to SEVERAL clients with a ``{client.name}`` rule renders each client's OWN
    name — never the first contact's (the multi-recipient fan-out would otherwise print client A's
    name in client B's notification: a cross-client disclosure)."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])  # 'Cleo Client'
    with super_engine.connect() as c:
        c2 = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :u, 'individual', 'Bea Buyer', :e) RETURNING id"
                ),
                {"t": ten, "u": f"nc2_{c1}", "e": f"bea_{c1}@client.test"},
            ).scalar_one()
        )
        c.commit()

    async with _service(db["app"]) as ses:
        await rules_service.create_rule(
            ses,
            ten,
            authored_by="firm",
            trigger_key="document.shared",
            audience="client",
            title="A document for {client.name}",
            default_in_app=True,
        )

    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document.sent",
        subject={"contact_ids": [c1, c2]},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
    finally:
        await engine.dispose()

    with super_engine.connect() as c:
        titles = dict(
            c.execute(
                text(
                    "SELECT recipient_contact_id, title FROM notifications.notifications "
                    "WHERE event_type = 'document.sent' "
                    "  AND recipient_kind = 'contact'"
                )
            ).all()
        )
    assert titles[c1] == "A document for Cleo Client"
    assert titles[c2] == "A document for Bea Buyer"


@pytest.mark.asyncio
async def test_consumer_renders_link_to_specific_document(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A rule whose destination is "the document" (``/documents/{document.uid}``) renders to the
    SPECIFIC document's URL per notification, so a click lands on the exact document that was sent —
    not a generic list. The uid rides the send route's path param, so no document row is needed."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    async with _service(db["app"]) as ses:
        await rules_service.create_rule(
            ses,
            ten,
            authored_by="firm",
            trigger_key="document.shared",
            audience="client",
            title="A document was shared with you",
            link="/documents/{document.uid}",
            default_in_app=True,
        )

    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document.sent",
        subject={"contact_ids": [c1]},
        path_params={"uid": "doc_abc123"},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
    finally:
        await engine.dispose()

    with super_engine.connect() as c:
        link = c.execute(
            text(
                "SELECT link FROM notifications.notifications "
                "WHERE event_type = 'document.sent' AND recipient_contact_id = :c"
            ),
            {"c": c1},
        ).scalar_one()
    assert link == "/documents/doc_abc123"


# The `notify` tests went with the feature: requests replaced it; nothing emits `document.notified`.

# ---- the router ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_subscriber_silent_by_default(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A WRAPPER notice (no dedicated template) defaults email OFF, so it enqueues no email out of
    the box — only notices with a dedicated template default email-on (see the next test)."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    await _seed_cursor_at_head(super_engine, "email")
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="client.bank_account_updated",
        subject={},
        path_params={"contact_id": c1},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await email_consumer.consume_once(maker)
    finally:
        await engine.dispose()
    with super_engine.connect() as c:
        n = int(
            c.execute(
                text("SELECT count(*) FROM flows.outbox WHERE tenant_id = :t"), {"t": ten}
            ).scalar_one()
        )
    assert n == 0


@pytest.mark.asyncio
async def test_document_notices_email_by_default(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """The two document notices ship email-ON (they carry a dedicated template), so a document-send
    event enqueues an email with NO tenant config — the one place the channel defaults on."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    await _seed_cursor_at_head(super_engine, "email")
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document.sent",
        subject={"contact_ids": [c1]},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await email_consumer.consume_once(maker)
    finally:
        await engine.dispose()
    with super_engine.connect() as c:
        n = int(
            c.execute(
                text(
                    "SELECT count(*) FROM flows.outbox "
                    "WHERE tenant_id = :t AND event_type = 'notify.documents'"
                ),
                {"t": ten},
            ).scalar_one()
        )
    assert n == 1


@pytest.mark.asyncio
async def test_email_subscriber_enqueues_when_category_email_on(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """With the firm's documents email default ON, a document-send event enqueues a branded
    notify.documents email to the client's address on flows.outbox (the dispatcher sends it)."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO notifications.tenant_config (tenant_id, category, enabled, email) "
                "VALUES (:t, 'documents', true, true)"
            ),
            {"t": ten},
        )
        c.commit()
    await _seed_cursor_at_head(super_engine, "email")
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document.sent",
        subject={"contact_ids": [c1]},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await email_consumer.consume_once(maker)
    finally:
        await engine.dispose()
    with super_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT event_type, payload FROM flows.outbox "
                    "WHERE tenant_id = :t AND event_type = 'notify.documents'"
                ),
                {"t": ten},
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["to"].startswith("cleo_")  # the client contact's address
    assert payload["html"]  # a rendered, branded body


@pytest.mark.asyncio
async def test_email_wraps_the_notification_content_with_the_link(
    db: dict[str, str],
    super_engine: Engine,
    notifbed: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rule set to the general 'notification' wrapper renders that firm-editable template filled
    with the notification's OWN title/body — merge fields resolved for THIS recipient — and a CTA
    absolutised to the linked item."""
    from app.notifications import email_consumer as ec

    monkeypatch.setattr(ec.settings, "client_portal_base_url", "https://portal.test", raising=False)
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO notifications.tenant_config (tenant_id, category, enabled, email) "
                "VALUES (:t, 'documents', true, true)"
            ),
            {"t": ten},
        )
        c.commit()
    async with _service(db["app"]) as ses:
        await rules_service.create_rule(
            ses,
            ten,
            authored_by="firm",
            trigger_key="document.shared",
            audience="client",
            title="A document for {client.name}",
            body="Your adviser has shared a document.",
            link="/documents/{document.uid}",
            default_in_app=True,
            default_email=True,
            # Force the general wrapper (this trigger's default is the 'document_sent' template).
            email_code="notification",
        )
    await _seed_cursor_at_head(super_engine, "email")
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document.sent",
        subject={"contact_ids": [c1]},
        path_params={"uid": "doc_xyz"},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await ec.consume_once(maker)
    finally:
        await engine.dispose()

    with super_engine.connect() as c:
        payload = (
            c.execute(
                text(
                    "SELECT payload FROM flows.outbox "
                    "WHERE tenant_id = :t AND event_type = 'notify.documents'"
                ),
                {"t": ten},
            )
            .mappings()
            .one()["payload"]
        )
    assert payload["subject"] == "A document for Cleo Client"
    assert "Your adviser has shared a document." in payload["html"]
    assert "https://portal.test/documents/doc_xyz" in payload["html"]


def test_document_upload_offers_the_document_link_target() -> None:
    """The client-upload notice offers a 'The document' click target — the uploaded document is
    resolved from the request item at delivery (context._resolved_document_uid), so it is a valid
    destination even though the event's path names the request item, not the document."""
    from app.notifications import catalogue

    trig = catalogue.trigger("client.document_uploaded")
    assert trig is not None
    targets = {t["key"] for t in catalogue.link_targets_for(trig)}
    assert "document" in targets


def test_the_notices_with_their_own_email_template_ship_pointing_at_it() -> None:
    """Four notices ship pointing at a dedicated email template — the two rich document emails and
    the two task notices, which inherited theirs from the bespoke sender they replaced. Every other
    audience defaults to the general wrapper."""
    by_key = {t["key"]: t for t in rules_service.catalogue_json()}

    def audience(trigger_key: str, audience_key: str) -> dict:
        return next(a for a in by_key[trigger_key]["audiences"] if a["key"] == audience_key)

    assert audience("document.shared", "client")["default_email_code"] == "document_sent"
    assert audience("document.request", "client")["default_email_code"] == "request_raised"
    assert audience("task.assigned", "assignee")["default_email_code"] == "task_assigned"
    assert audience("task.due_soon", "assignee")["default_email_code"] == "task_reminder"
    # The wrapper is still the default everywhere else.
    assert audience("task.completed", "assignee")["default_email_code"] is None


def test_wrapper_context_fills_the_slots() -> None:
    """The notifications consumer builds a DYNAMIC template code (so it's exempt from the parity
    file's literal-code scan); this pins that its context still fills the general wrapper's own
    slots (title/body/button_* + brand) — the keys the preview samples for 'notification'."""
    from app.email.preview import _CODE_KEYS
    from app.notifications import email_consumer as ec

    ctx = ec._template_context(
        brand_tokens={"name": "Oakview"},
        title="A document was shared",
        body="Open your portal.",
        cta=("Open", "https://portal.test/documents/doc_1"),
        portal_base="https://portal.test",
        rctx={"client": {"name": "Cleo Client"}, "document": {"name": "Suitability report"}},
    )
    # Every key the preview samples is set, so a firm's edited wrapper never renders a blank slot.
    assert "brand" in ctx
    for key in _CODE_KEYS["notification"]:
        assert key in ctx, f"the wrapper context is missing {key!r}"
    assert ctx["title"] == "A document was shared"
    assert ctx["button_url"] == "https://portal.test/documents/doc_1"
    assert ctx["document_name"] == "Suitability report"
    assert ctx["first_name"] == "Cleo"  # greeting name from the resolved full name


def test_portal_url_falls_back_to_the_recipient_base_when_there_is_no_cta() -> None:
    """§E: the document templates' "Open your portal" button reads `portal_url`. It keeps the
    notice's deep-link when there is one (so the document email still points at the document), but
    FALLS BACK to the recipient's own portal home when the CTA is empty — a staff-audience
    document-template rule, or a tenant with no registered platform host, used to leave `portal_url`
    blank and the `{% if portal_url %}` button vanished."""
    from app.notifications import email_consumer as ec

    ctx = ec._template_context(
        brand_tokens={}, title="t", body="", cta=None, portal_base="https://platform.test/", rctx={}
    )
    assert ctx["button_url"] == ""
    assert ctx["portal_url"] == "https://platform.test"
    ctx2 = ec._template_context(
        brand_tokens={},
        title="t",
        body="",
        cta=("Open", "https://portal.test/documents/doc_9"),
        portal_base="https://portal.test",
        rctx={},
    )
    assert ctx2["portal_url"] == "https://portal.test/documents/doc_9"
    # No CTA and no base ⇒ no portal_url, so the guard hides the button, not a broken href.
    ctx3 = ec._template_context(
        brand_tokens={}, title="t", body="", cta=None, portal_base=None, rctx={}
    )
    assert ctx3["portal_url"] == ""


def test_email_template_options_are_the_selectable_notification_codes() -> None:
    """The builder's "Email template" dropdown offers the notification-category codes, the general
    wrapper included. The category is the whole rule: the one code that used to be excluded by name
    (the retired external secure-link share) left the catalogue with its feature."""
    codes = {o["code"] for o in rules_service.email_templates_json()}
    # `task_notice` split into `task_assigned` + `task_reminder`. Both stay rule-offerable, so both
    # keep a `due_date` guard: a rule can render either against an event context with no deadline.
    assert {
        "notification",
        "document_sent",
        "request_raised",
        "task_assigned",
        "task_reminder",
    } <= codes
    # Transactional codes (invite / password reset) are never rule templates.
    assert "invite" not in codes and "password_reset" not in codes


@pytest.mark.asyncio
async def test_load_default_rules_seeds_builtins_and_is_idempotent(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """'Load defaults' materialises the catalogue's built-ins as REAL, editable/deletable rows
    (the UI fabricates nothing), carrying the document notices' own email templates, and re-running
    adds nothing."""
    ten = int(notifbed["tenant_a"])
    async with _service(db["app"]) as ses:
        created = await rules_service.load_default_rules(ses, ten)
    async with _service(db["app"]) as ses:
        rows = await rules_service.list_rules(ses, ten)
    assert created == len(rows) > 0
    assert all(r["uid"] and r["authored_by"] == "graphene" for r in rows)
    by_trig = {(r["trigger_key"], r["audience"]): r for r in rows}
    assert by_trig[("document.shared", "client")]["email_code"] == "document_sent"
    assert by_trig[("document.request", "client")]["email_code"] == "request_raised"
    async with _service(db["app"]) as ses:
        again = await rules_service.load_default_rules(ses, ten)
    assert again == 0


@pytest.mark.asyncio
async def test_rule_changes_are_versioned_with_attribution(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """Every create/update snapshots a version attributed to the tier that made it (operations =
    Graphene, firm = the firm), newest first — the change audit the firm can read. A Graphene create
    then a firm edit produces two versions with the right scopes + the value each held."""
    ten = int(notifbed["tenant_a"])
    async with _service(db["app"]) as ses:
        rule = await rules_service.create_rule(
            ses,
            ten,
            authored_by="graphene",
            trigger_key="document.shared",
            audience="client",
            title="v1 title",
        )
    uid = str(rule["uid"])
    async with _service(db["app"]) as ses:
        await rules_service.update_rule(ses, uid, scope="firm", title="v2 title")
    async with _service(db["app"]) as ses:
        versions = await rules_service.list_rule_versions(ses, uid)
        current = await rules_service.get_rule(ses, uid)
    assert [v["version_number"] for v in versions] == [2, 1]
    assert versions[0]["action"] == "updated"
    assert versions[0]["authored_by_scope"] == "firm"
    assert versions[0]["title"] == "v2 title"
    assert versions[1]["action"] == "created"
    assert versions[1]["authored_by_scope"] == "operations"
    assert versions[1]["title"] == "v1 title"
    assert current is not None and current["last_editor_scope"] == "firm"
    assert current["authored_by"] == "graphene"


def test_unknown_email_template_is_refused() -> None:
    """A rule's email_code must be a real selectable template — a typo can't silently render nothing
    when a firm turns the email on."""
    import pytest as _pytest

    with _pytest.raises(rules_service.RuleError):
        rules_service._validate("document.shared", "client", None, "not_a_template")
    rules_service._validate("document.shared", "client", None, "notification")
    rules_service._validate("document.shared", "client", None, "document_sent")


@pytest.mark.asyncio
async def test_document_share_notice_renders_its_own_template_by_default(
    db: dict[str, str],
    super_engine: Engine,
    notifbed: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no email_code chosen, the built-in document.shared notice renders the 'document_sent'
    template (not the general wrapper) — its shipped copy, filled from the event's context."""
    from app.notifications import email_consumer as ec

    monkeypatch.setattr(ec.settings, "client_portal_base_url", "https://portal.test", raising=False)
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO notifications.tenant_config (tenant_id, category, enabled, email) "
                "VALUES (:t, 'documents', true, true)"
            ),
            {"t": ten},
        )
        c.commit()
    await _seed_cursor_at_head(super_engine, "email")
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document.sent",
        subject={"contact_ids": [c1]},
        path_params={"uid": "doc_xyz"},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await ec.consume_once(maker)
    finally:
        await engine.dispose()
    with super_engine.connect() as c:
        payload = (
            c.execute(
                text(
                    "SELECT payload FROM flows.outbox "
                    "WHERE tenant_id = :t AND event_type = 'notify.documents'"
                ),
                {"t": ten},
            )
            .mappings()
            .one()["payload"]
        )
    assert "is ready for you" in payload["subject"]
    assert "has been shared" in payload["html"]
    assert "https://portal.test/documents/doc_xyz" in payload["html"]


@pytest.mark.asyncio
async def test_request_email_carries_item_list_and_note(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """The document.request notification email (request_raised template) carries the request's ITEM
    LIST + covering NOTE — resolved from the request the event created (_document_extras) — so
    retiring the inline documents email loses no content on the notification path."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    staff = str(notifbed["staff"])
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        rid = c.execute(
            text(
                "INSERT INTO documents.requests "
                "  (uid, tenant_id, contact_id, requested_by, priority, message) "
                "VALUES (:u, :t, :c, CAST(:by AS uuid), 'normal', :m) RETURNING id"
            ),
            {"u": f"req_{s}", "t": ten, "c": c1, "by": staff, "m": "Please send these over."},
        ).scalar_one()
        for title in ("Proof of identity", "Proof of address"):
            c.execute(
                text(
                    "INSERT INTO documents.request_items "
                    "  (uid, request_id, tenant_id, action, title) "
                    "VALUES (:u, :r, :t, 'upload', :ti)"
                ),
                {"u": f"rqi_{uuid.uuid4().hex[:8]}", "r": rid, "t": ten, "ti": title},
            )
        c.commit()
    await _seed_cursor_at_head(super_engine, "email")
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document_request.created",
        subject={"contact_ids": [c1]},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await email_consumer.consume_once(maker)
    finally:
        await engine.dispose()
    with super_engine.connect() as c:
        html = str(
            c.execute(
                text(
                    "SELECT payload FROM flows.outbox "
                    "WHERE tenant_id = :t AND event_type = 'notify.documents'"
                ),
                {"t": ten},
            )
            .mappings()
            .one()["payload"]["html"]
        )
    assert "Proof of identity" in html and "Proof of address" in html
    assert "Please send these over." in html


@pytest.mark.asyncio
async def test_group_request_notifies_every_member(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A GROUP document request fans out per member BELOW the tail's view: one audit action for the
    group endpoint, N per-member request rows. The ``document.request`` resolver expands the group,
    so BOTH consumers reach every member — the fix for "group requests notify no one". Each member
    gets an in-app documents notice AND a request_raised email carrying the shared item list."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    staff = str(notifbed["staff"])
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        c2 = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email) "
                    "VALUES (:t, :u, 'individual', 'Theo Trust', :e) RETURNING id"
                ),
                {"t": ten, "u": f"ngc2_{s}", "e": f"theo_{s}@client.test"},
            ).scalar_one()
        )
        gid = int(
            c.execute(
                text(
                    "INSERT INTO messaging.groups (tenant_id, name, created_by) "
                    "VALUES (:t, :n, CAST(:by AS uuid)) RETURNING id"
                ),
                {"t": ten, "n": f"Group {s}", "by": staff},
            ).scalar_one()
        )
        for cid in (c1, c2):
            c.execute(
                text(
                    "INSERT INTO messaging.group_members (tenant_id, group_id, contact_id) "
                    "VALUES (:t, :g, :c)"
                ),
                {"t": ten, "g": gid, "c": cid},
            )
            rid = c.execute(
                text(
                    "INSERT INTO documents.requests "
                    "  (uid, tenant_id, contact_id, requested_by, priority, message, group_id) "
                    "VALUES (:u, :t, :c, CAST(:by AS uuid), 'normal', 'Group ask.', :g) "
                    "RETURNING id"
                ),
                {"u": f"req_{uuid.uuid4().hex[:8]}", "t": ten, "c": cid, "by": staff, "g": gid},
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO documents.request_items "
                    "  (uid, request_id, tenant_id, action, title) "
                    "VALUES (:u, :r, :t, 'upload', 'Proof of identity')"
                ),
                {"u": f"rqi_{uuid.uuid4().hex[:8]}", "r": rid, "t": ten},
            )
        c.commit()
    await _seed_cursor_at_head(super_engine)  # in-app cursor
    await _seed_cursor_at_head(super_engine, "email")  # email cursor
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="message_group.document_requested",
        subject={},
        path_params={"group_id": gid},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)  # in-app
        await email_consumer.consume_once(maker)  # email
    finally:
        await engine.dispose()
    with super_engine.connect() as c:
        inapp = (
            c.execute(
                text(
                    "SELECT recipient_contact_id FROM notifications.notifications "
                    "WHERE tenant_id = :t AND category = 'documents'"
                ),
                {"t": ten},
            )
            .scalars()
            .all()
        )
        emails = (
            c.execute(
                text(
                    "SELECT payload FROM flows.outbox "
                    "WHERE tenant_id = :t AND event_type = 'notify.documents'"
                ),
                {"t": ten},
            )
            .mappings()
            .all()
        )
        want_emails = set(
            c.execute(
                text("SELECT email FROM crm.contacts WHERE id = ANY(:ids)"),
                {"ids": [c1, c2]},
            ).scalars()
        )
    assert set(inapp) == {c1, c2}
    assert {e["payload"]["to"] for e in emails} == want_emails
    assert all("Proof of identity" in e["payload"]["html"] for e in emails)


def test_every_trigger_listens_for_an_event_something_actually_emits() -> None:
    """A catalogue trigger keyed on a name no route emits is a feature that silently never fires.

    Nothing else catches it. The delivery tests below insert a SYNTHETIC action row with the event
    type written out by hand, so they prove the resolver and the fan-out and assume the very thing
    most likely to be wrong — that the route emits that name. Rename an operation id, or retire a
    route, and every one of those tests still passes while the notification stops existing. There is
    no error, no log and no failing gate; the only symptom is a firm quietly not being told things.

    So this asks the real app: collect what every mutating route would emit (`event_type_for`, the
    same call the middleware makes) plus the declared synthetic types, and require the catalogue's
    whole listening vocabulary to be inside it.
    """
    from fastapi.routing import APIRoute

    from app import audit_actions as actions
    from app.main import app as fastapi_app
    from app.notifications import catalogue as cat

    def walk(routes: object) -> object:
        # Included routers are nested rather than flattened into `app.routes`, so recurse.
        for r in routes:  # type: ignore[attr-defined]
            if isinstance(r, APIRoute):
                yield r
            inner = getattr(r, "original_router", None)
            if inner is not None:
                yield from walk(inner.routes)

    emitted = {
        actions.event_type_for(r)
        for r in walk(fastapi_app.routes)  # type: ignore[arg-type]
        if r.methods & {"POST", "PUT", "PATCH", "DELETE"}
    }
    emitted |= set(actions.SYNTHETIC_EVENT_TYPES.values())

    listened = {et for trig in cat.CATALOGUE for et in trig.event_types}
    dead = sorted(listened - emitted)
    assert not dead, (
        f"these notification triggers listen for event types nothing emits: {dead}. "
        "Either a route was renamed/retired, or the catalogue names an event that never existed — "
        "the notification simply never fires."
    )


# ---- chat messages notify the other party ----------------------------------------------------


@pytest.mark.asyncio
async def test_client_message_notifies_the_adviser(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A client's 1:1 chat message → an in-app notice for the client's book adviser. The client's
    login is the actor; the resolver maps it (the CONVERSATION it sent on → client_advisers) to the
    adviser, with NO SUBJECT_FIELDS entry and no message body — only the sender's name in the copy.

    The conversation + message are seeded because the event only ever EXISTS as the record of a real
    send: the resolver reads the thread off `messaging.messages` (not every contact the login is
    linked to — see `test_client_chat_notifies_only_the_thread_s_own_adviser`)."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    staff = str(notifbed["staff"])  # advises c1 via client_advisers
    client_login = str(notifbed["client_login"])  # c1's portal login = the actor
    with super_engine.connect() as c:
        conv_row = c.execute(
            text(
                "INSERT INTO messaging.conversations (tenant_id, contact_id, adviser_user_id) "
                "VALUES (:t, :c, :a) RETURNING id, uid"
            ),
            {"t": ten, "c": c1, "a": staff},
        ).one()
        conv = int(conv_row.id)
        # No `sender_user_id`: the send path never writes one for a client (see the note on the
        # other seed above, and `_resolve_message_thread_advisers`).
        c.execute(
            text(
                "INSERT INTO messaging.messages "
                "  (tenant_id, conversation_id, contact_id, sender_kind, body) "
                "VALUES (:t, :v, :c, 'client', 'hello')"
            ),
            {"t": ten, "v": conv, "c": c1},
        )
        c.commit()
    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="message.sent",
        # What the middleware lifts off the send response (event_schema/registry): whose channel it
        # is. Without it the notice falls back to naming the LOGIN.
        subject={"contact_id": c1},
        actor_id=client_login,
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
        await consumer.consume_once(maker)  # re-run: must not duplicate
    finally:
        await engine.dispose()
    with super_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT recipient_user_id::text, category, title "
                    "FROM notifications.notifications "
                    "WHERE tenant_id = :t AND category = 'messages' "
                    "  AND event_type = 'message.sent'"
                ),
                {"t": ten},
            )
            .mappings()
            .all()
        )
    assert [r["recipient_user_id"] for r in rows] == [staff]
    assert rows[0]["category"] == "messages"
    # The copy names the CLIENT whose channel it is, never the message body (never captured) and
    # never the LOGIN. This fixture is the real shape: the contact is "Cleo Client" while the login
    # carries no name at all, so {actor.name} falls back to its email address - an adviser reading
    # "New message from cleo@client.test" cannot tell who it is from. A login can also act for
    # several contacts, so the actor is not merely differently-spelled, it can be another person.
    assert rows[0]["title"] == "New message from Cleo Client"
    assert "@" not in rows[0]["title"], "named the login's email instead of the client"


@pytest.mark.asyncio
async def test_the_adviser_is_told_even_when_they_are_the_sending_login(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A client's message reaches their adviser - even when the adviser's login is the one that
    sent it.

    That combination is not exotic: a firm's own staff are often also its clients, so one
    `auth.users` row is both the contact's adviser and the login linked to the contact. A filter
    that skipped "your own message" by IDENTITY would have dropped exactly that person - the one
    adviser most likely to be watching for it, and (0245: one adviser per client) the only one
    there is to tell.

    The "don't tell me about my own send" case is not lost. It is settled by DIRECTION: a staff send
    never reaches this resolver at all (see the case above), which is where that rule belongs.
    """
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    staff = str(notifbed["staff"])
    login = str(notifbed["client_login"])
    with super_engine.connect() as c:
        # The client's own login TAKES OVER as the contact's one adviser (0245) -- `staff`, the
        # notifbed fixture's adviser, moves to a support role so the unique index allows it.
        c.execute(
            text(
                "UPDATE crm.client_advisers SET role = 'support' "
                "WHERE tenant_id = :t AND contact_id = :c AND adviser_id = CAST(:a AS uuid)"
            ),
            {"t": ten, "c": c1, "a": staff},
        )
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :c, :a, 'adviser')"
            ),
            {"t": ten, "c": c1, "a": login},
        )
        c.commit()
    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="message.sent",
        subject={"contact_id": c1},
        actor_id=login,
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
    finally:
        await engine.dispose()
    with super_engine.connect() as c:
        got = {
            r[0]
            for r in c.execute(
                text(
                    "SELECT recipient_user_id::text FROM notifications.notifications "
                    "WHERE tenant_id = :t AND event_type = 'message.sent'"
                ),
                {"t": ten},
            ).all()
        }
    assert got == {login}, f"the adviser should be told even though they sent it, got {got}"
    assert staff not in got, "told the support-role colleague instead of the adviser"


@pytest.mark.asyncio
async def test_advisers_of_contact_excludes_a_paraplanner(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """The notification audience 'adviser' is the one `role = 'adviser'` row (0245), unique per
    contact -- a paraplanner on the same team is not told as though they were the adviser."""
    from app.notifications import catalogue

    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    staff = str(notifbed["staff"])  # c1's adviser, from the notifbed fixture
    with super_engine.connect() as c:
        paraplanner = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :e, :w, true) RETURNING id"
                ),
                {"uid": f"npara_{c1}", "t": ten, "e": f"para_{c1}@firm.test", "w": f"nwpara_{c1}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :c, :a, 'paraplanner')"
            ),
            {"t": ten, "c": c1, "a": paraplanner},
        )
        c.commit()

    async with _service(db["app"]) as session:
        recipients = await catalogue._advisers_of_contact(session, c1, ten)

    user_ids = {r.user_id for r in recipients}
    assert user_ids == {staff}
    assert paraplanner not in user_ids


@pytest.mark.asyncio
async def test_the_named_contact_wins_over_a_newer_message_elsewhere(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """The event NAMES whose channel it is, and that beats inferring it from the newest message.

    The fallback lookup asks for the newest client message on any conversation this login can reach.
    A login linked to SEVERAL contacts (authorised person, delegate) breaks that: by the time the
    tail runs - deliberately LAG_SECONDS behind, and `resweep_once` re-reads minutes back - a newer
    message may exist on a different one of those contacts, sent by somebody else entirely. The
    inference then resolves the wrong client and notifies THEIR advisers, who have no connection to
    the message. That is a disclosure, not a missed notice.

    So this seeds exactly that shape: the actor's message on c1, then a NEWER one on c9, and the
    action naming c1. The c9 adviser must hear nothing.
    """
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    staff = str(notifbed["staff"])
    login = str(notifbed["client_login"])
    with super_engine.connect() as c:
        c9 = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name) "
                    "VALUES (:t, :u, 'individual', 'Other Client') RETURNING id"
                ),
                {"t": ten, "u": f"nrace_{ten}"},
            ).scalar_one()
        )
        other_adviser = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :e, :w, true) RETURNING id"
                ),
                {"uid": f"nrad_{ten}", "t": ten, "e": f"rad{ten}@firm.test", "w": f"nwrad_{ten}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :c, :a, 'adviser')"
            ),
            {"t": ten, "c": c9, "a": other_adviser},
        )
        # The one login reaches BOTH contacts - the condition the inference cannot survive.
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id) "
                "VALUES (:t, :c, :u) ON CONFLICT DO NOTHING"
            ),
            {"t": ten, "c": c9, "u": login},
        )
        for contact, when in ((c1, "-2 minutes"), (c9, "-1 minute")):  # c9's is the NEWER
            conv = int(
                c.execute(
                    text(
                        "INSERT INTO messaging.conversations (tenant_id, contact_id, "
                        "  adviser_user_id) VALUES (:t, :c, :a) RETURNING id"
                    ),
                    {"t": ten, "c": contact, "a": staff},
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO messaging.messages "
                    "  (tenant_id, conversation_id, contact_id, sender_kind, body, created_at) "
                    # Bound, not interpolated - a literal interval here is an S608 the linter is
                    # right to flag even in a test.
                    "VALUES (:t, :v, :c, 'client', 'hello', now() + CAST(:when AS interval))"
                ),
                {"t": ten, "v": conv, "c": contact, "when": when},
            )
        c.commit()
    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="message.sent",
        subject={"contact_id": c1},  # the event says c1; the NEWEST message is on c9
        actor_id=login,
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
    finally:
        await engine.dispose()
    with super_engine.connect() as c:
        got = [
            r[0]
            for r in c.execute(
                text(
                    "SELECT recipient_user_id::text FROM notifications.notifications "
                    "WHERE tenant_id = :t AND event_type = 'message.sent'"
                ),
                {"t": ten},
            ).all()
        ]
    assert got == [staff], f"expected c1's adviser only, got {got}"
    assert other_adviser not in got, "notified the advisers of a client the message was not about"


@pytest.mark.asyncio
async def test_an_adviser_s_own_message_does_not_notify_advisers(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A STAFF send notifies the client and NOBODY on the platform side.

    Both ends of the channel emit `message.sent`, and the adviser resolver asks "which thread has
    this login most recently sent a client message on". A staff login answers that just as readily
    as a client one, because staff can be linked to a contact too (`crm.contact_users` - their own
    client record at capacity 'self', or someone else's as an authorised person). The adviser's own
    message then notified the whole book adviser list, the sender included: three notices for one
    send, two of them telling people that the person who just typed the message had sent it.

    The direction marker is the route: a staff send names its conversation on the PATH, a client's
    thread route names nothing.
    """
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    staff = str(notifbed["staff"])
    client_login = str(notifbed["client_login"])
    with super_engine.connect() as c:
        conv_row = c.execute(
            text(
                "INSERT INTO messaging.conversations (tenant_id, contact_id, adviser_user_id) "
                "VALUES (:t, :c, :a) RETURNING id, uid"
            ),
            {"t": ten, "c": c1, "a": staff},
        ).one()
        conv, conv_uid = int(conv_row.id), str(conv_row.uid)
        # A client message already exists on the thread - the row the adviser resolver would latch
        # onto. The staff login is linked to the same contact, which is what made it reachable.
        c.execute(
            text(
                "INSERT INTO messaging.messages "
                "  (tenant_id, conversation_id, contact_id, sender_kind, body) "
                "VALUES (:t, :v, :c, 'client', 'earlier')"
            ),
            {"t": ten, "v": conv, "c": c1},
        )
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id) "
                "VALUES (:t, :c, :u) ON CONFLICT DO NOTHING"
            ),
            {"t": ten, "c": c1, "u": staff},
        )
        # A SECOND member of staff on the same client's team (0245: a support role, since `staff`
        # already holds the one `adviser` row), and not the one sending. Without them the case is
        # vacuous: the only team member would be the actor, whom the self-exclusion filter removes
        # anyway, so it would pass with the direction check deleted - which is exactly what it is
        # here to catch. The real book had this shape (two on the team; the colleague was notified
        # under the old free-for-all "every adviser" rule, which 0245 narrowed to the one adviser).
        colleague = str(
            c.execute(
                text(
                    # `name` is a GENERATED column here - it is derived, not supplied.
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :e, :w, true) RETURNING id"
                ),
                {
                    "uid": f"ncol_{conv}",
                    "t": ten,
                    "e": f"col{conv}@firm.test",
                    "w": f"nwcol_{conv}",
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :c, :a, :r)"
            ),
            {"t": ten, "c": c1, "a": colleague, "r": "support"},
        )
        c.commit()
    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="message.sent",
        subject={"contact_id": c1},
        path_params={"conversation_uid": conv_uid},  # the staff route names the UID (0112)
        actor_id=staff,
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
    finally:
        await engine.dispose()
    with super_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT recipient_kind, recipient_user_id::text AS u "
                    "FROM notifications.notifications "
                    "WHERE tenant_id = :t AND event_type = 'message.sent'"
                ),
                {"t": ten},
            )
            .mappings()
            .all()
        )
    kinds = sorted(r["recipient_kind"] for r in rows)
    assert kinds == ["contact"], f"a staff send must notify the client only, got {kinds}"
    assert staff not in [r["u"] for r in rows], "the sender was notified of their own message"
    assert colleague not in [r["u"] for r in rows], "a colleague was told about the adviser's send"
    assert client_login not in [r["u"] for r in rows]


@pytest.mark.asyncio
async def test_staff_message_notifies_the_conversation_contact(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A staff 1:1 chat message → an in-app notice for the conversation's client contact, resolved
    from the conversation_id on the route path."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    staff = str(notifbed["staff"])
    with super_engine.connect() as c:
        conv_uid = str(
            c.execute(
                text(
                    "INSERT INTO messaging.conversations (tenant_id, contact_id, adviser_user_id) "
                    "VALUES (:t, :c, CAST(:a AS uuid)) RETURNING uid"
                ),
                {"t": ten, "c": c1, "a": staff},
            ).scalar_one()
        )
        c.commit()
    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="message.sent",
        subject={},
        path_params={"conversation_uid": conv_uid},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
    finally:
        await engine.dispose()
    with super_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT recipient_contact_id FROM notifications.notifications "
                    "WHERE tenant_id = :t AND category = 'messages' "
                    "  AND event_type = 'message.sent'"
                ),
                {"t": ten},
            )
            .scalars()
            .all()
        )
    assert rows == [c1]


@pytest.mark.asyncio
async def test_group_message_notifies_every_member(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A staff GROUP chat message → an in-app notice for every group member contact (one send event,
    fanned out by the group resolver — the same expansion the group-request path uses)."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    staff = str(notifbed["staff"])
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        c2 = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name) "
                    "VALUES (:t, :u, 'individual', 'Theo Trust') RETURNING id"
                ),
                {"t": ten, "u": f"mgc2_{s}"},
            ).scalar_one()
        )
        gid = int(
            c.execute(
                text(
                    "INSERT INTO messaging.groups (tenant_id, name, created_by) "
                    "VALUES (:t, :n, CAST(:by AS uuid)) RETURNING id"
                ),
                {"t": ten, "n": f"Group {s}", "by": staff},
            ).scalar_one()
        )
        for cid in (c1, c2):
            c.execute(
                text(
                    "INSERT INTO messaging.group_members (tenant_id, group_id, contact_id) "
                    "VALUES (:t, :g, :c)"
                ),
                {"t": ten, "g": gid, "c": cid},
            )
        c.commit()
    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="message_group.message_sent",
        subject={},
        path_params={"group_id": gid},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
    finally:
        await engine.dispose()
    with super_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT recipient_contact_id FROM notifications.notifications "
                    "WHERE tenant_id = :t AND category = 'messages' "
                    "  AND event_type = 'message_group.message_sent'"
                ),
                {"t": ten},
            )
            .scalars()
            .all()
        )
    assert set(rows) == {c1, c2}


@pytest.mark.asyncio
async def test_preferences_flag_a_mandatory_notification(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A mandatory rule surfaces `mandatory=True` on the preferences surface, so the UI can lock it
    (the recipient can't turn off something the resolver forces on) — closing the D5 UX gap. A
    non-mandatory notification stays togglable."""
    ten = int(notifbed["tenant_a"])
    async with _service(db["app"]) as ses:
        await rules_service.create_rule(
            ses,
            ten,
            authored_by="graphene",
            trigger_key="contact.bank_details_changed",
            audience="adviser",
            title="A client's bank details changed",
            mandatory=True,
        )
    async with _staff(db["app"], str(notifbed["wos_staff"]), str(notifbed["app_a"])) as ses:
        prefs = await notif_router.get_preferences(session=ses, _=_principal())
    by = {p.trigger_key: p for p in prefs.preferences}
    assert by["contact.bank_details_changed"].mandatory is True
    assert by["task.assigned"].mandatory is False


@pytest.mark.asyncio
async def test_router_feed_unread_and_mark_read(
    db: dict[str, str], notifbed: dict[str, object]
) -> None:
    ten = int(notifbed["tenant_a"])
    staff = str(notifbed["staff"])
    async with _service(db["app"]) as ses:
        for i in range(3):
            await service.notify(
                ses,
                tenant_id=ten,
                recipients=[Recipient(kind="user", user_id=staff)],
                category="tasks",
                portal="platform",
                title=f"Task {i}",
                event_type="task.created",
                source_kind="action",
                source_ref=f"r{i}:{uuid.uuid4().hex}",
            )

    async with _staff(db["app"], str(notifbed["wos_staff"]), str(notifbed["app_a"])) as ses:
        page = await notif_router.list_notifications(
            before=None, unread=False, limit=20, session=ses, _=_principal()
        )
        assert len(page.items) == 3
        second_page = await notif_router.list_notifications(
            before=None, unread=False, limit=1, offset=1, session=ses, _=_principal()
        )
        assert [item.uid for item in second_page.items] == [page.items[1].uid]
        assert second_page.has_more is True
        assert (await notif_router.unread_count(session=ses, _=_principal())).count == 3
        assert page.unread_count == 3
        first = page.items[0].uid
        await notif_router.mark_read(first, session=ses, _=_principal())
        assert (await notif_router.unread_count(session=ses, _=_principal())).count == 2
        await notif_router.mark_all_read(session=ses, _=_principal())
        assert (await notif_router.unread_count(session=ses, _=_principal())).count == 0


@pytest.mark.asyncio
async def test_router_feed_narrows_to_the_active_contact(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A login linked to more than one client contact must be able to view/clear one contact's
    notifications without touching the other's — the active-contact selector applied to the
    notifications feed (docs/portal-customisation.md)."""
    tenant, c1 = int(notifbed["tenant_a"]), int(notifbed["c1"])
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        c2 = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name) "
                    "VALUES (:t, :u, 'individual', 'Other Client') RETURNING id"
                ),
                {"t": tenant, "u": f"nc2_{s}"},
            ).scalar_one()
        )
        c.commit()

    async with _service(db["app"]) as ses:
        for contact, title in ((c1, "For c1"), (c2, "For c2")):
            await service.notify(
                ses,
                tenant_id=tenant,
                recipients=[Recipient(kind="contact", contact_id=contact)],
                category="tasks",
                portal="client",
                title=title,
                event_type="action.tasks-create-task",
                source_kind="action",
                source_ref=f"multi:{contact}:{uuid.uuid4().hex}",
            )

    async with _client(db["app"], str(uuid.uuid4()), tenant, [c1, c2]) as ses:
        everything = await notif_router.list_notifications(
            before=None, unread=False, limit=20, session=ses, _=_principal()
        )
        assert {n.title for n in everything.items} == {"For c1", "For c2"}

        only_c1 = await notif_router.list_notifications(
            before=None, unread=False, limit=20, contact_id=c1, session=ses, _=_principal()
        )
        assert [n.title for n in only_c1.items] == ["For c1"]
        assert (
            await notif_router.unread_count(contact_id=c1, session=ses, _=_principal())
        ).count == 1
        assert (
            await notif_router.unread_count(contact_id=c2, session=ses, _=_principal())
        ).count == 1

        await notif_router.mark_all_read(contact_id=c1, session=ses, _=_principal())
        assert (
            await notif_router.unread_count(contact_id=c1, session=ses, _=_principal())
        ).count == 0
        assert (
            await notif_router.unread_count(contact_id=c2, session=ses, _=_principal())
        ).count == 1


@pytest.mark.asyncio
async def test_the_realtime_ticket_listens_on_the_contact_asked_for(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """The live channel follows the ACTIVE contact, not an arbitrary member of the scope.

    A client login linked to two contacts keys its socket per CONTACT (that is the grain a
    delivered notification's recipient key uses). Keyed on ``contact_scope()[0]`` it listened on
    whichever id the claim array happened to put first, so a notification about the client on
    screen pushed to nobody and one about the other linked client woke the bell. An id the session
    does not hold is REFUSED rather than replaced by the first one: a silent fallback subscribes
    the reader to a different client's channel while answering 200.
    """
    tenant, c1 = int(notifbed["tenant_a"]), int(notifbed["c1"])
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        c2 = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name) "
                    "VALUES (:t, :u, 'individual', 'Second Client') RETURNING id"
                ),
                {"t": tenant, "u": f"wsc_{s}"},
            ).scalar_one()
        )
        c.commit()

    async with _client(db["app"], str(uuid.uuid4()), tenant, [c1, c2]) as ses:
        # The active contact is the one asked for, whichever end of the scope array it sits on.
        for contact in (c1, c2):
            answer = await notif_router.mint_ticket(contact_id=contact, session=ses)
            assert live_push.redeem_ticket(answer["ticket"]) == realtime.contact_key(contact)

        # No contact named — the old behaviour, kept for a single-contact login.
        answer = await notif_router.mint_ticket(session=ses)
        assert live_push.redeem_ticket(answer["ticket"]) == realtime.contact_key(c1)

        # A contact this session does not hold is refused, not silently swapped for c1.
        with pytest.raises(HTTPException) as refusal:
            await notif_router.mint_ticket(contact_id=c1 + c2 + 10_000, session=ses)
        assert refusal.value.status_code == 403


@pytest.mark.asyncio
async def test_stored_rule_adds_a_non_builtin_audience(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """The 'adviser' audience on document.shared is NOT a built-in default; a firm authoring a rule
    for it (in notifications.rules) makes it fire — the client's adviser gets an in-app row."""
    ten = int(notifbed["tenant_a"])
    staff = str(notifbed["staff"])  # advises c1 (notifbed client_advisers link)
    c1 = int(notifbed["c1"])
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO notifications.rules (uid, tenant_id, trigger_key, audience, label, "
                "  title, body, default_in_app) "
                "VALUES ('nrule_advtest', :t, 'document.shared', 'adviser', 'Doc delivered', "
                "  'Your document was delivered', 'Delivered to the client portal.', true)"
            ),
            {"t": ten},
        )
        c.commit()
    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document.sent",
        subject={"contact_ids": [c1]},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
    finally:
        await engine.dispose()
    with super_engine.connect() as c:
        adviser_titles = (
            c.execute(
                text(
                    "SELECT title FROM notifications.notifications "
                    "WHERE recipient_user_id = CAST(:u AS uuid)"
                ),
                {"u": staff},
            )
            .scalars()
            .all()
        )
        client_count = c.execute(
            text(
                "SELECT count(*) FROM notifications.notifications WHERE recipient_contact_id = :c"
            ),
            {"c": c1},
        ).scalar_one()
    assert adviser_titles == ["Your document was delivered"]
    assert client_count == 1


@pytest.mark.asyncio
async def test_rules_service_crud_and_catalogue_validation(
    db: dict[str, str], notifbed: dict[str, object]
) -> None:
    ten = int(notifbed["tenant_a"])
    async with _service(db["app"]) as ses:
        created = await rules_service.create_rule(
            ses,
            ten,
            authored_by="graphene",
            trigger_key="document.shared",
            audience="adviser",
            title="Delivered",
            body="A document was delivered.",
            default_in_app=True,
        )
        assert_public_id(created["uid"])
        rules = await rules_service.list_rules(ses, ten)
        assert any(r["uid"] == created["uid"] for r in rules)

        with pytest.raises(rules_service.RuleError):
            await rules_service.create_rule(
                ses,
                ten,
                authored_by="graphene",
                trigger_key="nope.nope",
                audience="client",
                title="x",
            )
        with pytest.raises(rules_service.RuleError):
            await rules_service.create_rule(
                ses,
                ten,
                authored_by="graphene",
                trigger_key="task.assigned",
                audience="client",
                title="x",  # task.assigned only offers 'assignee'
            )


@pytest.mark.asyncio
async def test_clone_rules_carries_model_rules_and_is_idempotent(db: dict[str, str]) -> None:
    """The model → new-firm clone must copy the authored RULES, not only the category config: a
    firm that inherits the model's defaults must inherit its curated notifications too. Each clone
    gets a fresh uid + the destination tenant, preserving the guardrails; a re-run adds nothing."""
    async with _service(db["app"]) as ses:

        async def _tenant(uid: str) -> int:
            return int(
                (
                    await ses.execute(
                        text(
                            "INSERT INTO auth.tenants (uid, alias, name) "
                            "VALUES (:u, :u, :u) RETURNING id"
                        ),
                        {"u": uid},
                    )
                ).scalar_one()
            )

        src = await _tenant(f"mdl_{uuid.uuid4().hex[:8]}")
        dst = await _tenant(f"new_{uuid.uuid4().hex[:8]}")
        seeded = await rules_service.create_rule(
            ses,
            src,
            authored_by="graphene",
            firm_editable=False,
            mandatory=True,
            trigger_key="document.shared",
            audience="adviser",
            title="Model notice",
        )

        assert await rules_service.clone_rules(ses, src, dst) == 1
        cloned = await rules_service.list_rules(ses, dst)
        assert len(cloned) == 1
        c = cloned[0]
        assert c["uid"] != seeded["uid"] and c["tenant_id"] == dst  # fresh uid, new tenant
        assert c["trigger_key"] == "document.shared" and c["audience"] == "adviser"
        assert c["title"] == "Model notice"
        assert c["authored_by"] == "graphene"
        assert c["firm_editable"] is False and c["mandatory"] is True

        assert await rules_service.clone_rules(ses, src, dst) == 0
        assert len(await rules_service.list_rules(ses, dst)) == 1


@pytest.mark.asyncio
async def test_dismiss_hides_from_feed_and_unread_count(
    db: dict[str, str], notifbed: dict[str, object]
) -> None:
    ten = int(notifbed["tenant_a"])
    staff = str(notifbed["staff"])
    async with _service(db["app"]) as ses:
        await service.notify(
            ses,
            tenant_id=ten,
            recipients=[Recipient(kind="user", user_id=staff)],
            category="tasks",
            portal="platform",
            title="dismiss me",
            event_type="task.created",
            source_kind="action",
            source_ref=f"d:{uuid.uuid4().hex}",
        )
    async with _staff(db["app"], str(notifbed["wos_staff"]), str(notifbed["app_a"])) as ses:
        page = await notif_router.list_notifications(
            before=None, unread=False, limit=20, session=ses, _=_principal()
        )
        uid = next(i.uid for i in page.items if i.title == "dismiss me")
        assert (await notif_router.unread_count(session=ses, _=_principal())).count == 1

        await notif_router.dismiss(uid, session=ses, _=_principal())

        after = await notif_router.list_notifications(
            before=None, unread=False, limit=20, session=ses, _=_principal()
        )
        assert all(i.uid != uid for i in after.items)  # gone from the feed
        assert (await notif_router.unread_count(session=ses, _=_principal())).count == 0


@pytest.mark.asyncio
async def test_tenant_off_switch_suppresses_a_category(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    ten = int(notifbed["tenant_a"])
    staff = str(notifbed["staff"])
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO notifications.tenant_config (tenant_id, category, enabled) "
                "VALUES (:t, 'tasks', false)"
            ),
            {"t": ten},
        )
        c.commit()
    async with _service(db["app"]) as ses:
        n = await service.notify(
            ses,
            tenant_id=ten,
            recipients=[Recipient(kind="user", user_id=staff)],
            category="tasks",
            portal="platform",
            title="blocked by firm switch",
            event_type="task.created",
            source_kind="action",
            source_ref=f"off:{uuid.uuid4().hex}",
        )
        assert n == 0  # firm disabled the category — nobody gets it


@pytest.mark.asyncio
async def test_resolution_tenant_default_then_user_override(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    ten = int(notifbed["tenant_a"])
    staff = str(notifbed["staff"])
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO notifications.tenant_config (tenant_id, category, enabled, in_app) "
                "VALUES (:t, 'tasks', true, false)"
            ),
            {"t": ten},
        )
        c.commit()

    async def _fire(ref: str) -> int:
        async with _service(db["app"]) as ses:
            return await service.notify(
                ses,
                tenant_id=ten,
                recipients=[Recipient(kind="user", user_id=staff)],
                category="tasks",
                portal="platform",
                title="t",
                event_type="task.created",
                source_kind="action",
                source_ref=ref,
            )

    assert await _fire(f"a:{uuid.uuid4().hex}") == 0  # tenant default off, no user override

    async with _service(db["app"]) as ses:
        await ses.execute(
            text(
                "INSERT INTO notifications.preferences (tenant_id, recipient_kind, "
                "  recipient_user_id, category, in_app) VALUES (:t, 'user', :u, 'tasks', true)"
            ),
            {"t": ten, "u": staff},
        )
    assert await _fire(f"b:{uuid.uuid4().hex}") == 1


@pytest.mark.asyncio
async def test_tenant_config_audience_split_and_mandatory_forces_over_optout(
    db: dict[str, str], notifbed: dict[str, object]
) -> None:
    """The admin defaults split by audience, and a MANDATORY firm default forces a category on for
    that audience even over the recipient's own opt-out (docs/notifications.md, img4)."""
    ten = int(notifbed["tenant_a"])
    staff = str(notifbed["staff"])

    async with _service(db["app"]) as ses:
        pairs = {
            (r["category"], r["audience"]) for r in await notif_config.get_tenant_config(ses, ten)
        }
    assert ("tasks", "adviser") in pairs and ("tasks", "client") in pairs  # both
    assert ("documents", "client") in pairs  # client-only
    assert ("documents", "adviser") not in pairs
    assert ("clients", "adviser") in pairs and ("clients", "client") not in pairs  # staff-only

    async with _service(db["app"]) as ses:
        await ses.execute(
            text(
                "INSERT INTO notifications.preferences (tenant_id, recipient_kind, "
                "  recipient_user_id, category, in_app) VALUES (:t, 'user', :u, 'clients', false)"
            ),
            {"t": ten, "u": staff},
        )
        await notif_config.set_tenant_config(
            ses,
            ten,
            [
                {
                    "category": "clients",
                    "audience": "adviser",
                    "enabled": True,
                    "in_app": True,
                    "email": False,
                    "mandatory": True,
                }
            ],
        )

    async with _service(db["app"]) as ses:
        n = await service.notify(
            ses,
            tenant_id=ten,
            recipients=[Recipient(kind="user", user_id=staff)],
            category="clients",
            portal="platform",
            title="mandatory",
            event_type="client.bank_account_updated",
            source_kind="action",
            source_ref=f"mand:{uuid.uuid4().hex}",
            audience="adviser",
            default_in_app=True,
        )
    assert n == 1  # forced on despite the adviser's opt-out


@pytest.mark.asyncio
async def test_admin_tenant_config_roundtrip(
    db: dict[str, str], notifbed: dict[str, object]
) -> None:
    ten = int(notifbed["tenant_a"])
    async with _service(db["app"]) as ses:
        cfg = await notif_config.get_tenant_config(ses, ten)
        cats = {c["category"] for c in cfg}
        assert "tasks" in cats and "documents" in cats
        assert "compliance" not in cats  # operations-only, not firm-configurable
        assert all(c["enabled"] for c in cfg)  # default enabled before any config

        await notif_config.set_tenant_config(
            ses, ten, [{"category": "documents", "enabled": False, "in_app": None, "email": None}]
        )
        after = {c["category"]: c for c in await notif_config.get_tenant_config(ses, ten)}
        assert after["documents"]["enabled"] is False


@pytest.mark.asyncio
async def test_router_preferences_roundtrip(
    db: dict[str, str], notifbed: dict[str, object]
) -> None:
    async with _staff(db["app"], str(notifbed["wos_staff"]), str(notifbed["app_a"])) as ses:
        prefs = await notif_router.get_preferences(session=ses, _=_principal())
        # Staff prefs are per NOTIFICATION now, not per category.
        keys = {p.trigger_key for p in prefs.preferences}
        assert all(p.trigger_key for p in prefs.preferences)
        assert "task.assigned" in keys  # the assignee built-in
        assert "document.request" not in keys

        await notif_router.set_preferences(
            notif_router.PreferencesUpdate(
                preferences=[
                    notif_router.PreferenceUpdate(
                        category="tasks", trigger_key="task.assigned", in_app=False
                    )
                ]
            ),
            session=ses,
            _=_principal(),
        )
        after = await notif_router.get_preferences(session=ses, _=_principal())
        task_pref = next(p for p in after.preferences if p.trigger_key == "task.assigned")
        assert task_pref.in_app is False


@pytest.mark.asyncio
async def test_client_preference_labels_never_expose_delivery_merge_tokens(
    db: dict[str, str], notifbed: dict[str, object]
) -> None:
    async with _client(
        db["app"],
        str(notifbed["client_login"]),
        int(notifbed["tenant_a"]),
        [int(notifbed["c1"])],
    ) as ses:
        prefs = await notif_router.get_client_preferences(session=ses, _=_principal())

    message = next(p for p in prefs.preferences if p.trigger_key == "message.received_by_client")
    assert message.label == "New message from your adviser"
    assert all("{" not in p.label and "}" not in p.label for p in prefs.preferences)


@pytest.mark.asyncio
async def test_client_preferences_are_self_scoped_not_advised(
    db: dict[str, str], notifbed: dict[str, object]
) -> None:
    """A client sets their OWN preference (self = the login's ``crm.contact_users`` link, here c1),
    and an adviser who merely has c1 in their wider ``contact_scope()`` (via ``client_advisers``,
    NOT ``contact_users``) can neither read it nor clobber it through this door. This is the
    self-vs-scope fix: scoping the write to ``contact_scope()`` would let a dual client+adviser
    login rewrite the preferences of every client it advises."""
    ten = int(notifbed["tenant_a"])
    client_login = str(notifbed["client_login"])
    c1 = [int(notifbed["c1"])]
    trig = "document.request"

    async def _in_app(ses: AsyncSession) -> bool:
        prefs = (await notif_router.get_client_preferences(session=ses, _=_principal())).preferences
        return next(p for p in prefs if p.trigger_key == trig).in_app

    async def _set(ses: AsyncSession, *, in_app: bool) -> None:
        await notif_router.set_client_preferences(
            notif_router.PreferencesUpdate(
                preferences=[
                    notif_router.PreferenceUpdate(
                        category="documents", trigger_key=trig, in_app=in_app
                    )
                ]
            ),
            session=ses,
            _=_principal(),
        )

    async with _client(db["app"], client_login, ten, c1) as ses:
        await _set(ses, in_app=False)
        assert await _in_app(ses) is False

    # An advising staff login has no contact_users self link, so it cannot rewrite this preference.
    async with _staff(db["app"], str(notifbed["wos_staff"]), str(notifbed["app_a"])) as ses:
        await _set(ses, in_app=True)

    async with _client(db["app"], client_login, ten, c1) as ses:
        assert await _in_app(ses) is False


@pytest.mark.asyncio
async def test_client_pref_is_per_notification_in_resolution(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """A client muting ONE document notification suppresses only that one — its sibling in the same
    category still fires. This is the whole point of per-notification prefs (vs the old per-category
    toggle): resolution keys the client's override by the trigger."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO notifications.preferences (tenant_id, recipient_kind, "
                "  recipient_contact_id, category, trigger_key, in_app) "
                "VALUES (:t, 'contact', :c, 'documents', 'document.request', false)"
            ),
            {"t": ten, "c": c1},
        )
        c.commit()

    async def _fire(trigger: str, ref: str) -> int:
        async with _service(db["app"]) as ses:
            return await service.notify(
                ses,
                tenant_id=ten,
                recipients=[Recipient(kind="contact", contact_id=c1)],
                category="documents",
                portal="client",
                title="t",
                event_type="action.x",
                source_kind="action",
                source_ref=ref,
                audience="client",
                trigger_key=trigger,
                default_in_app=True,
            )

    assert await _fire("document.request", f"req:{uuid.uuid4().hex}") == 0
    assert await _fire("document.shared", f"shr:{uuid.uuid4().hex}") == 1


@pytest.mark.asyncio
async def test_staff_prefs_are_staff_audience_only(
    db: dict[str, str], notifbed: dict[str, object]
) -> None:
    """The staff prefs page shows only STAFF-facing notifications (adviser/assignee), never a
    client-ADDRESSED one — a user-keyed preference for a client notification would be inert. This
    is now reinforced by 0119's identity boundary: a staff identity cannot ALSO hold client
    membership. A staff user DOES get document-category ADVISER notices (a client uploaded a
    document), so the invariant is per-audience, not per-category."""
    async with _staff(db["app"], str(notifbed["wos_staff"]), str(notifbed["app_a"])) as ses:
        keys = {
            p.trigger_key
            for p in (await notif_router.get_preferences(session=ses, _=_principal())).preferences
        }
    assert "task.assigned" in keys  # a staff notification is present
    assert "document.request" not in keys
    assert "document.shared" not in keys


# ---- realtime (the SSE delivery path; the poll is the fallback) --------------------------------


def test_realtime_key_for_maps_user_contact_and_unroutable() -> None:
    """A delivered notification's recipient becomes the key its stream listens on — per user for
    staff, per contact for a client; an id-less recipient is unroutable (None)."""
    assert realtime.key_for(Recipient(kind="user", user_id="u1")) == "ntf:user:u1"
    assert realtime.key_for(Recipient(kind="contact", contact_id=7)) == "ntf:contact:7"
    assert realtime.key_for(Recipient(kind="user")) is None
    assert realtime.key_for(Recipient(kind="contact")) is None


@pytest.mark.asyncio
async def test_realtime_announce_pg_notifies_each_key_once() -> None:
    """announce dedups per key and issues one pg_notify per distinct recipient — a person hearing
    about several new rows at once needs only one nudge (the browser refetches regardless)."""

    class _Recording:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def execute(self, _stmt: object, params: dict[str, object]) -> None:
            self.calls.append(params)

    ses = _Recording()
    u = Recipient(kind="user", user_id="u1")
    c = Recipient(kind="contact", contact_id=7)
    await realtime.announce(ses, [u, u, c])  # type: ignore[arg-type]  # duck-typed session
    assert {p["k"] for p in ses.calls} == {"ntf:user:u1", "ntf:contact:7"}
    assert {p["c"] for p in ses.calls} == {realtime.CHANNEL}
    assert len(ses.calls) == 2


@pytest.mark.asyncio
async def test_a_delivered_notification_announces_on_the_channel(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """End to end: the worker announces each NEW in-app row on the realtime channel, in the SAME
    transaction as the insert (so it fires on commit). A LISTENer on the same DB — what the API
    bridge holds — receives the recipient's key, which is what turns the bell's poll into a push."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document_request.created",
        subject={"contact_id": c1},
    )
    dsn = db["app"].replace("+psycopg", "").replace("+asyncpg", "")
    listener = psycopg.connect(dsn, autocommit=True)
    try:
        listener.execute(f"LISTEN {realtime.CHANNEL}")
        engine = create_async_engine(db["app"])
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await consumer.consume_once(maker)
        finally:
            await engine.dispose()
        received = [n.payload for n in listener.notifies(timeout=5, stop_after=1)]
    finally:
        listener.close()
    assert realtime.contact_key(c1) in received


# ── the resweep: the repair pass for the LAG_SECONDS hole ────────────────────────────────────────
# The tail ignores rows younger than 5s (`consumer.LAG_SECONDS`) because an id is taken at INSERT
# and the row appears at COMMIT, so a late commit would be skipped for ever. `resweep_once` re-reads
# the recent past regardless of the cursor, `source_ref`'s unique index making redelivery a no-op.


@pytest.mark.asyncio
async def test_resweep_recovers_an_action_the_cursor_skipped(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """THE HOLE, made concrete: an action the tail will never read again is still delivered.

    The skip is simulated the way it really happens — the cursor sits PAST a committed row — because
    the real cause (two writes committing out of id order) cannot be staged deterministically."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    aid = _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document.sent",
        subject={"contact_ids": [c1]},
    )
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO notifications.cursor (consumer, position) "
                "VALUES ('notifications', :p) "
                "ON CONFLICT (consumer) DO UPDATE SET position = :p"
            ),
            {"p": aid},
        )
        c.commit()

    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
        with super_engine.connect() as c:
            missed = int(
                c.execute(
                    text(
                        "SELECT count(*) FROM notifications.notifications "
                        "WHERE source_action_id = :a"
                    ),
                    {"a": aid},
                ).scalar_one()
            )
        assert missed == 0, "precondition: the tail must have skipped this row"

        read = await consumer.resweep_once(maker)
        assert read >= 1
    finally:
        await engine.dispose()

    with super_engine.connect() as c:
        recovered = int(
            c.execute(
                text(
                    "SELECT count(*) FROM notifications.notifications WHERE source_action_id = :a"
                ),
                {"a": aid},
            ).scalar_one()
        )
    assert recovered >= 1, "the resweep must deliver a notification the cursor skipped"


@pytest.mark.asyncio
async def test_resweep_never_duplicates_what_the_tail_already_delivered(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """The whole design rests on this: the repair pass is free to be blind because re-delivery is a
    no-op. If the `source_ref` unique index ever stopped covering it, every sweep would re-notify
    the last ten minutes — so this is the assertion that keeps the repair safe to run on a timer."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    await _seed_cursor_at_head(super_engine)
    aid = _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document.sent",
        subject={"contact_ids": [c1]},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.consume_once(maker)
        with super_engine.connect() as c:
            after_tail = int(
                c.execute(
                    text(
                        "SELECT count(*) FROM notifications.notifications "
                        "WHERE source_action_id = :a"
                    ),
                    {"a": aid},
                ).scalar_one()
            )
        assert after_tail >= 1, "precondition: the tail delivered it"

        await consumer.resweep_once(maker)
        await consumer.resweep_once(maker)
    finally:
        await engine.dispose()

    with super_engine.connect() as c:
        after_sweeps = int(
            c.execute(
                text(
                    "SELECT count(*) FROM notifications.notifications WHERE source_action_id = :a"
                ),
                {"a": aid},
            ).scalar_one()
        )
    assert after_sweeps == after_tail, "a resweep must not re-notify an already-delivered action"


@pytest.mark.asyncio
async def test_resweep_leaves_the_cursor_alone(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """The repair pass is not the tail. If it moved the cursor it would skip the rows the tail has
    yet to reach — turning a safety net into the very fault it exists to catch."""
    ten = int(notifbed["tenant_a"])
    c1 = int(notifbed["c1"])
    head = await _seed_cursor_at_head(super_engine)
    _insert_action(
        super_engine,
        tenant_id=ten,
        event_type="document.sent",
        subject={"contact_ids": [c1]},
    )
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await consumer.resweep_once(maker)
    finally:
        await engine.dispose()

    with super_engine.connect() as c:
        position = int(
            c.execute(
                text("SELECT position FROM notifications.cursor WHERE consumer = 'notifications'")
            ).scalar_one()
        )
    assert position == head, "the resweep must not advance the tail's cursor"


# ── A rule supplies the values its template's DECLARED variables need ────────────────────────────
# The gap this closes: an email template may declare placeholder keys, and until now NOTHING filled
# them — the built-in send sites are keyed by code and pass a fixed set, and the flows `send_email`
# action resolves `documents.templates`, a different system. So an author could declare `plan_name`,
# insert it, and ship an email with a blank where it stood.


class TestTheScopeVocabulary:
    """The dotted paths a rule's copy may name, and the walk that resolves one."""

    def test_the_scope_vocabulary_only_names_fields_the_context_can_produce(self) -> None:
        from app.notifications.context import KNOWN_SCOPE_FIELDS, SCOPE_FIELDS

        # Every path is dotted, lowercase and two segments — the shape `render._lookup` walks.
        for path in KNOWN_SCOPE_FIELDS:
            assert path.count(".") == 1, path
            assert path == path.lower(), path
        # The always-available set does not depend on a subject, so it must not name one.
        for path in SCOPE_FIELDS[None]:
            assert not path.startswith(("account.", "task.", "document.", "client."))

    def test_resolve_reads_a_path_or_answers_none(self) -> None:
        from app.notifications.render import _lookup as resolve_scope_path

        ctx = {"account": {"value": "£1,000.00"}, "client": {"name": "Priya"}}
        assert resolve_scope_path(ctx, "account.value") == "£1,000.00"
        assert resolve_scope_path(ctx, "client.name") == "Priya"
        # Absent is None, never an error: a notice must not fail because one fact is missing.
        assert resolve_scope_path(ctx, "account.currency") is None
        assert resolve_scope_path(ctx, "task.title") is None
        # A path landing on a dict is not a value.
        assert resolve_scope_path(ctx, "account") is None

    def test_the_account_scope_formats_money_rather_than_printing_a_numeric(self) -> None:
        # Stage-A substitution is deliberately not a template engine (no filters), so an unformatted
        # value would read "184230.55" in a client's email.
        from app.notifications.context import _symbol
        from app.render.jinja_engine import _fmt_currency

        assert _fmt_currency(184230.55, _symbol("GBP")) == "£184,230.55"
        # An unknown code falls back to the code itself — wrong money beats verbose money.
        assert _fmt_currency(10, _symbol("CHF")).startswith("CHF ")


class TestTheCtaButtonLabel:
    """What the button SAYS, per recipient kind. A staff notice said a bare "Open" until Aug 2026 —
    nothing, on a control whose whole job is to name where it goes — while the identical client
    notice said "Open your portal". The platform portal is a staff member's portal exactly as the
    client portal is a client's, so both in-portal branches now say the same thing.
    """

    CLIENT_BASE = "https://clients.firm.test"
    PLATFORM_BASE = "https://app.firm.test"

    def _cta(self, link: str | None, kind: str):
        from app.notifications import email_consumer, registry

        r = (
            registry.Recipient(kind="user", user_id="u-1")
            if kind == "user"
            else registry.Recipient(kind="contact", contact_id=1)
        )
        return email_consumer._cta(
            link, r, client_base=self.CLIENT_BASE, platform_base=self.PLATFORM_BASE
        )

    def test_an_in_portal_path_reads_the_same_for_staff_and_client(self) -> None:
        assert self._cta("/tasks", "user") == ("Open your portal", f"{self.PLATFORM_BASE}/tasks")
        assert self._cta("/documents", "contact") == (
            "Open your portal",
            f"{self.CLIENT_BASE}/documents",
        )

    def test_each_kind_is_sent_to_its_OWN_portal(self) -> None:
        # The label being shared must not blur the destinations: staff go to the platform host,
        # a client to the client host, and swapping them would send a client to a staff login.
        assert self._cta("/x", "user")[1].startswith(self.PLATFORM_BASE)
        assert self._cta("/x", "contact")[1].startswith(self.CLIENT_BASE)

    def test_an_absolute_link_keeps_the_bare_wording(self) -> None:
        # It may point anywhere — another system, a vendor page — so "your portal" would be a claim
        # about a destination we do not control.
        for kind in ("user", "contact"):
            assert self._cta("https://vendor.example/x", kind) == (
                "Open",
                "https://vendor.example/x",
            )

    def test_no_link_and_no_base_both_mean_no_button(self) -> None:
        from app.notifications import email_consumer, registry

        assert self._cta(None, "user") is None
        assert self._cta("", "contact") is None
        # A relative path with no base would otherwise ship a broken href.
        assert (
            email_consumer._cta(
                "/tasks",
                registry.Recipient(kind="user", user_id="u-1"),
                client_base=self.CLIENT_BASE,
                platform_base=None,
            )
            is None
        )


def test_priority_matches_the_check_constraint_in_every_migration_that_stores_it() -> None:
    """`registry.Priority` against the SQL that enforces it.

    The value arrives on a request BODY (`NotificationRuleCreate`/`Update`), and the only thing that
    had ever refused a bad one was a Postgres CHECK — which surfaces as a 500 naming a column, where
    a typed field answers 422 naming the field. Two migrations carry the constraint, so both are
    read: a fourth member added to one CHECK and not the other is a row a rule can hold and a
    notification cannot.
    """
    migrations = Path(__file__).resolve().parents[1] / "migrations" / "sql"
    declared = set(get_args(registry.Priority))
    assert declared, "registry.Priority is not a Literal of values"

    checked: dict[str, set[str]] = {}
    for name in ("0063_notifications.sql", "0068_notification_rules.sql"):
        sql = (migrations / name).read_text(encoding="utf-8")
        match = re.search(r"priority[^,]*?CHECK \(priority IN \(([^)]*)\)\)", sql)
        assert match, f"{name}: no priority CHECK found — has the column moved?"
        checked[name] = set(re.findall(r"'([a-z]+)'", match.group(1)))

    for name, values in checked.items():
        assert values == declared, (
            f"{name}'s CHECK allows {sorted(values)} but registry.Priority declares "
            f"{sorted(declared)} — change them together"
        )


@pytest.mark.asyncio
async def test_internal_ops_reads_no_notifications_raised_for_the_platform(
    db: dict[str, str], notifbed: dict[str, object]
) -> None:
    """0173: a notification belongs to the portal it was raised FOR.

    The defect: `auth.tenant_lineage() <> '{}'` is true in BOTH staff portals, so the user branch
    read "every row addressed to me, wherever I am signed in" — and an ops user who also held a
    platform-side recipient identity met their adviser notifications in the internal ops bell.

    Same user, same rows, two sessions: the platform one still sees its notification, the ops one
    sees nothing. Nothing writes an 'operations' row today, so "nothing" is the whole correct
    answer for that portal — and the row is NOT deleted, it is simply not addressed to that portal.
    """
    ten = int(notifbed["tenant_a"])
    staff = str(notifbed["staff"])
    async with _service(db["app"]) as ses:
        await service.notify(
            ses,
            tenant_id=ten,
            recipients=[Recipient(kind="user", user_id=staff)],
            category="tasks",
            portal="platform",
            title="A task was assigned to you",
            event_type="task.created",
            source_kind="action",
            source_ref=f"ops:{uuid.uuid4().hex}",
        )

    async with _staff(db["app"], str(notifbed["wos_staff"]), str(notifbed["app_a"])) as ses:
        titles = (
            (await ses.execute(text("SELECT title FROM notifications.notifications")))
            .scalars()
            .all()
        )
        assert titles == ["A task was assigned to you"]

    async with _ops(db["app"], staff, ten) as ses:
        count = (
            await ses.execute(text("SELECT count(*) FROM notifications.notifications"))
        ).scalar_one()
        assert count == 0

    # And it cannot be marked read from the portal that may not read it — the mark policy moves
    # with the read one, or the badge could be cleared from a bell that never showed the row.
    async with _ops(db["app"], staff, ten) as ses:
        marked = (
            await ses.execute(
                text("UPDATE notifications.notifications SET read_at = now() RETURNING 1")
            )
        ).all()
        assert marked == []


@pytest.mark.asyncio
async def test_router_feed_takes_value_sets_a_date_range_and_a_state_set(
    db: dict[str, str], super_engine: Engine, notifbed: dict[str, object]
) -> None:
    """Every value filter on this feed took ONE value, so `?category=tasks&category=documents` was
    read as its LAST value and a two-category question came back with one category's rows."""
    async with _service(db["app"]) as ses:
        for kind, rank in (("tasks", "high"), ("documents", "normal"), ("transfers", "low")):
            await service.notify(
                ses,
                tenant_id=int(notifbed["tenant_a"]),
                recipients=[Recipient(kind="user", user_id=str(notifbed["staff"]))],
                category=kind,
                priority=rank,
                portal="platform",
                title=f"A {kind} notice",
                event_type="task.created",
                source_kind="action",
                source_ref=f"multi:{kind}:{uuid.uuid4().hex}",
            )

    async with _staff(db["app"], str(notifbed["wos_staff"]), str(notifbed["app_a"])) as ses:

        async def feed(**kw: Any) -> Any:
            return await notif_router.list_notifications(
                before=None, unread=False, limit=20, session=ses, _=_principal(), **kw
            )

        both = await feed(category=["tasks", "documents"])
        assert {n.category for n in both.items} == {"tasks", "documents"}
        # A bare string is still ONE value, not a list of its characters.
        assert {n.category for n in (await feed(category="tasks")).items} == {"tasks"}
        assert {n.priority for n in (await feed(priority=["high", "low"])).items} == {"high", "low"}

        # `until` is compared against the END of its day, or "today to today" is an empty window.
        # The bound is the session's calendar day. date.today() follows the process timezone, and
        # those two disagree for the hour after midnight during British Summer Time.
        today = (await ses.execute(text("SELECT CURRENT_DATE"))).scalar_one()
        assert len((await feed(since=today.isoformat(), until=today.isoformat())).items) == 3
        assert (await feed(until=(today - timedelta(days=1)).isoformat())).items == []

        # `state` reads three columns: Unread means a row still ON the bell, so it excludes the
        # dismissed.
        uids = {n.title: n.uid for n in (await feed()).items}
        await notif_router.dismiss(uids["A transfers notice"], session=ses, _=_principal())
        assert {n.title for n in (await feed(state=["dismissed"])).items} == {"A transfers notice"}
        assert {n.title for n in (await feed(state=["unread"])).items} == {
            "A tasks notice",
            "A documents notice",
        }


def test_policy_channels_masks_review_notices_by_policy() -> None:
    """The per-policy approval-notice channels are read off a review.requested event's metadata,
    and None for any other event (no mask)."""
    from app.notifications.consumer import _policy_channels

    assert _policy_channels({"event_type": "run.completed"}) is None
    # A review subject without the notify keys (older event) is not masked either.
    assert _policy_channels({"metadata": {"subject": {"reviewer_role_slug": "x"}}}) is None
    assert _policy_channels(
        {"metadata": {"subject": {"notify_in_app": True, "notify_email": False}}}
    ) == {"in_app": True, "email": False}
    assert _policy_channels(
        {"metadata": {"subject": {"notify_in_app": False, "notify_email": True}}}
    ) == {"in_app": False, "email": True}
