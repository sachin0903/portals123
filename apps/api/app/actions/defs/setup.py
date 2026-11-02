"""client.setup on baton — the onboarding plan, as a composite of the other actions.

It reads the application (every person and account), creates each client, opens each account, and
attaches the money, model and scheme-administrator sync each account captured. Per-child firm
policy (switched off, or held for approval) is applied by the engine's before_child hook, so a
held child shows in the inbox on its own — no separate union-of-roles hold.

All three providers run here: Seccl opens the client then the account, SEI enrols both in one
call and then waits for the account to be Open before the pay-to and currencies (``sei.enrol``,
``sei.pay_to``, ``sei.currencies`` are durable waits), and Morgan Lloyd is synced after the account
when it administers the scheme.
"""

from __future__ import annotations

from typing import Any, cast

from baton import Permanent, RunContext, action


@action(key="load.plan", name="Load onboarding plan", idempotent=True)
async def load_plan(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    """The people and accounts this application sets up, with each account's scheme administrator
    and the money/model/fee it captured. Reuses the existing household loader."""
    from app.actions.household import load_household
    from app.actions.setup import _intents, _scheme_administrator
    from app.providers import attach_scheme_administrators

    household = await load_household(ctx.session, tenant_id=ctx.tenant_id, account_id=account_id)
    if household is None:
        raise Permanent("That account has no household to set up.")
    accounts: list[dict[str, Any]] = []
    for item in household["accounts"]:
        product_id = item.get("product_id")
        if product_id is not None:
            await attach_scheme_administrators(
                ctx.session, ctx.tenant_id, product_id=int(product_id)
            )
        scheme = await _scheme_administrator(ctx.session, tenant_id=ctx.tenant_id, account=item)
        accounts.append(
            {
                "id": item["id"],
                "uid": item["uid"],
                "contact_id": item.get("contact_id"),
                "custodian": item.get("custodian"),
                "scheme_administrator": scheme,
                "intents": sorted(_intents(item.get("data"), {}, item)),
            }
        )
    clients = [
        {"contact_id": party["contact_id"], "uid": party["uid"]} for party in household["parties"]
    ]
    return {
        "application_uid": household.get("application_uid"),
        "clients": clients,
        "accounts": accounts,
    }


@action(
    key="client.setup",
    name="Set up client and accounts",
    subject_kinds=("account", "contact"),
    meta={
        "catalogue": True,
        "category": "clients",
        "kind": "plan",
        "providers": ["seccl", "sei", "morgan-lloyd"],
        "event_type": "client.setup_completed",
        "reads": "The application: each person and every account they hold, and whatever money "
        "each account captured.",
    },
)
async def client_setup(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    plan = await ctx.run(load_plan, account_id=account_id)

    # Seccl opens the client before the account, so every client is created first.
    for client in plan["clients"]:
        await ctx.run(
            "client.create",
            key=f"client.create:{client['uid']}",
            contact_id=client["contact_id"],
        )

    for account in plan["accounts"]:
        uid, aid = account["uid"], account["id"]
        intents = set(account["intents"])
        await ctx.run("account.create", key=f"account.create:{uid}", account_id=aid)
        if account.get("scheme_administrator") == "morgan-lloyd" and account.get("contact_id"):
            await ctx.run(
                "client.sync",
                key=f"client.sync:{uid}",
                contact_id=account["contact_id"],
                account_id=aid,
                enrol=True,
            )
        if "model" in intents:
            await ctx.run("model.attach", key=f"model.attach:{uid}", account_id=aid)
        if "payment" in intents:
            await ctx.run("transaction.payment", key=f"transaction.payment:{uid}", account_id=aid)

    return cast(
        dict[str, Any],
        {
            "application_uid": plan["application_uid"],
            "clients": len(plan["clients"]),
            "accounts": len(plan["accounts"]),
        },
    )
