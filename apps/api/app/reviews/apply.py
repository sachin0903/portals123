"""Wave-1 apply / before / describe for designated four-eye actions.

Each wired catalogue key registers four callables. ``apply`` is the same service function
the live door already calls, so a held command and an immediate write cannot drift.
``capture_before`` / ``conflicts`` are the stale-apply guard. ``describe`` is what a
reviewer reads — never raw JSON, and never a sentence that dumps bank digits.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import bank_accounts as bank_accounts_mod
from app import contacts as contacts_mod
from app import instructions as instructions_mod
from app.reviews.catalogue import ActionSpec, require_wired

Json = dict[str, Any]


@dataclass(frozen=True)
class WiredAction:
    spec: ActionSpec
    capture_before: Callable[..., Awaitable[Json | None]]
    apply: Callable[..., Awaitable[Any]]
    describe: Callable[[Json | None, Json], dict[str, str]]


_BANK_FIELDS = ("name", "bank", "sort_code", "account_number", "iban", "bic", "method", "scheme")


def _bank_from_payload(payload: Json) -> object:
    """The contact door dumps the bank fields on the payload; the account door nests them."""
    nested = payload.get("bank_account", payload.get("bank_accounts"))
    if nested is not None:
        return nested
    if any(key in payload for key in _BANK_FIELDS):
        return {key: payload[key] for key in _BANK_FIELDS if key in payload}
    return None


def _bank_summary(row: object) -> str:
    if not row:
        return "No bank details"
    if isinstance(row, list):
        if not row:
            return "No bank details"
        return _bank_summary(row[0]) + (f" (+{len(row) - 1} more)" if len(row) > 1 else "")
    if not isinstance(row, dict):
        return "Set"
    name = str(row.get("name") or "").strip()
    if row.get("iban"):
        label = f"IBAN {row['iban']}"
    elif row.get("sort_code") or row.get("account_number"):
        sort = str(row.get("sort_code") or "").strip() or "—"
        acct = str(row.get("account_number") or "").strip() or "—"
        label = f"{sort} {acct}"
    else:
        label = "Set"
    return f"{name} · {label}" if name else label


def _bank_describe(before: Json | None, payload: Json) -> dict[str, str]:
    return {
        "summary": "Change bank details",
        "before": _bank_summary((before or {}).get("value") if before else None),
        "after": _bank_summary(_bank_from_payload(payload)),
    }


async def _contact_bank_before(
    session: AsyncSession, *, tenant_id: int, payload: Json
) -> Json | None:
    row = (
        await session.execute(
            text(
                "SELECT bank_account FROM crm.contacts "
                "WHERE id = :c AND tenant_id = :t AND deleted_at IS NULL"
            ),
            {"c": int(payload["contact_id"]), "t": tenant_id},
        )
    ).scalar_one_or_none()
    return {"value": row}


async def _contact_bank_apply(session: AsyncSession, *, tenant_id: int, payload: Json) -> Any:
    return await contacts_mod.set_bank_account(
        session,
        tenant_id=tenant_id,
        contact_id=int(payload["contact_id"]),
        bank_account=_bank_from_payload(payload),
    )


async def _account_bank_before(
    session: AsyncSession, *, tenant_id: int, payload: Json
) -> Json | None:
    row = (
        await session.execute(
            text("SELECT bank_accounts FROM platform.accounts WHERE id = :a AND tenant_id = :t"),
            {"a": int(payload["account_id"]), "t": tenant_id},
        )
    ).scalar_one_or_none()
    return {"value": row}


async def _account_bank_apply(session: AsyncSession, *, tenant_id: int, payload: Json) -> Any:
    return await bank_accounts_mod.set_for_account(
        session,
        tenant_id=tenant_id,
        account_id=int(payload["account_id"]),
        entries=payload.get("bank_accounts"),
    )


async def _account_model_before(
    session: AsyncSession, *, tenant_id: int, payload: Json
) -> Json | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT a.model_id, m.name AS model_name "
                    "FROM platform.accounts a "
                    "LEFT JOIN platform.models m ON m.id = a.model_id "
                    "WHERE a.id = :a AND a.tenant_id = :t"
                ),
                {"a": int(payload["account_id"]), "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


async def _account_model_apply(session: AsyncSession, *, tenant_id: int, payload: Json) -> Any:
    return await instructions_mod.set_account_model(
        session,
        tenant_id=tenant_id,
        account_id=int(payload["account_id"]),
        model_id=payload.get("model_id"),
    )


def _account_model_describe(before: Json | None, payload: Json) -> dict[str, str]:
    prev = (before or {}).get("model_name") or "No model"
    after_id = payload.get("model_id")
    after_name = payload.get("model_name")
    if after_id is None:
        after = "No model"
    elif after_name:
        after = str(after_name)
    else:
        after = "A different model"
    return {
        "summary": "Change the model mandate",
        "before": str(prev),
        "after": after,
    }


async def _vulnerability_before(
    session: AsyncSession, *, tenant_id: int, payload: Json
) -> Json | None:
    import json as _json

    row = (
        (
            await session.execute(
                text(
                    "SELECT is_vulnerable, vulnerability_reasons, vulnerability_characteristics, "
                    "       vulnerability_is_temporary, vulnerability_notes "
                    "FROM crm.contacts WHERE id = :c AND tenant_id = :t AND deleted_at IS NULL"
                ),
                {"c": int(payload["contact_id"]), "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    normalised: Json = _json.loads(_json.dumps(dict(row), default=str))
    return normalised


async def _vulnerability_apply(session: AsyncSession, *, tenant_id: int, payload: Json) -> Any:
    return await contacts_mod.set_vulnerability(
        session,
        tenant_id=tenant_id,
        contact_id=int(payload["contact_id"]),
        vulnerability=payload.get("vulnerability") or {},
    )


def _vulnerability_summary(d: Json | None, *, reasons_key: str) -> str:
    if not d:
        return "Unknown"
    if not d.get("is_vulnerable"):
        return "Not vulnerable"
    reasons = d.get(reasons_key)
    n = len(reasons) if isinstance(reasons, list) else 0
    return "Vulnerable" + (f" · {n} reason{'' if n == 1 else 's'}" if n else "")


def _vulnerability_describe(before: Json | None, payload: Json) -> dict[str, str]:
    # `before` carries the db column names, the proposed change carries the body's.
    return {
        "summary": "Change the vulnerability record",
        "before": _vulnerability_summary(before, reasons_key="vulnerability_reasons"),
        "after": _vulnerability_summary(payload.get("vulnerability"), reasons_key="reasons"),
    }


async def _account_fee_packages_before(
    session: AsyncSession, *, tenant_id: int, payload: Json
) -> Json | None:
    ids = (
        (
            await session.execute(
                text(
                    "SELECT fee_package_id FROM platform.account_fee_packages "
                    "WHERE account_id = :a AND tenant_id = :t ORDER BY sort_order"
                ),
                {"a": int(payload["account_id"]), "t": tenant_id},
            )
        )
        .scalars()
        .all()
    )
    current = [int(i) for i in ids]
    # The names beside the ids, for both sides of the change, so the reviewer reads "Platform
    # fee, Adviser fee" rather than "2 packages". Part of the snapshot, so the apply-time
    # compare still holds (a package renamed in the meantime is a conflict, which is honest).
    proposed = [int(i) for i in payload.get("fee_package_ids") or []]
    wanted = sorted(set(current) | set(proposed))
    names: dict[str, str] = {}
    if wanted:
        rows = (
            await session.execute(
                text(
                    "SELECT id, name FROM platform.fee_packages "
                    "WHERE tenant_id = :t AND id = ANY(CAST(:ids AS int[]))"
                ),
                {"t": tenant_id, "ids": wanted},
            )
        ).mappings()
        names = {str(row["id"]): str(row["name"]) for row in rows}
    return {"fee_package_ids": current, "names": names}


async def _account_fee_packages_apply(
    session: AsyncSession, *, tenant_id: int, payload: Json
) -> Any:
    return await instructions_mod.set_account_fee_packages(
        session,
        tenant_id=tenant_id,
        account_id=int(payload["account_id"]),
        fee_package_ids=[int(i) for i in payload.get("fee_package_ids") or []],
    )


def _account_fee_packages_describe(before: Json | None, payload: Json) -> dict[str, str]:
    names = (before or {}).get("names")
    lookup: dict[str, str] = names if isinstance(names, dict) else {}

    def said(ids: object) -> str:
        if not isinstance(ids, list) or not ids:
            return "None"
        named = [lookup.get(str(i)) for i in ids]
        if all(named):
            return ", ".join(str(n) for n in named)
        return f"{len(ids)} package{'' if len(ids) == 1 else 's'}"

    return {
        "summary": "Change the fee packages",
        "before": said((before or {}).get("fee_package_ids")),
        "after": said(payload.get("fee_package_ids")),
    }


async def _contact_details_before(
    session: AsyncSession, *, tenant_id: int, payload: Json
) -> Json | None:
    fields = list((payload.get("fields") or {}).keys())
    return await contacts_mod.snapshot_contact_fields(
        session, tenant_id=tenant_id, contact_id=int(payload["contact_id"]), fields=fields
    )


async def _contact_details_apply(session: AsyncSession, *, tenant_id: int, payload: Json) -> Any:
    return await contacts_mod.update_contact(
        session,
        tenant_id=tenant_id,
        contact_id=int(payload["contact_id"]),
        fields=payload.get("fields") or {},
    )


_FIELD_LABELS = {
    "first_name": "first name",
    "middle_name": "middle name",
    "surname": "surname",
    "name": "name",
    "email": "email",
    "mobile": "mobile",
    "phone": "phone",
    "title": "title",
    "address": "address",
    "dob": "date of birth",
    "nationality": "nationality",
    "tax_residency": "tax residency",
    "occupation": "occupation",
    "employment_status": "employment status",
    "marital_status": "marital status",
    "employer_bank_account": "employer bank account",
}


def _contact_details_describe(before: Json | None, payload: Json) -> dict[str, str]:
    # The field NAMES, never their values — a details review names what is being edited without
    # putting a client's personal data in the inbox line.
    keys = list((payload.get("fields") or {}).keys())
    names = ", ".join(_FIELD_LABELS.get(k, k.replace("_", " ")) for k in sorted(keys))
    return {
        "summary": "Edit client details",
        "before": "Current details",
        "after": f"Change {names}" if names else "No change",
    }


def conflicts(before: Json | None, current: Json | None) -> bool:
    """True when the live row no longer matches the snapshot taken at propose time.

    A snapshot compares the KEYS IT CAPTURED. The capture functions grow over time (fee packages
    gained ``names``, vulnerability gained its characteristics), and a hold proposed before such
    a change must still apply cleanly after it: the record did not move, the snapshot's shape
    did. ``names`` is presentation for the reviewer, not the record, so a package renamed while
    the hold waited is not a conflict either."""
    if isinstance(before, dict) and isinstance(current, dict):
        return any(current.get(key) != value for key, value in before.items() if key != "names")
    return before != current


HANDLERS: dict[str, WiredAction] = {}


def _register(key: str, capture: Any, apply: Any, describe: Any) -> None:
    spec = require_wired(key)
    HANDLERS[key] = WiredAction(spec=spec, capture_before=capture, apply=apply, describe=describe)


_register(
    "contact.bank_details_changed",
    _contact_bank_before,
    _contact_bank_apply,
    _bank_describe,
)
_register(
    "account.bank_details_changed",
    _account_bank_before,
    _account_bank_apply,
    _bank_describe,
)
_register(
    "account.model_changed",
    _account_model_before,
    _account_model_apply,
    _account_model_describe,
)
_register(
    "client.vulnerability_updated",
    _vulnerability_before,
    _vulnerability_apply,
    _vulnerability_describe,
)
_register(
    "client.details_changed",
    _contact_details_before,
    _contact_details_apply,
    _contact_details_describe,
)
_register(
    "account.fee_packages_changed",
    _account_fee_packages_before,
    _account_fee_packages_apply,
    _account_fee_packages_describe,
)


def handler(key: str) -> WiredAction:
    h = HANDLERS.get(key)
    if h is None:
        raise KeyError(f"No apply path registered for '{key}'")
    return h
