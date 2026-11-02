"""Client actions on baton.

Each firm-facing action (``client.details`` and the rest) is a composite: it resolves the
client's provider, then runs that provider's leaf. The provider leaves (``seccl.update_client``,
and SEI / Morgan Lloyd as they are migrated) are the single calls; dispatch is by key, so a
provider lights up for every composite as soon as its leaf is registered.
"""

from __future__ import annotations

from typing import Any, cast

from baton import Permanent, RunContext, Skip, action

from app.actions.defs.resolve import guard_seccl, resolve_contact_provider

_CLIENT_META = {
    "catalogue": True,
    "category": "clients",
    "kind": "composite",
    "providers": ["sei", "seccl", "morgan-lloyd"],
}


# ---- provider leaves --------------------------------------------------------


@action(key="seccl.update_client", name="Update Seccl client", retries=2, idempotent=True)
async def seccl_update_client(ctx: RunContext, *, contact_id: int, part: str) -> dict[str, Any]:
    """One Seccl client update. ``part`` selects which block (details, bank, vulnerability,
    circumstances) is sent — the same split the old ``_seccl_client_part`` handler made."""
    from app.integrations.seccl.instruct import update_circumstances, update_client

    coro = (
        update_circumstances(ctx.session, tenant_id=ctx.tenant_id, contact_id=contact_id)
        if part == "circumstances"
        else update_client(ctx.session, tenant_id=ctx.tenant_id, contact_id=contact_id, part=part)
    )
    updated = await guard_seccl(ctx, coro)
    ctx.evidence(
        method="PATCH",
        url=str(updated.get("url") or "/client"),
        request=updated.get("request"),
        response=updated,
    )
    return updated


@action(key="seccl.create_client", name="Create Seccl client", idempotent=True)
async def seccl_create_client(ctx: RunContext, *, contact_id: int) -> dict[str, Any]:
    """Create the client at Seccl under the lead adviser's node. ``create_client`` self-guards:
    a second create returns ``already_linked``, which we skip."""
    from app.integrations.seccl.enrol import create_client

    created = await create_client(ctx.session, tenant_id=ctx.tenant_id, contact_id=contact_id)
    if created.get("skipped") == "already_linked":
        raise Skip("The client is already at Seccl.")
    ctx.evidence(method="POST", url="/client", request=created.get("request"), response=created)
    return created


# ---- composites -------------------------------------------------------------


async def _update_client(ctx: RunContext, *, contact_id: int, part: str) -> dict[str, Any]:
    provider = await ctx.run(resolve_contact_provider, contact_id=contact_id)
    # Only these providers have an update_client leaf. Any other resolves to an unregistered action
    # key; refuse cleanly the way client.create does rather than let the registry raise opaquely.
    if provider not in {"seccl", "morgan-lloyd"}:
        raise Permanent(f"client update is not available for {provider}.")
    result = await ctx.run(f"{provider}.update_client", contact_id=contact_id, part=part)
    return cast(dict[str, Any], result)


@action(
    key="client.details",
    name="Update client details",
    subject_kinds=("contact",),
    meta={
        **_CLIENT_META,
        "event_type": "client.details_updated",
        "reads": "The client's name, email, phone, address, date of birth, and tax identity.",
    },
)
async def client_details(ctx: RunContext, *, contact_id: int) -> dict[str, Any]:
    return await _update_client(ctx, contact_id=contact_id, part="details")


@action(
    key="client.bank",
    name="Update client bank account",
    subject_kinds=("contact",),
    meta={
        **_CLIENT_META,
        "event_type": "client.bank_updated",
        "holds_record": True,
        "reads": "The bank account on the client.",
    },
)
async def client_bank(ctx: RunContext, *, contact_id: int) -> dict[str, Any]:
    return await _update_client(ctx, contact_id=contact_id, part="bank")


@action(
    key="client.vulnerability",
    name="Update client vulnerability",
    subject_kinds=("contact",),
    meta={
        **_CLIENT_META,
        "event_type": "client.vulnerability_updated",
        "reads": "The vulnerability flag, reasons, and notes on the client.",
    },
)
async def client_vulnerability(ctx: RunContext, *, contact_id: int) -> dict[str, Any]:
    return await _update_client(ctx, contact_id=contact_id, part="vulnerability")


@action(
    key="client.circumstances",
    name="Update client circumstances",
    subject_kinds=("contact",),
    meta={
        **_CLIENT_META,
        "event_type": "client.circumstances_updated",
        "reads": "Pension details, employment, source of wealth, and tax relief.",
    },
)
async def client_circumstances(ctx: RunContext, *, contact_id: int) -> dict[str, Any]:
    return await _update_client(ctx, contact_id=contact_id, part="circumstances")


@action(
    key="client.create",
    name="Create client",
    subject_kinds=("contact",),
    one_open_per_subject=True,
    meta={
        **_CLIENT_META,
        "event_type": "client.created",
        "reads": "The client record, and the firm's Seccl node.",
    },
)
async def client_create(ctx: RunContext, *, contact_id: int) -> dict[str, Any]:
    provider = await ctx.run(resolve_contact_provider, contact_id=contact_id)
    if provider == "seccl":
        created = await ctx.run("seccl.create_client", contact_id=contact_id)
        return cast(dict[str, Any], created)
    # Only Seccl opens a client as a separate first step. SEI enrols the client as part of
    # account.create (sei.enrol); Morgan Lloyd syncs it after. So a non-Seccl client.create has
    # nothing to do here, and it must NOT fail — client.setup must not block before account.create
    # runs. This body has already called resolve.contact_provider, so the run is a COMPOSITE: a
    # composite signals "nothing to do" by RETURNING, never by raising Skip. Skip is honoured only
    # in a leaf attempt; raised from a composite it falls through to the run-failed path — which is
    # exactly what failed client.setup against SEI.
    return {"deferred": provider, "detail": "created when the account is opened, not as a step"}
