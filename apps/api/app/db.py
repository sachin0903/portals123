import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import cast

from fastapi import Depends, HTTPException, Request, status
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from sqlalchemy import Connection, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, SessionTransaction

import app.tracing as _tracing
from app.auth import (
    Principal,
    build_jwt_claims,
    get_current_principal,
    is_system,
    require_permission,
    resolve_claims_cached,
)
from app.config import settings
from app.permissions import (
    ALL,
    BOOK_READ,
    BOOK_WRITE,
    CASES_READ,
    CONTACTS_READ,
    CONTACTS_READ_ALL,
    CONTACTS_SYNC_MORGAN_LLOYD,
    CONTACTS_WRITE,
    DOCUMENTS_READ,
    DOCUMENTS_WRITE,
    FLOWS_READ,
    INSTRUCTIONS_READ,
    INSTRUCTIONS_WRITE,
    SCREENING_READ,
    TASKS_READ,
    TENANT_PORTAL_READ,
    TENANT_PORTAL_WRITE,
    Permission,
)


class Base(DeclarativeBase):
    """Declarative base. No ORM domain models yet -- the schema is SQL-first
    (migrations/sql/*.sql); this exists for Alembic's target_metadata."""


class ApiSession(Session):
    """The sync session behind every ``SessionLocal`` session: one whose transactions run with
    Postgres JIT off (``_jit_off`` below)."""


@event.listens_for(ApiSession, "after_begin")
def _jit_off(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    """Turn JIT off for this transaction.

    JIT is a Postgres DEFAULT (on since 12), not something this app chose: the server compiles any
    statement whose ESTIMATED cost passes ``jit_above_cost`` (100k), and optimises and inlines it
    past 500k. RLS is what makes those estimates wrong. A policy subquery the planner cannot index
    is costed as a scan per row, so ``GET /documents`` — joined to ``documents.files``, whose read
    policy is an EXISTS over ``documents.documents`` — was estimated at 1.4M+ with a few hundred
    documents, and spent ~1.4s compiling a statement that runs in ~50ms. Every page load, because
    the compiled code is not cached. A sweep of the platform's list endpoints with JIT on and off
    found none that JIT made faster: these are short, indexed statements, and the analytical work
    is DuckDB's, not Postgres's.

    ``SET LOCAL``, not a connection option (``-c jit=off``): it ends with the transaction, which is
    what keeps it correct under PgBouncer transaction pooling — a pooler rejects that startup
    parameter, and a session-level SET would land on whichever backend served it. Not ``ALTER ROLE
    app SET jit``: managed environments provision the roles out of band and the migrator has no
    CREATEROLE (``migrations/bootstrap/01-roles.sql``).
    """
    connection.exec_driver_sql("SET LOCAL jit = off")


engine = create_async_engine(settings.database_url, pool_pre_ping=True)
# One child span per statement. Not psycopg as well — that would double-count every query.
# capture_parameters stays off: the span text has placeholders ($1), not values. The
# process-wide create_engine patch this also installs is removed so the datalake and
# Alembic engines are not instrumented.
SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
_tracing.release_global_engine_wrap()
SessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, sync_session_class=ApiSession, expire_on_commit=False
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """A plain transactional session (no tenant context). System/non-tenant work only."""
    async with SessionLocal() as session, session.begin():
        yield session


@asynccontextmanager
async def system_transaction() -> AsyncIterator[AsyncSession]:
    """A service_role (BYPASSRLS) transaction for system/background work with no request
    principal -- e.g. the WorkOS sync on startup. Not a FastAPI dependency; use directly:
    ``async with system_transaction() as session: ...``."""
    async with SessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        yield session


async def _refuse_if_suspended(session: AsyncSession, tenant_id: int) -> None:
    """Refuse the request when the firm is suspended (``auth.tenants.enabled = false``).

    This is the enforcement half of the admin kill switch. Switching a firm off revokes its people's
    live WorkOS sessions (``provisioning.revoke_tenant_sessions``) and the portals refuse the
    sign-in, but neither of those stops a token that is already in flight, and nothing in
    ``auth.resolve_claims`` or the RLS policies looks at ``enabled`` — so without this check the
    operator-facing promise ("every member is blocked from both portals") was a label on a flag.
    Applied on both firm-facing request paths (``tenant_session``, ``firm_session``); deliberately
    NOT on ``admin_session``, whose principal is Graphene Internal Operations — locking ops staff
    out of the surface that suspends and un-suspends firms would make the switch one-way.

    403, not 404: the caller is authenticated and their firm genuinely exists.

    **Call this only AFTER ``request.jwt.claims`` has been published.** ``auth.tenants`` is no
    longer world-readable to ``authenticated`` — 0030 replaced ``auth_tenants_auth_read`` with
    ``auth_tenants_scoped_read`` (``id = auth.tenant_id() OR id = ANY(auth.tenant_lineage()) OR
    auth.is_system()``), and ``auth.tenant_id()`` resolves from the claims. Run it before the claims
    are set and the SELECT returns NO ROW under RLS, so the check silently passes for every
    suspended firm — a kill switch that reads as implemented and enforces nothing.

    Fails closed on a missing row for the same reason: ``resolve_claims`` has already proved the
    tenant exists, so "not visible" means the check is mis-wired, not that the firm is fine."""
    enabled = (
        await session.execute(
            text("SELECT enabled FROM auth.tenants WHERE id = :t"), {"t": tenant_id}
        )
    ).scalar_one_or_none()
    if enabled is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not verify this firm's status",
        )
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This firm's access is suspended.",
        )


def _require_organisation(principal: Principal) -> None:
    """The cheap fail-closed gate on the firm-facing session paths: no active org in the token
    means no tenant context is possible, so refuse before opening a connection.

    A view-as session (``app/view_as.py``) is the one principal with no organisation at all --
    it is not a WorkOS session -- and its tenant comes from the adviser's own identity inside
    ``auth.resolve_view_as_claims``. The thorough check (a resolved tenant) still applies to it."""
    if principal.organisation_id is None and principal.view_as_contact_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organisation context for this principal",
        )


async def tenant_session(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[AsyncSession]:
    """A transactional session with the RLS context set for this request.

    The request path connects as the ``app`` login role but RLS policies are
    ``TO authenticated``, so we ``SET LOCAL ROLE authenticated`` for the transaction.
    ``auth.resolve_claims`` (SECURITY DEFINER) turns the verified WorkOS identity into
    the pre-resolved internal claims the RLS helpers read; we merge those with the
    JWT-derived half and publish the lot as ``request.jwt.claims`` via
    ``set_config(..., is_local => true)`` -- transaction-scoped, which is what keeps
    isolation correct under PgBouncer transaction pooling.

    Fails closed: if the principal's WorkOS user/org doesn't resolve to a known
    ``auth.users`` row + tenant, we refuse (403) rather than hand back a session whose
    empty claims would (correctly) see nothing but still mask a misconfiguration.
    """
    # Cheap fail-closed: no active org in the token => no tenant context is possible,
    # so refuse before opening a connection (the resolve check below is the thorough one).
    _require_organisation(principal)

    async with SessionLocal() as session, session.begin():
        # Assume the RLS principal for this transaction (app is a member of authenticated).
        await session.execute(text("SET LOCAL ROLE authenticated"))

        resolved = await resolve_claims_cached(
            request,
            session,
            principal.user_id,
            principal.organisation_id,
            principal.client_id,
            principal.view_as_contact_id,
        )

        if not resolved or resolved.get("user_uuid") is None or resolved.get("tenant_id") is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No resolvable tenant/identity context for this principal",
            )
        # App-only portal (0107): NULL means "no active membership for the application this session
        # signed in through" — refused here, plainly, rather than defaulted anywhere.
        if resolved.get("portal") is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account has no access to this portal",
            )

        claims = {**build_jwt_claims(principal), **resolved}
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        # AFTER the claims are published -- auth.tenants is claim-scoped under RLS (see the helper).
        await _refuse_if_suspended(session, cast(int, resolved["tenant_id"]))
        yield session


@dataclass
class FirmContext:
    """A firm self-service request context: an elevated session PLUS the caller's own tenant id
    (resolved from their verified identity) and principal. Endpoints must scope every write to
    ``tenant_id`` and never accept a tenant id from the client."""

    session: AsyncSession
    tenant_id: int
    principal: Principal


async def firm_session(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[FirmContext]:
    """A firm-scoped, self-service session for the platform portal (a firm managing its OWN people
    + roles). Runs as ``service_role`` — writes to ``auth.*`` need it — but the caller's tenant is
    resolved from THEIR verified identity (``auth.resolve_claims``), and endpoints force every
    query to that ``tenant_id``, so a firm can only ever touch its own data. Permission checks
    (``auth.require_permission``) gate each action. Claims are published for ``audit.record()``.

    Distinct from ``admin_session`` (Graphene cross-tenant, system-gated) and ``tenant_session``
    (RLS ``authenticated`` role, read hot path)."""
    _require_organisation(principal)
    async with SessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        resolved = await resolve_claims_cached(
            request,
            session,
            principal.user_id,
            principal.organisation_id,
            principal.client_id,
            principal.view_as_contact_id,
        )
        if not resolved or resolved.get("tenant_id") is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No resolvable tenant/identity context for this principal",
            )
        # App-only portal (0107): the platform portal's self-service surface requires an active
        # PLATFORM membership — the resolved portal says whether this session has one.
        if resolved.get("portal") not in ("platform", "operations"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account has no access to this portal",
            )
        claims = {**build_jwt_claims(principal), **resolved}
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        await _refuse_if_suspended(session, cast(int, resolved["tenant_id"]))
        yield FirmContext(
            session=session, tenant_id=cast(int, resolved["tenant_id"]), principal=principal
        )


async def portal_session(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[FirmContext]:
    """``firm_session``'s plumbing for endpoints a CLIENT may legitimately call — the elevated
    request-fulfilment paths (a client uploading into their own request), whose authorisation
    boundary is the handler's own ownership predicate over ``auth.contact_scope()``.

    Same shape as ``firm_session`` (service_role, tenant from the caller's own resolved identity,
    claims published for audit + scope helpers) but the portal requirement is only "SOME portal":
    any active membership for the application the session signed in through — client included. A
    session with NO resolved portal (0107) is still refused."""
    _require_organisation(principal)
    async with SessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        resolved = await resolve_claims_cached(
            request,
            session,
            principal.user_id,
            principal.organisation_id,
            principal.client_id,
            principal.view_as_contact_id,
        )
        if not resolved or resolved.get("tenant_id") is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No resolvable tenant/identity context for this principal",
            )
        if resolved.get("portal") is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account has no access to this portal",
            )
        claims = {**build_jwt_claims(principal), **resolved}
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        await _refuse_if_suspended(session, cast(int, resolved["tenant_id"]))
        yield FirmContext(
            session=session, tenant_id=cast(int, resolved["tenant_id"]), principal=principal
        )


async def admin_session(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[AsyncSession]:
    """A cross-tenant session for the Graphene admin (Internal Operations) surface. Runs as
    ``service_role`` (BYPASSRLS) so ops staff can read/provision across tenants, and publishes the
    actor's claims to ``request.jwt.claims`` so ``audit.record()`` stamps who did it.

    **Coarse gate (defence in depth):** the caller must be a Graphene *operations* principal —
    ``is_system`` (an operations role carrying the `system` permission) OR resolved to the
    operations tenant (ops staff authenticate into the operations org, which lives on that tenant).
    A firm user therefore never opens an admin session. **Granular RBAC** is enforced per-endpoint
    with ``auth.require_permission`` against the seeded operations permissions (``tenants:read`` /
    ``tenants:manage`` / ``operations:manage`` / ``model:manage`` / ``audit:read``); ``system``
    bypasses them. Every write here lands in ``audit.log`` via the DML triggers.
    """
    from app import environments  # local import: environments has no dependency on db

    async with SessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        resolved = await resolve_claims_cached(
            request, session, principal.user_id, principal.organisation_id, principal.client_id
        )

        if not is_system(principal):
            try:
                ops_tenant_id: int | None = await environments.operations_tenant_id(session)
            except LookupError:
                ops_tenant_id = None
            # App-only portal (0107): an operations session must BE an operations-portal session —
            # resolved to the ops tenant AND through the operations application with an active
            # operations membership. A firm staffer (or an ops person's own platform session)
            # never opens an admin session.
            if (
                not resolved
                or resolved.get("tenant_id") != ops_tenant_id
                or resolved.get("portal") != "operations"
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Operations access required",
                )

        claims = {**build_jwt_claims(principal), **(resolved or {})}
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        yield session


# ---- The portal waterfall: Graphene operations acting on a tenant's book --------------------
# Ops act as a proxy middle-office for tenant firms, so the admin portal must be able to show —
# and, for the named support actions, DO — everything the platform portal does, per tenant.
# `book_session` / `firm_book_session` are the book READS' session dependencies and
# `firm_book_write_session` is the proxy WRITES': without the optional `?tenant=<uid>` they ARE
# `tenant_session` / `firm_session` (pure delegation — the platform/client portals cannot tell
# the difference, which is the regression bar). With it, an operations principal holding
# `book:read` reads the TARGET firm's data with firm-staff-equivalent read visibility, and one
# holding `book:write` performs the firm-staff write with firm-staff-equivalent claims — the
# swapped handlers' own domain gates and validation run untouched against the target tenant.
# The pattern mirrors routers/storage.py::storage_session, tightened: the param is
# operations-ONLY (a firm user naming any tenant — their own included — is refused; the portals
# never send it, so a firm request carrying it is somebody probing).

# The read visibility an operations book reader borrows for the one request: the slugs a firm's
# own read-all staffer would hold across the swapped surfaces. `contacts:read_all` is what turns
# `auth.can_read_all()` on under RLS (whole-firm visibility, not one adviser's book); the rest
# satisfy the swapped handlers' own `require_permission` gates. DOCUMENTS_WRITE is the one
# deliberate write slug here: GET /documents/clients gates on it (it is the document-authoring
# pick-list), and withholding it would 403 that read for ops. It cannot authorise a write
# because the overlay only ever exists inside a `?tenant=` request, and every endpoint wired to
# `book_session` is a GET published into RLS claims — the proxy WRITE surface lives on
# `firm_book_write_session`, which gates the param on `book:write` BEFORE any overlay exists.
_BOOK_READ_VISIBILITY: tuple[str, ...] = (
    CONTACTS_READ.slug,
    CONTACTS_READ_ALL.slug,
    SCREENING_READ.slug,  # the firm-facing (withheld) screening view; ops detail lives at /aml
    DOCUMENTS_READ.slug,
    DOCUMENTS_WRITE.slug,
    TASKS_READ.slug,
    FLOWS_READ.slug,
    CASES_READ.slug,
)

# The firm-context (service_role) book sessions carry the slugs the swapped FirmContext handlers
# gate on, and only those — nothing wider is minted:
#   * `instructions:read` — GET /firm/contacts/{id}/instructions (the pending list the proxy
#     forms read before raising or cancelling anything);
#   * `instructions:write` — the money-movement proxy writes (the bank-account/model PUTs,
#     instruction create/cancel), split out of `contacts:write` with the instructions:* family;
#   * `contacts:write` — the remaining client-record proxy write (PATCH /firm/contacts);
#   * `contacts:sync_morgan_lloyd` — Sync to Morgan Lloyd (split out of write: minting a person
#     in a live CRM is not editing a client record).
# The write slugs cannot let a `book:read` holder write: every proxy write endpoint's session
# dependency is `firm_book_write_session`, which refuses `?tenant=` without `book:write` inside
# `_resolve_book_target` BEFORE this overlay is ever granted. And they never reach published RLS
# claims — `book_session` (the `authenticated` RLS path) grants only _BOOK_READ_VISIBILITY, so
# no RLS write policy can ever be satisfied by an overlay slug. Any endpoint newly wired to the
# firm book sessions must respect that split: writes take the write twin, never the read one.
_BOOK_FIRM_VISIBILITY: tuple[str, ...] = (
    *_BOOK_READ_VISIBILITY,
    CONTACTS_WRITE.slug,
    CONTACTS_SYNC_MORGAN_LLOYD.slug,
    INSTRUCTIONS_READ.slug,
    INSTRUCTIONS_WRITE.slug,
)


def _grant_book_visibility(principal: Principal, slugs: tuple[str, ...]) -> None:
    """Grant the firm-staff-equivalent slugs on the request principal, in place.

    WHY in place: the swapped handlers gate with ``require_permission(principal, ...)`` on their
    own ``Depends(get_current_principal)``, and FastAPI caches that dependency per request — so
    this is the very object the handler body will see, without touching a single handler.
    The mutation is
    request-scoped in production because ``get_current_principal`` builds a fresh ``Principal``
    per request; it happens only on the ``?tenant=`` path, after the operations + book-permission
    gates have already passed.
    """
    missing = [slug for slug in slugs if slug not in principal.permissions]
    if missing:
        principal.permissions = [*principal.permissions, *missing]


async def _resolve_book_target(
    session: AsyncSession,
    principal: Principal,
    resolved: dict[str, object] | None,
    tenant_uid: str,
    permission: Permission,
) -> tuple[int, list[int]]:
    """Gate the ``?tenant=`` cross-tenant book request and resolve its target.

    Needs a ``service_role`` session: ``auth.tenants`` is claim-scoped under RLS (0030) and no
    claims are published yet. Gate order is deliberate — operations scope (403, mirroring
    ``admin_session``'s coarse gate verbatim), then the named book ``permission`` (403;
    ``book:read`` for the read sessions, ``book:write`` for the write twin — so a book READER
    can never reach a proxy write; ``system`` bypasses inside ``require_permission``), then the
    uid lookup (404, the ``storage_session`` precedent) — so a non-operations caller can never
    use the response code as a tenant-existence oracle.

    Returns ``(target_tenant_id, target_lineage)`` — the lineage (target + descendants) is what
    ``auth.resolve_claims`` would mint as ``tenant_ids`` for the target's own staff.
    """
    from app import environments  # local import: environments has no dependency on db

    if not is_system(principal):
        try:
            ops_tenant_id: int | None = await environments.operations_tenant_id(session)
        except LookupError:
            ops_tenant_id = None
        # Mirrors admin_session's coarse gate exactly: the ops TENANT and the operations PORTAL —
        # an ops person's own platform-app session must not reach another firm's book.
        if (
            not resolved
            or resolved.get("tenant_id") != ops_tenant_id
            or resolved.get("portal") != "operations"
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operations access required",
            )
    require_permission(principal, permission)

    target_id = (
        await session.execute(
            text("SELECT id FROM auth.tenants WHERE uid = :uid"), {"uid": tenant_uid}
        )
    ).scalar_one_or_none()
    if target_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown tenant")
    lineage = (
        await session.execute(
            text(
                "SELECT COALESCE(array_agg(id), '{}'::int[]) FROM auth.tenants "
                "WHERE :t = ANY(lineage)"
            ),
            {"t": int(target_id)},
        )
    ).scalar_one()
    return int(target_id), [int(i) for i in lineage]


async def book_session(
    request: Request,
    tenant: str | None = None,
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[AsyncSession]:
    """``tenant_session`` with an optional operations-only target tenant (``?tenant=<uid>``).

    No param ⇒ EXACTLY ``tenant_session`` — delegation, not a copy, so the firm-staff and client
    request paths are byte-identical to before (suspension check included).

    ``?tenant=<uid>`` ⇒ the caller must be an operations principal holding ``book:read``
    (``_resolve_book_target``). The yielded session is then an ``authenticated`` RLS session
    whose claims are minted FOR THE TARGET TENANT with whole-firm read visibility: the target's
    lineage as ``tenant_ids`` (+ ``contacts:read_all`` via the visibility overlay, so
    ``auth.can_read_all()`` holds) and an EMPTY client scope. Every RLS-scoped read in the
    untouched handlers therefore behaves exactly as it does for the target firm's own read-all
    staff — and rows from any other tenant (the operations tenant's included) stay invisible,
    because RLS, not the handler, is still the gate. ``sub``/``user_uuid`` stay the acting
    operator's own, so ``audit.record()`` and any ``auth.uid()`` policy branch keep naming the
    real person.

    Deliberately NOT ``_refuse_if_suspended`` on the foreign path: suspension locks the firm
    out, not Graphene — the same reasoning as ``admin_session``.
    """
    if tenant is None:
        async for session in tenant_session(request, principal):
            yield session
        return

    # The complete tenant workspace supersedes the narrower book overlay for an operator who
    # holds it, exactly as it does on the firm-context twins below: these endpoints (the book
    # reads, the document/task/flow listings) are most of what the workspace is FOR, and refusing
    # them for want of the separate `book:read` grant is what made the workspace's own tabs 403.
    if holds_workspace_permission(principal, TENANT_PORTAL_READ):
        async for session in target_tenant_session(request, tenant, principal):
            yield session
        return

    async with SessionLocal() as session, session.begin():
        # Gates + target resolution need service_role (auth.tenants is claim-scoped and there
        # are no claims yet); the session is DOWNGRADED to `authenticated` before it is yielded,
        # so the handler still reads under RLS — the claims, not the role, widen the view.
        await session.execute(text("SET LOCAL ROLE service_role"))
        resolved = await resolve_claims_cached(
            request, session, principal.user_id, principal.organisation_id, principal.client_id
        )
        target_id, target_lineage = await _resolve_book_target(
            session, principal, resolved, tenant, BOOK_READ
        )
        _grant_book_visibility(principal, _BOOK_READ_VISIBILITY)
        claims = {
            **build_jwt_claims(principal),
            **(resolved or {}),
            # The book claims: the TARGET tenant with a firm-staff lineage and no client scope.
            # `portal` stays the operator's own ('operations' — a staff portal everywhere it is
            # consulted); `role_ids` is emptied because the operator holds no roles in the
            # target tenant (role-assigned task filters must not accidentally match).
            "tenant_id": target_id,
            "tenant_ids": target_lineage,
            "contact_ids": [],
            "account_ids": [],
            "role_ids": [],
        }
        await session.execute(text("SET LOCAL ROLE authenticated"))
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        yield session


async def _firm_book(
    request: Request, tenant: str | None, principal: Principal, permission: Permission
) -> AsyncIterator[FirmContext]:
    """The shared engine behind ``firm_book_session`` / ``firm_book_write_session`` — identical
    plumbing, one difference: which book permission ``?tenant=`` demands. See the wrappers."""
    if tenant is None:
        async for ctx in firm_session(request, principal):
            yield ctx
        return

    async with SessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        resolved = await resolve_claims_cached(
            request, session, principal.user_id, principal.organisation_id, principal.client_id
        )
        target_id, _ = await _resolve_book_target(session, principal, resolved, tenant, permission)
        _grant_book_visibility(principal, _BOOK_FIRM_VISIBILITY)
        claims = {**build_jwt_claims(principal), **(resolved or {})}
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        yield FirmContext(session=session, tenant_id=target_id, principal=principal)


async def firm_book_session(
    request: Request,
    tenant: str | None = None,
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[FirmContext]:
    """``firm_session`` with the same optional operations-only target as ``book_session`` — for
    the staff-side reads whose handlers take a ``FirmContext`` and force every query to
    ``ctx.tenant_id`` (the applications listings/detail, the instructions list).

    No param ⇒ EXACTLY ``firm_session``. ``?tenant=<uid>`` ⇒ the same gates as ``book_session``
    (operations scope + ``book:read``), then a ``service_role`` session whose ``FirmContext`` is
    pinned to the TARGET tenant — the handlers' own ``WHERE tenant_id = :t`` does the scoping,
    exactly as it does for the firm's own staff. The published claims stay the ACTING OPERATOR's
    own (the ``storage_session`` precedent): audit must name the real person, and these handlers
    never read through RLS (``service_role`` bypasses it), so no target-tenant claims are needed.

    READS ONLY. A write handler wired here would be reachable with nothing but ``book:read`` —
    use ``firm_book_write_session`` for anything that mutates the target's book.
    """
    # The complete tenant workspace supersedes the narrower legacy book overlay for operators who
    # hold it. Keep accepting book:read so the existing `/tenants/[uid]/book` routes remain valid,
    # but let `/operate` reuse these established firm-context endpoints under tenant_portal:read.
    if tenant is not None and holds_workspace_permission(principal, TENANT_PORTAL_READ):
        async for ctx in target_firm_session(request, tenant, principal):
            yield ctx
        return
    async for ctx in _firm_book(request, tenant, principal, BOOK_READ):
        yield ctx


async def firm_book_write_session(
    request: Request,
    tenant: str | None = None,
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[FirmContext]:
    """``firm_book_session``'s WRITE twin — the session dependency for the proxy middle-office
    ACTIONS (ops acting on a firm's book on the firm's behalf: edit client details, bank
    details, the account's model mandate, raise/cancel money-movement instructions).

    No param ⇒ EXACTLY ``firm_session`` (the platform portal's own write path, byte-identical).
    ``?tenant=<uid>`` ⇒ operations scope + **``book:write``** — ``book:read`` alone is refused
    (403) inside ``_resolve_book_target`` before any visibility overlay exists, so a book READER
    can never reach a proxy write. The yielded ``FirmContext`` is pinned to the TARGET tenant, so
    every domain gate and validation in the untouched handlers (``contacts:write`` via the
    overlay, tenant-scoped model/account ownership checks, the withdrawal funding gate) runs
    exactly as it does for the target firm's own staff. The published claims stay the ACTING
    OPERATOR's own — ``sub``/``user_uuid`` are the operator's — so ``audit.record()`` stamps the
    real person on every row the write touches, while the AFFECTED row's ``tenant_id`` (the
    trigger stamps the row's own) keeps the trail in the target firm's file.
    """
    # Account/client actions mounted by the complete tenant workspace use the same domain handlers
    # as the older book proxy. tenant_portal:write is the outer authority there; target_firm_session
    # still pins every query to the named firm and preserves the operator in the audit claims.
    if tenant is not None and holds_workspace_permission(principal, TENANT_PORTAL_WRITE):
        async for ctx in target_firm_session(request, tenant, principal):
            yield ctx
        return
    async for ctx in _firm_book(request, tenant, principal, BOOK_WRITE):
        yield ctx


# ---- The complete tenant workspace -----------------------------------------------------------
# Unlike the deliberately narrow book proxy above, these dependencies are the generic door for
# Graphene operations to use a firm's platform-business APIs. They preserve the existing path
# exactly when no target is named, and add an operations-only target when ``?tenant=<uid>`` is
# present. Router-local domain checks stay in place: the request-scoped overlay gives those
# unchanged checks the same platform permission vocabulary a fully-authorised firm operator has.

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# The workspace borrows the target firm's own permission vocabulary, and it borrows it in the
# WIDTH the request was authorised at. A mutating request authorised by `tenant_portal:write`
# gets everything a fully-authorised firm operator holds; a safe request gets the reads only.
#
# The split is not decoration. `build_jwt_claims` republishes `principal.permissions` INTO
# `request.jwt.claims`, so an overlay slug is visible to every RLS policy for the rest of the
# request — granting the write vocabulary on a GET would hand a read-only operator claims that
# satisfy the target firm's write policies. `documents:write` is the one deliberate non-read slug
# in the read tuple, for the same reason `_BOOK_READ_VISIBILITY` carries it: GET
# /documents/clients gates on it (it is the document-authoring pick-list) and withholding it 403s
# a read. Everything else non-read — `documents:send`, `documents:admin`, `forms:submit`,
# `automations:pause`, the `storage:*` credential family — stays out of a read.
_WORKSPACE_WRITE_VISIBILITY: tuple[str, ...] = tuple(
    permission.slug for permission in ALL if permission.portal == "platform"
)
_WORKSPACE_READ_VISIBILITY: tuple[str, ...] = tuple(
    permission.slug
    for permission in ALL
    if permission.portal == "platform"
    and (permission.action.startswith("read") or permission.slug == DOCUMENTS_WRITE.slug)
)


def _workspace_visibility(method: str) -> tuple[str, ...]:
    """The overlay for one request: the read vocabulary for a safe method, the full one else."""
    return _WORKSPACE_READ_VISIBILITY if method in _SAFE_METHODS else _WORKSPACE_WRITE_VISIBILITY


def holds_workspace_permission(principal: Principal, permission: Permission) -> bool:
    """Does this principal hold the tenant-workspace permission (write implying read)?

    This is what makes the older, narrower book proxy defer to the workspace: the two families
    front the SAME domain handlers (a client record's bank details, an account's model mandate,
    the applications listings), so an operator holding `tenant_portal:*` must reach them through
    those endpoints too, rather than being refused for want of the separate `book:*` grant.
    `system` is deliberately NOT treated as holding it, which is why this is `satisfies` MINUS the
    system bypass rather than `satisfies` itself: a system principal keeps the book proxy's own
    path, where the operations-scope check has always been skippable for it. Routing it through the
    workspace instead would refuse a Graphene superuser whose session is not an operations one —
    which is precisely what `test_actions.py`'s proxied book write found.
    """
    held = set(principal.permissions)
    if permission.slug in held:
        return True
    read, sep, _ = permission.slug.rpartition(":read")
    return bool(sep) and f"{read}:write" in held


async def _resolve_tenant_portal_target(
    session: AsyncSession,
    principal: Principal,
    resolved: dict[str, object] | None,
    tenant_uid: str,
    permission: Permission,
) -> tuple[int, list[int]]:
    """Authorise and resolve one operations tenant-workspace target.

    The portal check is unconditional, including for ``system``: a system grant is privilege, not
    permission to use a platform/client application as an operations session. The tenant lookup
    follows the gates to avoid an existence oracle. Suspension locks the firm's own users out; it
    does not lock Graphene support out of the tenant workspace, matching ``admin_session`` and the
    narrower book proxy.
    """
    from app import environments

    try:
        ops_tenant_id: int | None = await environments.operations_tenant_id(session)
    except LookupError:
        ops_tenant_id = None
    if (
        not resolved
        or resolved.get("tenant_id") != ops_tenant_id
        or resolved.get("portal") != "operations"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operations access required",
        )
    require_permission(principal, permission)

    target = (
        (
            await session.execute(
                text("SELECT id FROM auth.tenants WHERE uid = :uid"),
                {"uid": tenant_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown tenant")
    target_id = int(target["id"])
    lineage = (
        await session.execute(
            text(
                "SELECT COALESCE(array_agg(id), '{}'::int[]) FROM auth.tenants "
                "WHERE :t = ANY(lineage)"
            ),
            {"t": target_id},
        )
    ).scalar_one()
    return target_id, [int(i) for i in lineage]


def _target_claims(
    principal: Principal,
    resolved: dict[str, object] | None,
    target_id: int,
    target_lineage: list[int],
) -> dict[str, object]:
    """Claims for target data with the real operator identity retained."""
    return {
        **build_jwt_claims(principal),
        **(resolved or {}),
        "tenant_id": target_id,
        "tenant_ids": target_lineage,
        "contact_ids": [],
        "account_ids": [],
        "role_ids": [],
    }


async def target_tenant_session(
    request: Request,
    tenant: str | None = None,
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[AsyncSession]:
    """``tenant_session`` plus an optional operations tenant-workspace target.

    No target delegates verbatim. A target requires ``tenant_portal:read`` for safe methods and
    ``tenant_portal:write`` otherwise, then yields an ``authenticated`` session under target-firm
    RLS claims. The operator's ``sub``/``user_uuid`` remain unchanged for attribution.
    """
    if tenant is None:
        async for session in tenant_session(request, principal):
            yield session
        return

    permission = TENANT_PORTAL_READ if request.method in _SAFE_METHODS else TENANT_PORTAL_WRITE
    async with SessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        resolved = await resolve_claims_cached(
            request, session, principal.user_id, principal.organisation_id, principal.client_id
        )
        target_id, lineage = await _resolve_tenant_portal_target(
            session, principal, resolved, tenant, permission
        )
        _grant_book_visibility(principal, _workspace_visibility(request.method))
        claims = _target_claims(principal, resolved, target_id, lineage)
        await session.execute(text("SET LOCAL ROLE authenticated"))
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        yield session


async def target_tenant_read_session(
    request: Request,
    tenant: str | None = None,
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[AsyncSession]:
    """Target-aware read session for POST endpoints that only mint ephemeral read channels.

    Realtime tickets are POSTed because they create a one-use credential, but they do not mutate
    tenant data. A read-only operations user must therefore be able to subscribe to the target
    firm's live feed without gaining any of the write authority used by ordinary targeted POSTs.
    """
    if tenant is None:
        async for session in tenant_session(request, principal):
            yield session
        return

    async with SessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        resolved = await resolve_claims_cached(
            request, session, principal.user_id, principal.organisation_id, principal.client_id
        )
        target_id, lineage = await _resolve_tenant_portal_target(
            session, principal, resolved, tenant, TENANT_PORTAL_READ
        )
        # A ticket mints a read channel, so it borrows a read's vocabulary whatever its method.
        _grant_book_visibility(principal, _WORKSPACE_READ_VISIBILITY)
        claims = _target_claims(principal, resolved, target_id, lineage)
        await session.execute(text("SET LOCAL ROLE authenticated"))
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        yield session


async def target_firm_session(
    request: Request,
    tenant: str | None = None,
    principal: Principal = Depends(get_current_principal),
) -> AsyncIterator[FirmContext]:
    """``firm_session`` plus an optional operations tenant-workspace target.

    The target path keeps ``service_role`` for handlers that explicitly scope every query through
    ``FirmContext.tenant_id``. Target claims still name the firm while retaining the operator's
    identity, so database audit triggers and request action events agree on actor and target.
    """
    if tenant is None:
        async for ctx in firm_session(request, principal):
            yield ctx
        return

    permission = TENANT_PORTAL_READ if request.method in _SAFE_METHODS else TENANT_PORTAL_WRITE
    async with SessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        resolved = await resolve_claims_cached(
            request, session, principal.user_id, principal.organisation_id, principal.client_id
        )
        target_id, lineage = await _resolve_tenant_portal_target(
            session, principal, resolved, tenant, permission
        )
        _grant_book_visibility(principal, _workspace_visibility(request.method))
        claims = _target_claims(principal, resolved, target_id, lineage)
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        yield FirmContext(session=session, tenant_id=target_id, principal=principal)


@asynccontextmanager
async def as_service_role(session: AsyncSession) -> AsyncIterator[None]:
    """Run one piece of work elevated inside an RLS-bound request, then hand the transaction back
    as it was found. For a system write a request's own role may not make: ``crm.kyc_checks``
    grants ``authenticated`` SELECT and nothing else (a KYC verdict is the system's to write, as on
    a client's own terms acceptance), and ``compliance.*`` grants a firm status columns only (an
    adviser's upload of a client's identity photos). The caller scopes every statement it runs
    elevated: RLS does not.

    ``set_config('role', …, true)`` is ``SET LOCAL ROLE`` with a bind parameter, so it reverts when
    the transaction ends. The previous role is restored explicitly as well, because the flow engine
    already calls this on a ``service_role`` session and must keep it for the rest of its work.
    """
    previous = str((await session.execute(text("SELECT current_role"))).scalar_one())
    await session.execute(
        text("SELECT set_config('role', 'service_role', true)"),
    )
    try:
        yield
    finally:
        await session.execute(text("SELECT set_config('role', :role, true)"), {"role": previous})
