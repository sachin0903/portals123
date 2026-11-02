"""Booked money the action sends on. The screen writes the row; these handlers read it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.attempt import Attempt, OperationError

PAYMENT_MISSING = "Book the payment on the account first. This action sends that row."
PAYMENT_SEVERAL = "More than one payment is booked on this account. This action sends one row."
CONTRIBUTION_MISSING = "Book the contribution on the account first. This action records that row."
CONTRIBUTION_SEVERAL = (
    "More than one contribution is booked on this account. This action records one row."
)
TRANSFER_MISSING = "Raise the transfer on the account first. This action records that row."
TRANSFER_SEVERAL = "More than one transfer is booked on this account. This action records one row."

# A pending instruction whose outbound row is already submitted must not be sent again.
UNSENT_PAYMENTS = """
FROM platform.instructions i
JOIN platform.accounts a ON a.id = i.account_id AND a.tenant_id = i.tenant_id
WHERE i.tenant_id = :t AND i.account_id = :a
  AND i.kind = 'expected_in' AND i.status = 'pending'
  AND NOT EXISTS (
    SELECT 1 FROM platform.transactions tx
    WHERE tx.tenant_id = i.tenant_id
      AND tx.source_system = 'graphene'
      AND tx.source_transaction_id = i.uid
      AND tx.data ->> 'origin' = 'instruction'
      AND tx.status IN ('submitted', 'posted')
  )
"""


def select_named(
    rows: Sequence[Mapping[Any, Any]],
    *,
    named: str | None,
    missing: str,
    several: str,
) -> Mapping[Any, Any]:
    """The booked row this step sends. A step with no name sends the only one."""
    chosen = named or ""
    if chosen:
        match = next((row for row in rows if str(row["uid"]) == chosen), None)
        if match is None:
            raise OperationError(missing, permanent=True)
        return match
    if not rows:
        raise OperationError(missing, permanent=True)
    if len(rows) > 1:
        raise OperationError(several, permanent=True)
    return rows[0]


async def _booked_uid(
    session: AsyncSession,
    *,
    payload: dict[str, Any],
    sql: str,
    missing: str,
    several: str,
) -> str:
    """The workbench names the row. A payment or transfer run names the account."""
    named = payload.get("uid")
    if isinstance(named, str) and named:
        return named
    tenant_id = payload.get("tenant_id")
    account_id = payload.get("account_id")
    if tenant_id is None or account_id is None:
        raise OperationError(missing, permanent=True)
    found = (
        (await session.execute(text(sql), {"t": int(tenant_id), "a": int(account_id)}))
        .scalars()
        .all()
    )
    row = select_named(
        [{"uid": str(uid)} for uid in found],
        named=None,
        missing=missing,
        several=several,
    )
    return str(row["uid"])


async def lodge_contribution(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del subjects
    from app import contributions

    record = await contributions.get_contribution(
        session, tenant_id=int(payload["tenant_id"]), uid=str(payload["uid"])
    )
    if record is None:
        raise OperationError("Contribution not found.", permanent=True)
    report = await contributions.lodge_expectation(
        session, tenant_id=int(payload["tenant_id"]), contribution=record
    )
    ok = report.get("status") == "instructed"
    return Attempt(
        ok=ok,
        method="POST",
        url="contribution/lodge",
        response=report,
        error=None if ok else str(report.get("detail") or report.get("status") or ""),
    )


async def record_contribution(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del subjects
    from app import contributions
    from app.integrations import morgan_lloyd

    tenant_id = int(payload["tenant_id"])
    uid = await _booked_uid(
        session,
        payload=payload,
        sql=(
            "SELECT uid FROM platform.contributions "
            "WHERE tenant_id = :t AND account_id = :a "
            "  AND ml_contribution_id IS NULL AND status <> 'cancelled' "
            "ORDER BY id"
        ),
        missing=CONTRIBUTION_MISSING,
        several=CONTRIBUTION_SEVERAL,
    )
    record = await contributions.get_contribution(session, tenant_id=tenant_id, uid=uid)
    if record is None:
        raise OperationError("Contribution not found.", permanent=True)
    if record.get("custodian") != "sei" or not record.get("is_pension"):
        raise OperationError("Morgan Lloyd administers SEI SIPPs.", permanent=True)
    if record["ml_contribution_id"]:
        raise OperationError(
            "This contribution is already recorded in Morgan Lloyd.", permanent=True
        )
    context = await contributions.ml_context(session, tenant_id=tenant_id, contribution=record)
    sent_status = int(morgan_lloyd.build_contribution(context)["cam_status"])
    ml_id = await morgan_lloyd.create_contribution(context)
    saved = await contributions.mark_ml_recorded(
        session,
        tenant_id=tenant_id,
        uid=uid,
        ml_contribution_id=ml_id,
        ml_status=sent_status,
    )
    return Attempt(ok=True, method="POST", url="cam_createcontribution", response=saved)


async def record_transfer(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del subjects
    from app import transfers
    from app.integrations import morgan_lloyd

    tenant_id = int(payload["tenant_id"])
    uid = await _booked_uid(
        session,
        payload=payload,
        sql=(
            "SELECT uid FROM platform.transfers "
            "WHERE tenant_id = :t AND account_id = :a "
            "  AND ml_transfer_id IS NULL AND status <> 'cancelled' "
            "ORDER BY id"
        ),
        missing=TRANSFER_MISSING,
        several=TRANSFER_SEVERAL,
    )
    record = await transfers.get_transfer(session, tenant_id=tenant_id, uid=uid)
    if record is None:
        raise OperationError("Transfer not found.", permanent=True)
    if record.get("route") != "ml_sei":
        raise OperationError("Morgan Lloyd administers SEI SIPPs.", permanent=True)
    if record["ml_transfer_id"]:
        raise OperationError("This transfer is already recorded in Morgan Lloyd.", permanent=True)
    context = await transfers.ml_context(session, tenant_id=tenant_id, transfer=record)
    try:
        ml_id = await morgan_lloyd.create_transfer_at_ml(context)
    except Exception as exc:
        if not transfers._instruction_failure(exc):
            raise
        saved = await transfers.remember_provider_failure(
            session, tenant_id=tenant_id, uid=uid, message=str(exc)
        )
        return Attempt(
            ok=False,
            method="POST",
            url="cam_createtransfer",
            response=saved,
            error=str(exc),
        )
    saved = await transfers.mark_ml_recorded(
        session, tenant_id=tenant_id, uid=uid, ml_transfer_id=ml_id
    )
    return Attempt(ok=True, method="POST", url="cam_createtransfer", response=saved)


#: Instruction kind → the action that sends that booked row.
ACTION_FOR_KIND: dict[str, str] = {
    "expected_in": "transaction.payment",
    "model_topup": "transaction.payment",
    "withdrawal": "transaction.withdrawal",
    "model_withdrawal": "transaction.withdrawal",
    "adhoc_fee": "transaction.fee",
}

MONEY_DOOR_ACTIONS: tuple[str, ...] = (
    "transaction.payment",
    "transaction.withdrawal",
    "transaction.fee",
)


async def switched_off(session: AsyncSession, *, tenant_id: int, kind: str) -> bool:
    """True when a policy exists and the action that sends this kind is switched off.

    No policy row is not off: the door that already books the instruction keeps working.
    """
    action_key = ACTION_FOR_KIND.get(kind)
    if action_key is None:
        return False
    from app.actions.runs import _policy

    policy = await _policy(session, tenant_id=tenant_id, action_key=action_key, provider=None)
    return policy is not None and not bool(policy["enabled"])


async def requires_reason(session: AsyncSession, *, tenant_id: int, kind: str) -> bool:
    """True when this send's action asks for a reason. Approval and the reason are separate."""
    action_key = ACTION_FOR_KIND.get(kind)
    if action_key is None:
        return False
    from app.actions.runs import _policy

    policy = await _policy(session, tenant_id=tenant_id, action_key=action_key, provider=None)
    return policy is not None and bool(policy["require_justification"])


async def approval_holds(session: AsyncSession, *, tenant_id: int, kind: str) -> bool:
    """True when the send itself is the four-eye, so the reviews hold must not also fire."""
    action_key = ACTION_FOR_KIND.get(kind)
    if action_key is None:
        return False
    from app.actions.runs import _approval

    on, _roles = await _approval(session, tenant_id=tenant_id, action_key=action_key)
    return on


async def money_action_flags(
    session: AsyncSession, *, tenant_id: int
) -> tuple[list[str], list[str]]:
    """Send actions this firm has switched off, and those whose form must ask for a reason.

    A missing policy is neither: the control stays, and the form does not ask.
    """
    rows = (
        await session.execute(
            text(
                "SELECT action_key, enabled, require_justification FROM actions.policies "
                "WHERE tenant_id = :t AND provider IS NULL AND action_key = ANY(:keys) "
                "ORDER BY action_key"
            ),
            {"keys": list(MONEY_DOOR_ACTIONS), "t": tenant_id},
        )
    ).mappings()
    disabled: list[str] = []
    reasons: list[str] = []
    for row in rows.all():
        key = str(row["action_key"])
        if not row["enabled"]:
            disabled.append(key)
        if row["require_justification"]:
            reasons.append(key)
    return disabled, reasons


async def disabled_money_actions(session: AsyncSession, *, tenant_id: int) -> list[str]:
    """Action keys this firm has switched off. A missing policy is not in the list."""
    disabled, _reasons = await money_action_flags(session, tenant_id=tenant_id)
    return disabled


_NOT_BATON = object()


async def _instruction_input(session: AsyncSession, *, tenant_id: int, uid: str) -> dict[str, Any]:
    """A readable snapshot of what was booked, for the run detail's Input panel — so a run reads as
    the fields the adviser set (Withdrawal £1,000, an adviser fee, auto-disinvest) and not the
    opaque {account_id, instruction_uid} the action is called with."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT kind, amount::float8 AS amount, adviser_fee::float8 AS adviser_fee, "
                    "       auto_disinvest, due_date, category, payment_method "
                    "FROM platform.instructions WHERE tenant_id = :t AND uid = :u"
                ),
                {"t": tenant_id, "u": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return {}
    snapshot: dict[str, Any] = {"kind": row["kind"], "amount": float(row["amount"])}
    if row["adviser_fee"]:
        snapshot["adviser_fee"] = float(row["adviser_fee"])
    if row["auto_disinvest"]:
        snapshot["auto_disinvest"] = True
    if row["due_date"]:
        snapshot["due_date"] = str(row["due_date"])
    if row["category"]:
        snapshot["category"] = row["category"]
    if row["payment_method"]:
        snapshot["payment_method"] = row["payment_method"]
    return snapshot


#: The ceding-scheme lines a raise stores on ``platform.transfers.data`` (the form's own words;
#: the provider flags beside them — ``provider_error``, ``seccl_placed``, ``sei_accepted`` — are
#: outcomes, not input, and stay off the snapshot).
_TRANSFER_DATA_FIELDS: tuple[str, ...] = (
    "provider_address_line1",
    "provider_address_line2",
    "provider_city",
    "provider_postcode",
    "provider_country",
    "fca_reference",
    "external_account_type",
    "assets",
    "documents",
)


async def _transfer_input(session: AsyncSession, *, tenant_id: int, uid: str) -> dict[str, Any]:
    """Every field of the raise-a-transfer form, as the run's Input — so a transfer the custodian
    refused still carries the ceding provider, policy number, values, dates, the scheme's address
    and the SEI lines, and ops can set it up by hand at SEI without going back to the drawer. The
    run's params are only ``{account_id, uid, leg}``; this is what the adviser actually asked for.
    """
    from app.transfers import PENSION_TYPES

    row = (
        (
            await session.execute(
                text(
                    "SELECT t.ceding_provider, t.policy_number, t.transfer_type, t.basis, "
                    "       t.extent, t.crystallisation, t.pension_type, t.safeguarded_benefits, "
                    "       t.crystallised_value::float8 AS crystallised_value, "
                    "       t.uncrystallised_value::float8 AS uncrystallised_value, "
                    "       t.total_value::float8 AS total_value, t.currency, t.expected_date, "
                    "       t.note, t.data, "
                    "       a.name AS account_name, a.uid AS account_uid, "
                    "       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), "
                    "                c.name) AS client_name "
                    "FROM platform.transfers t "
                    "  LEFT JOIN platform.accounts a ON a.id = t.account_id "
                    "  LEFT JOIN crm.contacts c ON c.id = t.contact_id "
                    "WHERE t.tenant_id = :t AND t.uid = :u"
                ),
                {"t": tenant_id, "u": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return {}
    snapshot: dict[str, Any] = {}
    for key in (
        "client_name",
        "account_name",
        "account_uid",
        "ceding_provider",
        "policy_number",
        "transfer_type",
        "basis",
        "extent",
        "crystallisation",
    ):
        if row[key] not in (None, ""):
            snapshot[key] = row[key]
    if row["pension_type"] is not None:
        snapshot["pension_type"] = int(row["pension_type"])
        label = PENSION_TYPES.get(int(row["pension_type"]))
        if label:
            snapshot["pension_type_label"] = label
    if row["safeguarded_benefits"] is not None:
        snapshot["safeguarded_benefits"] = bool(row["safeguarded_benefits"])
    for key in ("crystallised_value", "uncrystallised_value", "total_value"):
        if row[key] is not None:
            snapshot[key] = float(row[key])
    if row["currency"]:
        snapshot["currency"] = row["currency"]
    if row["expected_date"]:
        snapshot["expected_date"] = str(row["expected_date"])
    if row["note"]:
        snapshot["note"] = row["note"]
    data = row["data"] if isinstance(row["data"], dict) else {}
    for key in _TRANSFER_DATA_FIELDS:
        value = data.get(key)
        if value not in (None, "", [], {}):
            snapshot[key] = value
    return snapshot


async def _lodge_on_baton(
    session: AsyncSession,
    *,
    tenant_id: int,
    action_key: str,
    account: dict[str, Any],
    instruction_uid: str,
    contact_id: int | None,
    requested_by: str | None,
    justification: str | None,
    enforce_invoke: bool,
) -> Any:
    """Send the booked instruction through baton. The run keys on the instruction uid, so a second
    send is the same run; the worker advances it, and an approval hold comes back as ``Held`` so the
    doors keep returning 202 unchanged."""
    from app.actions import service
    from app.actions.runs import Held, account_subjects, caller_ids

    actor = requested_by
    role_ids: list[int] | None = None
    if enforce_invoke:
        resolved_actor, resolved_roles = await caller_ids(session)
        actor = requested_by or resolved_actor
        role_ids = resolved_roles
    snapshot = await _instruction_input(session, tenant_id=tenant_id, uid=instruction_uid)
    result = await service.start(
        session,
        action_key=action_key,
        tenant_id=tenant_id,
        params={"account_id": int(account["id"]), "instruction_uid": instruction_uid},
        idempotency_key=f"instruction:{instruction_uid}",
        requested_by=actor,
        role_ids=role_ids,
        subjects=account_subjects(account, contact_id),
        contact_id=contact_id,
        justification=(justification or "").strip() or None,
        meta={"input": snapshot} if snapshot else None,
    )
    if result.run.state.type.value == "paused":
        return Held(
            {"status": "awaiting_approval", "run_uid": result.run.uid, "action_key": action_key}
        )
    return None


async def _transfer_on_baton(
    session: AsyncSession,
    *,
    tenant_id: int,
    account: dict[str, Any],
    transfer_uid: str,
    leg: str,
    contact_id: int | None,
    requested_by: str | None,
    justification: str | None,
    enforce_invoke: bool,
) -> Any:
    """Send a raised transfer through ``transaction.transfer`` on baton when the flag is on. The run
    keys on the transfer uid, with the later SEI receipt leg on its own ``:sei`` key so it is a
    fresh run, not a resume of the raise."""
    from app.actions import service
    from app.actions.runs import Held, account_subjects, caller_ids

    actor = requested_by
    role_ids: list[int] | None = None
    if enforce_invoke:
        resolved_actor, resolved_roles = await caller_ids(session)
        actor = requested_by or resolved_actor
        role_ids = resolved_roles
    key = f"transfer:{transfer_uid}:sei" if leg == "sei" else f"transfer:{transfer_uid}"
    snapshot = await _transfer_input(session, tenant_id=tenant_id, uid=transfer_uid)
    if leg == "sei" and snapshot:
        snapshot = {"leg": "SEI receipt of the completed SIPP transfer", **snapshot}
    result = await service.start(
        session,
        action_key="transaction.transfer",
        tenant_id=tenant_id,
        params={"account_id": int(account["id"]), "uid": transfer_uid, "leg": leg},
        idempotency_key=key,
        requested_by=actor,
        role_ids=role_ids,
        subjects=account_subjects(account, contact_id),
        contact_id=contact_id,
        justification=(justification or "").strip() or None,
        meta={"input": snapshot} if snapshot else None,
        # The SEI receipt is the second leg of a transfer the firm's approvers already agreed
        # to when it was raised; it is not a second request to approve.
        approved=leg == "sei",
    )
    if result.run.state.type.value == "paused":
        return Held(
            {
                "status": "awaiting_approval",
                "run_uid": result.run.uid,
                "action_key": "transaction.transfer",
            }
        )
    return None


async def lodge_instruction(
    session: AsyncSession,
    *,
    tenant_id: int,
    kind: str,
    instruction_uid: str,
    account_id: int,
    contact_id: int | None = None,
    requested_by: str | None = None,
    justification: str | None = None,
    enforce_invoke: bool = True,
) -> Any:
    """Send one booked instruction through its action. The run key is the instruction uid.

    A second call is the same run. The handler sends that row, and does nothing when the
    transaction link is already there. No custodian on the account is the old skip: there
    is no provider step to record as a success.
    """
    from app import outbound
    from app.actions.runs import _load_account

    action_key = ACTION_FOR_KIND.get(kind)
    if action_key is None:
        return await outbound.dispatch(
            session,
            tenant_id=tenant_id,
            origin="instruction",
            origin_uid=instruction_uid,
            account_id=account_id,
        )
    if await switched_off(session, tenant_id=tenant_id, kind=kind):
        from app.actions.runs import ActionError

        raise ActionError("This action is switched off.", status=409)
    account = await _load_account(session, tenant_id=tenant_id, account_id=account_id)
    if contact_id is None and account is not None and account.get("contact_id") is not None:
        contact_id = int(account["contact_id"])
    if account is None or not account.get("custodian"):
        return await outbound.dispatch(
            session,
            tenant_id=tenant_id,
            origin="instruction",
            origin_uid=instruction_uid,
            account_id=account_id,
        )
    return await _lodge_on_baton(
        session,
        tenant_id=tenant_id,
        action_key=action_key,
        account=account,
        instruction_uid=instruction_uid,
        contact_id=contact_id,
        requested_by=requested_by,
        justification=justification,
        enforce_invoke=enforce_invoke,
    )


async def lodge_transfer(
    session: AsyncSession,
    *,
    tenant_id: int,
    record: Mapping[str, Any],
    requested_by: str | None = None,
    justification: str | None = None,
    enforce_invoke: bool = True,
    leg: str = "raise",
) -> Any:
    """Send one raised transfer through ``transaction.transfer``.

    The run key is the transfer uid, so a second raise is the same run. The later SEI
    leg of a SIPP uses its own key: the raise already succeeded by leaving SEI until
    Morgan Lloyd had finished, and resuming that run would not send the receipt.
    An account with no SEI or Seccl custodian has nothing to send.
    """
    from app.actions.runs import _load_account

    account_id = int(record["account_id"])
    transfer_uid = str(record["uid"])
    contact_id = record.get("contact_id")
    account = await _load_account(session, tenant_id=tenant_id, account_id=account_id)
    if account is None:
        return None
    if contact_id is None and account.get("contact_id") is not None:
        contact_id = int(account["contact_id"])
    elif contact_id is not None:
        contact_id = int(contact_id)
    custodian = str(account.get("custodian") or "")
    if custodian not in {"sei", "seccl"}:
        return None
    return await _transfer_on_baton(
        session,
        tenant_id=tenant_id,
        account=account,
        transfer_uid=transfer_uid,
        leg=leg,
        contact_id=contact_id,
        requested_by=requested_by,
        justification=justification,
        enforce_invoke=enforce_invoke,
    )
