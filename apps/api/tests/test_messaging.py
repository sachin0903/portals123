"""Client<->adviser chat (schema `messaging`, app/messaging.py, app/routers/messaging.py).

Driven over real HTTP (ASGI) against the real testcontainer Postgres, mirroring
`test_book_session.py`'s harness — only `get_current_principal` is overridden, so the whole
chain (router -> service -> RLS) is exercised exactly as production would run it.

What matters here, tied to the four requirements this feature was built for:

  1/2. a client only ever reaches THEIR OWN contact's thread — never a caller-supplied
       adviser/conversation id;
  3/4. staff with `messages:read`/`messages:write` can search/reply across their WHOLE
       tenant's book (not narrowed to "my clients" or to `crm.client_advisers`), but never
       reach another tenant's conversation, even by guessing its id;
  5.   unread state is a read-receipt computation (`client_last_read_at`/`staff_last_read_at`),
       never a stored counter — proven by sending, reading, and re-checking the flag.

Plus the resolve_claims gotchas this suite caught while it was being built:

  * a staff adviser's OWN ASSIGNED CLIENTS are folded into their `contact_scope()` too, so "is
    contact_scope() non-empty" cannot be the signal for "this caller is a client";
  * `auth.role()` alone isn't that signal either — a real client's WorkOS role is never
    literally the string 'client' (it's a role slug like 'org-advised-client'). The actual
    signal is `auth.portal()`, classified server-side from the token's APPLICATION client id
    (`auth.tenant_environments.client_portal_client_id`/`platform_client_id`, 0107) held only
    with an active membership for that portal — which is why `seed` registers a real
    environment row per firm and `act` gives each login the RIGHT application id for its
    portal, rather than one interchangeable value for everyone. The first version of this
    suite used one org for every login and every assertion still passed, entirely on the old
    `role() = 'client'` fallback — a green suite that never actually exercised the real signal.
    That fallback is deleted now (`messaging._is_client_portal_session` is
    `auth.portal() = 'client'` only), so the classification here is the whole of the gate.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import db as db_mod
from app import live_push
from app import storage as storage_mod
from app import tenant_storage as tenant_storage_mod
from app.auth import Principal, get_current_principal
from app.main import app

# ---- seed ------------------------------------------------------------------------------------


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, str]:
    """Two tenants. Firm A carries an adviser, a client assigned to that adviser, a second
    staff member who is NOT on any book (permission, not assignment, is what lets them
    message), and a client with NO adviser assigned. Firm B carries its own adviser +
    client, entirely unrelated, to prove cross-tenant isolation."""
    s = uuid.uuid4().hex[:8]
    out: dict[str, str] = {"suffix": s}
    with super_engine.connect() as c:

        def mk_tenant(tag: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"msgten_{tag}_{s}", "a": f"msgfirm-{tag}-{s}", "n": f"MsgFirm {tag}"},
                ).scalar_one()
            )

        def mk_environment(tenant: int, tag: str) -> tuple[str, str]:
            """The real classification path `auth.resolve_claims` uses for the `portal` claim
            (0107): it matches the TOKEN'S APPLICATION client id against this row's
            `client_portal_client_id`/`platform_client_id` — and then requires an ACTIVE
            membership for that portal — never the login's `role` string."""
            client_app = f"app_client_{tag}_{s}"
            platform_app = f"app_platform_{tag}_{s}"
            c.execute(
                text(
                    "INSERT INTO auth.tenant_environments "
                    "  (tenant_id, stage, client_portal_client_id, platform_client_id) "
                    "VALUES (:t, 'dev', :c, :p)"
                ),
                {"t": tenant, "c": client_app, "p": platform_app},
            )
            return client_app, platform_app

        def mk_staff(tenant: int, tag: str) -> tuple[str, str]:
            wos = f"msgwos_{tag}_{s}"
            uid = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, "
                        "  first_name, last_name, enabled) "
                        "VALUES (:uid, :t, :email, :wos, :first, 'Adviser', true) RETURNING id"
                    ),
                    {
                        "uid": f"msgusr_{tag}_{s}",
                        "t": tenant,
                        "email": f"{tag}_{s}@msgfirm.test",
                        "wos": wos,
                        "first": tag.title(),
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, 'platform')"
                ),
                {"u": uid, "t": tenant},
            )
            return uid, wos

        def mk_client(tenant: int, tag: str, adviser_uuid: str | None) -> tuple[str, str, int]:
            """A client login (auth.users) linked to a fresh crm.contacts row via
            crm.contact_users, optionally assigned an adviser via crm.client_advisers."""
            wos = f"msgwos_{tag}_{s}"
            uid = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                        "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                    ),
                    {
                        "uid": f"msgusr_{tag}_{s}",
                        "t": tenant,
                        "email": f"{tag}_{s}@msgclient.test",
                        "wos": wos,
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, 'client')"
                ),
                {"u": uid, "t": tenant},
            )
            contact_id = int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, name, service_type) "
                        "VALUES (:t, :cuid, 'individual', :n, 'advised') RETURNING id"
                    ),
                    {"t": tenant, "cuid": f"msgcon_{tag}_{s}", "n": f"Contact-{tag}-{s}"},
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                    "VALUES (:t, :c, :u, 'self')"
                ),
                {"t": tenant, "c": contact_id, "u": uid},
            )
            if adviser_uuid is not None:
                c.execute(
                    text(
                        "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                        "VALUES (:t, :c, :a, 'adviser')"
                    ),
                    {"t": tenant, "c": contact_id, "a": adviser_uuid},
                )
            return uid, wos, contact_id

        firm_a = mk_tenant("a")
        client_app_a, platform_app_a = mk_environment(firm_a, "a")
        adviser_a_uuid, adviser_a_wos = mk_staff(firm_a, "advisera")
        # A SECOND member of firm A who is nobody's assigned adviser. Real firms answer a client
        # from more than one desk, and "the adviser assigned to this client" is then no longer a
        # safe stand-in for "who wrote this message".
        _paraplanner_a_uuid, paraplanner_a_wos = mk_staff(firm_a, "paraplannera")
        _staff_a_uuid, staff_a_wos = mk_staff(firm_a, "staffa")
        _client_a_uid, client_a_wos, contact_a = mk_client(firm_a, "clienta", adviser_a_uuid)
        _client_a2_uid, client_a2_wos, contact_a2 = mk_client(firm_a, "clienta2", adviser_a_uuid)
        _unassigned_uid, unassigned_wos, unassigned_contact = mk_client(firm_a, "noadviser", None)

        firm_b = mk_tenant("b")
        client_app_b, platform_app_b = mk_environment(firm_b, "b")
        adviser_b_uuid, adviser_b_wos = mk_staff(firm_b, "adviserb")
        _client_b_uid, client_b_wos, contact_b = mk_client(firm_b, "clientb", adviser_b_uuid)

        c.commit()

    out.update(
        firm_a=str(firm_a),
        firm_b=str(firm_b),
        client_app_a=client_app_a,
        platform_app_a=platform_app_a,
        client_app_b=client_app_b,
        platform_app_b=platform_app_b,
        adviser_a_wos=adviser_a_wos,
        paraplanner_a_wos=paraplanner_a_wos,
        staff_a_wos=staff_a_wos,
        adviser_b_wos=adviser_b_wos,
        client_a_wos=client_a_wos,
        client_a2_wos=client_a2_wos,
        client_b_wos=client_b_wos,
        unassigned_wos=unassigned_wos,
        contact_a=str(contact_a),
        contact_a2=str(contact_a2),
        contact_b=str(contact_b),
        unassigned_contact=str(unassigned_contact),
    )
    return out


# ---- harness (mirrors test_book_session.py) ---------------------------------------------------


@pytest_asyncio.fixture
async def client(db: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "SessionLocal", maker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


ActAs = Callable[..., None]


@pytest.fixture
def act(seed: dict[str, str]) -> AsyncIterator[ActAs]:
    """Install a principal for subsequent requests. `who` keys into `seed`'s *_wos columns;
    `firm` + whether `who` is a client login picks which of the firm's TWO registered
    APPLICATIONS this login's token names (`client_id`) — `auth.resolve_claims` classifies the
    `portal` claim from THIS plus an active membership (0107), never from `role` (a real
    client's WorkOS role is never literally 'client', so handing every login one
    interchangeable application id would let every test pass on a fallback without ever
    exercising the real classification — see the module docstring)."""
    is_client_who = {"client_a", "client_a2", "client_b", "unassigned"}

    def _as(who: str, perms: list[str], role: str, firm: str) -> None:
        wos = seed[f"{who}_wos"]
        app_key = f"{'client' if who in is_client_who else 'platform'}_app_{firm}"
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            user_id=wos,
            organisation_id=f"org_msgfirm_{firm}_{seed['suffix']}",
            role=role,
            permissions=list(perms),
            client_id=seed[app_key],
        )

    yield _as
    app.dependency_overrides.pop(get_current_principal, None)


# ---- client side: a client only ever reaches THEIR adviser ----------------------------------


@pytest.mark.asyncio
async def test_client_thread_resolves_the_assigned_adviser(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    act("client_a", [], "client", "a")
    res = await client.get("/messaging/thread")
    assert res.status_code == 200
    body = res.json()
    assert body["conversation"]["contact_id"] == int(seed["contact_a"])
    assert body["conversation"]["adviser_user_id"] is not None
    assert body["messages"] == []


@pytest.mark.asyncio
async def test_client_with_no_adviser_assigned_still_gets_a_thread(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """A conversation is with the firm, not with a named adviser. The pointer stays NULL
    until someone is assigned; the client can still send."""
    act("unassigned", [], "client", "a")
    res = await client.get("/messaging/thread")
    assert res.status_code == 200
    body = res.json()
    assert body["conversation"]["contact_id"] == int(seed["unassigned_contact"])
    assert body["conversation"]["adviser_user_id"] is None
    assert body["messages"] == []


@pytest.mark.asyncio
async def test_client_send_then_read_back(client: AsyncClient, act: ActAs) -> None:
    act("client_a", [], "client", "a")
    sent = await client.post("/messaging/thread/messages", json={"text": "Hello Sarah"})
    assert sent.status_code == 200
    assert sent.json()["sender_kind"] == "client"

    thread = (await client.get("/messaging/thread")).json()
    assert [m["body"] for m in thread["messages"]] == ["Hello Sarah"]


@pytest.mark.asyncio
async def test_a_real_world_client_role_slug_is_not_refused(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """The bug this test exists to pin: a real client's WorkOS role is never literally the
    string 'client' — it's a role slug like 'org-advised-client' (whatever the firm's WorkOS
    project happens to call its client role). `auth.portal() = 'client'` is the ONLY signal now
    (0107 deleted the `role() = 'client'` fallback outright); checking role() alone refused
    every real client-portal login. The seed holds a `portal = 'client'` membership and `act`
    presents the client application's id regardless of the role string, exactly like a real
    login, so this exercises the same path a real client hits."""
    act("client_a", [], "org-advised-client", "a")
    res = await client.get("/messaging/thread")
    assert res.status_code == 200
    assert res.json()["conversation"]["contact_id"] == int(seed["contact_a"])


@pytest.mark.asyncio
async def test_thread_contact_id_selects_which_linked_contacts_channel(
    client: AsyncClient, seed: dict[str, str], act: ActAs, super_engine: Engine
) -> None:
    """A login linked to more than one client contact has ONE conversation per contact
    (messaging.conversations' unique key is (tenant_id, contact_id)) -- ?contact_id= is the
    active-contact selector that picks which one a request means, both for reading the thread
    and for the ticket's subscription key (mint_ticket)."""
    tenant_a = int(seed["firm_a"])
    contact_a = int(seed["contact_a"])
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        client_a_uid = c.execute(
            text("SELECT id FROM auth.users WHERE workos_user_id = :w"),
            {"w": seed["client_a_wos"]},
        ).scalar_one()
        adviser_uid = c.execute(
            text("SELECT id FROM auth.users WHERE workos_user_id = :w"),
            {"w": seed["adviser_a_wos"]},
        ).scalar_one()
        second_contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, service_type) "
                    "VALUES (:t, :u, 'individual', 'Second Contact', 'advised') RETURNING id"
                ),
                {"t": tenant_a, "u": f"msgcon_second_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                "VALUES (:t, :c, :u, 'authorised')"
            ),
            {"t": tenant_a, "c": second_contact, "u": client_a_uid},
        )
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :c, :a, 'adviser')"
            ),
            {"t": tenant_a, "c": second_contact, "a": adviser_uid},
        )
        c.commit()

    act("client_a", [], "client", "a")
    first = await client.get("/messaging/thread", params={"contact_id": contact_a})
    second = await client.get("/messaging/thread", params={"contact_id": second_contact})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["conversation"]["contact_id"] == contact_a
    assert second.json()["conversation"]["contact_id"] == second_contact
    assert first.json()["conversation"]["id"] != second.json()["conversation"]["id"]

    # An out-of-scope contact_id is refused-back-to-default, never honoured.
    outside = await client.get("/messaging/thread", params={"contact_id": int(seed["contact_b"])})
    assert outside.json()["conversation"]["contact_id"] == contact_a

    # Sending with contact_id lands in THAT contact's own conversation.
    sent = await client.post(
        "/messaging/thread/messages",
        params={"contact_id": second_contact},
        json={"text": "Hello from the second contact"},
    )
    assert sent.status_code == 200
    thread_two = (
        await client.get("/messaging/thread", params={"contact_id": second_contact})
    ).json()
    assert [m["body"] for m in thread_two["messages"]] == ["Hello from the second contact"]
    thread_one = (await client.get("/messaging/thread", params={"contact_id": contact_a})).json()
    assert thread_one["messages"] == []


@pytest.mark.asyncio
async def test_an_adviser_calling_the_client_endpoint_is_refused_not_treated_as_their_client(
    client: AsyncClient, act: ActAs
) -> None:
    """The resolve_claims gotcha: adviser_a's contact_scope() is non-empty (it includes their
    OWN assigned client, contact_a), so the client-only endpoint must refuse them on the portal
    claim, never silently resolve "their" thread as client_a's."""
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    res = await client.get("/messaging/thread")
    assert res.status_code == 404


# ---- staff side: whole-tenant visibility, never cross-tenant --------------------------------


@pytest.mark.asyncio
async def test_staff_without_the_permission_is_refused(client: AsyncClient, act: ActAs) -> None:
    act("adviser_a", [], "adviser", "a")
    assert (await client.get("/messaging/conversations")).status_code == 403


@pytest.mark.asyncio
async def test_staff_sees_the_whole_tenant_book_and_can_search_it(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    # A message must exist for the channel to be listed at all (nothing to list before the
    # client's own session first resolves their thread).
    act("client_a", [], "client", "a")
    await client.post(
        "/messaging/thread/messages", json={"text": "hi — the xylophone certificate is attached"}
    )

    act("adviser_a", ["messages:read"], "adviser", "a")
    listing = await client.get("/messaging/conversations")
    assert listing.status_code == 200
    rows = listing.json()
    assert any(r["contact_id"] == int(seed["contact_a"]) for r in rows)
    assert next(r for r in rows if r["contact_id"] == int(seed["contact_a"]))["unread"] is True
    # Both projections of a conversation publish the client's uid — the inbox reads this listing,
    # and the rail then asks `GET /crm/contacts/{contact_uid}` for the advisers on that client.
    assert next(r for r in rows if r["contact_id"] == int(seed["contact_a"]))["contact_uid"]

    found = await client.get("/messaging/conversations", params={"q": "Contact-clienta"})
    assert any(r["contact_id"] == int(seed["contact_a"]) for r in found.json())
    # A WORD FROM A MESSAGE finds the thread it was said on. The box reads "Search direct
    # messages…", and matching the client's name alone made it a lie.
    by_body = await client.get("/messaging/conversations", params={"q": "xylophone"})
    assert [r["contact_id"] for r in by_body.json()] == [int(seed["contact_a"])]
    missing = await client.get("/messaging/conversations", params={"q": "nobody-matches-this"})
    assert missing.json() == []


@pytest.mark.asyncio
async def test_staff_can_open_a_conversation_with_a_client_who_has_never_logged_in(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """The client here never sends anything — this is the "New" flow: an adviser messaging a
    client first, who has no portal login at all yet. It must not require a prior client
    message or session to exist."""
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    opened = await client.post(
        "/messaging/conversations", json={"contact_id": int(seed["contact_a"])}
    )
    assert opened.status_code == 200
    # The platform inbox and the client record's Chats section both render off this response
    # directly (no separate lookup) — it must name the client, not just carry their id.
    assert opened.json()["contact_name"]
    # And their UID, which is what a surface holding only a conversation asks the book with:
    # `GET /crm/contacts/{contact_uid}` is uid-addressed, so the inbox's rail cannot read its own
    # client's advisers from the serial alone.
    assert opened.json()["contact_uid"]
    conversation_uid = opened.json()["uid"]

    sent = await client.post(
        f"/messaging/conversations/{conversation_uid}/messages",
        json={"text": "Welcome aboard — let's get your Fact Find started."},
    )
    assert sent.status_code == 200

    # The client sees it as unread the first time they DO log in.
    act("client_a", [], "client", "a")
    thread = (await client.get("/messaging/thread")).json()
    assert thread["conversation"]["unread"] is True
    assert thread["messages"][0]["body"] == "Welcome aboard — let's get your Fact Find started."


@pytest.mark.asyncio
async def test_reopening_a_conversation_writes_nothing(
    client: AsyncClient, seed: dict[str, str], act: ActAs, super_engine: Engine
) -> None:
    """Opening an EXISTING conversation must not touch the row.

    THE DEFECT. This is a get-or-create, and the client record's Messages tab calls it on every
    page view — and again on every `invalidate`. Its `ON CONFLICT DO UPDATE` re-set
    `adviser_user_id` to the value it already held, which fires the UPDATE trigger `0080` attached,
    so every view of a thread left an `audit.log` row for a conversation nobody had touched. The
    action stream had the same shape one layer up ("opened a conversation with X", dozens a day),
    which is why that route is now on `EXEMPT_MUTATING_ROUTES`.

    Asserted on the audit spine rather than on `updated_at`, because the spine is what a compliance
    reader actually opens — and it is the thing that was filling up.
    """
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    body = {"contact_id": int(seed["contact_a"])}

    first = await client.post("/messaging/conversations", json=body)
    assert first.status_code == 200

    def audit_rows() -> int:
        with super_engine.connect() as c:
            return int(
                c.execute(
                    text(
                        "SELECT count(*) FROM audit.log "
                        "WHERE entity_type = 'messaging.conversations'"
                    )
                ).scalar_one()
            )

    after_create = audit_rows()
    # …and again, the way a second page view does.
    again = await client.post("/messaging/conversations", json=body)
    assert again.status_code == 200
    # Same conversation, still fully named — the read-back arm must return what the insert would.
    assert again.json()["uid"] == first.json()["uid"]
    assert again.json()["contact_name"] == first.json()["contact_name"]
    assert audit_rows() == after_create, "re-opening an existing conversation wrote to the row"


@pytest.mark.asyncio
async def test_staff_opening_a_conversation_needs_messages_write_not_just_read(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    act("adviser_a", ["messages:read"], "adviser", "a")
    res = await client.post("/messaging/conversations", json={"contact_id": int(seed["contact_a"])})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_staff_can_open_a_conversation_for_a_client_with_no_adviser_assigned(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    act("adviser_a", ["messages:write"], "adviser", "a")
    res = await client.post(
        "/messaging/conversations", json={"contact_id": int(seed["unassigned_contact"])}
    )
    assert res.status_code == 200
    assert res.json()["contact_id"] == int(seed["unassigned_contact"])
    assert res.json()["adviser_user_id"] is None


@pytest.mark.asyncio
async def test_staff_opening_a_conversation_resolves_the_adviser_not_another_team_member(
    client: AsyncClient, seed: dict[str, str], act: ActAs, super_engine: Engine
) -> None:
    """The conversation's adviser pointer (`get_or_create_conversation_for_contact`) used to be a
    bare `LIMIT 1` with no `ORDER BY` at all -- whichever team row Postgres happened to return
    first. `role = 'adviser'` is unique per contact (0245), so the pointer can now ask for it by
    name instead of by luck. The non-adviser role is inserted FIRST, so a naive `LIMIT 1` would
    have returned it rather than the adviser."""
    with super_engine.connect() as c:
        adviser_id = c.execute(
            text("SELECT id FROM auth.users WHERE workos_user_id = :w"),
            {"w": seed["staff_a_wos"]},
        ).scalar_one()
        paraplanner_id = c.execute(
            text("SELECT id FROM auth.users WHERE workos_user_id = :w"),
            {"w": seed["paraplanner_a_wos"]},
        ).scalar_one()
        for user_id, role in ((paraplanner_id, "paraplanner"), (adviser_id, "adviser")):
            c.execute(
                text(
                    "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                    "VALUES (:t, :c, :a, :r)"
                ),
                {
                    "t": int(seed["firm_a"]),
                    "c": int(seed["unassigned_contact"]),
                    "a": user_id,
                    "r": role,
                },
            )
        c.commit()

    act("staff_a", ["messages:write"], "adviser", "a")
    res = await client.post(
        "/messaging/conversations", json={"contact_id": int(seed["unassigned_contact"])}
    )
    assert res.status_code == 200
    assert str(res.json()["adviser_user_id"]) == str(adviser_id)


@pytest.mark.asyncio
async def test_staff_who_is_not_the_assigned_adviser_can_message_a_client(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """`messages:write` is the gate, not `crm.client_advisers`. staff_a is in the firm and
    assigned to nobody; they can still open contact_a's thread and send."""
    act("staff_a", ["messages:read", "messages:write"], "adviser", "a")
    opened = await client.post(
        "/messaging/conversations", json={"contact_id": int(seed["contact_a"])}
    )
    assert opened.status_code == 200
    uid = opened.json()["uid"]
    sent = await client.post(
        f"/messaging/conversations/{uid}/messages", json={"text": "Hello from ops"}
    )
    assert sent.status_code == 200
    assert sent.json()["body"] == "Hello from ops"


@pytest.mark.asyncio
async def test_staff_can_reopen_an_existing_conversation_after_the_adviser_is_unassigned(
    client: AsyncClient,
    seed: dict[str, str],
    act: ActAs,
    super_engine: Engine,
) -> None:
    """Un-assigning someone must not hide a conversation that already has messages — the
    client's Messages tab used to 404 over a live thread, which the portal rendered as
    "couldn't be loaded"."""
    act("adviser_a", ["messages:write"], "adviser", "a")
    opened = await client.post(
        "/messaging/conversations", json={"contact_id": int(seed["contact_a"])}
    )
    assert opened.status_code == 200
    uid = opened.json()["uid"]
    with super_engine.connect() as c:
        c.execute(
            text("DELETE FROM crm.client_advisers WHERE contact_id = :c"),
            {"c": int(seed["contact_a"])},
        )
        c.commit()
    again = await client.post(
        "/messaging/conversations", json={"contact_id": int(seed["contact_a"])}
    )
    assert again.status_code == 200
    assert again.json()["uid"] == uid


@pytest.mark.asyncio
async def test_staff_cannot_open_a_conversation_for_another_firms_client(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    act("adviser_b", ["messages:write"], "adviser", "b")
    res = await client.post("/messaging/conversations", json={"contact_id": int(seed["contact_a"])})
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_staff_reply_lands_in_the_clients_own_thread(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    act("client_a", [], "client", "a")
    conversation_uid = (await client.get("/messaging/thread")).json()["conversation"]["uid"]

    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    reply = await client.post(
        f"/messaging/conversations/{conversation_uid}/messages", json={"text": "On it"}
    )
    assert reply.status_code == 200
    assert reply.json()["sender_kind"] == "staff"

    act("client_a", [], "client", "a")
    thread = (await client.get("/messaging/thread")).json()
    assert thread["messages"][-1]["body"] == "On it"
    assert thread["conversation"]["unread"] is True  # the client hasn't read it yet


@pytest.mark.asyncio
async def test_staff_message_carries_the_senders_real_name_and_role(
    client: AsyncClient, seed: dict[str, str], act: ActAs, super_engine: Engine
) -> None:
    """The Slack-style header needs a real name + role, not a hardcoded 'Staff' — resolved via
    auth.organisation_memberships/auth.roles (independent of the test's mocked Principal, which
    carries its own role/permissions strings only for the authz check). A client-sent message
    carries neither: the frontend already has the client's own name off the conversation."""
    with super_engine.connect() as c:
        role_id = c.execute(
            text(
                "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                "VALUES (:t, 'owner', 'Owner', 'platform') RETURNING id"
            ),
            {"t": int(seed["firm_a"])},
        ).scalar_one()
        c.execute(
            text(
                "UPDATE auth.organisation_memberships SET role_id = :r "
                "WHERE tenant_id = :t AND user_id = "
                "  (SELECT id FROM auth.users WHERE workos_user_id = :wos)"
            ),
            {"r": role_id, "t": int(seed["firm_a"]), "wos": seed["adviser_a_wos"]},
        )
        c.commit()

    act("client_a", [], "client", "a")
    conversation_uid = (await client.get("/messaging/thread")).json()["conversation"]["uid"]

    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    reply = await client.post(
        f"/messaging/conversations/{conversation_uid}/messages", json={"text": "On it"}
    )
    assert reply.status_code == 200
    assert reply.json()["sender_name"] == "Advisera Adviser"
    assert reply.json()["sender_role"] == "Owner"

    # Reading the thread back later resolves the same, not just the just-sent response.
    listed = (await client.get(f"/messaging/conversations/{conversation_uid}/messages")).json()
    assert listed[-1]["sender_name"] == "Advisera Adviser"
    assert listed[-1]["sender_role"] == "Owner"

    act("client_a", [], "client", "a")
    sent = await client.post("/messaging/thread/messages", json={"text": "thanks"})
    assert sent.json()["sender_name"] is None
    assert sent.json()["sender_role"] is None


@pytest.mark.asyncio
async def test_the_clients_own_thread_names_each_staff_sender_not_their_adviser(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """A client reading their own thread sees WHO wrote each message.

    The read joins `auth.users` for the sender's name, which a CLIENT session cannot do: 0107
    gives it an empty `auth.tenant_lineage()`, and `auth_users_scoped_read` then lets it see only
    its own row. Every staff sender came back with `sender_name: null`, and the portal — having
    nothing else — labelled them all with the adviser assigned to the client. In a firm where two
    people answer the same client, the second person's replies were published under the first
    person's name.

    So this asserts on the CLIENT'S OWN endpoint, deliberately: the staff endpoint runs on
    service_role, where the join has always worked, and pinning it there is what let this pass
    while the client portal was wrong. The paraplanner is nobody's assigned adviser, so the old
    fallback is guaranteed to name the wrong person if it comes back.
    """
    act("client_a", [], "client", "a")
    conversation_uid = (await client.get("/messaging/thread")).json()["conversation"]["uid"]

    for who, body in (("adviser_a", "From the adviser"), ("paraplanner_a", "From the paraplanner")):
        act(who, ["messages:read", "messages:write"], "adviser", "a")
        sent = await client.post(
            f"/messaging/conversations/{conversation_uid}/messages", json={"text": body}
        )
        assert sent.status_code == 200

    act("client_a", [], "client", "a")
    thread = (await client.get("/messaging/thread")).json()
    by_body = {m["body"]: m for m in thread["messages"]}
    assert by_body["From the adviser"]["sender_name"] == "Advisera Adviser"
    assert by_body["From the paraplanner"]["sender_name"] == "Paraplannera Adviser"
    # Two messages, two names — the failure mode was one name on both.
    assert (
        by_body["From the adviser"]["sender_name"] != by_body["From the paraplanner"]["sender_name"]
    )


@pytest.mark.asyncio
async def test_a_clients_own_message_still_carries_no_sender_name(
    client: AsyncClient, act: ActAs
) -> None:
    """The fill-in is for STAFF senders. A client's own message has no `auth.users` row behind it
    at all (`sender_user_id` is NULL on that path), so there is nothing to resolve and nothing
    must be invented — the portal draws those as "You" off `sender_kind`."""
    act("client_a", [], "client", "a")
    await client.post("/messaging/thread/messages", json={"text": "mine"})
    thread = (await client.get("/messaging/thread")).json()
    mine = next(m for m in thread["messages"] if m["body"] == "mine")
    assert mine["sender_user_id"] is None
    assert mine["sender_name"] is None


@pytest.mark.asyncio
async def test_a_firm_b_adviser_cannot_reach_firm_as_conversation_even_by_guessing_its_id(
    client: AsyncClient, act: ActAs
) -> None:
    act("client_a", [], "client", "a")
    conversation_uid = (await client.get("/messaging/thread")).json()["conversation"]["uid"]

    act("adviser_b", ["messages:read", "messages:write"], "adviser", "b")
    assert (
        await client.get(f"/messaging/conversations/{conversation_uid}/messages")
    ).status_code == 404
    assert (
        await client.post(
            f"/messaging/conversations/{conversation_uid}/messages", json={"text": "nope"}
        )
    ).status_code == 404
    # And firm B's own listing never surfaces firm A's conversation at all.
    listing = (await client.get("/messaging/conversations")).json()
    assert all(r["uid"] != conversation_uid for r in listing)


# ---- unread is a read-receipt computation, not a stored counter ------------------------------


@pytest.mark.asyncio
async def test_unread_clears_on_read_for_both_sides(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    act("client_a", [], "client", "a")
    await client.post("/messaging/thread/messages", json={"text": "one"})
    conversation_uid = (await client.get("/messaging/thread")).json()["conversation"]["uid"]

    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    before = next(
        r
        for r in (await client.get("/messaging/conversations")).json()
        if r["uid"] == conversation_uid
    )
    assert before["unread"] is True
    await client.post(f"/messaging/conversations/{conversation_uid}/read")
    after = next(
        r
        for r in (await client.get("/messaging/conversations")).json()
        if r["uid"] == conversation_uid
    )
    assert after["unread"] is False

    await client.post(f"/messaging/conversations/{conversation_uid}/messages", json={"text": "re"})

    act("client_a", [], "client", "a")
    assert (await client.get("/messaging/thread")).json()["conversation"]["unread"] is True
    await client.post("/messaging/thread/read")
    assert (await client.get("/messaging/thread")).json()["conversation"]["unread"] is False


@pytest.mark.asyncio
async def test_the_chats_nav_dot_is_tenant_wide_and_clears_on_read(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """GET /messaging/unread — the same staff-read-receipt predicate as the per-row `unread`
    on GET /messaging/conversations, just collapsed to one boolean for the nav. A SECOND client
    of the SAME adviser proves it's tenant-wide, not this-one-conversation-only."""
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    assert (await client.get("/messaging/unread")).json()["unread"] is False

    act("client_a", [], "client", "a")
    await client.post("/messaging/thread/messages", json={"text": "one"})
    conversation_uid = (await client.get("/messaging/thread")).json()["conversation"]["uid"]

    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    assert (await client.get("/messaging/unread")).json()["unread"] is True
    await client.post(f"/messaging/conversations/{conversation_uid}/read")
    assert (await client.get("/messaging/unread")).json()["unread"] is False

    # A SECOND client, same firm, same adviser — still tenant-wide.
    act("client_a2", [], "client", "a")
    await client.post("/messaging/thread/messages", json={"text": "two"})

    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    assert (await client.get("/messaging/unread")).json()["unread"] is True

    # Firm B's adviser never sees firm A's unread client message.
    act("adviser_b", ["messages:read", "messages:write"], "adviser", "b")
    assert (await client.get("/messaging/unread")).json()["unread"] is False


@pytest.mark.asyncio
async def test_the_unread_check_can_be_narrowed_to_one_client(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """The client record's own Chats section asks about ONE client, via `?contact_id=` — must
    not answer for the whole tenant, and must not create a conversation for a client who has
    never messaged (a bare read, no side effect)."""
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    # A2 has no conversation at all yet — reads as not-unread, not a 404 or a created row.
    assert (
        await client.get("/messaging/unread", params={"contact_id": int(seed["contact_a2"])})
    ).json()["unread"] is False

    act("client_a", [], "client", "a")
    await client.post("/messaging/thread/messages", json={"text": "hello"})

    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    assert (
        await client.get("/messaging/unread", params={"contact_id": int(seed["contact_a"])})
    ).json()["unread"] is True
    # A2 is still clean — this client's message doesn't bleed into another's dot.
    assert (
        await client.get("/messaging/unread", params={"contact_id": int(seed["contact_a2"])})
    ).json()["unread"] is False
    # A2 still has no conversation row — the read-only check above never created one.
    a2_conversations = [
        r
        for r in (await client.get("/messaging/conversations")).json()
        if r["contact_id"] == int(seed["contact_a2"])
    ]
    assert a2_conversations == []


# ---- attachments (0081_messaging_attachments) ------------------------------------------------


@pytest.fixture
def attachment_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """Fake the tenant-bucket object store so these tests never touch real S3/RustFS — same
    monkeypatch-the-seam pattern test_documents_api.py's `wire` fixture uses for
    `storage.put_document`. Every seeded tenant gets a fake ACTIVE store (none of them carry a
    real `storage.tenants` row), keyed so cross-tenant leakage would show up as a KeyError."""
    blobs: dict[str, bytes] = {}

    async def _get_tenant_store(session: object, tenant_id: int) -> tenant_storage_mod.TenantStore:
        return tenant_storage_mod.TenantStore(
            tenant_id=tenant_id,
            kind="rustfs",
            endpoint="http://fake-rustfs.test",
            region=None,
            bucket=f"t-fake-{tenant_id}",
            public_base_url=None,
            status="active",
        )

    async def _put(
        store: tenant_storage_mod.TenantStore, key: str, data: bytes, content_type: str
    ) -> None:
        blobs[f"{store.bucket}/{key}"] = data

    async def _get(store: tenant_storage_mod.TenantStore, key: str) -> tuple[bytes, str]:
        return blobs[f"{store.bucket}/{key}"], "application/octet-stream"

    monkeypatch.setattr(tenant_storage_mod, "get_tenant_store", _get_tenant_store)
    monkeypatch.setattr(storage_mod, "put_tenant_object", _put)
    monkeypatch.setattr(storage_mod, "get_tenant_object", _get)
    return blobs


@pytest.mark.asyncio
async def test_client_can_attach_a_file_and_staff_can_download_it(
    client: AsyncClient, act: ActAs, attachment_store: dict[str, bytes]
) -> None:
    act("client_a", [], "client", "a")
    sent = await client.post(
        "/messaging/thread/attachments",
        files={"file": ("notes.txt", b"call back re: ISA transfer", "text/plain")},
        data={"text": ""},
    )
    assert sent.status_code == 200
    message = sent.json()
    assert message["sender_kind"] == "client"
    assert message["body"] is None  # attachment-only — no text was sent
    assert message["attachments"][0]["filename"] == "notes.txt"
    assert message["attachments"][0]["content_type"] == "text/plain"
    attachment_uid = message["attachments"][0]["uid"]

    thread = (await client.get("/messaging/thread")).json()
    assert thread["messages"][0]["attachments"][0]["uid"] == attachment_uid

    act("adviser_a", ["messages:read"], "adviser", "a")
    conversation_uid = (await client.get("/messaging/conversations")).json()[0]["uid"]
    messages = (await client.get(f"/messaging/conversations/{conversation_uid}/messages")).json()
    assert messages[0]["attachments"][0]["uid"] == attachment_uid

    download = await client.get(
        f"/messaging/conversations/{conversation_uid}/attachments/{attachment_uid}"
    )
    assert download.status_code == 200
    assert download.content == b"call back re: ISA transfer"
    assert "notes.txt" in download.headers["content-disposition"]


@pytest.mark.asyncio
async def test_staff_can_attach_a_file_and_client_can_download_it(
    client: AsyncClient, act: ActAs, attachment_store: dict[str, bytes]
) -> None:
    act("client_a", [], "client", "a")
    conversation_uid = (await client.get("/messaging/thread")).json()["conversation"]["uid"]

    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    sent = await client.post(
        f"/messaging/conversations/{conversation_uid}/attachments",
        files={"file": ("illustration.pdf", b"%PDF-1.7\nfake but sniffable", "application/pdf")},
        data={"text": "Here's the illustration we discussed"},
    )
    assert sent.status_code == 200
    message = sent.json()
    assert message["sender_kind"] == "staff"
    assert message["body"] == "Here's the illustration we discussed"
    assert message["attachments"][0]["content_type"] == "application/pdf"
    attachment_uid = message["attachments"][0]["uid"]

    act("client_a", [], "client", "a")
    download = await client.get(f"/messaging/thread/attachments/{attachment_uid}")
    assert download.status_code == 200
    assert download.content == b"%PDF-1.7\nfake but sniffable"


@pytest.mark.asyncio
async def test_several_files_ride_ONE_message(
    client: AsyncClient, act: ActAs, attachment_store: dict[str, bytes]
) -> None:
    """Attaching three documents is ONE message carrying three files, not three messages.

    0081 made `message_id` UNIQUE, so the old shape could only be one-file-per-message: attaching
    three produced three rows, and any note typed alongside them landed on the first with the other
    two arriving bare underneath. 0123 drops that constraint.

    The storage key moved with it. It was `messages/{message_id}`, which two files on one message
    collide on — the second would overwrite the first in the object store while both rows survived,
    so one attachment downloaded the other's bytes. Hence the download assertions: they are the only
    thing that catches a key collision, since the metadata looks perfect either way.
    """
    act("client_a", [], "client", "a")
    conversation_uid = (await client.get("/messaging/thread")).json()["conversation"]["uid"]

    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    sent = await client.post(
        f"/messaging/conversations/{conversation_uid}/attachments",
        files=[
            ("file", ("first.pdf", b"%PDF-1.7\nfirst", "application/pdf")),
            ("file", ("second.pdf", b"%PDF-1.7\nsecond", "application/pdf")),
        ],
        data={"text": "Both of these, please"},
    )
    assert sent.status_code == 200
    message = sent.json()
    # ONE message…
    assert message["body"] == "Both of these, please"
    # …carrying BOTH files, in the order they were sent.
    assert [a["filename"] for a in message["attachments"]] == ["first.pdf", "second.pdf"]

    # …and each downloads its OWN bytes, which is what a colliding storage key would break.
    act("client_a", [], "client", "a")
    for attachment, expected in zip(
        message["attachments"], (b"%PDF-1.7\nfirst", b"%PDF-1.7\nsecond"), strict=True
    ):
        got = await client.get(f"/messaging/thread/attachments/{attachment['uid']}")
        assert got.status_code == 200
        assert got.content == expected

    # The thread reads back as one message too — a join rather than an aggregate would have
    # returned it once per file.
    thread = (await client.get("/messaging/thread")).json()
    carrying = [m for m in thread["messages"] if m["body"] == "Both of these, please"]
    assert len(carrying) == 1
    assert len(carrying[0]["attachments"]) == 2


@pytest.mark.asyncio
async def test_attachment_upload_rejects_disallowed_content(
    client: AsyncClient, act: ActAs, attachment_store: dict[str, bytes]
) -> None:
    act("client_a", [], "client", "a")
    res = await client.post(
        "/messaging/thread/attachments",
        files={
            "file": (
                "virus.exe",
                b"MZ\x90\x00\x03\x00\x00\x00binary-not-office",
                "application/octet-stream",
            )
        },
        data={"text": ""},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_a_firm_b_adviser_cannot_download_firm_as_attachment_even_by_guessing_its_id(
    client: AsyncClient, seed: dict[str, str], act: ActAs, attachment_store: dict[str, bytes]
) -> None:
    act("client_a", [], "client", "a")
    sent = await client.post(
        "/messaging/thread/attachments",
        files={"file": ("private.txt", b"sensitive", "text/plain")},
        data={"text": ""},
    )
    attachment_uid = sent.json()["attachments"][0]["uid"]

    # A REAL firm-B conversation id (its own client) — the attachment_id is the only thing
    # guessed here, closer to what an attacker actually controls than a made-up conversation id.
    act("adviser_b", ["messages:read", "messages:write"], "adviser", "b")
    firm_b_conversation_uid = (
        await client.post("/messaging/conversations", json={"contact_id": int(seed["contact_b"])})
    ).json()["uid"]
    assert (
        await client.get(
            f"/messaging/conversations/{firm_b_conversation_uid}/attachments/{attachment_uid}"
        )
    ).status_code == 404

    act("unassigned", [], "client", "a")
    assert (await client.get(f"/messaging/thread/attachments/{attachment_uid}")).status_code == 404


# ---- document-request <-> chat echo (app/documents/service.py, routers/documents.py) --------


@pytest.mark.asyncio
async def test_creating_a_document_request_echoes_into_the_clients_chat(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """Raised from anywhere (not just the chat composer) — this is create_request's own echo,
    so a firm's Documents-tab request lands in the client's thread the same as a chat-raised one."""
    act("adviser_a", ["documents:send"], "adviser", "a")
    created = await client.post(
        "/documents/requests",
        json={
            "contact_id": int(seed["contact_a"]),
            "items": [{"action": "upload", "title": "Bank statement"}],
        },
    )
    assert created.status_code == 201

    act("client_a", [], "client", "a")
    thread = (await client.get("/messaging/thread")).json()
    last = thread["messages"][-1]
    assert last["sender_kind"] == "staff"
    assert last["body"] == "Requested: Bank statement"
    assert last["request_item"] == {
        "uid": created.json()["items"][0]["uid"],
        "title": "Bank statement",
        "action": "upload",
        "status": "pending",
    }


@pytest.mark.asyncio
async def test_the_request_echo_carries_its_request_item_immediately_not_only_on_reload(
    client: AsyncClient, seed: dict[str, str], act: ActAs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`send_staff_message_for_request_item` used to hand back `request_item: None` on its
    own return value — correct on a LATER `list_messages` reload (which joins
    `documents.request_items` live), but wrong on the WS push this same call fires
    immediately, which is what an already-open chat window renders with no reload at all
    (it rendered as plain "Requested: ..." text, no card, until someone reloaded). Capture
    the actual push payload rather than trusting a reload to paper over it."""
    pushed: list[dict] = []

    async def fake_push(key: str, payload: dict) -> None:
        pushed.append(payload)

    monkeypatch.setattr(live_push, "push", fake_push)

    act("adviser_a", ["documents:send"], "adviser", "a")
    created = await client.post(
        "/documents/requests",
        json={
            "contact_id": int(seed["contact_a"]),
            "items": [{"action": "upload", "title": "Bank statement"}],
        },
    )
    assert created.status_code == 201

    assert len(pushed) == 2  # one push to the client, one to staff
    expected_item = {
        "uid": created.json()["items"][0]["uid"],
        "title": "Bank statement",
        "action": "upload",
        "status": "pending",
    }
    for payload in pushed:
        assert payload["message"]["request_item"] == expected_item


@pytest.mark.asyncio
async def test_fulfilling_a_request_item_echoes_into_the_advisers_chat(
    client: AsyncClient, seed: dict[str, str], act: ActAs, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The upload path writes to the private-documents bucket — fake it out, same as
    # test_documents_api.py's `wire` fixture, so this never touches real S3/RustFS.
    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        pass

    monkeypatch.setattr(storage_mod, "put_document", _put)
    act("adviser_a", ["documents:send"], "adviser", "a")
    created = await client.post(
        "/documents/requests",
        json={
            "contact_id": int(seed["contact_a"]),
            "items": [{"action": "upload", "title": "Bank statement"}],
        },
    )
    body = created.json()
    req_uid = body["uid"]
    item_uid = body["items"][0]["uid"]

    act("client_a", [], "client", "a")
    fulfilled = await client.post(
        f"/documents/requests/{req_uid}/items/{item_uid}/fulfill",
        files={"file": ("statement.pdf", b"%PDF-1.7\nfake but sniffable", "application/pdf")},
    )
    assert fulfilled.status_code == 201, fulfilled.text

    act("adviser_a", ["messages:read"], "adviser", "a")
    conversation_uid = (await client.get("/messaging/conversations")).json()[0]["uid"]
    messages = (await client.get(f"/messaging/conversations/{conversation_uid}/messages")).json()
    last = messages[-1]
    assert last["sender_kind"] == "client"
    assert last["body"] == "Uploaded: Bank statement"


@pytest.mark.asyncio
async def test_fulfilling_a_request_via_the_chat_attachment_card_marks_it_fulfilled(
    client: AsyncClient, seed: dict[str, str], act: ActAs, attachment_store: dict[str, bytes]
) -> None:
    """The rich "click to upload" card: a client attaches a file in their own thread, naming the
    request_item_uid it fulfils — no documents.documents row this way (the blob lands in
    messaging.attachments instead), but the request/item status must still flip, same as the
    Requests-page path does."""
    act("adviser_a", ["documents:send"], "adviser", "a")
    created = await client.post(
        "/documents/requests",
        json={
            "contact_id": int(seed["contact_a"]),
            "items": [{"action": "upload", "title": "Bank statement"}],
        },
    )
    req_uid = created.json()["uid"]
    item_uid = created.json()["items"][0]["uid"]

    act("client_a", [], "client", "a")
    sent = await client.post(
        "/messaging/thread/attachments",
        files={"file": ("statement.pdf", b"%PDF-1.7\nfake but sniffable", "application/pdf")},
        data={"text": "", "request_item_uid": item_uid},
    )
    assert sent.status_code == 200
    sent_body = sent.json()
    assert sent_body["attachments"][0]["filename"] == "statement.pdf"
    # The attachment message itself carries the request's TITLE (not the raw filename) — the
    # rich "here's the document" card, distinct from a plain paperclip-sent attachment.
    assert sent_body["request_item"] == {
        "uid": item_uid,
        "title": "Bank statement",
        "action": "upload",
        "status": "fulfilled",
    }

    # The item and its parent request both flip to fulfilled.
    act("adviser_a", ["documents:read"], "adviser", "a")
    requests = (await client.get("/documents/requests")).json()
    detail = next(r for r in requests if r["uid"] == req_uid)
    assert detail["status"] == "fulfilled"
    assert detail["items"][0]["status"] == "fulfilled"

    # And the ORIGINAL "Requested" card now reads as fulfilled too (live-joined, not stale).
    act("client_a", [], "client", "a")
    thread = (await client.get("/messaging/thread")).json()
    requested = next(m for m in thread["messages"] if m["body"] == "Requested: Bank statement")
    assert requested["request_item"]["status"] == "fulfilled"

    # The attachment message's own request_item link persists too — a fresh reload (the JOIN in
    # list_messages) must agree with the hand-built response above, not just the initial reply.
    reloaded = next(m for m in thread["messages"] if m["id"] == sent_body["id"])
    assert reloaded["request_item"] == sent_body["request_item"]
    assert reloaded["attachments"][0]["filename"] == "statement.pdf"


@pytest.mark.asyncio
async def test_fulfilling_via_chat_refuses_an_already_fulfilled_item(
    client: AsyncClient, seed: dict[str, str], act: ActAs, attachment_store: dict[str, bytes]
) -> None:
    act("adviser_a", ["documents:send"], "adviser", "a")
    created = await client.post(
        "/documents/requests",
        json={
            "contact_id": int(seed["contact_a"]),
            "items": [{"action": "upload", "title": "Bank statement"}],
        },
    )
    item_uid = created.json()["items"][0]["uid"]

    act("client_a", [], "client", "a")
    first = await client.post(
        "/messaging/thread/attachments",
        files={"file": ("statement.pdf", b"%PDF-1.7\none", "application/pdf")},
        data={"text": "", "request_item_uid": item_uid},
    )
    assert first.status_code == 200

    second = await client.post(
        "/messaging/thread/attachments",
        files={"file": ("statement-again.pdf", b"%PDF-1.7\ntwo", "application/pdf")},
        data={"text": "", "request_item_uid": item_uid},
    )
    assert second.status_code == 404


@pytest.mark.asyncio
async def test_fulfilling_via_chat_refuses_someone_elses_request_item(
    client: AsyncClient, seed: dict[str, str], act: ActAs, attachment_store: dict[str, bytes]
) -> None:
    act("adviser_a", ["documents:send"], "adviser", "a")
    created = await client.post(
        "/documents/requests",
        json={
            "contact_id": int(seed["contact_a"]),
            "items": [{"action": "upload", "title": "Bank statement"}],
        },
    )
    item_uid = created.json()["items"][0]["uid"]

    act("client_b", [], "client", "b")
    res = await client.post(
        "/messaging/thread/attachments",
        files={"file": ("statement.pdf", b"%PDF-1.7\nfake", "application/pdf")},
        data={"text": "", "request_item_uid": item_uid},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_a_request_for_a_client_with_no_adviser_assigned_still_succeeds(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """The chat echo is best-effort: a missing conversation used to mean "no adviser
    assigned" and that must not fail the request. `contacts:read_all` is needed here just
    to REACH the unassigned contact at all (`_assert_visible_contact` otherwise scopes a
    staffer to their own assigned book). The echo now opens the thread rather than skipping
    it — assignment is not a gate."""
    act("adviser_a", ["documents:send", "contacts:read_all"], "adviser", "a")
    res = await client.post(
        "/documents/requests",
        json={
            "contact_id": int(seed["unassigned_contact"]),
            "items": [{"action": "upload", "title": "Proof of address"}],
        },
    )
    assert res.status_code == 201


# ---- groups: a staff-only saved shortcut, NOT a new conversation type ------------------------


@pytest.mark.asyncio
async def test_staff_can_create_a_group_and_it_lists_with_both_members(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    created = await client.post(
        "/messaging/groups",
        json={
            "name": "The Smith Household",
            "contact_ids": [int(seed["contact_a"]), int(seed["contact_a2"])],
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["name"] == "The Smith Household"
    assert {m["contact_id"] for m in body["members"]} == {
        int(seed["contact_a"]),
        int(seed["contact_a2"]),
    }

    listing = await client.get("/messaging/groups")
    assert listing.status_code == 200
    assert any(g["id"] == body["id"] and g["name"] == "The Smith Household" for g in listing.json())

    found = await client.get("/messaging/groups", params={"q": "smith"})
    assert any(g["id"] == body["id"] for g in found.json())
    missing = await client.get("/messaging/groups", params={"q": "nobody-matches-this"})
    assert missing.json() == []


@pytest.mark.asyncio
async def test_creating_a_group_refuses_an_empty_name_or_no_members(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    act("adviser_a", ["messages:write"], "adviser", "a")
    no_name = await client.post(
        "/messaging/groups", json={"name": "  ", "contact_ids": [int(seed["contact_a"])]}
    )
    assert no_name.status_code == 422
    no_members = await client.post(
        "/messaging/groups", json={"name": "Empty group", "contact_ids": []}
    )
    assert no_members.status_code == 422


@pytest.mark.asyncio
async def test_creating_a_group_refuses_a_foreign_or_unknown_contact_id(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    act("adviser_a", ["messages:write"], "adviser", "a")
    foreign = await client.post(
        "/messaging/groups",
        json={
            "name": "Cross-tenant",
            "contact_ids": [int(seed["contact_a"]), int(seed["contact_b"])],
        },
    )
    assert foreign.status_code == 422
    unknown = await client.post(
        "/messaging/groups", json={"name": "Ghost", "contact_ids": [999999999]}
    )
    assert unknown.status_code == 422


@pytest.mark.asyncio
async def test_a_brand_new_message_less_group_still_names_its_members(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """`get_group_feed` returns `members` alongside `messages` precisely so a caller can name
    every member of a group that has never sent anything yet — inferring membership from who has
    posted would miss them entirely."""
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    group_uid = (
        await client.post(
            "/messaging/groups",
            json={
                "name": "Fresh household",
                "contact_ids": [int(seed["contact_a"]), int(seed["contact_a2"])],
            },
        )
    ).json()["uid"]
    feed = await client.get(f"/messaging/groups/{group_uid}/messages")
    assert feed.status_code == 200
    body = feed.json()
    assert body["messages"] == []
    assert {m["contact_id"] for m in body["members"]} == {
        int(seed["contact_a"]),
        int(seed["contact_a2"]),
    }


@pytest.mark.asyncio
async def test_creating_a_group_admits_a_member_with_no_adviser_assigned(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """Assignment is not a gate on messaging — grouping an unassigned client used to 422
    because create_group mirrored the old conversation-create refusal."""
    act("adviser_a", ["messages:write", "contacts:read_all"], "adviser", "a")
    res = await client.post(
        "/messaging/groups",
        json={
            "name": "Has an unassigned client",
            "contact_ids": [int(seed["contact_a"]), int(seed["unassigned_contact"])],
        },
    )
    assert res.status_code == 200
    assert {m["contact_id"] for m in res.json()["members"]} == {
        int(seed["contact_a"]),
        int(seed["unassigned_contact"]),
    }


@pytest.mark.asyncio
async def test_creating_a_group_needs_messages_write_not_just_read(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    act("adviser_a", ["messages:read"], "adviser", "a")
    res = await client.post(
        "/messaging/groups", json={"name": "Nope", "contact_ids": [int(seed["contact_a"])]}
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_group_endpoints_need_the_permission_and_stay_within_the_firm(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    group_uid = (
        await client.post(
            "/messaging/groups",
            json={"name": "Perm check", "contact_ids": [int(seed["contact_a"])]},
        )
    ).json()["uid"]

    act("adviser_a", [], "adviser", "a")
    assert (await client.get("/messaging/groups")).status_code == 403
    assert (await client.get(f"/messaging/groups/{group_uid}/messages")).status_code == 403
    assert (
        await client.post(f"/messaging/groups/{group_uid}/messages", json={"text": "hi"})
    ).status_code == 403
    assert (
        await client.post(
            f"/messaging/groups/{group_uid}/attachments",
            files={"file": ("statement.pdf", b"%PDF-1.7\nfake", "application/pdf")},
            data={"text": ""},
        )
    ).status_code == 403

    # Another firm's adviser can't reach it even by guessing its id.
    act("adviser_b", ["messages:read", "messages:write"], "adviser", "b")
    assert (await client.get(f"/messaging/groups/{group_uid}/messages")).status_code == 404
    assert (
        await client.post(f"/messaging/groups/{group_uid}/messages", json={"text": "nope"})
    ).status_code == 404
    listing = (await client.get("/messaging/groups")).json()
    assert all(g["uid"] != group_uid for g in listing)


@pytest.mark.asyncio
async def test_sending_to_a_group_fans_out_into_each_members_own_real_conversation(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """The whole point of the fan-out model: a client never perceives that grouping exists — the
    message lands in their own ordinary conversation, exactly as an individual reply would."""
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    group_uid = (
        await client.post(
            "/messaging/groups",
            json={
                "name": "Household",
                "contact_ids": [int(seed["contact_a"]), int(seed["contact_a2"])],
            },
        )
    ).json()["uid"]

    sent = await client.post(
        f"/messaging/groups/{group_uid}/messages", json={"text": "Reminder: statements due Friday"}
    )
    assert sent.status_code == 200
    batch = sent.json()
    assert batch["body"] == "Reminder: statements due Friday"
    assert batch["sender_kind"] == "staff"
    assert {r["contact_id"] for r in batch["recipients"]} == {
        int(seed["contact_a"]),
        int(seed["contact_a2"]),
    }

    # Each client sees it in their OWN thread, with no trace of the group.
    act("client_a", [], "client", "a")
    thread_a = (await client.get("/messaging/thread")).json()
    assert thread_a["messages"][-1]["body"] == "Reminder: statements due Friday"

    act("client_a2", [], "client", "a")
    thread_a2 = (await client.get("/messaging/thread")).json()
    assert thread_a2["messages"][-1]["body"] == "Reminder: statements due Friday"


@pytest.mark.asyncio
async def test_a_group_send_is_invisible_from_the_advisers_direct_message_view(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """The other half of the fan-out model: a group broadcast lands in the member's REAL
    conversation row (so the client sees it, and it's the same row a genuine 1:1 reply would use
    later), but the adviser's own Direct-message lens — both the per-conversation thread and the
    conversations listing's preview — must not show it. It belongs to the group's own feed, not
    to what looks like an ordinary DM."""
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    group_uid = (
        await client.post(
            "/messaging/groups",
            json={"name": "Household", "contact_ids": [int(seed["contact_a"])]},
        )
    ).json()["uid"]
    await client.post(f"/messaging/groups/{group_uid}/messages", json={"text": "Broadcast only"})

    # The conversation now exists (the fan-out created it), but its Direct-message thread is
    # empty and its listing preview shows no trace of the broadcast.
    conversation_uid = (
        await client.get("/messaging/conversations", params={"q": "Contact-clienta"})
    ).json()[0]["uid"]
    thread = await client.get(f"/messaging/conversations/{conversation_uid}/messages")
    assert thread.status_code == 200
    assert thread.json() == []

    listing = (await client.get("/messaging/conversations")).json()
    row = next(r for r in listing if r["contact_id"] == int(seed["contact_a"]))
    assert row["last_message"] is None
    assert row["last_message_at"] is None

    # A genuine 1:1 reply afterwards shows up normally, on its own — the group send stays absent.
    reply = await client.post(
        f"/messaging/conversations/{conversation_uid}/messages", json={"text": "A real DM"}
    )
    assert reply.status_code == 200
    thread_after = (
        await client.get(f"/messaging/conversations/{conversation_uid}/messages")
    ).json()
    assert [m["body"] for m in thread_after] == ["A real DM"]
    listing_after = (await client.get("/messaging/conversations")).json()
    row_after = next(r for r in listing_after if r["contact_id"] == int(seed["contact_a"]))
    assert row_after["last_message"] == "A real DM"


@pytest.mark.asyncio
async def test_group_feed_shows_only_messages_sent_through_the_group(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """The bug this pins: a group's feed used to merge each member's WHOLE 1:1 history, so a
    plain DM sent outside the group (the paperclip, or "New") showed up in the group's own view
    too. Now it's scoped to `group_id` — only what was actually sent through the group."""
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    group_uid = (
        await client.post(
            "/messaging/groups",
            json={
                "name": "Household",
                "contact_ids": [int(seed["contact_a"]), int(seed["contact_a2"])],
            },
        )
    ).json()["uid"]

    # A direct, non-group message to one member — must NOT leak into the group's feed.
    opened = await client.post(
        "/messaging/conversations", json={"contact_id": int(seed["contact_a"])}
    )
    direct_conversation_uid = opened.json()["uid"]
    await client.post(
        f"/messaging/conversations/{direct_conversation_uid}/messages",
        json={"text": "A private aside, not for the group"},
    )

    await client.post(f"/messaging/groups/{group_uid}/messages", json={"text": "To both"})

    # A client's reply, even to a conversation the group touched, is a normal DM — not a group
    # message — so it must not appear in the group's feed either.
    act("client_a", [], "client", "a")
    await client.post("/messaging/thread/messages", json={"text": "Reply from Alice"})

    act("adviser_a", ["messages:read"], "adviser", "a")
    feed = await client.get(f"/messaging/groups/{group_uid}/messages")
    assert feed.status_code == 200
    body = feed.json()
    assert {m["contact_id"] for m in body["members"]} == {
        int(seed["contact_a"]),
        int(seed["contact_a2"]),
    }
    # ONE batch — not one row per recipient — even though it fanned out to two members.
    rows = body["messages"]
    assert len(rows) == 1
    batch = rows[0]
    assert batch["body"] == "To both"
    assert batch["sender_kind"] == "staff"
    assert {r["contact_id"] for r in batch["recipients"]} == {
        int(seed["contact_a"]),
        int(seed["contact_a2"]),
    }


@pytest.mark.asyncio
async def test_two_separate_group_sends_stay_as_two_separate_batches(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """The companion to the single-batch collapse above: two DIFFERENT sends must not merge into
    one just because they land in the same group — each keeps its own `batch_id`/recipients."""
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    group_uid = (
        await client.post(
            "/messaging/groups",
            json={
                "name": "Household",
                "contact_ids": [int(seed["contact_a"]), int(seed["contact_a2"])],
            },
        )
    ).json()["uid"]

    await client.post(f"/messaging/groups/{group_uid}/messages", json={"text": "First"})
    await client.post(f"/messaging/groups/{group_uid}/messages", json={"text": "Second"})

    feed = await client.get(f"/messaging/groups/{group_uid}/messages")
    rows = feed.json()["messages"]
    assert [r["body"] for r in rows] == ["First", "Second"]
    for row in rows:
        assert {r["contact_id"] for r in row["recipients"]} == {
            int(seed["contact_a"]),
            int(seed["contact_a2"]),
        }


@pytest.mark.asyncio
async def test_sending_an_attachment_to_a_group_fans_it_out_like_a_text_message(
    client: AsyncClient, seed: dict[str, str], act: ActAs, attachment_store: dict[str, bytes]
) -> None:
    """The attachment counterpart to the fan-out model: the SAME file lands in every member's
    own conversation as its OWN copy (own attachment row), and the group's feed still collapses
    the two per-member rows into one batch, same as a text send."""
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    group_uid = (
        await client.post(
            "/messaging/groups",
            json={
                "name": "Household",
                "contact_ids": [int(seed["contact_a"]), int(seed["contact_a2"])],
            },
        )
    ).json()["uid"]

    sent = await client.post(
        f"/messaging/groups/{group_uid}/attachments",
        files={"file": ("statement.pdf", b"%PDF-1.7\nfake but sniffable", "application/pdf")},
        data={"text": "Here's the statement"},
    )
    assert sent.status_code == 200
    batch = sent.json()
    assert batch["body"] == "Here's the statement"
    assert batch["attachments"][0]["content_type"] == "application/pdf"
    assert {r["contact_id"] for r in batch["recipients"]} == {
        int(seed["contact_a"]),
        int(seed["contact_a2"]),
    }

    # Each client got their OWN copy — same content, distinct attachment rows.
    act("client_a", [], "client", "a")
    attachment_a = (await client.get("/messaging/thread")).json()["messages"][-1]["attachments"][0]
    download_a = await client.get(f"/messaging/thread/attachments/{attachment_a['uid']}")
    assert download_a.status_code == 200
    assert download_a.content == b"%PDF-1.7\nfake but sniffable"

    act("client_a2", [], "client", "a")
    attachment_a2 = (await client.get("/messaging/thread")).json()["messages"][-1]["attachments"][0]
    assert attachment_a2["id"] != attachment_a["id"]

    # The group's own feed still shows ONE batch, downloadable via its representative
    # conversation + attachment handles (the same conversation-scoped route a DM download uses).
    act("adviser_a", ["messages:read"], "adviser", "a")
    feed = (await client.get(f"/messaging/groups/{group_uid}/messages")).json()
    assert len(feed["messages"]) == 1
    feed_batch = feed["messages"][0]
    assert feed_batch["attachments"][0]["content_type"] == "application/pdf"
    download = await client.get(
        f"/messaging/conversations/{feed_batch['conversation_uid']}"
        f"/attachments/{feed_batch['attachments'][0]['uid']}"
    )
    assert download.status_code == 200
    assert download.content == b"%PDF-1.7\nfake but sniffable"


@pytest.mark.asyncio
async def test_raising_a_request_against_a_group_fans_out_to_every_member(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """The request counterpart to the fan-out model: each member gets their OWN independent
    request (fulfilled on its own), the echo lands in their OWN thread — and is invisible from
    the adviser's Direct-message view of that same conversation, same rule a text/attachment
    group send follows — while the group's own feed collapses both echoes into one entry."""
    act("adviser_a", ["messages:read", "messages:write", "documents:send"], "adviser", "a")
    group_uid = (
        await client.post(
            "/messaging/groups",
            json={
                "name": "Household",
                "contact_ids": [int(seed["contact_a"]), int(seed["contact_a2"])],
            },
        )
    ).json()["uid"]

    res = await client.post(
        f"/messaging/groups/{group_uid}/requests",
        json={"items": [{"action": "upload", "title": "Bank statement"}]},
    )
    assert res.status_code == 200
    req_uids = res.json()["request_uids"]
    assert len(req_uids) == 2
    assert len(set(req_uids)) == 2  # two INDEPENDENT requests, not one shared row

    act("client_a", [], "client", "a")
    thread_a = (await client.get("/messaging/thread")).json()
    echo_a = thread_a["messages"][-1]
    assert echo_a["body"] == "Requested: Bank statement"
    assert echo_a["request_item"]["title"] == "Bank statement"

    act("client_a2", [], "client", "a")
    thread_a2 = (await client.get("/messaging/thread")).json()
    assert thread_a2["messages"][-1]["body"] == "Requested: Bank statement"

    # The adviser's own Direct-message view of contact_a's conversation shows nothing — the
    # request echo is group-tagged, same exclusion a text/attachment send gets.
    act("adviser_a", ["messages:read"], "adviser", "a")
    conversation_uid = (
        await client.get("/messaging/conversations", params={"q": "Contact-clienta"})
    ).json()[0]["uid"]
    direct_thread = await client.get(f"/messaging/conversations/{conversation_uid}/messages")
    assert direct_thread.json() == []
    listing = (await client.get("/messaging/conversations")).json()
    row = next(r for r in listing if r["contact_id"] == int(seed["contact_a"]))
    assert row["last_message"] is None

    # The group's own feed shows ONE batch, tagged with both members.
    feed = (await client.get(f"/messaging/groups/{group_uid}/messages")).json()
    assert len(feed["messages"]) == 1
    batch = feed["messages"][0]
    assert batch["body"] == "Requested: Bank statement"
    assert {r["contact_id"] for r in batch["recipients"]} == {
        int(seed["contact_a"]),
        int(seed["contact_a2"]),
    }


@pytest.mark.asyncio
async def test_a_multi_item_group_request_batches_each_item_separately(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """A request with two items sent to two members must produce TWO batches (one per item,
    each with both recipients) — not one batch with four recipients, and not four separate rows.
    Batching at the whole-request level would wrongly merge the two DIFFERENT items' echoes."""
    act("adviser_a", ["messages:read", "messages:write", "documents:send"], "adviser", "a")
    group_uid = (
        await client.post(
            "/messaging/groups",
            json={
                "name": "Household",
                "contact_ids": [int(seed["contact_a"]), int(seed["contact_a2"])],
            },
        )
    ).json()["uid"]

    res = await client.post(
        f"/messaging/groups/{group_uid}/requests",
        json={
            "items": [
                {"action": "upload", "title": "Bank statement"},
                {"action": "upload", "title": "Proof of address"},
            ]
        },
    )
    assert res.status_code == 200

    feed = (await client.get(f"/messaging/groups/{group_uid}/messages")).json()
    rows = feed["messages"]
    assert {r["body"] for r in rows} == {"Requested: Bank statement", "Requested: Proof of address"}
    for row in rows:
        assert {r["contact_id"] for r in row["recipients"]} == {
            int(seed["contact_a"]),
            int(seed["contact_a2"]),
        }


@pytest.mark.asyncio
async def test_group_request_needs_documents_send_not_just_messages_write(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    act("adviser_a", ["messages:read", "messages:write"], "adviser", "a")
    group_uid = (
        await client.post(
            "/messaging/groups",
            json={"name": "Household", "contact_ids": [int(seed["contact_a"])]},
        )
    ).json()["uid"]
    res = await client.post(
        f"/messaging/groups/{group_uid}/requests",
        json={"items": [{"action": "upload", "title": "Bank statement"}]},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_group_feed_shows_the_rich_request_card_not_a_plain_bubble(
    client: AsyncClient, seed: dict[str, str], act: ActAs
) -> None:
    """The bug this pins: a group-raised request's echo used to render as plain text in the
    group feed (no `documents.request_items` join at all) — it must carry the same
    `request_item` the DM thread's card reads live off, status included."""
    act("adviser_a", ["messages:read", "messages:write", "documents:send"], "adviser", "a")
    group_uid = (
        await client.post(
            "/messaging/groups",
            json={
                "name": "Household",
                "contact_ids": [int(seed["contact_a"]), int(seed["contact_a2"])],
            },
        )
    ).json()["uid"]
    await client.post(
        f"/messaging/groups/{group_uid}/requests",
        json={"items": [{"action": "upload", "title": "Bank statement"}]},
    )
    feed = (await client.get(f"/messaging/groups/{group_uid}/messages")).json()
    batch = feed["messages"][0]
    assert batch["request_item"]["title"] == "Bank statement"
    assert batch["request_item"]["status"] == "pending"
    assert batch["request_item"]["action"] == "upload"


@pytest.mark.asyncio
async def test_fulfilling_a_group_request_surfaces_in_the_group_feed_not_the_dm_view(
    client: AsyncClient, seed: dict[str, str], act: ActAs, attachment_store: dict[str, bytes]
) -> None:
    """The other half: a client's response to a group-raised request must land in the GROUP's
    feed (tagged with that one client), not just the member's own thread — and, symmetrically
    with the request that asked for it, stays invisible from the adviser's Direct-message view
    of that same conversation."""
    act("adviser_a", ["messages:read", "messages:write", "documents:send"], "adviser", "a")
    group_uid = (
        await client.post(
            "/messaging/groups",
            json={
                "name": "Household",
                "contact_ids": [int(seed["contact_a"]), int(seed["contact_a2"])],
            },
        )
    ).json()["uid"]
    await client.post(
        f"/messaging/groups/{group_uid}/requests",
        json={"items": [{"action": "upload", "title": "Bank statement"}]},
    )

    act("client_a", [], "client", "a")
    thread = (await client.get("/messaging/thread")).json()
    item_uid = thread["messages"][-1]["request_item"]["uid"]
    fulfil = await client.post(
        "/messaging/thread/attachments",
        files={"file": ("statement.pdf", b"%PDF-1.7\nfake but sniffable", "application/pdf")},
        data={"text": "", "request_item_uid": item_uid},
    )
    assert fulfil.status_code == 200

    # Invisible from the adviser's Direct-message view — both the ask and the response are
    # group-tagged, same rule a text/attachment group send follows.
    act("adviser_a", ["messages:read"], "adviser", "a")
    conversation_uid = (
        await client.get("/messaging/conversations", params={"q": "Contact-clienta"})
    ).json()[0]["uid"]
    direct = (await client.get(f"/messaging/conversations/{conversation_uid}/messages")).json()
    assert direct == []

    # The group's feed shows BOTH: the request (fanned to two members, so its OWN status stays
    # ambiguous — only one of the two has fulfilled — which is why that read is per-member, not
    # asserted here) and the fulfilment itself, tagged with only the one client who responded.
    feed = (await client.get(f"/messaging/groups/{group_uid}/messages")).json()
    rows = feed["messages"]
    assert len(rows) == 2
    fulfil_batch = next(r for r in rows if r["sender_kind"] == "client")
    assert fulfil_batch["attachments"][0]["content_type"] == "application/pdf"
    assert fulfil_batch["request_item"]["status"] == "fulfilled"
    assert [r["contact_id"] for r in fulfil_batch["recipients"]] == [int(seed["contact_a"])]


@pytest.mark.asyncio
async def test_raising_a_group_request_mirrors_each_members_item_as_its_own_task(
    client: AsyncClient, seed: dict[str, str], act: ActAs, super_engine: Engine
) -> None:
    """docs/tasks.md: a request-raised task links back via `source_kind`/`source_ref`, never
    `subject_type='document'` (that means an actual documents.documents row). The group fan-out
    must mirror this per MEMBER — two independent tasks, not one shared task, mirroring the two
    independent `documents.requests` rows `create_group_request` already creates."""
    act("adviser_a", ["messages:read", "messages:write", "documents:send"], "adviser", "a")
    group_uid = (
        await client.post(
            "/messaging/groups",
            json={
                "name": "Household",
                "contact_ids": [int(seed["contact_a"]), int(seed["contact_a2"])],
            },
        )
    ).json()["uid"]
    res = await client.post(
        f"/messaging/groups/{group_uid}/requests",
        json={"items": [{"action": "upload", "title": "Bank statement for the group"}]},
    )
    assert res.status_code == 200

    with super_engine.connect() as c:
        tasks = (
            c.execute(
                text(
                    "SELECT assignee_contact_id, category, source_kind, source_ref, status "
                    "FROM tasks.tasks "
                    "WHERE tenant_id = :t AND title = 'Bank statement for the group' "
                    "ORDER BY id"
                ),
                {"t": int(seed["firm_a"])},
            )
            .mappings()
            .all()
        )
    assert {t["assignee_contact_id"] for t in tasks} == {
        int(seed["contact_a"]),
        int(seed["contact_a2"]),
    }
    assert all(t["category"] == "upload" for t in tasks)
    assert all(t["source_kind"] == "document" for t in tasks)
    assert all(t["status"] == "open" for t in tasks)
    # Distinct source_ref per member's own request item — never collapsed into one shared task.
    assert len({t["source_ref"] for t in tasks}) == 2
