"""Morgan Lloyd actions on baton.

client.sync is Morgan Lloyd's own action (create or resume the contact, scheme and policy). The
morgan-lloyd.update_client leaf lets the shared client.* composites reach a Morgan Lloyd contact.
Both are thin adapters over the existing handlers (app.integrations.morgan_lloyd.actions)."""

from __future__ import annotations

from typing import Any, cast

from api_log import ApiCall, recording_into
from baton import Permanent, Retry, RunContext, action


def _record_call_evidence(ctx: RunContext, calls: list[ApiCall]) -> ApiCall | None:
    """Put a vendor call on the attempt so the run shows the URL it hit, what it sent and what came
    back: the one that FAILED if any did, else the last made. Returns the call recorded, if any.

    A Morgan Lloyd sync is several calls (the adviser, the contact, each scheme and policy); the
    attempt carries one HTTP record, so this picks the call worth reading — on a refusal, the one
    that was refused, whose body is the whole explanation (`0x80040224 ni already used`)."""
    if not calls:
        return None
    call = next((c for c in reversed(calls) if not c.ok), calls[-1])
    ctx.evidence(
        method=call.method,
        url=call.url or call.operation,
        request=call.request,
        response=call.response,
        status_code=call.status_code,
    )
    return call


def _raise_for(exc: Exception, call: ApiCall | None) -> Exception:
    """How a Dynamics failure is classified. A refusal Dynamics meant (a 4xx — `ni already used`,
    a missing lookup) is permanent: another try sends the same thing and gets the same answer. A
    5xx or a lost answer is transient, so the leaf's `retries` apply and, past them, the run parks
    for a person rather than being written off."""
    status = call.status_code if call is not None else None
    if status is None or status >= 500:
        return Retry(str(exc), status_code=status)
    return Permanent(str(exc), status_code=status)


async def _attempt(
    ctx: RunContext, attempt: Any, *, default_url: str, calls: list[ApiCall] | None = None
) -> dict[str, Any]:
    # Prefer the real recorded exchange over the handler's synthetic summary; fall back to the
    # attempt's own fields when nothing was captured.
    if not (calls and _record_call_evidence(ctx, calls)):
        ctx.evidence(
            method=attempt.method or "POST",
            url=attempt.url or default_url,
            request=attempt.request,
            response=attempt.response,
            status_code=attempt.status_code,
        )
    if not attempt.ok:
        raise Permanent(attempt.error or "Morgan Lloyd refused the call.")
    return cast(dict[str, Any], attempt.response)


# ---- provider leaves --------------------------------------------------------


@action(
    key="morgan-lloyd.sync",
    name="Sync to Morgan Lloyd",
    idempotent=True,
    # A refused or unreachable sync parks for a person, like every other provider leaf, rather
    # than failing the run outright: the operator who pressed Sync retries it from the run, and
    # the sync is idempotent so the retry finishes what the first press left.
    on_failure="needs_attention",
)
async def ml_sync(
    ctx: RunContext,
    *,
    contact_id: int | None = None,
    account_id: int | None = None,
    enrol: bool = False,
) -> dict[str, Any]:
    """Create or resume the client (and, when enrolling, the scheme and policy). Resumable: the
    underlying sync is idempotent, so a repeat finishes what an earlier run left."""
    from app.integrations.morgan_lloyd.actions import sync_morgan_lloyd

    payload = {
        "tenant_id": ctx.tenant_id,
        "contact_id": contact_id,
        "account_id": account_id,
        "enrol": enrol,
    }
    # Collect the vendor calls this sync makes, so the run shows the URL, request and response —
    # even when the sync RAISES (a refused `cam_createcontact`), where the error string was the
    # only trace before and the attempt showed "No address recorded".
    calls: list[ApiCall] = []
    try:
        with recording_into(calls.append):
            attempt = await sync_morgan_lloyd(ctx.session, payload=payload, subjects=[])
    except Exception as exc:
        raise _raise_for(exc, _record_call_evidence(ctx, calls)) from exc
    return await _attempt(ctx, attempt, default_url="morgan-lloyd/sync", calls=calls)


@action(
    key="morgan-lloyd.update_client",
    name="Update Morgan Lloyd client",
    retries=2,
    idempotent=True,
    on_failure="needs_attention",
)
async def ml_update_client(ctx: RunContext, *, contact_id: int, part: str) -> dict[str, Any]:
    from app.integrations.morgan_lloyd.actions import _ml_part

    handler = _ml_part(part)
    calls: list[ApiCall] = []
    try:
        with recording_into(calls.append):
            attempt = await handler(
                ctx.session,
                payload={"tenant_id": ctx.tenant_id, "contact_id": contact_id},
                subjects=[],
            )
    except Exception as exc:
        # Before this every failure was Permanent, which made `retries=2` dead configuration: no
        # exception could ever be retried. A 5xx now retries; a refusal still parks at once.
        raise _raise_for(exc, _record_call_evidence(ctx, calls)) from exc
    return await _attempt(ctx, attempt, default_url="morgan-lloyd/contact", calls=calls)


# ---- composite --------------------------------------------------------------


@action(
    key="client.sync",
    name="Sync client to Morgan Lloyd",
    subject_kinds=("contact",),
    one_open_per_subject=True,
    meta={
        "catalogue": True,
        "category": "clients",
        "kind": "composite",
        "providers": ["morgan-lloyd"],
        "event_type": "client.synced",
        "reads": "The client, and each pension account's scheme.",
    },
)
async def client_sync(
    ctx: RunContext, *, contact_id: int, account_id: int | None = None, enrol: bool = False
) -> dict[str, Any]:
    synced = await ctx.run(
        "morgan-lloyd.sync", contact_id=contact_id, account_id=account_id, enrol=enrol
    )
    return cast(dict[str, Any], synced)
