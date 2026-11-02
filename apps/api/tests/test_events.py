"""WorkOS Events consumer — applying events to the mirror (create / update / delete).

Exercises app.events._apply directly with fabricated event objects (the WorkOS SDK's event
variants expose .event + .data), against the real testcontainer DB via service_role.

One-firm-org (0107): a WorkOS organisation membership is the PERSON's authentication, not a
portal. Which portals a person may use lives exclusively in ``auth.organisation_memberships``,
so a membership event maps state onto the person's EXISTING rows and never invents one, and it
never reads roles — roles are push-only. Deletion means "out of the firm": every portal row goes
inactive, none is hard-deleted (which portals existed is our record, not WorkOS's).
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import events


async def _tenant_with_role(session: AsyncSession) -> int:
    suffix = uuid.uuid4().hex[:10]
    tid = int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, 'Ev Co') RETURNING id"
                ),
                {"uid": f"ten_{suffix}", "alias": f"ev-{suffix}"},
            )
        ).scalar_one()
    )
    await session.execute(
        text(
            "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
            "VALUES (:t, 'adviser', 'Adviser', 'organisation', 'platform')"
        ),
        {"t": tid},
    )
    return tid


def _event(kind: str, data: object) -> SimpleNamespace:
    return SimpleNamespace(id=f"event_{uuid.uuid4().hex[:8]}", event=kind, data=data)


def _membership(wu: str, status: str) -> SimpleNamespace:
    """The shape the SDK's membership event data exposes. It still carries a role — which the
    consumer must IGNORE (roles are push-only; portal rows are ours)."""
    return SimpleNamespace(
        user_id=wu,
        organization_id="org_x",
        status=SimpleNamespace(value=status),
        role=SimpleNamespace(slug="org-director"),
    )


async def _mk_user(session: AsyncSession, tid: int, wu: str, *, verified: bool = True) -> None:
    user = SimpleNamespace(
        id=wu, email=f"{wu}@x.test", first_name="Ev", last_name="Ann", email_verified=verified
    )
    await events._apply(session, None, tid, _event("user.created", user))


async def _apply_membership(
    session: AsyncSession, tid: int, kind: str, wu: str, status: str, workos: object = None
) -> None:
    await events._apply(
        session, workos, tid, _event(f"organization_membership.{kind}", _membership(wu, status))
    )


async def _seed_portal_row(
    session: AsyncSession, tid: int, wu: str, portal: str, status: str, role_slug: str | None = None
) -> None:
    """A portal row as OUR provisioning writes it — the rows the events map state onto."""
    await session.execute(
        text(
            "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, status, "
            "  role_id) "
            "SELECT u.id, :t, :p, :s, r.id FROM auth.users u "
            "LEFT JOIN auth.roles r ON r.tenant_id = :t AND r.slug = :rs "
            "WHERE u.workos_user_id = :wu"
        ),
        {"t": tid, "p": portal, "s": status, "rs": role_slug, "wu": wu},
    )


async def _rows(session: AsyncSession, tid: int, wu: str) -> dict[str, tuple[str, str | None]]:
    """{portal: (status, role_slug)} for the person's mirror rows."""
    got = (
        await session.execute(
            text(
                "SELECT m.portal, m.status, r.slug FROM auth.organisation_memberships m "
                "JOIN auth.users u ON u.id = m.user_id "
                "LEFT JOIN auth.roles r ON r.id = m.role_id "
                "WHERE u.workos_user_id = :wu AND m.tenant_id = :t"
            ),
            {"wu": wu, "t": tid},
        )
    ).all()
    return {r.portal: (r.status, r.slug) for r in got}


@pytest.mark.asyncio
async def test_event_lifecycle_create_update_delete(service_session: AsyncSession) -> None:
    tid = await _tenant_with_role(service_session)
    wu = f"user_{uuid.uuid4().hex[:8]}"

    async def user_row() -> object:
        return (
            await service_session.execute(
                text("SELECT enabled, name FROM auth.users WHERE workos_user_id = :wu"),
                {"wu": wu},
            )
        ).one()

    # user.created (verified) -> enabled user, no membership rows invented
    await _mk_user(service_session, tid, wu, verified=True)
    row = await user_row()
    assert row.enabled is True
    assert row.name == "Ev Ann"  # composed from first/last
    assert await _rows(service_session, tid, wu) == {}

    # user.updated with a new name is reflected
    updated = SimpleNamespace(
        id=wu, email=f"{wu}@x.test", first_name="Eve", last_name="Adams", email_verified=True
    )
    await events._apply(service_session, None, tid, _event("user.updated", updated))
    assert (await user_row()).name == "Eve Adams"

    # organization_membership.created -> maps state onto EXISTING rows only; with none, it
    # invents nothing (portal access is granted by OUR provisioning, never by a WorkOS event).
    await _apply_membership(service_session, tid, "created", wu, "active")
    assert await _rows(service_session, tid, wu) == {}

    # With a pending row provisioned by us, active + verified promotes it — and the role stays
    # exactly what we set (the event's own role slug is ignored; roles are push-only).
    await _seed_portal_row(service_session, tid, wu, "platform", "pending", role_slug="adviser")
    await _apply_membership(service_session, tid, "updated", wu, "active")
    assert await _rows(service_session, tid, wu) == {"platform": ("active", "adviser")}

    # organization_membership.deleted -> the person is out of the firm: every portal row goes
    # INACTIVE; none is deleted (which portals existed is our record, not WorkOS's).
    await _apply_membership(service_session, tid, "deleted", wu, "active")
    assert await _rows(service_session, tid, wu) == {"platform": ("inactive", "adviser")}

    # user.deleted -> user disabled
    await events._apply(service_session, None, tid, _event("user.deleted", SimpleNamespace(id=wu)))
    assert (await user_row()).enabled is False


@pytest.mark.asyncio
async def test_membership_events_map_state_and_never_touch_roles_or_invent_rows(
    service_session: AsyncSession,
) -> None:
    """The person-level contract, edge on. The membership is the person's authentication, so an
    inactive event downs ALL the person's portal rows at once; a later active event revives only
    PENDING rows (re-activating an inactive one is our decision, made in provisioning, never a
    side effect of a WorkOS state ping); and the event's role slug never lands anywhere — the old
    consumer resolved it into role_id, which let a rename inside WorkOS silently strip or swap a
    member's permissions."""
    tid = await _tenant_with_role(service_session)
    wu = f"user_{uuid.uuid4().hex[:8]}"
    await _mk_user(service_session, tid, wu, verified=True)
    await _seed_portal_row(service_session, tid, wu, "platform", "active", role_slug="adviser")
    # A staff identity can legitimately hold Platform + Operations on Graphene's own tenant. The
    # old test used Client + Platform, which 0119 now correctly refuses at the database boundary.
    await _seed_portal_row(service_session, tid, wu, "operations", "pending")

    # inactive ⇒ every portal row inactive; role untouched; still exactly the two rows we made.
    await _apply_membership(service_session, tid, "updated", wu, "inactive")
    assert await _rows(service_session, tid, wu) == {
        "platform": ("inactive", "adviser"),
        "operations": ("inactive", None),
    }

    # active again ⇒ nothing changes: only PENDING rows are promoted, and there are none.
    await _apply_membership(service_session, tid, "updated", wu, "active")
    assert await _rows(service_session, tid, wu) == {
        "platform": ("inactive", "adviser"),
        "operations": ("inactive", None),
    }


@pytest.mark.asyncio
async def test_membership_active_does_not_promote_an_unverified_persons_pending_rows(
    service_session: AsyncSession,
) -> None:
    """pending→active requires the person to be verified (enabled): an invite is not access
    until the person has proven the mailbox, whatever WorkOS says the membership state is."""
    tid = await _tenant_with_role(service_session)
    wu = f"user_{uuid.uuid4().hex[:8]}"
    await _mk_user(service_session, tid, wu, verified=False)
    await _seed_portal_row(service_session, tid, wu, "platform", "pending")

    await _apply_membership(service_session, tid, "created", wu, "active")
    assert await _rows(service_session, tid, wu) == {"platform": ("pending", None)}


@pytest.mark.asyncio
async def test_membership_event_for_an_unmirrored_user_fetches_the_person_and_invents_no_rows(
    service_session: AsyncSession,
) -> None:
    """An out-of-order membership event (its user.created not yet consumed) must ensure the
    mirror user exists — fetched from WorkOS — and still invent no portal rows."""
    tid = await _tenant_with_role(service_session)
    wu = f"user_{uuid.uuid4().hex[:8]}"
    wuser = SimpleNamespace(
        id=wu, email=f"{wu}@x.test", first_name="Out", last_name="Order", email_verified=True
    )

    async def get_user(_uid: str) -> object:
        return wuser

    fake_workos = SimpleNamespace(user_management=SimpleNamespace(get_user=get_user))

    await _apply_membership(service_session, tid, "created", wu, "active", workos=fake_workos)
    row = (
        await service_session.execute(
            text("SELECT enabled, name FROM auth.users WHERE workos_user_id = :wu"), {"wu": wu}
        )
    ).one()
    assert row.enabled is True and row.name == "Out Order"
    assert await _rows(service_session, tid, wu) == {}


# ---- the mirror refuses what it cannot represent, and the stream survives it ----------------
#
# All three of these reproduce one real incident: a user deleted and re-created in WorkOS, in a dev
# deployment where two tenant environments had been pointed at the SAME WorkOS project. Each
# environment's drain then claimed every mirrored row for its own tenant, and the collision landed
# on `users_tenant_email_key` as a psycopg IntegrityError — which, because the cursor is only
# persisted once a page completes, wedged that environment permanently.


async def _user_row(session: AsyncSession, wu: str) -> object | None:
    return (
        await session.execute(
            text("SELECT tenant_id, email, enabled FROM auth.users WHERE workos_user_id = :wu"),
            {"wu": wu},
        )
    ).one_or_none()


@pytest.mark.asyncio
async def test_a_mirrored_user_is_never_re_homed_by_another_tenants_stream(
    service_session: AsyncSession,
) -> None:
    """The `workos_user_id` match is global, so tenant B's event stream can find tenant A's row.
    It must refuse rather than write `tenant_id = B` onto it."""
    a = await _tenant_with_role(service_session)
    b = await _tenant_with_role(service_session)
    wu = f"user_{uuid.uuid4().hex[:8]}"
    user = SimpleNamespace(
        id=wu, email="rehome@x.test", first_name="Re", last_name="Home", email_verified=True
    )
    await events._apply(service_session, None, a, _event("user.created", user))
    assert (await _user_row(service_session, wu)).tenant_id == a

    with pytest.raises(events.MirrorConflict) as caught:
        await events._apply(service_session, None, b, _event("user.updated", user))
    # The refusal names both sides, and never the email.
    assert str(b) in str(caught.value) and str(a) in str(caught.value)
    assert "rehome@x.test" not in str(caught.value)
    # …and the row is untouched.
    assert (await _user_row(service_session, wu)).tenant_id == a


@pytest.mark.asyncio
async def test_two_logins_cannot_take_one_email_in_a_tenant(service_session: AsyncSession) -> None:
    """A deleted-and-recreated WorkOS user is a SECOND user id with the same address. Moving the
    email onto the new login would need two rows to share (tenant_id, email); the mirror says no,
    by name, instead of letting the unique index raise."""
    tid = await _tenant_with_role(service_session)
    first = f"user_{uuid.uuid4().hex[:8]}"
    second = f"user_{uuid.uuid4().hex[:8]}"
    await events._apply(
        service_session,
        None,
        tid,
        _event(
            "user.created", SimpleNamespace(id=first, email="taken@x.test", email_verified=True)
        ),
    )
    await events._apply(
        service_session,
        None,
        tid,
        _event(
            "user.created", SimpleNamespace(id=second, email="other@x.test", email_verified=True)
        ),
    )

    moved = SimpleNamespace(id=second, email="taken@x.test", email_verified=True)
    with pytest.raises(events.MirrorConflict) as caught:
        await events._apply(service_session, None, tid, _event("user.updated", moved))
    assert first in str(caught.value)
    assert "taken@x.test" not in str(caught.value)
    # Both rows stand as they were.
    assert (await _user_row(service_session, first)).email == "taken@x.test"
    assert (await _user_row(service_session, second)).email == "other@x.test"


@pytest.mark.asyncio
async def test_identity_wins_over_email_when_the_two_match_different_rows(
    service_session: AsyncSession,
) -> None:
    """The lookup used to be one OR'd statement under `LIMIT 1` with no ORDER BY: with a row
    matching by workos id and a DIFFERENT row matching (tenant, email), which one it picked was up
    to the planner. `workos_user_id` is the identity, so it decides."""
    tid = await _tenant_with_role(service_session)
    wu = f"user_{uuid.uuid4().hex[:8]}"
    other = f"user_{uuid.uuid4().hex[:8]}"
    await events._apply(
        service_session,
        None,
        tid,
        _event("user.created", SimpleNamespace(id=wu, email="mine@x.test", email_verified=True)),
    )
    await events._apply(
        service_session,
        None,
        tid,
        _event(
            "user.created", SimpleNamespace(id=other, email="theirs@x.test", email_verified=True)
        ),
    )
    # An update for `wu` that (wrongly) carries the other row's address: the email branch would
    # match `other`, the identity branch matches `wu`. Identity wins, so this is the email clash
    # above rather than a silent write to somebody else's row.
    with pytest.raises(events.MirrorConflict):
        await events._apply(
            service_session,
            None,
            tid,
            _event(
                "user.updated", SimpleNamespace(id=wu, email="theirs@x.test", email_verified=True)
            ),
        )
    assert (await _user_row(service_session, other)).email == "theirs@x.test"


@pytest.mark.asyncio
async def test_one_unconvergeable_event_is_parked_and_the_stream_moves_on(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wedge, and the fix. A page whose FIRST event cannot be applied must still leave the
    cursor past it and the later events applied — before, the IntegrityError aborted the whole
    page, the cursor was never saved, and every tick re-read the same page and failed on the same
    event."""
    home = await _tenant_with_role(service_session)
    other = await _tenant_with_role(service_session)
    env_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.tenant_environments (tenant_id, stage, events_cursor) "
                    "VALUES (:t, 'dev', 'event_start') RETURNING id"
                ),
                {"t": other},
            )
        ).scalar_one()
    )
    # A login mirrored under `home`: `other`'s stream cannot claim it (the re-home refusal).
    squatter = f"user_{uuid.uuid4().hex[:8]}"
    await events._apply(
        service_session,
        None,
        home,
        _event(
            "user.created", SimpleNamespace(id=squatter, email="held@x.test", email_verified=True)
        ),
    )
    fresh = f"user_{uuid.uuid4().hex[:8]}"
    bad = _event(
        "user.updated", SimpleNamespace(id=squatter, email="held@x.test", email_verified=True)
    )
    good = _event(
        "user.created", SimpleNamespace(id=fresh, email="fresh@x.test", email_verified=True)
    )

    async def _client_for_env(_session: object, _env_id: int) -> object:
        async def list_events(**_kw: object) -> object:
            return SimpleNamespace(data=[bad, good])

        return SimpleNamespace(events=SimpleNamespace(list_events=list_events))

    monkeypatch.setattr(events.environments, "client_for_env", _client_for_env)

    applied = await events.consume(service_session, env_id)

    assert applied == 1  # the good one; the bad one is parked, not retried
    assert (await _user_row(service_session, fresh)).tenant_id == other
    assert (await _user_row(service_session, squatter)).tenant_id == home  # untouched
    cursor = (
        await service_session.execute(
            text("SELECT events_cursor FROM auth.tenant_environments WHERE id = :e"), {"e": env_id}
        )
    ).scalar_one()
    assert cursor == good.id  # advanced PAST the parked event, so it never blocks again


@pytest.mark.asyncio
async def test_waiting_for_a_workos_event_page_does_not_block_the_event_loop(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slow identity-provider I/O must not stop unrelated requests being scheduled."""
    tenant_id = await _tenant_with_role(service_session)
    env_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.tenant_environments (tenant_id, stage, events_cursor) "
                    "VALUES (:t, 'dev', 'event_start') RETURNING id"
                ),
                {"t": tenant_id},
            )
        ).scalar_one()
    )
    request_started = asyncio.Event()
    release_request = asyncio.Event()

    async def list_events(**_kw: object) -> object:
        request_started.set()
        await release_request.wait()
        return SimpleNamespace(data=[])

    async def client_for_env(_session: object, _env_id: int) -> object:
        return SimpleNamespace(events=SimpleNamespace(list_events=list_events))

    monkeypatch.setattr(events.environments, "client_for_env", client_for_env)
    consume_task = asyncio.create_task(events.consume(service_session, env_id))
    await asyncio.wait_for(request_started.wait(), timeout=0.2)

    scheduled = asyncio.Event()

    async def unrelated_request() -> None:
        await asyncio.sleep(0)
        scheduled.set()

    await asyncio.wait_for(unrelated_request(), timeout=0.2)
    assert scheduled.is_set()
    assert not consume_task.done()

    release_request.set()
    assert await asyncio.wait_for(consume_task, timeout=0.2) == 0


@pytest.mark.asyncio
async def test_first_run_walks_to_the_newest_event_even_when_order_desc_is_ignored(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The db-reset login bug. WorkOS `list_events(order='desc')` with `events=` still
    returns oldest-first (the vendor ignores `order` on a typed stream). Seeding
    `limit=1, order=desc` therefore stored the OLDEST id, and the next tick replayed
    every membership that ever existed. A firm-org `deleted` then a re-add left portal
    rows `inactive` forever, because an `active` event only promotes `pending`.

    First-run must walk oldest-first to the TAIL and persist that id, without applying."""
    tid = await _tenant_with_role(service_session)
    env_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.tenant_environments (tenant_id, stage) "
                    "VALUES (:t, 'dev') RETURNING id"
                ),
                {"t": tid},
            )
        ).scalar_one()
    )
    stream = [SimpleNamespace(id=f"event_{i}") for i in range(5)]
    index = {e.id: i for i, e in enumerate(stream)}
    seen_after: list[object] = []

    async def list_events(**kw: object) -> SimpleNamespace:
        # Reproduce the vendor: `order` is ignored; the page is always oldest-first.
        seen_after.append(kw.get("after"))
        after = kw.get("after")
        limit = int(kw["limit"]) if kw.get("limit") is not None else 100
        start = 0 if after is None else index[str(after)] + 1
        return SimpleNamespace(data=stream[start : start + limit])

    async def _client_for_env(_session: object, _env_id: int) -> object:
        return SimpleNamespace(events=SimpleNamespace(list_events=list_events))

    async def _sync_tenant(_session: object, _tid: int, **_kw: object) -> dict[str, int]:
        return {"members": 0}

    monkeypatch.setattr(events, "_PAGE", 2)
    monkeypatch.setattr(events.environments, "client_for_env", _client_for_env)
    monkeypatch.setattr(events.provisioning, "sync_tenant", _sync_tenant)

    heartbeats: list[None] = []
    applied = await events.consume(
        service_session, env_id, progress=lambda: heartbeats.append(None)
    )
    assert applied == 0
    assert seen_after == [None, "event_1", "event_3"]
    # Once after the reconcile and once after every WorkOS event page. A large first-run seed
    # therefore remains healthy while it walks history instead of looking wedged for the duration.
    assert len(heartbeats) == 4
    cursor = (
        await service_session.execute(
            text("SELECT events_cursor FROM auth.tenant_environments WHERE id = :e"),
            {"e": env_id},
        )
    ).scalar_one()
    assert cursor == "event_4"


@pytest.mark.asyncio
async def test_membership_event_for_a_foreign_org_does_not_inactivate_the_person(
    service_session: AsyncSession,
) -> None:
    """The project's event stream includes every organisation, not just the firm org. A
    deleted leftover Client/Platform org membership is not "out of the firm"."""
    tid = await _tenant_with_role(service_session)
    wu = f"user_{uuid.uuid4().hex[:8]}"
    await _mk_user(service_session, tid, wu, verified=True)
    await _seed_portal_row(service_session, tid, wu, "platform", "active", role_slug="adviser")

    leftover = _membership(wu, "inactive")
    leftover.organization_id = "org_old_portal"
    await events._apply(
        service_session,
        None,
        tid,
        _event("organization_membership.deleted", leftover),
        firm_org_id="org_firm",
    )
    assert await _rows(service_session, tid, wu) == {"platform": ("active", "adviser")}

    leftover.organization_id = "org_firm"
    await events._apply(
        service_session,
        None,
        tid,
        _event("organization_membership.deleted", leftover),
        firm_org_id="org_firm",
    )
    assert await _rows(service_session, tid, wu) == {"platform": ("inactive", "adviser")}
