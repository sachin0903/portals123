"""Open an account by enqueueing ``client.setup`` and draining the steps we can send.

The run engine records and executes steps. Which clients and accounts a setup
covers, and what "skipped" means when the session cannot load them, lives here.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import regular_movements
from app.actions.household import load_household


def _json_bag(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _intents(data: Any, config: dict[str, Any], account: dict[str, Any] | None = None) -> set[str]:
    bag = _json_bag(data)
    row = account or {}
    found: set[str] = set()
    if config.get("model_id") or bag.get("model_id") or row.get("model_id"):
        found.add("model")
    if config.get("fee_package_id") or bag.get("fee_package_id"):
        found.add("fee")
    if bag.get("has_transfer") or bag.get("transfer") or row.get("transfer"):
        found.add("transfer")
    if (
        bag.get("initial_payment")
        or bag.get("payment")
        or regular_movements.initial_cash_deposit_of(data, row.get("payments"))
    ):
        found.add("payment")
    if (
        bag.get("regular_contribution")
        or bag.get("regular_payment")
        or regular_movements.regular_movements_of(row.get("payments"))
    ):
        found.add("regular_payment")
    if bag.get("regular_withdrawal") or regular_movements.regular_movements_of(
        row.get("withdrawals")
    ):
        found.add("regular_withdrawal")
    if bag.get("beneficiaries"):
        found.add("beneficiaries")
    return found


async def _fill_custodians(
    session: AsyncSession, tenant_id: int, accounts: list[dict[str, Any]]
) -> None:
    """Copy a custodian the attach just wrote onto accounts the plan already loaded.

    ``load_household`` runs first. An account that had no custodian, and whose
    product now names exactly one, is updated on the row; the in-memory plan
    still holds the empty value unless it is read back.
    """
    pending = [
        account for account in accounts if not account.get("custodian") and account.get("id")
    ]
    if not pending:
        return
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, custodian::text AS custodian FROM platform.accounts "
                    "WHERE tenant_id = :t AND id = ANY(:ids)"
                ),
                {"t": tenant_id, "ids": [int(account["id"]) for account in pending]},
            )
        )
        .mappings()
        .all()
    )
    found = {int(row["id"]): row["custodian"] for row in rows}
    for account in pending:
        stamped = found.get(int(account["id"]))
        if stamped:
            account["custodian"] = stamped


async def _scheme_administrator(
    session: AsyncSession, *, tenant_id: int, account: dict[str, Any]
) -> str | None:
    from app.providers import administered, administrators

    if not administered(account.get("product_type")):
        return None
    product_id = account.get("product_id")
    if product_id is None:
        return None
    provider = (
        await session.execute(
            text(
                "SELECT pp.provider::text FROM platform.product_platforms pp "
                "WHERE pp.product_id = :p AND pp.tenant_id = :t "
                "  AND pp.provider = ANY(:admins) "
                "  AND COALESCE(pp.enabled, true) "
                "ORDER BY (pp.role = 'scheme_administrator') DESC NULLS LAST "
                "LIMIT 1"
            ),
            {"t": tenant_id, "p": product_id, "admins": list(administrators())},
        )
    ).scalar_one_or_none()
    return str(provider) if provider else None


_SETUP_SKIPPED = {
    "status": "succeeded",
    "run_uid": None,
    "platform_sync": {
        "administrator": {"status": "skipped"},
        "custodian": {"status": "pending_ops"},
    },
}


async def _settle_on_baton(
    session: AsyncSession,
    *,
    tenant_id: int,
    account_id: int,
) -> dict[str, Any] | None:
    """Start ``client.setup`` on baton for the journey, when the firm has flipped the flag. Returns
    ``None`` to fall through to the legacy engine.

    One plan opens every account on the application, so the run is keyed on the application: the
    other account's ``setup_account`` step attaches to the same run (``start`` returns it rather
    than making a second), and both steps record its uid. The worker advances the run; the
    ``on_run_terminal`` hook then releases every step that recorded it. The book's custodian and
    scheme-administrator mappings are connected here first, so ``load.plan`` reads a sendable plan;
    nothing is drained inline, so a freshly started run is always ``waiting``."""
    from app.actions import service
    from app.providers import attach_custodians, attach_scheme_administrators

    household = await load_household(session, tenant_id=tenant_id, account_id=account_id)
    if household is None:
        return dict(_SETUP_SKIPPED)
    account = next(
        (item for item in household["accounts"] if item["id"] == account_id),
        household["accounts"][0] if household["accounts"] else None,
    )
    if account is None:
        return dict(_SETUP_SKIPPED)
    seen_products: set[int] = set()
    for item in household["accounts"]:
        product_id = item.get("product_id")
        if product_id is None or int(product_id) in seen_products:
            continue
        seen_products.add(int(product_id))
        await attach_scheme_administrators(session, tenant_id, product_id=int(product_id))
        await attach_custodians(session, tenant_id, product_id=int(product_id))
    admin = await _scheme_administrator(session, tenant_id=tenant_id, account=account)
    subjects: list[dict[str, Any]] = []
    seen_contacts: set[str] = set()
    for party in household["parties"]:
        uid = party.get("uid")
        if not uid or uid in seen_contacts:
            continue
        seen_contacts.add(uid)
        subjects.append(
            {"entity_type": "contact", "entity_uid": uid, "entity_id": party.get("contact_id")}
        )
    for item in household["accounts"]:
        if item.get("uid"):
            subjects.append(
                {"entity_type": "account", "entity_uid": item["uid"], "entity_id": item["id"]}
            )
    application_uid = household.get("application_uid")
    idem = (
        f"client.setup:application:{application_uid}"
        if application_uid
        else f"client.setup:account:{account['uid']}"
    )
    result = await service.start(
        session,
        action_key="client.setup",
        tenant_id=tenant_id,
        params={"account_id": account_id},
        idempotency_key=idem,
        requested_by=None,
        role_ids=None,
        subjects=subjects,
        contact_id=int(account["contact_id"]) if account.get("contact_id") else None,
    )
    state = result.run.state
    waiting = state.type.value not in {"completed", "failed", "cancelled"}
    run_status = (
        "awaiting_approval"
        if state.type.value == "paused"
        else ("queued" if waiting else "succeeded")
    )
    administrator: dict[str, Any] = {"status": "skipped"}
    if admin == "morgan-lloyd":
        administrator = {
            "provider": "morgan-lloyd",
            "status": "queued" if waiting else "synced",
        }
    return {
        "status": "waiting" if waiting else "succeeded",
        "run_uid": result.run.uid,
        "run_status": run_status,
        "platform_sync": {
            "administrator": administrator,
            "custodian": {"status": "pending_ops"},
            "action_run_uid": result.run.uid,
        },
    }


async def settle_account_setup(
    session: AsyncSession,
    *,
    tenant_id: int,
    account_id: int,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enqueue ``client.setup`` for an activated account and drain what we can."""
    result = await _settle_on_baton(session, tenant_id=tenant_id, account_id=account_id)
    return result if result is not None else dict(_SETUP_SKIPPED)
