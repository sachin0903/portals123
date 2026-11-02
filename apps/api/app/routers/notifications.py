"""Notifications — the signed-in user's own feed + preferences.

Every endpoint is on ``tenant_session`` and carries NO permission gate: RLS scopes
``notifications.notifications`` to the caller's own rows (a staff user by ``auth.uid()``, a client
by ``auth.contact_scope()``), exactly like ``GET /tasks/me``. The caller can read their feed, count
unread, and mark rows read — nothing else; inserts are the worker's alone (service_role).

Preferences come in two shapes. ``/preferences`` is USER-keyed (recipient_kind='user') — the staff
surface, returning the categories staff receive. ``/client-preferences`` is CONTACT-keyed and
scoped by ``auth.contact_scope()`` — the client portal's own door to the notifications addressed to
them. Both merge stored overrides with the shipped defaults; a mandatory rule forces its default in
the resolver regardless, so a toggle for a mandatory category is a no-op by design.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import listing, live_push
from app.audit_actions import writes_no_row
from app.auth import Principal, get_current_principal
from app.config import settings
from app.db import tenant_session
from app.listing import DEFAULT_LIMIT
from app.notifications import catalogue, realtime

router = APIRouter(prefix="/notifications", tags=["notifications"])

_MAX_PAGE = 50


class NotificationOut(BaseModel):
    uid: str
    event_type: str
    category: str
    title: str
    body: str | None
    link: str | None
    priority: str
    subject_type: str | None
    subject_id: str | None
    read_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    has_more: bool
    #: Per-filter option values, computed over the match set with each filter's own clause lifted.
    #: Default-empty so the other callers of this model (the bell's feed) need no change.
    facets: dict[str, list[str]] = {}
    #: Same number ``GET /notifications/unread-count`` returns (unread, not dismissed; the
    #: active-contact slice when ``contact_id`` is given). Independent of the feed's own
    #: filters so the shell can paint the bell from one round trip.
    unread_count: int = 0


class UnreadCount(BaseModel):
    count: int


class ChannelPref(BaseModel):
    category: str
    label: str
    description: str
    in_app: bool
    email: bool
    email_available: bool = False  # v1: the email channel is inert; the UI shows it disabled
    # A preference is per NOTIFICATION (the catalogue trigger), grouped under `category`, on both
    # the client and adviser surfaces. (Kept optional for any legacy per-category caller.)
    trigger_key: str | None = None
    # This notification is forced on for the recipient (a mandatory rule, or a mandatory firm-config
    # default) — the resolver ignores their toggle, so the surface shows it locked rather than
    # letting them "turn off" something they'll still receive.
    mandatory: bool = False


async def _mandatory_flags(
    session: AsyncSession, config_audience: str
) -> tuple[dict[tuple[str, str], bool], dict[str, bool]]:
    """Where a notification is forced on, read under the caller's own RLS: a stored rule's
    ``mandatory`` per ``(trigger, audience)``, and the firm-config ``mandatory`` per category for
    ``config_audience`` ('adviser' | 'client'). A client-portal caller reads only its own
    client-audience rules and no firm config (RLS), so a client's mandatory reflects rule-level
    flags — the common case, and all a client could act on anyway."""
    rules = {
        (r["trigger_key"], r["audience"]): bool(r["mandatory"])
        for r in (
            await session.execute(
                text("SELECT trigger_key, audience, mandatory FROM notifications.rules")
            )
        ).mappings()
    }
    cfg = {
        r["category"]: bool(r["mandatory"])
        for r in (
            await session.execute(
                text(
                    "SELECT category, mandatory FROM notifications.tenant_config "
                    "WHERE audience = :a"
                ),
                {"a": config_audience},
            )
        ).mappings()
    }
    return rules, cfg


class PreferencesOut(BaseModel):
    preferences: list[ChannelPref]


class PreferenceUpdate(BaseModel):
    category: str
    in_app: bool
    email: bool = False
    trigger_key: str | None = None


class PreferencesUpdate(BaseModel):
    preferences: list[PreferenceUpdate]


_FEED_COLS = (
    "uid, event_type, category, title, body, link, priority, subject_type, subject_id, "
    "read_at, created_at"
)


async def _unread_total(session: AsyncSession, contact_id: int | None) -> int:
    """The bell-badge count — unread and not dismissed, RLS-scoped, optionally one contact."""
    n = (
        await session.execute(
            text(
                "SELECT count(*) FROM notifications.notifications "
                "WHERE read_at IS NULL AND dismissed_at IS NULL "
                "AND (CAST(:cid AS int) IS NULL OR recipient_contact_id = CAST(:cid AS int))"
            ),
            {"cid": contact_id},
        )
    ).scalar_one()
    return int(n)


def _bound(value: str | None, field: str) -> date | None:
    """One end of the feed's date range, as a DATE, or 422.

    Unparseable is a CLIENT error and says so, rather than reaching the database as a string
    for it to choke on -- see the note at the call site.
    """
    cleaned = listing.clean(value)
    if cleaned is None:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} must be an ISO date (e.g. 2026-07-28)",
        ) from exc


@router.get("", response_model=NotificationPage, name="list-notifications")
async def list_notifications(
    before: int | None = Query(None, description="Keyset cursor: return rows with id < before"),
    # Annotated, NOT `= Query(...)`: the latter makes the DEFAULT the `Query(...)` sentinel object,
    # so any direct call to this coroutine (the router tests do exactly that) gets a Query where it
    # expects a value. A `Query` is truthy, so `include_dismissed` defaulted to "on" and the feed
    # quietly served dismissed rows to the bell. Same trap aml.py records for its `page`.
    before_uid: Annotated[
        str | None, Query(description="Keyset cursor by public uid: rows older than this one")
    ] = None,
    unread: bool = Query(False, description="Only unread notifications"),
    include_dismissed: Annotated[
        bool, Query(description="Include dismissed rows — the all-notifications archive")
    ] = False,
    q: Annotated[str | None, Query(description="Search the title and body")] = None,
    # SEVERAL VALUES EACH. These took one apiece, so `?category=a&category=b` read as its LAST.
    category: Annotated[
        list[str] | None, Query(description="Registry categories - repeat for several")
    ] = None,
    priority: Annotated[
        list[str] | None, Query(description="low | normal | high - repeat for several")
    ] = None,
    state: Annotated[
        list[str] | None, Query(description="unread | read | dismissed - repeat for several")
    ] = None,
    since: Annotated[str | None, Query(description="ISO date: on or after")] = None,
    until: Annotated[str | None, Query(description="ISO date: on or before")] = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=_MAX_PAGE),
    offset: int = 0,
    contact_id: int | None = None,
    session: AsyncSession = Depends(tenant_session),
    _: Principal = Depends(get_current_principal),
) -> NotificationPage:
    """The caller's own feed, newest first. RLS scopes to the caller's rows.

    **Dismissed rows are hidden by default and shown with ``include_dismissed``.** Dismissing is
    the BELL's action — "take this off my bell" — not a delete, so the all-notifications page asks
    for them and gets the complete archive. Before this parameter the same `dismissed_at IS NULL`
    applied to both surfaces, which made the bell's × silently remove a notification from the only
    page that could still show it.

    ``before_uid`` is the keyset cursor a client can actually name. ``before`` takes the table's
    own id, which is not on the wire (``NotificationOut`` carries ``uid``, and a sequential id
    would leak row ordering and volume — the same reason the admin endpoints address by uid). The
    server resolves the uid to its id here instead; an unknown uid returns an empty page rather
    than silently paging from the top, because a cursor that quietly means "start again" produces
    a list that repeats forever as the reader presses Load more.

    **Search and filters.** ``q`` matches the title or the body; ``category`` and ``priority``
    narrow to one value; ``state`` is ``unread``/``read``/``dismissed``. All server-side, because
    this listing PAGES — filtering the 25 rows in hand would answer "nothing about the Ashworth
    transfer" when it means "not on this page". ``state=dismissed`` implies the archive, so it
    does not also need ``include_dismissed``.

    ``facets`` come back beside the rows, each computed over the match set with its OWN filter
    lifted, so choosing a category does not leave that category as the only one on offer.

    ``contact_id``, when given, narrows to that ONE linked contact's notifications — the
    active-contact selector for a client-portal login linked to more than one client. ANDed on
    top of whatever RLS already allows; a staff caller's own ``recipient_user_id`` rows never
    match it, but staff never pass it, so this is a no-op for them."""
    needle = listing.clean(q)
    params: dict[str, Any] = {
        "lim": limit + 1,
        "offset": max(0, offset),
        # Escaped before the wildcards go on, or a notification titled "100% funded" turns a
        # search for it into a match on every row.
        "q": f"%{listing.escape_like(needle)}%" if needle else None,
        "category": listing.clean_many(category),
        "priority": listing.clean_many(priority),
        # PARSED HERE, never handed to Postgres: `CAST(:since AS date)` turns
        # `?since=2026-13-45` into a 500, which a loader shows as an empty archive.
        "since": _bound(since, "since"),
        "until": _bound(until, "until"),
    }
    states = set(listing.clean_many(state) or [])

    def narrowing(*, skip: str | None = None) -> list[str]:
        """The WHERE clauses, optionally with ONE filter lifted — which is what a facet is.

        A facet computed over rows already narrowed by its own filter is a one-way door: pick
        "tasks" and tasks becomes the only category on offer, with no way back but clearing it
        by hand. The same rule `app/listing.py` states for the in-Python listings.
        """
        out: list[str] = []
        # `state=dismissed` is a request FOR the dismissed rows, so it overrides the default hide
        # rather than fighting it — asking for them and being handed none would read as a bug.
        if "dismissed" not in states and not include_dismissed:
            out.append("dismissed_at IS NULL")
        # Unread and Read both mean a row still ON the bell, so each excludes the
        # dismissed: otherwise Unread would pull back every dismissed-but-unread row.
        state_or = [
            clause
            for value, clause in (
                ("unread", "(read_at IS NULL AND dismissed_at IS NULL)"),
                ("read", "(read_at IS NOT NULL AND dismissed_at IS NULL)"),
                ("dismissed", "dismissed_at IS NOT NULL"),
            )
            if value in states
        ]
        if state_or:
            out.append("(" + " OR ".join(state_or) + ")")
        if needle:
            out.append("(title ILIKE :q ESCAPE '\\' OR coalesce(body, '') ILIKE :q ESCAPE '\\')")
        if skip != "category" and params["category"] is not None:
            out.append("category = ANY(CAST(:category AS text[]))")
        if skip != "priority" and params["priority"] is not None:
            out.append("priority = ANY(CAST(:priority AS text[]))")
        # `created_at` is a timestamptz and the bounds are dates: `until` is its day's END.
        if params["since"] is not None:
            out.append("created_at >= :since")
        if params["until"] is not None:
            out.append("created_at < :until + INTERVAL '1 day'")
        return out

    clauses = narrowing()
    cursor = before
    if before_uid is not None:
        cursor = (
            await session.execute(
                text("SELECT id FROM notifications.notifications WHERE uid = :u"),
                {"u": before_uid},
            )
        ).scalar_one_or_none()
        if cursor is None:
            return NotificationPage(
                items=[], has_more=False, unread_count=await _unread_total(session, contact_id)
            )
    if cursor is not None:
        clauses.append("id < :before")
        params["before"] = cursor
    if unread:
        clauses.append("read_at IS NULL")
    if contact_id is not None:
        clauses.append("recipient_contact_id = :cid")
        params["cid"] = contact_id
    # Possibly empty now: the archive with no cursor and no filters narrows by nothing but RLS.
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = (
        (
            await session.execute(
                text(
                    f"SELECT id, {_FEED_COLS} FROM notifications.notifications{where} "  # noqa: S608
                    "ORDER BY id DESC LIMIT :lim OFFSET :offset"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    has_more = len(rows) > limit
    items = [
        NotificationOut(**{k: r[k] for k in NotificationOut.model_fields}) for r in rows[:limit]
    ]

    async def facet(key: str, column: str) -> list[str]:
        """One filter's options, over the match set with that filter's own clause lifted."""
        lifted = narrowing(skip=key)
        where_lifted = (" WHERE " + " AND ".join(lifted)) if lifted else ""
        # `column` is a literal from this function's two call sites, never caller text; the filter
        # values stay bound parameters.
        sql = (
            f"SELECT DISTINCT {column} AS value FROM notifications.notifications{where_lifted}"  # noqa: S608
            f"{' AND' if lifted else ' WHERE'} {column} IS NOT NULL AND {column} <> '' "
            "ORDER BY value"
        )
        result = await session.execute(text(sql), params)
        return [str(v) for (v,) in result.all()]

    return NotificationPage(
        items=items,
        has_more=has_more,
        facets={
            "category": await facet("category", "category"),
            "priority": await facet("priority", "priority"),
        },
        unread_count=await _unread_total(session, contact_id),
    )


@router.get("/unread-count", response_model=UnreadCount, name="notifications-unread-count")
async def unread_count(
    contact_id: int | None = None,
    session: AsyncSession = Depends(tenant_session),
    _: Principal = Depends(get_current_principal),
) -> UnreadCount:
    """The bell-badge count — the endpoint a poll hits. RLS-scoped to the caller; narrowed to
    one linked contact when ``contact_id`` is given (see ``list_notifications``). The shell's
    first paint now reads the same number off ``GET /notifications`` (``unread_count``); this
    stays for the client-side poller."""
    return UnreadCount(count=await _unread_total(session, contact_id))


@router.post("/{uid}/read", name="mark-notification-read")
async def mark_read(
    uid: str,
    session: AsyncSession = Depends(tenant_session),
    _: Principal = Depends(get_current_principal),
) -> dict[str, bool]:
    """Mark one notification read. RLS restricts the update to the caller's own row."""
    await session.execute(
        text(
            "UPDATE notifications.notifications SET read_at = COALESCE(read_at, now()) "
            "WHERE uid = :u"
        ),
        {"u": uid},
    )
    return {"ok": True}


@router.post("/{uid}/dismiss", name="dismiss-notification")
async def dismiss(
    uid: str,
    session: AsyncSession = Depends(tenant_session),
    _: Principal = Depends(get_current_principal),
) -> dict[str, bool]:
    """Remove one notification from the caller's feed (soft). RLS restricts it to their own row.
    Also stamps read_at so a dismissed-while-unread row stops counting toward the badge."""
    await session.execute(
        text(
            "UPDATE notifications.notifications "
            "SET dismissed_at = now(), read_at = COALESCE(read_at, now()) WHERE uid = :u"
        ),
        {"u": uid},
    )
    return {"ok": True}


@router.post("/dismiss-all", name="dismiss-all-notifications")
async def dismiss_all(
    contact_id: int | None = None,
    session: AsyncSession = Depends(tenant_session),
    _: Principal = Depends(get_current_principal),
) -> dict[str, bool]:
    """Empty the caller's bell: dismiss every row not already dismissed.

    One statement rather than the portal looping the single-dismiss endpoint, because the bell
    shows a window of about twenty and the backlog behind it can be hundreds — a loop would be
    hundreds of round trips to clear a list the reader sees twenty rows of.

    It covers EVERYTHING undismissed, not just the loaded window, which is what "dismiss all"
    means to the person pressing it; dismissing only what is on screen leaves the list to refill
    on the next refetch and reads as a control that did not work. Nothing is lost either way —
    the all-notifications page asks for dismissed rows too (see ``list_notifications``).

    ``read_at`` is stamped alongside, exactly as the single dismiss does, so the badge empties
    with the list rather than counting rows nothing can now show. ``contact_id`` narrows to one
    linked contact, for the same reason ``mark_all_read`` takes it."""
    await session.execute(
        text(
            "UPDATE notifications.notifications "
            "SET dismissed_at = now(), read_at = COALESCE(read_at, now()) "
            "WHERE dismissed_at IS NULL "
            "AND (CAST(:cid AS int) IS NULL OR recipient_contact_id = CAST(:cid AS int))"
        ),
        {"cid": contact_id},
    )
    return {"ok": True}


@router.post("/read-all", name="mark-all-notifications-read")
async def mark_all_read(
    contact_id: int | None = None,
    session: AsyncSession = Depends(tenant_session),
    _: Principal = Depends(get_current_principal),
) -> dict[str, bool]:
    """Mark every unread notification of the caller read. ``contact_id``, when given, narrows
    to one linked contact — so "mark all read" while viewing one client doesn't also clear
    notifications belonging to a login's OTHER linked contact."""
    await session.execute(
        text(
            "UPDATE notifications.notifications SET read_at = now() "
            "WHERE read_at IS NULL "
            "AND (CAST(:cid AS int) IS NULL OR recipient_contact_id = CAST(:cid AS int))"
        ),
        {"cid": contact_id},
    )
    return {"ok": True}


@router.get("/preferences", response_model=PreferencesOut, name="notification-preferences")
async def get_preferences(
    session: AsyncSession = Depends(tenant_session),
    _: Principal = Depends(get_current_principal),
) -> PreferencesOut:
    """The caller's per-NOTIFICATION channel preferences — one row per staff-facing built-in
    notification (a catalogue trigger + its adviser/assignee audience), grouped by category on the
    surface. The adviser mirror of the client preferences page.

    USER-keyed, so they only ever gate notifications addressed to the caller AS A USER (staff
    audiences). Stored overrides merged with each notification's shipped default; a mandatory one is
    still shown but the resolver forces the default, so toggling it is a no-op by design."""
    overrides = {
        (r["category"], r["trigger_key"]): r
        for r in (
            await session.execute(
                text(
                    "SELECT category, trigger_key, in_app, email FROM notifications.preferences "
                    "WHERE recipient_kind = 'user' AND recipient_user_id = auth.uid() "
                    "  AND trigger_key IS NOT NULL"
                )
            )
        ).mappings()
    }
    rule_m, cfg_m = await _mandatory_flags(session, "adviser")
    prefs: list[ChannelPref] = []
    for trig, aud in catalogue.staff_builtins():
        o = overrides.get((trig.category, trig.key))
        prefs.append(
            ChannelPref(
                category=trig.category,
                trigger_key=trig.key,
                # The trigger's plain label — a settings row names the EVENT, not the delivered copy
                # (which carries merge tokens like {client.name} that have nothing to resolve here).
                label=trig.label,  # "When a task is assigned"
                description=aud.label,  # "The assignee" / "The client's adviser"
                in_app=aud.default_in_app if not o or o["in_app"] is None else bool(o["in_app"]),
                email=aud.default_email if not o or o["email"] is None else bool(o["email"]),
                email_available=settings.email_notifications_enabled,
                mandatory=rule_m.get((trig.key, aud.key), aud.mandatory)
                or cfg_m.get(trig.category, False),
            )
        )
    return PreferencesOut(preferences=prefs)


@router.put("/preferences", response_model=PreferencesOut, name="set-notification-preferences")
async def set_preferences(
    body: PreferencesUpdate,
    session: AsyncSession = Depends(tenant_session),
    _: Principal = Depends(get_current_principal),
) -> PreferencesOut:
    """Upsert the caller's per-notification overrides (user-keyed). An item that isn't a real
    staff-facing notification (category + trigger) is skipped."""
    valid = {(t.category, t.key) for t, _a in catalogue.staff_builtins()}
    for p in body.preferences:
        if (p.category, p.trigger_key) not in valid:
            continue
        await session.execute(
            text(
                "INSERT INTO notifications.preferences (tenant_id, recipient_kind, "
                "  recipient_user_id, category, trigger_key, in_app, email) "
                "VALUES (auth.tenant_id(), 'user', auth.uid(), :cat, :tk, :in_app, :email) "
                "ON CONFLICT ON CONSTRAINT preferences_key "
                "  DO UPDATE SET in_app = EXCLUDED.in_app, email = EXCLUDED.email, "
                "    updated_at = now()"
            ),
            {"cat": p.category, "tk": p.trigger_key, "in_app": p.in_app, "email": p.email},
        )
    return await get_preferences(session=session, _=_)


# --- Client preferences (contact-keyed) --------------------------------------------------------
# A client-portal caller has no staff `auth.users` membership in the firm's sense; their
# notifications are addressed to a CONTACT and gated by a contact-keyed preference. These two
# endpoints are the client's own door to THEIR OWN preferences.
#
# Scope is the login's SELF contact(s) — `crm.contact_users WHERE user_id = auth.uid()`, the party
# a login IS (the documents pattern: `cu.user_id = actor`) — NOT `auth.contact_scope()`. The two
# differ for a dual client+adviser login: `contact_scope()` is `contact_users ∪ client_advisers`,
# so scoping the WRITE to it would let such a login change the preferences of every client it
# advises. `contact_users ⊆ contact_scope()`, so the preferences RLS WITH CHECK still passes.


@router.get(
    "/client-preferences", response_model=PreferencesOut, name="notification-client-preferences"
)
async def get_client_preferences(
    session: AsyncSession = Depends(tenant_session),
    _: Principal = Depends(get_current_principal),
) -> PreferencesOut:
    """The client's per-NOTIFICATION preferences — one row per client-facing built-in notification
    (a catalogue trigger + its client audience), grouped by category on the surface.

    Stored overrides — read across the login's own self contacts (a client usually has one) — merged
    with each notification's shipped default. A mandatory notification is still returned so the
    client sees it, but the resolver forces the default regardless, so toggling it is a no-op by
    design (the firm/admin guardrail wins)."""
    overrides = {
        (r["category"], r["trigger_key"]): r
        for r in (
            await session.execute(
                text(
                    "SELECT DISTINCT ON (category, trigger_key) "
                    "  category, trigger_key, in_app, email "
                    "FROM notifications.preferences "
                    "WHERE recipient_kind = 'contact' AND trigger_key IS NOT NULL "
                    "  AND recipient_contact_id IN "
                    "    (SELECT contact_id FROM crm.contact_users WHERE user_id = auth.uid()) "
                    "ORDER BY category, trigger_key, updated_at DESC"
                )
            )
        ).mappings()
    }
    rule_m, cfg_m = await _mandatory_flags(session, "client")
    prefs: list[ChannelPref] = []
    for trig, aud in catalogue.client_builtins():
        o = overrides.get((trig.category, trig.key))
        prefs.append(
            ChannelPref(
                category=trig.category,
                trigger_key=trig.key,
                # A preferences row has no event context. Dynamic delivered copy such as
                # "New message from {actor.name}" therefore supplies a context-free label rather
                # than leaking its merge token onto the client screen.
                label=aud.preference_label or aud.default_title,
                description=trig.label,  # "When a document is shared with a client"
                in_app=aud.default_in_app if not o or o["in_app"] is None else bool(o["in_app"]),
                email=aud.default_email if not o or o["email"] is None else bool(o["email"]),
                email_available=settings.email_notifications_enabled,
                mandatory=rule_m.get((trig.key, aud.key), aud.mandatory)
                or cfg_m.get(trig.category, False),
            )
        )
    return PreferencesOut(preferences=prefs)


@router.put(
    "/client-preferences",
    response_model=PreferencesOut,
    name="set-notification-client-preferences",
)
async def set_client_preferences(
    body: PreferencesUpdate,
    session: AsyncSession = Depends(tenant_session),
    _: Principal = Depends(get_current_principal),
) -> PreferencesOut:
    """Upsert the client's per-notification overrides — one row per SELF contact (the login's own
    `contact_users` links, not its wider `contact_scope()`), so a dual client+adviser login can't
    change the preferences of a client it merely advises. RLS's WITH CHECK still holds (self ⊆
    scope); an item that isn't a real client-facing notification (category + trigger) is skipped."""
    valid = {(t.category, t.key) for t, _a in catalogue.client_builtins()}
    for p in body.preferences:
        if (p.category, p.trigger_key) not in valid:
            continue
        await session.execute(
            text(
                "INSERT INTO notifications.preferences (tenant_id, recipient_kind, "
                "  recipient_contact_id, category, trigger_key, in_app, email) "
                "SELECT auth.tenant_id(), 'contact', cu.contact_id, :cat, :tk, :in_app, :email "
                "FROM crm.contact_users cu WHERE cu.user_id = auth.uid() "
                "ON CONFLICT ON CONSTRAINT preferences_key "
                "  DO UPDATE SET in_app = EXCLUDED.in_app, email = EXCLUDED.email, "
                "    updated_at = now()"
            ),
            {"cat": p.category, "tk": p.trigger_key, "in_app": p.in_app, "email": p.email},
        )
    return await get_client_preferences(session=session, _=_)


# ---- realtime (the bell's live channel; the poll is the fallback) -----------------------------


@router.post("/ticket", name="ticket")
@writes_no_row
async def mint_ticket(
    contact_id: Annotated[
        int | None,
        Query(description="Which linked contact to listen for — the client portal's active one"),
    ] = None,
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, str]:
    """A short-lived, single-use ticket for the SSE stream that follows — the caller's bearer
    token never reaches browser JS (app/notifications/realtime.py, reusing app/live_push.py's
    ticket store). Staff listen per USER, a client per CONTACT: the same grain
    a delivered notification's recipient key uses (``realtime.key_for``), so the worker's announce
    reaches exactly this reader. ``auth.portal() = 'client'`` is the client signal, not a non-empty
    contact_scope() (an adviser's own clients fold into theirs too — see the messaging ticket).

    ``contact_id`` is WHICH contact, for a client login linked to more than one — the portal's
    active-contact switcher, the same parameter every read on that portal already takes. Without it
    the key was ``contact_scope()[0]``, an arbitrary member of the array: a notification about the
    client actually on screen pushed to a reader nobody was listening on (it surfaced on the 30s
    badge poll instead), while one about a different linked client woke the bell.

    An id the session does not hold is **refused**, not quietly replaced by the first one — falling
    back would subscribe the reader to a different client's channel while answering 200, which is
    the confusion this parameter exists to end. The check is the membership test and nothing more:
    the nudge carries no content, and the refetch it triggers is RLS-scoped on its own."""
    is_client = (
        await session.execute(text("SELECT auth.portal() = 'client' OR auth.role() = 'client'"))
    ).scalar()
    if is_client:
        contact_ids = (await session.execute(text("SELECT auth.contact_scope()"))).scalar()
        if not contact_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="No linked contact for this session"
            )
        if contact_id is not None and contact_id not in {int(c) for c in contact_ids}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="That contact is not linked to this session",
            )
        key = realtime.contact_key(contact_id if contact_id is not None else int(contact_ids[0]))
    else:
        uid = (await session.execute(text("SELECT auth.uid()"))).scalar()
        if uid is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="No user for this session"
            )
        key = realtime.user_key(str(uid))
    return {"ticket": live_push.mint_ticket(key)}


@router.get("/events", name="notifications-events", include_in_schema=False)
async def notifications_events(request: Request) -> StreamingResponse:
    """The bell's live channel — the worker's announce fans out through ``live_push.push``, so
    a reader gets the same contentless nudge and refetches its unread count.

    The ticket comes from the private ``x-realtime-ticket`` header rather than the URL (a stream
    URL is logged; a ticket in a log line is redeemable) and is redeemed single-use here, so the
    key this reader listens on was decided by the authenticated ``POST /notifications/ticket``.
    A missing or spent ticket is a 401 before the stream opens.

    No session dependency: holding one for the life of a connection would exhaust the pool with
    idle readers, and there is nothing here to read — the ticket already carries the key."""
    key = live_push.redeem_ticket(request.headers.get(live_push.TICKET_HEADER, ""))
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired realtime ticket"
        )
    # Subscribed before the response, not inside the stream: a notification announced in that
    # gap would otherwise be pushed to nobody, and the bell would simply stay quiet.
    subscriber = live_push.subscribe(key)
    return StreamingResponse(
        live_push.sse_stream(subscriber),
        media_type="text/event-stream",
        headers=live_push.SSE_HEADERS,
    )
