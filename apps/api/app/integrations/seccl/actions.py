"""Seccl steps for a firm action. The registry wires these; it does not own them."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.attempt import Attempt, Handler, OperationError
from app.actions.payload import _from_report, _ids, _sent, transfer_attempt


async def create_seccl_client(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """Onboarding's first Seccl step. The account step reads the link this writes."""
    del subjects
    from app.integrations.seccl.enrol import create_client

    tenant_id = payload.get("tenant_id")
    contact_id = payload.get("contact_id")
    if contact_id is None and payload.get("account_id") is not None and tenant_id is not None:
        contact_id = (
            await session.execute(
                text("SELECT contact_id FROM platform.accounts WHERE id = :a AND tenant_id = :t"),
                {"a": int(payload["account_id"]), "t": int(tenant_id)},
            )
        ).scalar_one_or_none()
    if contact_id is None or tenant_id is None:
        raise OperationError("Creating a Seccl client needs the client.", permanent=True)
    created = await create_client(session, tenant_id=int(tenant_id), contact_id=int(contact_id))
    return Attempt(
        ok=True,
        skipped=created.get("skipped") == "already_linked",
        method="POST",
        url="/client",
        request=created.get("request"),
        response=created,
    )


async def create_seccl_account(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del subjects
    from app.integrations.seccl.enrol import create_account

    account_id = payload.get("account_id")
    tenant_id = payload.get("tenant_id")
    if account_id is None or tenant_id is None:
        raise OperationError("Creating a Seccl account needs the account.", permanent=True)
    created = await create_account(session, tenant_id=int(tenant_id), account_id=int(account_id))
    return Attempt(
        ok=True,
        skipped=created.get("skipped") == "already_linked",
        method="POST",
        url="/account",
        request=created.get("request"),
        response=created,
    )


def _seccl_client_part(part: str) -> Handler:
    async def handler(
        session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
    ) -> Attempt:
        del subjects
        from app.integrations.seccl.instruct import update_client

        tenant_id, account_id, contact_id = await _ids(payload)
        if contact_id is None and account_id is not None:
            contact_id = (
                await session.execute(
                    text(
                        "SELECT contact_id FROM platform.accounts WHERE id = :a AND tenant_id = :t"
                    ),
                    {"a": account_id, "t": tenant_id},
                )
            ).scalar_one_or_none()
        if contact_id is None:
            raise OperationError("Updating a Seccl client needs the client.", permanent=True)
        if part == "circumstances":
            from app.integrations.seccl.instruct import update_circumstances

            updated = await update_circumstances(
                session, tenant_id=tenant_id, contact_id=int(contact_id)
            )
            return _sent(updated, method="PATCH", url=str(updated.get("url") or "/client"))
        updated = await update_client(
            session, tenant_id=tenant_id, contact_id=int(contact_id), part=part
        )
        return _sent(updated, method="PATCH", url="/client")

    return handler


async def seccl_account_bank(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """Write the account's own bank account at Seccl. Seccl holds account-level bank details in the
    same shape as the client's, so this is a partial ``PATCH /account`` with ``bankDetails``."""
    del subjects
    from app.integrations.seccl.instruct import set_account_bank

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError("Setting the account bank needs the account.", permanent=True)
    result = await set_account_bank(session, tenant_id=tenant_id, account_id=account_id)
    if result.get("skipped"):
        return Attempt(ok=True, skipped=True, method="PATCH", url="/account", response=result)
    return _sent(result, method="PATCH", url="/account")


async def update_seccl_account(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del subjects
    from app.integrations.seccl.instruct import update_account

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError("Updating a Seccl account needs the account.", permanent=True)
    updated = await update_account(session, tenant_id=tenant_id, account_id=account_id)
    return _sent(updated, method="PATCH", url="/account")


async def _booked(
    session: AsyncSession, payload: dict[str, Any], *, kinds: set[str], missing: str, url: str
) -> Attempt:
    from app.integrations.seccl.instruct import send_booked

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError(missing, permanent=True)
    named = payload.get("instruction_uid")
    report = await send_booked(
        session,
        tenant_id=tenant_id,
        account_id=account_id,
        kinds=kinds,
        missing=missing,
        instruction_uid=named if isinstance(named, str) and named else None,
    )
    return _from_report(report, method="POST", url=url)


async def seccl_payment(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del subjects
    missing = "Book the payment on the account first. This action sends that row."
    try:
        return await _booked(
            session,
            payload,
            kinds={"expected_in", "model_topup", "contribution", "personal_contribution"},
            missing=missing,
            url="/portfoliotransactiongroup",
        )
    except OperationError as exc:
        if str(exc) != missing:
            raise
    return await _book_initial_cash(session, payload, missing=missing)


async def _book_initial_cash(
    session: AsyncSession, payload: dict[str, Any], *, missing: str
) -> Attempt:
    """The journey stores an opening amount and does not book an instruction. Book it, then send."""
    from app import instructions
    from app.integrations.seccl.instruct import initial_cash_amount

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError(missing, permanent=True)
    row = (
        (
            await session.execute(
                text(
                    "SELECT data, payments FROM platform.accounts WHERE id = :a AND tenant_id = :t"
                ),
                {"a": account_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    amount = initial_cash_amount(row["data"], row["payments"]) if row else None
    if amount is None:
        raise OperationError(missing, permanent=True)
    existing = (
        await session.execute(
            text(
                "SELECT 1 FROM platform.transactions "
                "WHERE tenant_id = :t AND account_id = :a AND source_system = 'graphene' "
                "  AND data->>'origin_kind' = 'expected_in' AND status <> 'cancelled' "
                "LIMIT 1"
            ),
            {"t": tenant_id, "a": account_id},
        )
    ).first()
    if existing is not None:
        return Attempt(
            ok=True,
            skipped=True,
            method="POST",
            url="/portfoliotransactiongroup",
            response={"skipped": "Already instructed"},
        )
    try:
        created = await instructions.create_instruction(
            session,
            tenant_id=tenant_id,
            created_by_workos=None,
            kind="expected_in",
            account_id=account_id,
            amount=amount,
        )
    except (ValueError, LookupError) as exc:
        raise OperationError(str(exc), permanent=True) from exc
    return await _booked(
        session,
        {**payload, "instruction_uid": str(created["uid"])},
        kinds={"expected_in"},
        missing=missing,
        url="/portfoliotransactiongroup",
    )


async def seccl_withdrawal(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del subjects
    return await _booked(
        session,
        payload,
        kinds={"withdrawal", "model_withdrawal"},
        missing="Book the withdrawal on the account first. This action sends that row.",
        url="/portfoliotransactiongroup",
    )


async def seccl_transfer(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """Place the transfer already raised on the account. The workbench reads that row's status."""
    del subjects
    from app import transfers
    from app.integrations.seccl.instruct import send_booked

    missing = "Raise the transfer on the account first. This action sends that row."
    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError(missing, permanent=True)
    # The door names the transfer. That row is the instruction; it is not an
    # outbound payment, and a transfer with no value has no payment row at all.
    named_transfer = payload.get("uid")
    if isinstance(named_transfer, str) and named_transfer:
        record = await transfers.get_transfer(session, tenant_id=tenant_id, uid=named_transfer)
        if record is None:
            raise OperationError(missing, permanent=True)
        report = await transfers.instruct_raised(session, tenant_id=tenant_id, record=record)
        return transfer_attempt(report, method="POST", url="/portfoliotransactiongroup")
    named = payload.get("instruction_uid")
    report = await send_booked(
        session,
        tenant_id=tenant_id,
        account_id=account_id,
        kinds={"transfer"},
        missing=missing,
        instruction_uid=named if isinstance(named, str) and named else None,
    )
    if isinstance(report, dict) and ("route" in report or "seccl_placed" in report):
        return transfer_attempt(report, method="POST", url="/portfoliotransactiongroup")
    return _from_report(report, method="POST", url="/portfoliotransactiongroup")


async def seccl_adhoc_fee(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del subjects
    return await _booked(
        session,
        payload,
        kinds={"adhoc_fee"},
        missing="Book the fee on the account first. This action sends that row.",
        url="/portfoliotransactiongroup",
    )


async def seccl_regular_payment(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del subjects
    from app.integrations.seccl.instruct import set_standing

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError("A standing payment needs the account.", permanent=True)
    updated = await set_standing(session, tenant_id=tenant_id, account_id=account_id, movement="In")
    return _sent(updated, method="PATCH", url="/account")


async def seccl_regular_withdrawal(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del subjects
    from app.integrations.seccl.instruct import set_standing

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError("A standing withdrawal needs the account.", permanent=True)
    updated = await set_standing(
        session, tenant_id=tenant_id, account_id=account_id, movement="Out"
    )
    return _sent(updated, method="PATCH", url="/account")


async def seccl_attach_model(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del subjects
    from app.integrations.seccl.instruct import attach_model

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError("Attaching a model needs the account.", permanent=True)
    updated = await attach_model(session, tenant_id=tenant_id, account_id=account_id)
    return Attempt(ok=True, method="POST", url="/event/", response=updated)


async def seccl_remove_model(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del session, payload, subjects
    from app.integrations.seccl.instruct import _MODEL_REMOVE

    raise OperationError(_MODEL_REMOVE, permanent=True)


async def seccl_attach_fee(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del subjects
    from app.integrations.seccl.instruct import attach_fee

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError("Attaching a fee needs the account.", permanent=True)
    updated = await attach_fee(session, tenant_id=tenant_id, account_id=account_id)
    return _sent(updated, method="PATCH", url="/account")


async def seccl_illustration(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del subjects
    from app.integrations.seccl.instruct import illustrate

    tenant_id, account_id, _contact = await _ids(payload)
    if account_id is None:
        raise OperationError("An illustration needs the account.", permanent=True)
    created = await illustrate(session, tenant_id=tenant_id, account_id=account_id)
    return _sent(created, method="POST", url="/illustration/product")


async def seccl_drawdown(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del session, payload, subjects
    from app.integrations.seccl.instruct import _DRAWDOWN

    raise OperationError(_DRAWDOWN, permanent=True)


async def seccl_trade(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del session, payload, subjects
    from app.integrations.seccl.instruct import _TRADE

    raise OperationError(_TRADE, permanent=True)
