"""Resolution leaves — the book reads that decide which provider an action goes to.

A composite body is deterministic and has no database session, so any read that steers it is a
leaf and therefore a stored, inspectable child run. These wrap the resolvers the old engine used
(app.actions.runs), so the choice is made exactly as before.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from baton import Permanent, RunContext, action

_CUSTODIANS = ("sei", "seccl", "morgan-lloyd")


async def guard_seccl(ctx: RunContext, coro: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """Run a Seccl provider call, translating a permanent OperationError into baton.Permanent so a
    refused mutation is not retried (classify_leaf only recognises baton errors; a plain
    OperationError reads as retryable). Records the refusal as evidence — the money leaves get this
    from ``_adapt``. A non-permanent refusal is re-raised to retry, as before."""
    from app.actions.attempt import OperationError

    try:
        return await coro
    except OperationError as exc:
        ctx.evidence(
            method=exc.method,
            url=exc.url,
            request=exc.request,
            response=exc.response,
            status_code=exc.status_code,
        )
        if exc.permanent:
            raise Permanent(str(exc)) from exc
        raise


@action(key="resolve.contact_provider", name="Resolve provider", idempotent=True)
async def resolve_contact_provider(
    ctx: RunContext, *, contact_id: int, providers: tuple[str, ...] = _CUSTODIANS
) -> str:
    """The provider a client-level action goes to: the contact's link, else its account's
    custodian, else the firm's only enabled platform — the old ``_provider_for_contact``."""
    from app.actions.runs import ActionError, _provider_for_contact

    try:
        return await _provider_for_contact(
            ctx.session,
            tenant_id=ctx.tenant_id,
            contact_id=contact_id,
            providers=tuple(providers),
        )
    except ActionError as exc:
        # An ambiguous or missing provider is not something a retry fixes.
        raise Permanent(str(exc)) from exc


@action(key="load.account_book", name="Load account book", idempotent=True)
async def load_account_book(ctx: RunContext, *, account_id: int) -> dict[str, object]:
    """The book an account-level action reads to choose its provider(s): the custodian the
    instruction path already uses, and the scheme administrator when the product names one."""
    from app.actions.runs import _load_account
    from app.actions.setup import _scheme_administrator

    account = await _load_account(ctx.session, tenant_id=ctx.tenant_id, account_id=account_id)
    if account is None or not account.get("custodian"):
        raise Permanent("That account has no custodian on the book.")
    scheme = await _scheme_administrator(ctx.session, tenant_id=ctx.tenant_id, account=account)
    return {
        "custodian": str(account["custodian"]),
        "scheme_administrator": scheme,
        "contact_id": account.get("contact_id"),
        "product_type": account.get("product_type"),
    }
