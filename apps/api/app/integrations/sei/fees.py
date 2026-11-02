"""SEI fee types, and the one-time charge an ad hoc fee sends.

A payment's adviser fee overrides the receipt with the firm's real-time initial advice
type. A booked ad hoc fee is a different call: ``initateOneTimeFeesAdhoc``, a direct
charge of the one-time type. A firm with none, or with more than one, is not guessed.
"""

from __future__ import annotations

from datetime import date
from typing import Any

_FEE_TYPE_SQL = """
SELECT DISTINCT c.code
FROM ref.codes c
WHERE c.type = 'FEE_TYPE'
  AND c.qualifier_1 = 'Real Time Transaction'
  AND c.description IN (
    SELECT r.FEE_TYPE_DESCRIPTION
    FROM raw.fee_package_rule r
    JOIN ref.firms f ON f.firm_name = r.RESIDING_ORGANIZATION
    WHERE CAST(f.firm_id AS VARCHAR) = ?
      AND r.FEE_PACKAGE_TYPE = 'Real Time'
      AND lower(r.FEE_TYPE_DESCRIPTION) LIKE '%initial%advi%'
  )
"""


def choose_fee_type(codes: list[str]) -> str | None:
    """The single fee type, or None when the list is empty or ambiguous."""
    distinct = sorted({code for code in codes if code})
    if len(distinct) == 1:
        return distinct[0]
    return None


def initial_advice_fee_id(firm_id: str) -> str | None:
    """The firm's real-time initial advice fee type, from the SEI catalogue."""
    return choose_fee_type(_fee_codes(_FEE_TYPE_SQL, [str(firm_id)]))


# One-time types the firm's own packages name. ``FEE_TYPE`` is platform-wide, so the
# firm's rule descriptions are what keep another book's "Initial Adviser Fee" out.
_ONE_TIME_BY_CODE = """
SELECT DISTINCT c.code
FROM ref.codes c
WHERE c.type = 'FEE_TYPE'
  AND c.qualifier_1 = 'One Time'
  AND c.code = ?
  AND c.description IN (
    SELECT r.FEE_TYPE_DESCRIPTION
    FROM raw.fee_package_rule r
    JOIN ref.firms f ON f.firm_name = r.RESIDING_ORGANIZATION
    WHERE CAST(f.firm_id AS VARCHAR) = ?
  )
"""

_ONE_TIME_BY_DESCRIPTION = """
SELECT DISTINCT c.code
FROM ref.codes c
WHERE c.type = 'FEE_TYPE'
  AND c.qualifier_1 = 'One Time'
  AND lower(c.description) = lower(?)
  AND c.description IN (
    SELECT r.FEE_TYPE_DESCRIPTION
    FROM raw.fee_package_rule r
    JOIN ref.firms f ON f.firm_name = r.RESIDING_ORGANIZATION
    WHERE CAST(f.firm_id AS VARCHAR) = ?
  )
"""

_ONE_TIME_INITIAL = """
SELECT DISTINCT c.code
FROM ref.codes c
WHERE c.type = 'FEE_TYPE'
  AND c.qualifier_1 = 'One Time'
  AND c.description IN (
    SELECT r.FEE_TYPE_DESCRIPTION
    FROM raw.fee_package_rule r
    JOIN ref.firms f ON f.firm_name = r.RESIDING_ORGANIZATION
    WHERE CAST(f.firm_id AS VARCHAR) = ?
      AND lower(r.FEE_TYPE_DESCRIPTION) LIKE '%initial%advi%'
  )
"""

#: Direct charge on system code 33302. ``2`` is Do Not Charge and ``5`` is Invoice.
COLLECTION_DIRECT_CHARGE = "1"

ONE_TIME_FEE_URL = "/initiateonetimefeesadhocservice/initateOneTimeFeesAdhoc"


def one_time_fee_id(
    firm_id: str,
    *,
    description: str | None = None,
    code: str | None = None,
    named: bool = False,
) -> str | None:
    """The one-time fee type this charge should use.

    A named code is used when it is one of the firm's one-time types. A named
    description matches that type's name. With neither, and only when the
    instruction did not name a package, the firm's single one-time initial advice
    fee is the charge an amount-only fee books.
    """
    firm = str(firm_id)
    typed = str(code or "").strip()
    if typed:
        found = choose_fee_type(_fee_codes(_ONE_TIME_BY_CODE, [typed, firm]))
        if found:
            return found
    label = str(description or "").strip()
    if label:
        return choose_fee_type(_fee_codes(_ONE_TIME_BY_DESCRIPTION, [label, firm]))
    if named or typed:
        return None
    return choose_fee_type(_fee_codes(_ONE_TIME_INITIAL, [firm]))


def missing_one_time_fee(context: dict[str, Any]) -> list[str]:
    """What a one-time direct charge needs and this account has not got."""
    absent: list[str] = []
    if not context.get("sei_account_number"):
        absent.append("the account's SEI account number (links, provider 'sei')")
    if not context.get("sei_portfolio_id"):
        absent.append("the account's SEI portfolio id (links, link type 'portfolio')")
    if not context.get("firm_id"):
        absent.append("the tenant's SEI firm id (provider config 'firm_id')")
    if not context.get("fee_type"):
        absent.append("a one-time SEI fee type for this charge")
    return absent


def explain_missing_one_time_fee(context: dict[str, Any]) -> str | None:
    absent = missing_one_time_fee(context)
    if not absent:
        return None
    return "SEI requires these details before an ad hoc fee can be charged: " + ", ".join(absent)


def build_one_time_fee(context: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """A direct charge collected from the account on the instruction's date.

    ``collectionDate`` is ``MM/DD/YYYY`` on this service. The portfolio id is the
    numeric entity id, the same reduction a cash receipt uses.
    """
    from sei.models import money

    from app.integrations.sei.reference import portfolio_entity_id

    due = context.get("due_date")
    on = due if isinstance(due, date) else (today or date.today())
    portfolio = portfolio_entity_id(context["sei_portfolio_id"]) or str(context["sei_portfolio_id"])
    return {
        "firmId": str(context["firm_id"]),
        "accountNumber": str(context["sei_account_number"]),
        "portfolioId": portfolio,
        "collectionMethod": COLLECTION_DIRECT_CHARGE,
        "currencyCode": context.get("currency") or "GBP",
        "feeAmount": money(context["amount"]),
        "feeType": str(context["fee_type"]),
        "collectionDate": on.strftime("%m/%d/%Y"),
    }


async def create_one_time_fee(
    context: dict[str, Any], *, environment: str | None = None
) -> dict[str, Any]:
    """Charge the fee and return the activity SEI opened. Not reversible."""
    from app.integrations.sei.cash_receipts import (
        SeiNotConfigured,
        SeiUnavailable,
        _api_error_body,
        _as_payload,
        _settings,
        response_was_lost,
    )

    detail = explain_missing_one_time_fee(context)
    if detail:
        raise ValueError(detail)
    settings = _settings(environment)
    if not settings.configured:
        raise SeiNotConfigured("SEI is unavailable for this firm. Contact support.")
    from sei import SeiApiError, SeiAuthError, SeiClient, SeiConfigError

    attributes = build_one_time_fee(context)
    request_body = _as_payload(attributes)
    url = ONE_TIME_FEE_URL
    try:
        async with SeiClient(settings) as api:
            result = await api.create_adhoc_fee(attributes)
    except SeiConfigError as exc:
        raise SeiNotConfigured("SEI is unavailable for this firm. Contact support.") from exc
    except SeiAuthError as exc:
        raise SeiUnavailable(
            "SEI could not accept this request.",
            ambiguous=False,
            url=url,
            request=request_body,
            response=_api_error_body(exc),
        ) from exc
    except SeiApiError as exc:
        status = getattr(exc, "status", None)
        code = status if isinstance(status, int) else None
        message = (
            f"SEI could not accept this request (HTTP {code})."
            if code
            else "SEI did not confirm this fee."
        )
        raise SeiUnavailable(
            message,
            ambiguous=response_was_lost(code),
            status_code=code,
            url=url,
            request=request_body,
            response=_api_error_body(exc),
        ) from exc
    activity = result.get("activityId") or result.get("activity_id")
    if str(result.get("status") or "").upper() == "FAILURE" and not activity:
        raise SeiUnavailable(
            "SEI refused this fee.", ambiguous=False, url=url, request=request_body
        )
    return {
        "activity_id": str(activity) if activity else None,
        "status": result.get("status"),
        "request": attributes,
    }


def _fee_codes(sql: str, params: list[Any]) -> list[str]:
    from app.lake.core import connect

    connection = connect("sei")
    try:
        rows: list[Any] = connection.execute(sql, params).fetchall()
    finally:
        connection.close()
    return [str(row[0]) for row in rows if row and row[0]]
