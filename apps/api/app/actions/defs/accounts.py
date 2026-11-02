"""Account actions on baton.

``account.create`` and ``account.details`` are composites: they read the account's book, then run
the custodian's leaf. The Seccl leaves are single calls (its create self-guards against a second
open). SEI's account open is a chain of its own and is migrated separately.
"""

from __future__ import annotations

from typing import Any, cast

from baton import Permanent, RunContext, Skip, action

from app.actions.defs.resolve import guard_seccl, load_account_book

_ACCOUNT_META = {
    "catalogue": True,
    "category": "accounts",
    "kind": "composite",
    "providers": ["sei", "seccl", "morgan-lloyd"],
}


# ---- provider leaves --------------------------------------------------------


@action(key="seccl.create_account", name="Create Seccl account", idempotent=True)
async def seccl_create_account(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    """Open the Seccl wrapper. The client must already be linked (client.create ran first).
    ``create_account`` self-guards: a second open returns ``already_linked``, which we skip."""
    from app.integrations.seccl.enrol import create_account

    created = await create_account(ctx.session, tenant_id=ctx.tenant_id, account_id=account_id)
    if created.get("skipped") == "already_linked":
        raise Skip("The account is already open at Seccl.")
    ctx.evidence(method="POST", url="/account", request=created.get("request"), response=created)
    return created


@action(key="seccl.update_account", name="Update Seccl account", retries=2, idempotent=True)
async def seccl_update_account(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    from app.integrations.seccl.instruct import update_account

    updated = await guard_seccl(
        ctx, update_account(ctx.session, tenant_id=ctx.tenant_id, account_id=account_id)
    )
    ctx.evidence(method="PATCH", url="/account", request=updated.get("request"), response=updated)
    return updated


# ---- composites -------------------------------------------------------------


@action(
    key="account.create",
    name="Open account",
    subject_kinds=("account",),
    one_open_per_subject=True,
    meta={
        **_ACCOUNT_META,
        "event_type": "account.created",
        "reads": "The account, its product, and — when set — the model and scheme product.",
    },
)
async def account_create(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    book = await ctx.run(load_account_book, account_id=account_id)
    custodian = book["custodian"]
    if custodian == "seccl":
        created = await ctx.run("seccl.create_account", account_id=account_id)
        return cast(dict[str, Any], created)
    if custodian == "sei":
        # One enrolment opens the client and the account; the pay-to and currencies follow once
        # SEI reports the account Open (each a durable wait inside its leaf).
        opened = await ctx.run("sei.enrol", account_ids=[account_id])
        await ctx.run("sei.pay_to", account_id=account_id)
        await ctx.run("sei.currencies", account_id=account_id)
        return cast(dict[str, Any], opened)
    raise Permanent(f"account.create is not available for {custodian}.")


@action(
    key="account.details",
    name="Update account details",
    subject_kinds=("account",),
    meta={
        **_ACCOUNT_META,
        "providers": ["sei", "seccl"],
        "event_type": "account.details_updated",
        "reads": "The account's name. Currency is set when the account is opened.",
    },
)
async def account_details(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    book = await ctx.run(load_account_book, account_id=account_id)
    custodian = book["custodian"]
    if custodian == "seccl":
        updated = await ctx.run("seccl.update_account", account_id=account_id)
        return cast(dict[str, Any], updated)
    raise Permanent(f"account.details is not available for {custodian}.")


@action(key="seccl.attach_model", name="Attach Seccl model", retries=2, idempotent=True)
async def seccl_attach_model(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    from app.integrations.seccl.instruct import attach_model

    updated = await guard_seccl(
        ctx, attach_model(ctx.session, tenant_id=ctx.tenant_id, account_id=account_id)
    )
    ctx.evidence(method="POST", url="/event/", response=updated)
    return updated


@action(
    key="model.attach",
    name="Attach model",
    subject_kinds=("account",),
    meta={
        "catalogue": True,
        "category": "models",
        "kind": "composite",
        "providers": ["sei", "seccl"],
        "event_type": "account.model_updated",
        "reads": "The account's model. SEI is assigned it once the account is Open.",
    },
)
async def model_attach(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    book = await ctx.run(load_account_book, account_id=account_id)
    if book["custodian"] == "seccl":
        updated = await ctx.run("seccl.attach_model", account_id=account_id)
        return cast(dict[str, Any], updated)
    if book["custodian"] == "sei":
        updated = await ctx.run("sei.attach_model", account_id=account_id)
        return cast(dict[str, Any], updated)
    raise Permanent(f"model.attach is not available for {book['custodian']}.")


@action(
    key="account.pay_to",
    name="Create pay-to profile",
    subject_kinds=("account",),
    meta={
        **_ACCOUNT_META,
        "providers": ["sei"],
        "event_type": "account.pay_to_created",
        "reads": "The pay-to profile on the account at SEI, created from the bank details on file.",
    },
)
async def account_pay_to(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    # The firm-facing "where the account's money is paid to". At SEI this is a pay-to profile; a
    # Seccl account pays from the client's bank, so it has no account-level equivalent.
    book = await ctx.run(load_account_book, account_id=account_id)
    if book["custodian"] != "sei":
        raise Permanent("A pay-to profile is a SEI concept; this account is not at SEI.")
    placed = await ctx.run("sei.pay_to", account_id=account_id)
    return cast(dict[str, Any], placed or {})


@action(
    key="account.currencies",
    name="Permit account currencies",
    subject_kinds=("account",),
    meta={
        **_ACCOUNT_META,
        "providers": ["sei"],
        "event_type": "account.currencies_updated",
        "reads": "The currencies the firm permits on the account at SEI.",
    },
)
async def account_currencies(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    book = await ctx.run(load_account_book, account_id=account_id)
    if book["custodian"] != "sei":
        raise Permanent("Permitting currencies is a SEI concept; this account is not at SEI.")
    placed = await ctx.run("sei.currencies", account_id=account_id)
    return cast(dict[str, Any], placed or {})


# ---- fees, account bank, model removal --------------------------------------


@action(key="seccl.attach_fee", name="Attach Seccl fee", on_failure="needs_attention")
async def seccl_attach_fee(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    from app.actions.defs.money import _adapt
    from app.integrations.seccl.actions import seccl_attach_fee as handler

    return await _adapt(ctx, handler, {"account_id": account_id})


@action(key="sei.attach_fee", name="Attach SEI fee", on_failure="needs_attention")
async def sei_attach_fee(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    # Assign the account's ongoing fee package (feePackageProfileId) at SEI. The handler reads the
    # numeric accountId from retrieveAccountDetails and skips an already-assigned package.
    from app.actions.defs.money import _adapt
    from app.integrations.sei.actions import sei_attach_fee as handler

    return await _adapt(ctx, handler, {"account_id": account_id})


@action(
    key="fee.attach",
    name="Attach fee",
    subject_kinds=("account",),
    meta={
        **_ACCOUNT_META,
        "category": "fees",
        "providers": ["sei", "seccl"],
        "event_type": "account.fee_attached",
        "reads": "The account's fee package.",
    },
)
async def fee_attach(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    book = await ctx.run(load_account_book, account_id=account_id)
    custodian = book["custodian"]
    if custodian not in ("seccl", "sei"):
        raise Permanent(f"fee.attach is not available for {custodian}.")
    return cast(dict[str, Any], await ctx.run(f"{custodian}.attach_fee", account_id=account_id))


@action(key="seccl.account_bank", name="Set Seccl account bank", on_failure="needs_attention")
async def seccl_account_bank(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    from app.actions.defs.money import _adapt
    from app.integrations.seccl.actions import seccl_account_bank as handler

    return await _adapt(ctx, handler, {"account_id": account_id})


@action(
    key="account.bank",
    name="Set account bank",
    subject_kinds=("account",),
    meta={
        **_ACCOUNT_META,
        "providers": ["sei", "seccl"],
        "event_type": "account.bank_set",
        "reads": "The bank account money on this account is paid to (SEI: the pay-to profile).",
    },
)
async def account_bank(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    # The firm-facing account bank. Seccl supports an account-level bank (send not wired yet). SEI
    # has no single "account bank": money is paid to a pay-to profile (pay_from is a follow-up), so
    # the SEI path sets the pay-to profile.
    book = await ctx.run(load_account_book, account_id=account_id)
    custodian = book["custodian"]
    if custodian == "seccl":
        return cast(dict[str, Any], await ctx.run("seccl.account_bank", account_id=account_id))
    if custodian == "sei":
        return cast(dict[str, Any], await ctx.run("sei.pay_to", account_id=account_id) or {})
    raise Permanent(f"account.bank is not available for {custodian}.")


@action(
    key="model.remove",
    name="Remove model",
    subject_kinds=("account",),
    meta={
        **_ACCOUNT_META,
        "category": "models",
        "providers": ["sei"],
        "event_type": "account.model_removed",
        "reads": "The model mandate removed from the account.",
    },
)
async def model_remove(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    # Legitimate at SEI (implementation to follow); Seccl has no model-removal path.
    book = await ctx.run(load_account_book, account_id=account_id)
    if book["custodian"] == "sei":
        raise Permanent("Model removal at SEI is not implemented yet.")
    raise Permanent("Seccl has no model-removal path; reassign the model instead.")
