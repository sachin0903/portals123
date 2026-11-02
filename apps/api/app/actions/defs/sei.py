"""SEI actions on baton.

SEI opens the client and the account together, then the pay-to profile, then the other
currencies. Each is a leaf adapting the existing handler. The waits are durable: when SEI is
still opening the account the handler raises a non-permanent error, which the leaf turns into
NotReady — baton rests the run and wakes it to poll again, carrying the enrolment's business key
on the previous attempt so the retry resumes instead of enrolling a second time.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from baton import NotReady, Permanent, RunContext, Skip, action


def _refusal_evidence(ctx: RunContext, exc: Any) -> None:
    """Record what SEI said on a REFUSED call, so the run shows the request and the response and
    the detail page can read the reason out of the body rather than the message's tail."""
    ctx.evidence(
        method=getattr(exc, "method", None),
        url=getattr(exc, "url", None),
        request=getattr(exc, "request", None),
        response=getattr(exc, "response", None),
        status_code=getattr(exc, "status_code", None),
    )


@action(
    key="sei.enrol",
    name="Enrol client and account at SEI",
    on_failure="needs_attention",
    wait_timeout_seconds=1800,
)
async def sei_enrol(
    ctx: RunContext, *, account_ids: Sequence[int], application_uid: str | None = None
) -> dict[str, Any]:
    from app.actions.attempt import OperationError
    from app.integrations.sei.actions import create_sei_account
    from app.integrations.sei.enrol import resume_business_key

    resume = None
    if ctx.previous is not None:
        resume = resume_business_key(ctx.previous.error, ctx.previous.response)
    payload: dict[str, Any] = {
        "tenant_id": ctx.tenant_id,
        "account_ids": [int(a) for a in account_ids],
        "application_uid": application_uid,
    }
    if resume:
        payload["resume_business_key"] = resume
    try:
        attempt = await create_sei_account(ctx.session, payload=payload, subjects=[])
    except OperationError as exc:
        body = getattr(exc, "response", None)
        if exc.permanent:
            _refusal_evidence(ctx, exc)
            raise Permanent(str(exc)) from exc
        # Still opening: wait durably and poll again, keeping the business key to resume.
        raise NotReady(str(exc), delay=15, response=body) from exc
    ctx.evidence(
        method=attempt.method, url=attempt.url, request=attempt.request, response=attempt.response
    )
    return cast(dict[str, Any], attempt.response)


@action(
    key="sei.pay_to",
    name="Create SEI pay-to profile",
    on_failure="needs_attention",
    wait_timeout_seconds=1800,
    idempotent=True,
)
async def sei_pay_to(ctx: RunContext, *, account_id: int) -> dict[str, Any] | None:
    from app.actions.attempt import OperationError
    from app.integrations.sei.actions import sei_account_pay_to

    try:
        attempt = await sei_account_pay_to(
            ctx.session, payload={"tenant_id": ctx.tenant_id, "account_id": account_id}, subjects=[]
        )
    except OperationError as exc:
        if exc.permanent:
            _refusal_evidence(ctx, exc)
            raise Permanent(str(exc)) from exc
        raise NotReady(str(exc), delay=15) from exc
    if attempt.skipped:
        raise Skip("No bank details on file; no pay-to to create.")
    ctx.evidence(
        method=attempt.method, url=attempt.url, request=attempt.request, response=attempt.response
    )
    return cast("dict[str, Any] | None", attempt.response)


@action(
    key="sei.attach_model",
    name="Attach SEI model",
    on_failure="needs_attention",
    wait_timeout_seconds=600,
    idempotent=True,
)
async def sei_attach_model(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    from app.actions.attempt import OperationError
    from app.integrations.sei.actions import sei_attach_model as handler

    try:
        attempt = await handler(
            ctx.session,
            payload={"tenant_id": ctx.tenant_id, "account_id": account_id},
            subjects=[],
        )
    except OperationError as exc:
        if exc.permanent:
            raise Permanent(str(exc)) from exc
        # The account is still inside enrolment; rest the run and poll again — SEI assigns the
        # model once it reports the account Open, exactly as pay-to and currencies wait.
        raise NotReady(str(exc), delay=15) from exc
    ctx.evidence(
        method=attempt.method, url=attempt.url, request=attempt.request, response=attempt.response
    )
    return cast(dict[str, Any], attempt.response)


@action(
    key="sei.currencies",
    name="Add SEI currencies",
    on_failure="needs_attention",
    wait_timeout_seconds=600,
    idempotent=True,
)
async def sei_currencies(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    from app.actions.attempt import OperationError
    from app.integrations.sei.actions import sei_account_currencies

    try:
        attempt = await sei_account_currencies(
            ctx.session, payload={"tenant_id": ctx.tenant_id, "account_id": account_id}, subjects=[]
        )
    except OperationError as exc:
        if exc.permanent:
            _refusal_evidence(ctx, exc)
            raise Permanent(str(exc)) from exc
        raise NotReady(str(exc), delay=15) from exc
    ctx.evidence(
        method=attempt.method, url=attempt.url, request=attempt.request, response=attempt.response
    )
    return cast(dict[str, Any], attempt.response)
