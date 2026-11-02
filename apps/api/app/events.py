"""Incremental WorkOS reconcile via the Events API (per environment/project).

Dev/self-hosted deployments can't receive webhooks, so we poll the Events API instead — but
*incrementally*, with a persisted cursor per environment, applying each event exactly once
and in order (creates, updates, and deletes). This is the source of truth for keeping the
`auth.*` mirror in step with WorkOS.

Each tenant environment is its own WorkOS project (its API key scopes the event stream), so we
consume per environment. On first run (no cursor) we do one full reconcile to backfill existing
state, then advance the cursor to the latest event and go incremental from there.
"""

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from workos import AsyncWorkOSClient

from app import environments, provisioning, session_mirror
from app.ids import new_id

logger = logging.getLogger(__name__)

# The mirror-affecting events. Roles stay out (they are push-only), and so do auth
# attempts. Session events are the live-browser list: the payload is the session.
EVENT_TYPES = [
    "user.created",
    "user.updated",
    "user.deleted",
    "organization_membership.created",
    "organization_membership.updated",
    "organization_membership.deleted",
    "session.created",
    "session.revoked",
]
_PAGE = 100
# First-run seed walks the stream oldest-first. WorkOS retains ~30 days; this is a
# runaway guard, not a planning number (a real reset on this project is two pages).
_MAX_SEED_PAGES = 200


class MirrorConflict(Exception):
    """This event cannot be applied without making the mirror wrong, so it is parked.

    Raised where WorkOS asserts something the mirror's own constraints refuse — a login already
    mirrored under a different tenant, or an email another login in the tenant holds. It is a
    refusal, not a failure: the message names the rows so an operator can decide, and `consume`
    skips past it rather than retrying it for ever."""


async def consume(
    session: AsyncSession,
    tenant_environment_id: int,
    *,
    progress: Callable[[], None] | None = None,
) -> int:
    """Consume new WorkOS events for one environment into the mirror. First run backfills via a
    full reconcile then seeds the cursor; subsequent runs apply only events after the cursor.
    Returns the number of events applied."""
    row = (
        await session.execute(
            text(
                "SELECT tenant_id, events_cursor, firm_org_id "
                "FROM auth.tenant_environments WHERE id = :e"
            ),
            {"e": tenant_environment_id},
        )
    ).one()
    tenant_id, cursor, firm_org_id = int(row[0]), row[1], row[2]
    workos = await environments.client_for_env(session, tenant_environment_id)

    if cursor is None:
        # Seed: reconcile existing members, then start the stream at the NEWEST event so we
        # don't re-apply history the backfill already reflects.
        #
        # Do NOT `list_events(limit=1, order="desc")`. The SDK's default is desc, and we
        # used to pass desc explicitly, but the Events API ignores `order` once `events=`
        # is set (verified live 12 Sep 2026: desc and asc returned the same oldest event).
        # Seeding that id makes the next tick replay every membership that ever existed.
        # A later `organization_membership.deleted` then a re-add is the db-reset login
        # bug: delete downs every portal row, and an `active` event only promotes
        # `pending` — never `inactive` — so the owner stays locked out while WorkOS still
        # authenticates them.
        await provisioning.sync_tenant(session, tenant_id, push_roles=False, progress=progress)
        if progress is not None:
            progress()
        newest_id = await _head_event_id(workos, progress=progress)
        if newest_id is not None:
            await _save_cursor(session, tenant_environment_id, newest_id)
        return 0

    applied = 0
    skipped = 0
    while True:
        page = await workos.events.list_events(
            events=EVENT_TYPES, after=cursor, order="asc", limit=_PAGE
        )
        if progress is not None:
            progress()
        evs: list[Any] = list(page.data)
        if not evs:
            break
        for ev in evs:
            # Each event applies inside its OWN savepoint, and the cursor advances past it either
            # way. One event that cannot be applied used to stop the whole environment for good:
            # the cursor is only persisted once a page completes, so a failure mid-page discarded
            # the page's progress, the next tick re-fetched the same page, and it failed at the
            # same event — for ever, with every later event stuck behind it. (That is not
            # hypothetical: it is what a deleted-and-recreated user did to a dev environment, which
            # sat hours behind its sibling.) A savepoint is what makes "skip this one" possible at
            # all — the IntegrityError aborts the transaction, so without one every subsequent
            # statement in the page would fail too.
            #
            # Parked events are LOGGED, not stored: a park table is a schema decision, and the log
            # names the event id, which is all a replay needs (rewind the environment's cursor).
            try:
                async with session.begin_nested():
                    await _apply(session, workos, tenant_id, ev, firm_org_id=firm_org_id)
                applied += 1
            except MirrorConflict as exc:
                skipped += 1
                logger.error(
                    "parked WorkOS event %s (%s) for environment %s: %s",
                    getattr(ev, "id", "?"),
                    getattr(ev, "event", "?"),
                    tenant_environment_id,
                    exc,
                )
            except Exception:
                skipped += 1
                logger.exception(
                    "parked WorkOS event %s (%s) for environment %s",
                    getattr(ev, "id", "?"),
                    getattr(ev, "event", "?"),
                    tenant_environment_id,
                )
            cursor = ev.id
        await _save_cursor(session, tenant_environment_id, cursor)
        if len(evs) < _PAGE:
            break
    if skipped:
        logger.warning(
            "environment %s: %d event(s) parked, %d applied",
            tenant_environment_id,
            skipped,
            applied,
        )
    return applied


async def _head_event_id(
    workos: AsyncWorkOSClient, *, progress: Callable[[], None] | None = None
) -> str | None:
    """The newest event id in the filtered stream, by walking oldest-first to the tail.

    `order="desc"` is not a shortcut here — see `consume`. Returns None when the stream is
    empty. Caps at `_MAX_SEED_PAGES` so a stuck vendor page cannot loop the poller; a
    truncated seed still skips everything already walked, which is the point."""
    cursor: str | None = None
    last: str | None = None
    for _ in range(_MAX_SEED_PAGES):
        kw: dict[str, Any] = {"events": EVENT_TYPES, "order": "asc", "limit": _PAGE}
        if cursor is not None:
            kw["after"] = cursor
        page = await workos.events.list_events(**kw)
        if progress is not None:
            progress()
        evs: list[Any] = list(page.data)
        if not evs:
            break
        last = evs[-1].id
        cursor = last
        if len(evs) < _PAGE:
            break
    else:
        logger.warning(
            "WorkOS events seed stopped after %d pages at %s; remaining history may still replay",
            _MAX_SEED_PAGES,
            last,
        )
    return last


async def _save_cursor(session: AsyncSession, env_id: int, cursor: str) -> None:
    await session.execute(
        text("UPDATE auth.tenant_environments SET events_cursor = :c WHERE id = :e"),
        {"c": cursor, "e": env_id},
    )


def _is_foreign_org(data: Any, firm_org_id: str | None) -> bool:
    """True when this membership event is about an organisation that is not the firm's.

    The project's event stream carries every org, leftover Client/Platform orgs from the
    pre-0107 model included. Treating those as "the person left the firm" is what made a
    db-reset bootstrap look like "that account does not have access to this portal": a
    historical ``organization_membership.deleted`` for an old portal org inactivated every
    portal row, and the reconcile never promotes ``inactive`` back to ``active``."""
    if not firm_org_id:
        return False
    org_id = getattr(data, "organization_id", None)
    return bool(org_id) and str(org_id) != str(firm_org_id)


async def _apply(
    session: AsyncSession,
    workos: AsyncWorkOSClient,
    tenant_id: int,
    event: Any,
    *,
    firm_org_id: str | None = None,
) -> None:
    kind = getattr(event, "event", None)
    data = getattr(event, "data", None)
    if data is None:
        return
    if kind in ("user.created", "user.updated"):
        await _upsert_user(session, tenant_id, data)
    elif kind == "user.deleted":
        await session.execute(
            text("UPDATE auth.users SET enabled = false WHERE workos_user_id = :wu"),
            {"wu": data.id},
        )
    elif kind in ("organization_membership.created", "organization_membership.updated"):
        if _is_foreign_org(data, firm_org_id):
            return
        await _apply_membership_state(session, workos, tenant_id, data)
    elif kind == "organization_membership.deleted":
        # One-firm-org: the WorkOS membership is the PERSON's authentication, not a portal —
        # and that membership is the FIRM org's, not any org in the project. Its deletion
        # means "this person is out of the firm" — every portal row goes inactive. Portal
        # rows are never deleted from an event: which portals existed is our record, not WorkOS's.
        if _is_foreign_org(data, firm_org_id):
            return
        await session.execute(
            text(
                "UPDATE auth.organisation_memberships SET status = 'inactive' "
                "WHERE tenant_id = :t AND user_id = "
                "  (SELECT id FROM auth.users WHERE workos_user_id = :wu)"
            ),
            {"t": tenant_id, "wu": data.user_id},
        )
    elif kind in ("session.created", "session.revoked"):
        await session_mirror.apply_event(session, tenant_id, event)


async def _upsert_user(session: AsyncSession, tenant_id: int, user: Any) -> str:
    """Upsert a WorkOS user into the mirror (email, name, verified->enabled). Returns the id.

    Matched by `workos_user_id` FIRST — that column is globally UNIQUE, so it is the handle for a
    mirrored login — then, only if that finds nothing, by `(tenant_id, email)`, which is what
    converges a row provisioning invited before the WorkOS user existed. Two reads in that order,
    deliberately: the single statement this replaces OR'd the two branches under `LIMIT 1` with no
    `ORDER BY`, so when they matched DIFFERENT rows it picked one arbitrarily and then wrote the
    other one's identity onto it.

    **A mirrored user is never re-homed.** The update does not write `tenant_id`: a person's firm is
    decided when they are provisioned, and an event stream is not entitled to move them. Because the
    `workos_user_id` match is global, writing `tenant_id = :t` meant any environment's stream could
    pull an existing user into its own tenant — which is how two dev environments pointed at one
    WorkOS project ended up fighting over every row, and would be a cross-tenant write if a project
    were ever shared in earnest. A user whose row belongs to another tenant raises `MirrorConflict`.

    An email change onto an address another login in the same tenant already holds also raises,
    rather than letting `users_tenant_email_key` surface as a psycopg traceback: the refusal names
    the two logins so an operator can act, and `consume` parks the event instead of wedging the
    stream on it for ever. Neither refusal mentions the email itself — the workos id and the tenant
    are enough to act on, and a log line is not the place for someone's address."""
    verified = bool(getattr(user, "email_verified", False))
    params = {
        "t": tenant_id,
        "email": provisioning.fold_email(user.email),
        "fn": getattr(user, "first_name", None),
        "ln": getattr(user, "last_name", None),
        # auth.users.name is a generated column (first + last), so we never write it — it
        # tracks name changes automatically. avatar mirrors WorkOS's profile picture.
        "avatar": getattr(user, "profile_picture_url", None),
        "wu": user.id,
        "en": verified,
    }
    found = (
        await session.execute(
            text("SELECT id, tenant_id FROM auth.users WHERE workos_user_id = :wu"), params
        )
    ).first()
    if found is None:
        found = (
            await session.execute(
                text(
                    "SELECT id, tenant_id FROM auth.users WHERE tenant_id = :t AND email = :email"
                ),
                params,
            )
        ).first()
    existing = found[0] if found is not None else None
    if existing is not None and int(found[1]) != tenant_id:  # type: ignore[index]
        raise MirrorConflict(
            f"WorkOS user {user.id} is already mirrored under tenant {int(found[1])}, "  # type: ignore[index]
            f"and this stream belongs to tenant {tenant_id}; not re-homing it"
        )
    if existing is None:
        return str(
            (
                await session.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, first_name, last_name, "
                        "  avatar, workos_user_id, enabled) "
                        "VALUES (:uid, :t, :email, :fn, :ln, :avatar, :wu, :en) RETURNING id"
                    ),
                    # The uid came from `'usr_' || gen_random_uuid()` inline in the SQL, so a user
                    # created by a WorkOS event had a different id shape from one created by
                    # provisioning -- the same kind, two shapes. app/ids.py is the only minter.
                    {**params, "uid": new_id()},
                )
            ).scalar_one()
        )
    clash = (
        await session.execute(
            text(
                "SELECT workos_user_id FROM auth.users "
                "WHERE tenant_id = :t AND email = :email AND id <> :id"
            ),
            {**params, "id": existing},
        )
    ).first()
    if clash is not None:
        raise MirrorConflict(
            f"WorkOS user {user.id} would take an email that login {clash[0]} already holds in "
            f"tenant {tenant_id}; two logins cannot share one address"
        )
    # No `tenant_id` here, on purpose — see the docstring. Re-homing is what this refuses to do.
    await session.execute(
        text(
            "UPDATE auth.users SET email = :email, first_name = :fn, "
            "  last_name = :ln, avatar = :avatar, workos_user_id = :wu, "
            "  enabled = :en WHERE id = :id"
        ),
        {**params, "id": existing},
    )
    return str(existing)


async def _apply_membership_state(
    session: AsyncSession, workos: AsyncWorkOSClient, tenant_id: int, data: Any
) -> None:
    """Map a firm-org membership event onto the PERSON's mirror state (one-firm-org).

    WorkOS's membership is authentication, not a portal — which portals a person may use is
    exclusively ``auth.organisation_memberships`` and an event never invents a portal row. This
    ensures the mirror user exists (fetching from WorkOS for an out-of-order event), then maps the
    membership's state onto the person's own rows: inactive ⇒ all inactive; active + verified ⇒
    pending rows promoted to active."""
    uid = (
        await session.execute(
            text("SELECT id FROM auth.users WHERE workos_user_id = :wu"), {"wu": data.user_id}
        )
    ).scalar_one_or_none()
    verified = False
    if uid is None:
        wuser = await workos.user_management.get_user(data.user_id)
        uid = await _upsert_user(session, tenant_id, wuser)
        verified = bool(getattr(wuser, "email_verified", False))
    else:
        verified = bool(
            (
                await session.execute(
                    text("SELECT enabled FROM auth.users WHERE id = :id"), {"id": uid}
                )
            ).scalar_one()
        )

    status = data.status.value if hasattr(data.status, "value") else str(data.status)
    if status == "inactive":
        await session.execute(
            text(
                "UPDATE auth.organisation_memberships SET status = 'inactive' "
                "WHERE user_id = :u AND tenant_id = :t"
            ),
            {"u": uid, "t": tenant_id},
        )
    elif verified:
        await session.execute(
            text(
                "UPDATE auth.organisation_memberships SET status = 'active' "
                "WHERE user_id = :u AND tenant_id = :t AND status = 'pending'"
            ),
            {"u": uid, "t": tenant_id},
        )
