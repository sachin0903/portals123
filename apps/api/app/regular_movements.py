"""Regular payments and withdrawals on ``platform.accounts``.

``payments`` and ``withdrawals`` are jsonb lists of movements (``amount``, ``frequency``,
``next_date``, ``method``, ``reference``). The account record, onboarding and the PUT all write
that list. A provider sync writes its own rows into the same list (Seccl adds ``payment_day``).

Rows onboarding saved before it wrote lists hold an OBJECT instead: ``regular_payments`` nested
under ``payments`` beside the one-off ``initial_cash_deposit``, and a bare object for
``withdrawals``. Those rows are not migrated, so every reader here takes both forms.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

FREQUENCIES = frozenset({"monthly", "quarterly", "half_yearly", "annually"})
METHODS = frozenset({"direct_debit", "bank_transfer", "cheque", "internal", "card"})


def _amount(value: object) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _decoded(value: object) -> object:
    """Some reads hand jsonb back as its text."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return None


def _movement(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    amount = _amount(value.get("amount"))
    frequency = _text(value.get("frequency"))
    next_date = _text(value.get("next_date")) or _text(value.get("date"))
    method = _text(value.get("method"))
    reference = _text(value.get("reference"))
    if amount is None and not frequency and not next_date and not method and not reference:
        return None
    return {
        "amount": amount,
        "frequency": frequency,
        "next_date": next_date,
        "method": method,
        "reference": reference,
    }


def register_row(*, amount: object, frequency: object, next_date: object) -> dict[str, object]:
    """One movement as onboarding records it from the answers it was given."""
    return {
        "amount": _amount(amount),
        "frequency": _text(frequency),
        "next_date": _text(next_date),
        "method": None,
        "reference": None,
    }


def initial_cash_deposit_of(data: object, payments: object) -> dict[str, Any] | None:
    """The opening cash onboarding recorded: on the account's ``data``, or inside an older
    ``payments`` object. It is a one-off, so it is not a row of the payments list."""
    for bag in (_decoded(data), _decoded(payments)):
        if isinstance(bag, dict) and isinstance(deposit := bag.get("initial_cash_deposit"), dict):
            return deposit
    return None


def regular_movements_of(value: object) -> list[dict[str, object]]:
    """The register the account card renders, whatever form the jsonb arrived in."""
    value = _decoded(value)
    if isinstance(value, list):
        return [row for item in value if (row := _movement(item)) is not None]
    if not isinstance(value, dict):
        return []
    if "regular_payments" in value:
        return regular_movements_of(value.get("regular_payments"))
    one = _movement(value)
    return [one] if one else []


def standing_movements_of(value: object) -> list[dict[str, object]]:
    """The client record's standing panel: always a list, never the older onboarding object.

    A stored list passes through as written, because a provider row carries ``payment_day`` and
    ``auto_disinvest`` that ``regular_movements_of`` would drop. Anything else is read as the
    older object. The panel iterates this value, so an object here stops the whole page.
    """
    value = _decoded(value)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return regular_movements_of(value)


def _normalise_row(value: object, *, kind: str) -> dict[str, object]:
    row = _movement(value)
    if row is None:
        raise ValueError(f"Enter a {kind} amount greater than zero.")
    amount = row["amount"]
    if not isinstance(amount, int | float) or isinstance(amount, bool) or amount <= 0:
        raise ValueError(f"Enter a {kind} amount greater than zero.")
    frequency = row["frequency"]
    if frequency not in FREQUENCIES:
        raise ValueError(f"Choose a valid {kind} frequency.")
    method = row["method"]
    if method is not None and method not in METHODS:
        raise ValueError("Unknown payment method.")
    return {
        "amount": float(amount),
        "frequency": frequency,
        "next_date": row["next_date"],
        "method": method,
        "reference": row["reference"],
    }


def normalise_movements(rows: object, *, kind: str) -> list[dict[str, object]] | None:
    if rows is None:
        return None
    if not isinstance(rows, list):
        raise ValueError(f"{kind.capitalize()} must be a list.")
    if not rows:
        return None
    return [_normalise_row(row, kind=kind) for row in rows]


async def set_regular_movements(
    session: AsyncSession,
    *,
    tenant_id: int,
    account_id: int,
    payments: object,
    withdrawals: object,
) -> dict[str, object]:
    """Replace the account's regular payments and withdrawals. Empty lists clear."""
    stored_payments = normalise_movements(payments, kind="payment")
    stored_withdrawals = normalise_movements(withdrawals, kind="withdrawal")
    row = (
        (
            await session.execute(
                text(
                    "UPDATE platform.accounts "
                    "SET payments = CAST(:p AS jsonb), withdrawals = CAST(:w AS jsonb) "
                    "WHERE id = :a AND tenant_id = :t "
                    "RETURNING id, uid, payments, withdrawals"
                ),
                {
                    "p": json.dumps(stored_payments) if stored_payments is not None else None,
                    "w": json.dumps(stored_withdrawals) if stored_withdrawals is not None else None,
                    "a": account_id,
                    "t": tenant_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Account not found")
    return dict(row)
