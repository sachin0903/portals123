"""A member of staff acting in the client portal as one of their clients (``app/view_as.py``).

The feature's whole safety argument is that it can only ever NARROW. These tests hold that
argument up from both ends: the claim minter refuses a client the login cannot already reach,
and for one it can, the resulting session sees that client and nothing else -- not the rest of
the adviser's book, not another firm's client, and not the firm.

The rest is the session's life: it is opened with a reason, carried across by a ticket that
works once, and it stops working the moment it is ended or expired.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import view_as
from tests.conftest import principal_conn

_APP_CLIENT = "app_va_client"
_APP_PLATFORM = "app_va_platform"


@asynccontextmanager
async def _service(app_url: str) -> AsyncIterator[AsyncSession]:
    """A service_role session -- what mint / redeem / resolve run under."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        yield session
    await engine.dispose()


@pytest.fixture
def firm(super_engine: Engine) -> dict[str, object]:
    """One firm: a book-scoped adviser with one client, a second client outside their book, a
    firm-wide reader (contacts:read_all) with an empty book -- and a second firm with a client of
    its own, so cross-tenant reach can be asserted rather than assumed."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:

        def mk_tenant(tag: str) -> int:
            tenant = int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"vaten_{tag}_{s}", "a": f"vafirm-{tag}-{s}", "n": f"VA {tag} {s}"},
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                    "  client_portal_client_id, platform_client_id, client_portal_host) "
                    "VALUES (:t, 'dev', :env, :capp, :papp, :host)"
                ),
                {
                    "t": tenant,
                    "env": f"env_va_{tag}_{s}",
                    "capp": _APP_CLIENT,
                    "papp": _APP_PLATFORM,
                    "host": f"{tag}-{s}.portal.test",
                },
            )
            return tenant

        tenant = mk_tenant("a")
        other_tenant = mk_tenant("b")

        def mk_user(tenant_id: int, tag: str, first: str, last: str) -> tuple[str, str]:
            wos = f"vawos_{tag}_{s}"
            uid = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, "
                        "  first_name, last_name, enabled) "
                        "VALUES (:uid, :t, :email, :wos, :first, :last, true) RETURNING id"
                    ),
                    {
                        "uid": f"vausr_{tag}_{s}",
                        "t": tenant_id,
                        "email": f"{tag}_{s}@vafirm.test",
                        "wos": wos,
                        "first": first,
                        "last": last,
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, 'platform')"
                ),
                {"u": uid, "t": tenant_id},
            )
            return uid, wos

        adviser_id, adviser_wos = mk_user(tenant, "adviser", "Rowan", "Adviser")
        reader_id, reader_wos = mk_user(tenant, "reader", "Avery", "Reader")

        # The firm-wide reader: a platform role carrying contacts:read_all, on their membership.
        role_id = int(
            c.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                    "VALUES (:t, :slug, 'Reader', 'platform') RETURNING id"
                ),
                {"t": tenant, "slug": f"va-reader-{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.role_permissions (role_id, permission_id) "
                "SELECT :r, id FROM auth.permissions WHERE slug = 'contacts:read_all'"
            ),
            {"r": role_id},
        )
        c.execute(
            text(
                "UPDATE auth.organisation_memberships SET role_id = :r "
                "WHERE user_id = :u AND tenant_id = :t AND portal = 'platform'"
            ),
            {"r": role_id, "u": reader_id, "t": tenant},
        )

        def mk_contact(tenant_id: int, tag: str, name: str) -> tuple[int, str]:
            uid = f"vacon_{tag}_{s}"
            cid = int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, name, surname, "
                        "  service_type) "
                        "VALUES (:t, :uid, 'individual', :n, :sn, 'advised') RETURNING id"
                    ),
                    {"t": tenant_id, "uid": uid, "n": name, "sn": name.split()[-1]},
                ).scalar_one()
            )
            return cid, uid

        subject, subject_uid = mk_contact(tenant, "subject", "Beatrice Subject")
        other, _ = mk_contact(tenant, "other", "Cassian Other")
        foreign, _ = mk_contact(other_tenant, "foreign", "Delia Foreign")

        def mk_account(tenant_id: int, tag: str, contact: int) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO platform.accounts (tenant_id, contact_id, uid, status, value) "
                        "VALUES (:t, :con, :uid, 'active', '1000.00') RETURNING id"
                    ),
                    {"t": tenant_id, "con": contact, "uid": f"vaacc_{tag}_{s}"},
                ).scalar_one()
            )

        subject_account = mk_account(tenant, "subject", subject)
        mk_account(tenant, "other", other)

        # The adviser's book is the subject only. Nobody holds the other two.
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :con, :a, 'adviser')"
            ),
            {"t": tenant, "con": subject, "a": adviser_id},
        )
        c.commit()
    return {
        "tenant": tenant,
        "adviser_wos": adviser_wos,
        "reader_wos": reader_wos,
        "subject": subject,
        "subject_uid": subject_uid,
        "subject_account": subject_account,
        "other": other,
        "foreign": foreign,
    }


def _staff_claims(app_engine: Engine, sub: str) -> dict:
    with principal_conn(app_engine, {}) as conn:
        resolved = conn.execute(
            text("SELECT auth.resolve_claims(:w, NULL, :app)"), {"w": sub, "app": _APP_PLATFORM}
        ).scalar_one()
    return json.loads(resolved) if isinstance(resolved, str) else resolved


def _view_as_claims(app_engine: Engine, sub: str, contact_id: int) -> dict:
    with principal_conn(app_engine, {}) as conn:
        resolved = conn.execute(
            text("SELECT auth.resolve_view_as_claims(:w, NULL, :v)"),
            {"w": sub, "v": contact_id},
        ).scalar_one()
    return json.loads(resolved) if isinstance(resolved, str) else resolved


# ---- the narrowing ---------------------------------------------------------------------------


def test_a_view_as_session_sees_only_the_one_client(
    app_engine: Engine, firm: dict[str, object]
) -> None:
    """The point of the whole design. The staff resolution reaches the adviser's book; the view-as
    resolution of one client in it comes back as THAT client, under the client portal, with no
    staff roles and no lineage -- the shape a client's own login has."""
    sub = str(firm["adviser_wos"])
    ordinary = _staff_claims(app_engine, sub)
    assert ordinary["portal"] == "platform"
    assert ordinary["contact_ids"] == [firm["subject"]]
    assert ordinary["tenant_ids"] != []

    viewing = _view_as_claims(app_engine, sub, int(firm["subject"]))
    assert viewing["contact_ids"] == [firm["subject"]]
    assert viewing["account_ids"] == [firm["subject_account"]]
    assert viewing["portal"] == "client"
    assert viewing["tenant_id"] == firm["tenant"]
    assert viewing["tenant_ids"] == []
    assert viewing["role_ids"] == []
    assert viewing["view_as"] == firm["subject"]
    # Same person, in the audit sense.
    assert viewing["user_uuid"] == ordinary["user_uuid"]


def test_a_client_outside_the_book_is_refused_rather_than_narrowed_to(
    app_engine: Engine, firm: dict[str, object]
) -> None:
    """The minter is the gate, not the request layer: a client this login does not already reach
    as staff raises here, so no amount of asking at the HTTP surface can produce the session."""
    with pytest.raises(DBAPIError):
        _view_as_claims(app_engine, str(firm["adviser_wos"]), int(firm["other"]))


def test_a_client_of_another_firm_is_refused_even_to_a_firm_wide_reader(
    app_engine: Engine, firm: dict[str, object]
) -> None:
    """contacts:read_all is firm-wide, not platform-wide."""
    with pytest.raises(DBAPIError):
        _view_as_claims(app_engine, str(firm["reader_wos"]), int(firm["foreign"]))


def test_a_firm_wide_reader_may_view_a_client_outside_their_book(
    app_engine: Engine, firm: dict[str, object]
) -> None:
    """Administrators see every client record through contacts:read_all; view-as must follow."""
    sub = str(firm["reader_wos"])
    assert _staff_claims(app_engine, sub)["contact_ids"] == []

    viewing = _view_as_claims(app_engine, sub, int(firm["other"]))
    assert viewing["contact_ids"] == [firm["other"]]
    assert viewing["portal"] == "client"
    assert viewing["role_ids"] == []


def test_the_narrowed_claims_hide_the_rest_of_the_book_under_rls(
    app_engine: Engine, firm: dict[str, object]
) -> None:
    """The two policies 0232 re-cut. `crm.client_advisers` used to OR on `adviser_id = auth.uid()`
    with no guard, and the view-as principal's uuid IS the adviser's -- so a session captioned
    with one client's name would have listed every client that adviser holds."""
    sub = str(firm["adviser_wos"])
    viewing = _view_as_claims(app_engine, sub, int(firm["subject"]))
    claims = {**viewing, "sub": sub, "role": "client", "permissions": []}
    with principal_conn(app_engine, claims) as conn:
        assert conn.execute(text("SELECT auth.view_as()")).scalar_one() == firm["subject"]
        visible = conn.execute(
            text("SELECT contact_id FROM crm.client_advisers WHERE tenant_id = :t"),
            {"t": firm["tenant"]},
        ).scalars()
        assert set(visible) == {firm["subject"]}
        contacts = conn.execute(
            text("SELECT id FROM crm.contacts WHERE tenant_id = :t"), {"t": firm["tenant"]}
        ).scalars()
        assert set(contacts) == {firm["subject"]}

    # And the adviser's OWN platform session still sees its whole book through that branch.
    staff = _staff_claims(app_engine, sub)
    with principal_conn(
        app_engine, {**staff, "sub": sub, "role": "adviser", "permissions": ["contacts:read"]}
    ) as conn:
        assert conn.execute(text("SELECT auth.view_as()")).scalar_one() is None
        visible = conn.execute(
            text("SELECT contact_id FROM crm.client_advisers WHERE tenant_id = :t"),
            {"t": firm["tenant"]},
        ).scalars()
        assert set(visible) == {firm["subject"]}


def test_an_ordinary_session_carries_no_view_as_claim(app_engine: Engine) -> None:
    """Absent rather than false: `auth.view_as()` answers NULL for every normal session, so a
    surface asking "is this a view-as?" cannot be told yes by a default."""
    with principal_conn(app_engine, {"sub": "nobody"}) as conn:
        assert conn.execute(text("SELECT auth.view_as()")).scalar_one() is None


# ---- the session's life ----------------------------------------------------------------------


async def _mint(
    session: AsyncSession, firm: dict[str, object], reason: str
) -> view_as.MintedSession:
    return await view_as.mint(
        session,
        tenant_id=int(firm["tenant"]),
        adviser_sub=str(firm["adviser_wos"]),
        contact_id=int(firm["subject"]),
        reason=reason,
    )


@pytest.mark.asyncio
async def test_mint_hands_over_a_ticket_that_redeems_once(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """The ticket travels in a URL, so it has to be worth nothing the moment it has been used --
    a link forwarded on after the tab opened must not open a second session."""
    async with _service(db["app"]) as session:
        minted = await _mint(session, firm, "Walking through documents")
        first = await view_as.redeem_handoff(session, minted.handoff_ticket)
        second = await view_as.redeem_handoff(session, minted.handoff_ticket)

    assert first is not None and first.startswith(view_as.TOKEN_PREFIX)
    assert second is None


@pytest.mark.asyncio
async def test_the_session_resolves_to_the_member_of_staff_not_the_client(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """Whose keyboard it was. The subject on the session is the adviser's, which is what carries
    into every audit row the session writes."""
    async with _service(db["app"]) as session:
        minted = await _mint(session, firm, "Telephone call")
        token = await view_as.redeem_handoff(session, minted.handoff_ticket)
        assert token is not None
        live = await view_as.resolve(session, token)

    assert live is not None
    assert live.adviser_sub == firm["adviser_wos"]
    assert live.adviser_name == "Rowan Adviser"
    assert live.tenant_id == firm["tenant"]
    assert live.contact_id == firm["subject"]
    assert live.contact_name == "Beatrice Subject"
    assert live.reason == "Telephone call"


@pytest.mark.asyncio
async def test_ending_a_session_stops_it_resolving(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """Revocability is the reason the session is a row rather than a signed token: leaving it
    has to take effect on the next request, not at the end of the hour."""
    async with _service(db["app"]) as session:
        minted = await _mint(session, firm, "Checking a statement")
        token = await view_as.redeem_handoff(session, minted.handoff_ticket)
        assert token is not None
        assert await view_as.resolve(session, token) is not None

        assert await view_as.end(session, minted.uid) is True
        assert await view_as.resolve(session, token) is None
        # Leaving twice is not an error, it just changes nothing the second time.
        assert await view_as.end(session, minted.uid) is False


@pytest.mark.asyncio
async def test_an_expired_session_stops_resolving(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """Expiry is the database's, evaluated on every resolve, so a clock-skewed portal cannot
    keep a lapsed session alive."""
    async with _service(db["app"]) as session:
        minted = await _mint(session, firm, "Reviewing their home page")
        token = await view_as.redeem_handoff(session, minted.handoff_ticket)
        assert token is not None
        await session.execute(
            text(
                "UPDATE auth.view_as_sessions SET expires_at = now() - interval '1 second' "
                "WHERE uid = :uid"
            ),
            {"uid": minted.uid},
        )
        assert await view_as.resolve(session, token) is None


@pytest.mark.asyncio
async def test_the_row_never_holds_a_usable_credential(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """Only hashes, and only after the ticket is spent. A database dump taken at any point in a
    session's life yields something that identifies it but cannot be presented as it."""

    async def read(session: AsyncSession, uid: str) -> dict:
        return dict(
            (
                await session.execute(
                    text(
                        "SELECT token_hash, handoff_hash FROM auth.view_as_sessions "
                        " WHERE uid = :uid"
                    ),
                    {"uid": uid},
                )
            )
            .mappings()
            .one()
        )

    async with _service(db["app"]) as session:
        minted = await _mint(session, firm, "Answering a query")
        # Before redemption the session has no token at all -- there is nothing yet to leak.
        before = await read(session, minted.uid)
        assert before["token_hash"] is None

        token = await view_as.redeem_handoff(session, minted.handoff_ticket)
        after = await read(session, minted.uid)

    assert token is not None
    assert after["handoff_hash"] is None
    assert after["token_hash"] != token
    assert token not in json.dumps(after)
    assert minted.handoff_ticket not in json.dumps(after)


@pytest.mark.asyncio
async def test_the_adviser_can_read_their_own_session_under_rls(
    db: dict[str, str], app_engine: Engine, firm: dict[str, object]
) -> None:
    """The banner reads `GET /view-as/session` under `tenant_session` -- the RLS path -- so the
    read policy has to let the view-as principal (whose uuid is the adviser's) find the row."""
    async with _service(db["app"]) as session:
        minted = await _mint(session, firm, "Reading the banner")
    sub = str(firm["adviser_wos"])
    viewing = _view_as_claims(app_engine, sub, int(firm["subject"]))
    with principal_conn(
        app_engine, {**viewing, "sub": sub, "role": "client", "permissions": []}
    ) as conn:
        found = conn.execute(
            text("SELECT uid FROM auth.view_as_sessions WHERE uid = :u"), {"u": minted.uid}
        ).scalar_one_or_none()
        assert found == minted.uid
        # ...and nobody else's: a different adviser in the firm with no audit:read sees nothing.
    reader = _staff_claims(app_engine, str(firm["reader_wos"]))
    with principal_conn(
        app_engine,
        {**reader, "sub": firm["reader_wos"], "role": "adviser", "permissions": ["contacts:read"]},
    ) as conn:
        found = conn.execute(
            text("SELECT uid FROM auth.view_as_sessions WHERE uid = :u"), {"u": minted.uid}
        ).scalar_one_or_none()
        assert found is None


@pytest.mark.asyncio
async def test_a_session_for_an_unknown_login_is_not_opened(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """No local user in this firm, no session. Minting against an unmirrored subject would
    produce a row naming nobody, which is worse than a refusal."""
    async with _service(db["app"]) as session:
        with pytest.raises(LookupError):
            await view_as.mint(
                session,
                tenant_id=int(firm["tenant"]),
                adviser_sub="wos_nobody_at_all",
                contact_id=int(firm["subject"]),
                reason="Curiosity",
            )


def test_a_ticket_presented_as_a_session_token_is_refused() -> None:
    """The two prefixes are deliberately different so a ticket cannot be half-recognised as the
    credential it was exchanged for."""
    assert view_as.HANDOFF_PREFIX.startswith(view_as.TOKEN_PREFIX) is False


# ---- what does NOT travel into the session -----------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/applications/x/steps/accept_terms/accept"),
        ("POST", "/messaging/conversations/abc/messages"),
        ("PUT", "/notifications/preferences"),
        ("PATCH", "/crm/contacts/abc123def456"),
        ("DELETE", "/documents/abc"),
        ("POST", "/terms/abc/accept"),
        ("POST", "/me/page-view"),
    ],
)
def test_a_view_as_session_may_not_change_anything(method: str, path: str) -> None:
    """The session is for seeing, not doing. Accepting a client's terms from inside their own
    portal is the case that matters most: the record it left would look exactly like the client
    accepting them."""
    assert view_as.permits(method, path) is False


@pytest.mark.parametrize(
    "path",
    ["/crm/home", "/documents", "/notifications", "/view-as/session", "/applications/me", "/me"],
)
def test_a_view_as_session_may_read(path: str) -> None:
    """Reading is the entire point, so every GET the client portal makes goes through."""
    assert view_as.permits("GET", path) is True
    assert view_as.permits("HEAD", path) is True


def test_leaving_is_the_one_write_allowed() -> None:
    """Refusing this would be the one refusal that traps somebody inside the restriction."""
    assert view_as.permits("POST", "/view-as/end") is True
    assert view_as.permits("POST", "/view-as/end/") is True
    # Opening one is a staff action on a staff session, so it is not exempted here.
    assert view_as.permits("POST", "/view-as/open/abc123def456") is False


def test_the_guard_is_on_the_method_not_a_list_of_routes() -> None:
    """A mutating route that ships next month is covered the day it lands. This asserts the
    shape of the rule rather than any endpoint, because the endpoints will change."""
    assert view_as.permits("POST", "/some/surface/nobody/has/written/yet") is False
    assert view_as.permits("GET", "/some/surface/nobody/has/written/yet") is True
