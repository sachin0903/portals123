"""Resolving the uid in a path to the serial the query keys on.

A URL names a record by its `uid`; the database keys on the `id` beside it. Something has to
cross that boundary, and where it crosses decides how much it costs.

It used to cross in the PORTAL: `/crm/{contacts,accounts}/by-uid/{uid}` answered with the whole
record and `$lib/api`'s `contactIdFor`/`accountIdFor` kept the one integer out of it, so a form
action or a standalone `+server.ts` paid a second HTTP round trip -- and a full record read it
threw away -- for every mutation. Twenty-four call sites did that.

It crosses HERE now. The endpoint's path takes the uid, a dependency turns it into the id, and the
endpoint body is unchanged: `tenant_id: Annotated[int, Depends(admin_tenant_id)]` reads exactly as
`tenant_id: int` did to everything downstream. `Annotated` rather than a default value so the
parameter ORDER is untouched -- a defaulted parameter cannot precede `body: SomeModel`.

WHY A DEPENDENCY AND NOT A JOIN. Where an endpoint is one statement, `WHERE t.uid = :uid` really
is free -- the planner probes `tenants_uid_key` and nests into the rest. But most of these
endpoints are not one statement: they call `provisioning`, `environments`, `rbac`, `catalogue`,
which take `tenant_id: int` and are shared with the worker and the scripts. Their integer contract
is correct -- it is a foreign key -- so the resolve happens once at the door instead of pushing a
uid down through every service.

**The tenant map is cached; the book is not, deliberately.**

`auth.tenants` is a handful of rows, and a uid is never rewritten (`app/ids.py`), so uid -> id is
immutable for a tenant that exists. Only HITS are stored: a miss falls through to the query, so a
tenant created a second ago resolves on its first request, and nothing ever needs invalidating.

`crm.contacts` and `platform.accounts` are NOT cached, and that is a security property rather than
an oversight. Their resolve runs inside the caller's own RLS-bound session, so a uid outside the
caller's book returns nothing and 404s -- indistinguishable from "no such record", which is the
fail-closed behaviour `by-uid` was written with. A process-wide cache would answer from another
tenant's read and leave the refusal to whatever the endpoint did next.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import providers
from app.db import (
    FirmContext,
    admin_session,
    book_session,
    firm_book_session,
    firm_book_write_session,
    target_firm_session,
    target_tenant_session,
)

# A RESOLVER MUST DECLARE THE SAME SESSION DEPENDENCY AS THE ENDPOINTS THAT USE IT.
#
# FastAPI caches a dependency per request, so when the two agree the resolve rides the endpoint's
# own session and its scoping. When they disagree the resolver opens a SECOND session — and where
# the endpoint's session is target-aware (`?tenant=<uid>`, the Graphene operations workspace) that
# second session is bound to the OPERATOR's own tenant instead of the named firm. The uid then
# resolves against the wrong book and the endpoint answers 404: the firm's record is right there,
# and the workspace reports it does not exist.
#
# So the resolvers below take the target-aware sessions, which delegate verbatim when no target is
# named. The firm/client portals cannot tell the difference; the workspace stops 404ing.

#: uid -> id for tenants. Immutable and tiny; see the module note.
_TENANT_IDS: dict[str, int] = {}


async def _resolve(session: AsyncSession, sql: str, uid: str, what: str) -> int:
    row = (await session.execute(text(sql), {"uid": uid})).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")
    return int(row)


async def tenant_id_for_uid(session: AsyncSession, uid: str) -> int:
    """A tenant's serial id, cached. 404 on an unknown uid rather than a silent None: a mistyped
    uid asking "what did this firm do" must not be answered with what every firm did."""
    cached = _TENANT_IDS.get(uid)
    if cached is not None:
        return cached
    tenant_id = await _resolve(
        session, "SELECT id FROM auth.tenants WHERE uid = :uid", uid, "Tenant"
    )
    _TENANT_IDS[uid] = tenant_id
    return tenant_id


def forget_tenant(uid: str) -> None:
    """Drop a tenant from the map. Only a DELETE needs this -- a uid never changes hands, so the
    entry cannot go stale, but a deleted tenant's uid must stop resolving.

    PUBLIC, and CALLED: it was `_forget_tenant` with no caller anywhere, while
    `DELETE /admin/tenants/{tenant_uid}` existed and did not invalidate anything. So a deleted
    firm's uid kept resolving to its serial for the life of the process, and every
    `/admin/tenants/{uid}/…` read under it answered with rows that were gone rather than a 404 —
    the failure `tenant_id_for_uid`'s own docstring names, where a uid that should not resolve is
    "answered with what every firm did"."""
    _TENANT_IDS.pop(uid, None)


async def admin_tenant_id(tenant_uid: str, session: AsyncSession = Depends(admin_session)) -> int:
    """`/admin/tenants/{tenant_uid}/…` -> the serial the endpoint keys on."""
    return await tenant_id_for_uid(session, tenant_uid)


async def book_contact_id(contact_uid: str, session: AsyncSession = Depends(book_session)) -> int:
    """`/crm/contacts/{contact_uid}/…` -> the serial. Uncached and RLS-bound: a uid outside this
    caller's book is a 404, exactly as an out-of-scope id is."""
    return await _resolve(
        session,
        "SELECT id FROM crm.contacts WHERE uid = :uid",
        contact_uid,
        "Contact",
    )


async def book_account_id(account_uid: str, session: AsyncSession = Depends(book_session)) -> int:
    """`/crm/accounts/{account_uid}/…` -> the serial. Uncached and RLS-bound, as above."""
    return await _resolve(
        session, "SELECT id FROM platform.accounts WHERE uid = :uid", account_uid, "Account"
    )


async def firm_contact_id(contact_uid: str, ctx: FirmContext = Depends(target_firm_session)) -> int:
    """The `/firm/contacts/{contact_uid}/…` counterpart.

    The firm sessions yield a FirmContext, not a bare session: the tenant and principal ride with
    it, because a firm endpoint scopes every write to the tenant it resolved from a verified
    identity — its own staff's, or the firm an operations workspace request named — rather than one
    the client put in the body."""
    return await _resolve(
        ctx.session,
        "SELECT id FROM crm.contacts WHERE uid = :uid",
        contact_uid,
        "Contact",
    )


async def tenant_contact_id(
    contact_uid: str, session: AsyncSession = Depends(target_tenant_session)
) -> int:
    """The RLS-session counterpart. A separate function per session kind on purpose: see the note
    above `_TENANT_IDS` — a resolver declaring the same session as its endpoint shares one, and a
    resolver declaring a different one opens a second, bound to the wrong tenant."""
    return await _resolve(
        session,
        "SELECT id FROM crm.contacts WHERE uid = :uid",
        contact_uid,
        "Contact",
    )


async def tenant_account_id(
    account_uid: str, session: AsyncSession = Depends(target_tenant_session)
) -> int:
    """The RLS-session counterpart for an account."""
    return await _resolve(
        session, "SELECT id FROM platform.accounts WHERE uid = :uid", account_uid, "Account"
    )


# ---- the book contexts ------------------------------------------------------
# `firm_book_session` / `firm_book_write_session` yield a FirmContext rather than a bare session --
# an elevated session PLUS the caller's own tenant and principal, because these endpoints can be
# proxied by operations against another firm (`?tenant=<uid>`). The resolve rides the SAME context
# the endpoint declares, so it shares the request's one session and its scoping.


async def book_read_contact_id(
    contact_uid: str, ctx: FirmContext = Depends(firm_book_session)
) -> int:
    return await _resolve(
        ctx.session,
        "SELECT id FROM crm.contacts WHERE uid = :uid",
        contact_uid,
        "Contact",
    )


async def book_write_contact_id(
    contact_uid: str, ctx: FirmContext = Depends(firm_book_write_session)
) -> int:
    """`/firm/contacts/{contact_uid}/…` -> the serial, on the WRITE session.

    MATCHED TO THE SESSION, which is the whole reason this exists beside `firm_contact_id`.
    FastAPI caches a dependency per callable, so a route declaring `firm_contact_id` alongside
    `firm_book_write_session` opens BOTH: two pooled connections, two `resolve_claims` round trips
    and two `_refuse_if_suspended` checks per request, with the uid->id resolve committed in a
    different transaction from the write it feeds — so the contact can be archived in between. On
    the ops proxy path (`?tenant=<uid>`) the resolve additionally runs in the OPERATOR's tenant
    context and only survives because the SQL below carries no tenant predicate.
    """
    return await _resolve(
        ctx.session,
        "SELECT id FROM crm.contacts WHERE uid = :uid",
        contact_uid,
        "Contact",
    )


async def book_write_account_id(
    account_uid: str, ctx: FirmContext = Depends(firm_book_write_session)
) -> int:
    return await _resolve(
        ctx.session, "SELECT id FROM platform.accounts WHERE uid = :uid", account_uid, "Account"
    )


# ---- the firm-configured catalogue ------------------------------------------
# These sit under `/admin/tenants/{tenant_uid}/catalogue/…`, so the tenant is already resolved by
# `admin_tenant_id` and the row is resolved WITHIN it. Scoping the resolve rather than the endpoint
# alone is the fail-closed choice: a uid belonging to another firm's shelf resolves to nothing and
# 404s here, instead of yielding an id that the endpoint's own `AND tenant_id = :t` then rejects.


async def _scoped(session: AsyncSession, table: str, uid: str, tenant_id: int, what: str) -> int:
    row = (
        await session.execute(
            text(f"SELECT id FROM {table} WHERE uid = :uid AND tenant_id = :t"),  # noqa: S608
            {"uid": uid, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")
    return int(row)


async def catalogue_model_id(
    model_uid: str,
    tenant_id: int = Depends(admin_tenant_id),
    session: AsyncSession = Depends(admin_session),
) -> int:
    return await _scoped(session, "platform.models", model_uid, tenant_id, "Model")


async def catalogue_product_id(
    product_uid: str,
    tenant_id: int = Depends(admin_tenant_id),
    session: AsyncSession = Depends(admin_session),
) -> int:
    return await _scoped(session, "platform.products", product_uid, tenant_id, "Product")


async def catalogue_version_id(
    version_uid: str,
    tenant_id: int = Depends(admin_tenant_id),
    session: AsyncSession = Depends(admin_session),
) -> int:
    return await _scoped(
        session, "platform.model_versions", version_uid, tenant_id, "Model version"
    )


async def catalogue_commentary_id(
    commentary_uid: str,
    tenant_id: int = Depends(admin_tenant_id),
    session: AsyncSession = Depends(admin_session),
) -> int:
    return await _scoped(
        session, "platform.model_commentary", commentary_uid, tenant_id, "Commentary"
    )


async def catalogue_fee_package_id(
    fee_uid: str,
    tenant_id: int = Depends(admin_tenant_id),
    session: AsyncSession = Depends(admin_session),
) -> int:
    return await _scoped(session, "platform.fee_packages", fee_uid, tenant_id, "Fee package")


async def catalogue_provider_id(
    kind: str,
    provider_uid: str,
    tenant_id: int = Depends(admin_tenant_id),
    session: AsyncSession = Depends(admin_session),
) -> int:
    """`/catalogue/providers/{kind}/{provider_uid}` -- two registers behind one path.

    The table is chosen by `kind`, and an unknown kind is refused HERE rather than resolved against
    a guessed one: `catalogue._provider_tables` raises the same refusal a line later, and a resolve
    that fell back to either table would answer about the wrong register."""
    tables = {
        "third_party": "platform.third_party_providers",
        "transfer": "platform.transfer_providers",
    }
    table = tables.get(kind)
    if table is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="kind must be 'third_party' or 'transfer'",
        )
    return await _scoped(session, table, provider_uid, tenant_id, "Provider")


def platform_provider(provider: str) -> str:
    """A provider path param, validated against the registry.

    IT WAS A UID RESOLVE (0131). `platform.platforms` carried a `uid` so the admin URL could name
    a custodian without a serial — the estate's addressing rule — and the table is gone, because
    its `id` was a surrogate for a member of a closed vocabulary. The code IS the public
    identifier now: `/…/platforms/seccl` names the same thing `/…/platforms/VgEfVmBhIXkZ` did and
    a person can read it, which is what the rule was for.

    Not async and takes no session: there is nothing to look up.
    """
    if provider not in providers.PROVIDERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Platform not found")
    return provider


async def admin_environment_id(
    environment_uid: str, session: AsyncSession = Depends(admin_session)
) -> int:
    return await _resolve(
        session,
        "SELECT id FROM auth.tenant_environments WHERE uid = :uid",
        environment_uid,
        "Environment",
    )


async def document_type_id(
    type_uid: str, session: AsyncSession = Depends(target_tenant_session)
) -> int:
    """A firm's own document type. RLS scopes the read, so a uid outside the caller's tenant is a
    404 rather than a type from another firm's list."""
    return await _resolve(
        session,
        "SELECT id FROM documents.document_types WHERE uid = :uid",
        type_uid,
        "Document type",
    )


async def dead_letter_row_id(
    kind: str, row_uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> int:
    """`/flows/dead-letters/{kind}/{row_uid}/replay` -- two queues behind one path.

    Refused here for an unknown kind rather than resolved against a guessed table, for the same
    reason the provider registers are: a replay is a write, and one aimed at the wrong queue is a
    job restarted that nobody asked to restart."""
    tables = {"outbox": "flows.outbox", "render": "documents.render_jobs"}
    table = tables.get(kind)
    if table is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="kind must be 'outbox' or 'render'",
        )
    return await _resolve(
        ctx.session,
        f"SELECT id FROM {table} WHERE uid = :uid",  # noqa: S608 - fixed table map above
        row_uid,
        "Dead letter",
    )
