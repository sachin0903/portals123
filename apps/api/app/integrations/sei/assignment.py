"""Assign the account's model once SEI has opened the portfolio.

The variant is the model's type. SEI publishes three write paths, and each
accepts one ``MODEL_TYPE``:

* ``8`` investment → ``POST …/assignment/investment``
* ``4`` implementation → ``POST …/assignment/implementation``
* ``3`` asset allocation → ``POST …/assignment/allocation``

``toleranceMultiplier`` has to be a value SEI already associates with the firm
(error 40030). That value is what an earlier assignment read stored on
``raw.api_models``. A model with no published tolerance is not sent.
``eventProcessingId`` is mandatory on a first assignment (error 40034). SEI's
system code 37786 names ``3`` as As per Rule, which is what a new account follows.
"""

from __future__ import annotations

from typing import Any

from app.actions.attempt import OperationError

_INVESTMENT = "8"
_IMPLEMENTATION = "4"
_ALLOCATION = "3"

# System code 37786. Mandatory when the portfolio has no model yet.
_AS_PER_RULE = 3
# System code 37727. Implementation only, and only on a first assignment.
_SLEEVE_MODEL = 1

PATHS = {
    _INVESTMENT: "/pmemodelassignmentservices/PSM/model/assignment/investment",
    _IMPLEMENTATION: "/pmemodelassignmentservices/PSM/model/assignment/implementation",
    _ALLOCATION: "/pmemodelassignmentservices/PSM/model/assignment/allocation",
}


def _code(value: object) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def assignment_request(
    *,
    firm_id: str,
    account_number: str,
    portfolio_id: str,
    model_id: str,
    model_type: object,
    tolerance: object,
    schedule_id: object = None,
) -> tuple[str, dict[str, Any]]:
    """The path and the body for this model's type. A type we cannot send is named."""
    code = _code(model_type)
    path = PATHS.get(code)
    if path is None:
        shown = code or "unknown"
        raise OperationError(
            f"SEI model {model_id} is type {shown}. "
            "Assignment covers investment (8), implementation (4), and asset allocation (3). "
            "The model was not sent.",
            permanent=True,
        )
    if tolerance is None or str(tolerance).strip() == "":
        raise OperationError(
            f"SEI model {model_id} has no published tolerance multiplier. The model was not sent.",
            permanent=True,
        )
    try:
        multiplier = float(str(tolerance).strip())
    except (TypeError, ValueError) as exc:
        raise OperationError(
            f"SEI model {model_id} has no published tolerance multiplier. The model was not sent.",
            permanent=True,
        ) from exc
    body: dict[str, Any] = {
        "firmId": int(firm_id) if str(firm_id).isdigit() else firm_id,
        "accountNumber": account_number,
        "portfolioId": int(portfolio_id) if str(portfolio_id).isdigit() else portfolio_id,
        "modelId": int(model_id) if str(model_id).isdigit() else model_id,
        "toleranceMultiplier": multiplier,
        "eventProcessingId": _AS_PER_RULE,
    }
    if code == _IMPLEMENTATION:
        body["sleeveConfigurationType"] = _SLEEVE_MODEL
    schedule = _code(schedule_id)
    if schedule.isdigit() and code != _ALLOCATION:
        body["apsId"] = int(schedule)
    return path, body


def published_model(firm_id: str, model_id: str) -> dict[str, Any] | None:
    """Type, tolerance, and schedule from the assignment catalogue.

    ``None`` means that model is not in the catalogue. A warehouse that cannot
    be read raises, so a missing file is not reported as a model with no tolerance.
    """
    from app.lake import core

    try:
        conn = core.connect("sei")
    except core.CoreUnavailable as exc:
        raise OperationError(
            "SEI's model catalogue could not be read. The model was not sent.",
            permanent=True,
        ) from exc
    try:
        row = conn.execute(
            "SELECT model_type_code, tolerance_multiplier, schedule_id "
            "FROM raw.api_models WHERE firm_id = ? AND model_id = ?",
            [firm_id, model_id],
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 — the gap is named; the call is not sent
        raise OperationError(
            "SEI's model catalogue could not be read. The model was not sent.",
            permanent=True,
        ) from exc
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "model_type_code": row[0],
        "tolerance_multiplier": row[1],
        "schedule_id": row[2],
    }
