"""Record a Graphene contribution against a Morgan Lloyd scheme.

``cam_createcontribution`` (the docs head this section "Create Pension Contribution", but the
action name is ``cam_createcontribution`` — the two are not interchangeable). It writes a
``cam_contribution`` record, which is the *application*; ``cam_pensioncontribution``, returned
by Get Received Contributions, is a different entity with a different option set. Only the
first is created here.

Three mappings do real work, and each loses information if it is got wrong:

- **Category.** Graphene has five categories, Morgan Lloyd has three. ``transfer_in`` is a
  transfer, not a contribution, and ``not_a_contribution`` is the adviser saying this payment
  should never have been in the queue — neither is pushable, and both raise rather than being
  quietly coerced to ``OTHER``.
- **Frequency.** Morgan Lloyd needs the actual period (monthly/quarterly/…), which is why
  ``platform.contributions.frequency`` is not the portal's old one_off/regular binary.
- **Contributor.** A personal contribution identifies the contributor by Dynamics GUID
  (``cam_contributor``); anything else must name the employer/other payer instead. Those are
  the docs' ``^`` and ``*`` rules, and they are mutually exclusive.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
from morgan_lloyd import MorganLloydError

from app.integrations.morgan_lloyd.benefits import (
    MorganLloydUnavailable,
    _explain,
    _settings,
    configured,
    unconfigured,
)

# morgan_lloyd.enums.ContributionType — the 8091300xx set on cam_contribution. Inlined rather
# than imported so this module stays importable when the vendor package is absent (the same
# reason benefits.py imports the client lazily); the values are pinned by a test.
_TYPE_PERSONAL = 809130000
_TYPE_EMPLOYER = 809130001
_TYPE_OTHER = 809130002

#: Graphene category -> cam_contributiontype. Absent keys are deliberately unpushable.
CATEGORY_TO_ML: dict[str, int] = {
    "personal_contribution": _TYPE_PERSONAL,
    "employer_contribution": _TYPE_EMPLOYER,
    "third_party_contribution": _TYPE_OTHER,
}

#: Categories that are not contributions at Morgan Lloyd, and why.
UNPUSHABLE: dict[str, str] = {
    "transfer_in": (
        "A transfer in is not a contribution — record it as a transfer "
        "(cam_createtransfer) instead."
    ),
    "not_a_contribution": (
        "This payment was categorised as not a contribution, so there is nothing to record."
    ),
}

# morgan_lloyd.enums.PensionContributionFrequency
FREQUENCY_TO_ML: dict[str, int] = {
    "monthly": 809130000,
    "quarterly": 809130001,
    "half_yearly": 809130002,
    "annually": 809130003,
    "single": 809130004,
}

# morgan_lloyd.enums.ContributionStatus
STATUS_APPLICATION = 809130000
STATUS_ACTIVE = 809130001
STATUS_STOPPED = 809130002
STATUS_RECEIVED = 809130003

STATUS_LABELS: dict[int, str] = {
    STATUS_APPLICATION: "Application",
    STATUS_ACTIVE: "Active",
    STATUS_STOPPED: "Stopped",
    STATUS_RECEIVED: "Received",
}

#: The fields ``cam_updatecontributionapplication`` accepts. Anything else is dropped rather
#: than sent: the action is documented for amending the amount or stopping the application, and
#: Dynamics rejects the whole call for an unexpected field rather than ignoring it.
UPDATABLE_APPLICATION_FIELDS = ("cam_amount", "cam_status")


class NotAnApplication(RuntimeError):
    """The Morgan Lloyd record has moved past Application, so it can no longer be amended.

    ``cam_updatecontributionapplication`` is documented as valid only while ``cam_status`` is
    Application (809130000). Checked here rather than left to Dynamics because the vendor
    answers a stale update with a generic failure, which surfaces as a 502 that reads like an
    outage — when the truth is a 409: someone else moved the record on.
    """


# Fields required when the contribution type is NOT personal (the docs' `*` rule). Only the
# name is hard-required; the address block is optional but recorded when present.
_NON_PERSONAL_REQUIRED: tuple[tuple[str, str], ...] = (("employer_name", "contributor name"),)


def missing_fields(contribution: dict[str, Any]) -> list[str]:
    """Human-readable names of what Morgan Lloyd needs and this contribution hasn't got.

    Returned rather than substituted: the target is a real back-office record on a client's
    pension, and inventing an employer name or a scheme reference would put false data on it.
    """
    absent: list[str] = []
    if not contribution.get("ml_scheme_ref"):
        absent.append("a Morgan Lloyd scheme reference for the account")
    if not contribution.get("amount"):
        absent.append("an amount")
    if not (contribution.get("contribution_date") or contribution.get("created_at")):
        absent.append("a contribution date")

    category = str(contribution.get("category") or "")
    if category == "personal_contribution":
        if not contribution.get("ml_contact_id"):
            absent.append("a Morgan Lloyd contact for the client (push the client first)")
    elif category in CATEGORY_TO_ML:
        absent.extend(label for key, label in _NON_PERSONAL_REQUIRED if not contribution.get(key))
    return absent


def _money(value: Any) -> float:
    """Morgan Lloyd's ``cam_amount`` is a JSON number (unlike SEI, where everything is a
    string). Decimal is not JSON-serialisable, so convert at the boundary."""
    return float(Decimal(str(value)))


def build_contribution(contribution: dict[str, Any]) -> dict[str, Any]:
    """Map a ``platform.contributions`` row onto a ``cam_createcontribution`` payload.

    Raises ``ValueError`` for a category Morgan Lloyd has no concept of — see
    :data:`UNPUSHABLE`.
    """
    category = str(contribution.get("category") or "")
    if category in UNPUSHABLE:
        raise ValueError(UNPUSHABLE[category])
    contribution_type = CATEGORY_TO_ML.get(category)
    if contribution_type is None:
        raise ValueError(f"Unknown contribution category {category!r}.")

    frequency = FREQUENCY_TO_ML.get(str(contribution.get("frequency") or "single"))
    if frequency is None:
        raise ValueError(
            f"Morgan Lloyd has no frequency matching {contribution.get('frequency')!r}."
        )

    started = contribution.get("contribution_date") or contribution.get("created_at")
    payload: dict[str, Any] = {
        "schemeReference": contribution["ml_scheme_ref"],
        "cam_contributiontype": contribution_type,
        "cam_frequency": frequency,
        "cam_amount": _money(contribution["amount"]),
        "cam_startdate": str(started)[:10],
        # A booked contribution is an application until the money lands; a categorised credit
        # already has. "For an Investment Contribution set status Received + frequency Single."
        "cam_status": STATUS_RECEIVED if contribution.get("transaction_id") else STATUS_APPLICATION,
    }

    if contribution_type == _TYPE_PERSONAL:
        # `^`: the contributor is the member, addressed by Dynamics GUID.
        payload["cam_contributor"] = contribution["ml_contact_id"]
    else:
        # `*`: name the employer/other payer. The address block is optional; send only what we
        # hold, because an empty string is a value to Dynamics and would overwrite.
        payload["cam_employerothername"] = contribution["employer_name"]
        for column, field in (
            ("address_line1", "cam_addressline1"),
            ("address_line2", "cam_addressline2"),
            ("address_line3", "cam_addressline3"),
            ("city", "cam_city"),
            ("county", "cam_county"),
            ("postcode", "cam_postcode"),
            ("phone", "cam_phone"),
            ("email", "cam_email"),
        ):
            if contribution.get(column):
                payload[field] = contribution[column]

    if contribution.get("prefunded_ras_amount"):
        payload["cam_prefundedrasamount"] = _money(contribution["prefunded_ras_amount"])
    if contribution.get("investor_ref"):
        payload["cam_investorref"] = contribution["investor_ref"]
    return payload


async def create_contribution(contribution: dict[str, Any]) -> str:
    """Create the ``cam_contribution`` record and return its GUID.

    Raises ``ValueError`` when required details are missing (caller turns that into a 422),
    ``MorganLloydNotConfigured`` without credentials (503), ``MorganLloydUnavailable`` when
    Dynamics or Azure refuses (502).
    """
    absent = missing_fields(contribution)
    if absent:
        raise ValueError(
            "Morgan Lloyd requires these details before a contribution can be recorded: "
            + ", ".join(absent)
        )
    payload = build_contribution(contribution)  # raises for an unpushable category
    if not configured():
        raise unconfigured("MORGAN_LLOYD_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET")
    from morgan_lloyd.client import MorganLloydClient

    try:
        async with MorganLloydClient(settings=_settings()) as api:
            response = await api.create_pension_contribution(payload)
    # THE CLIENT DOES NOT RAISE httpx. `morgan_lloyd._base` wraps every transport and status
    # error into `MorganLloydApiError`, which descends from `MorganLloydError` and NOT from
    # `httpx.HTTPError` — so an `except httpx.HTTPError` here caught nothing and a rejected vendor
    # call, or an Azure Conditional Access block, escaped as an unhandled 500 with a stack trace
    # instead of the 502 this route's docstring promises. httpx stays in the tuple for anything
    # raised outside the client.
    except (MorganLloydError, httpx.HTTPError) as exc:
        raise MorganLloydUnavailable(_explain(exc)) from exc

    # The docs show the id nested under `contributionId.cam_contributionid`; accept the flat
    # spellings too rather than recording an empty link if the instance differs.
    nested = response.get("contributionId")
    if isinstance(nested, dict):
        value = nested.get("cam_contributionid")
        if isinstance(value, str) and value:
            return value
    for key in ("cam_contributionid", "contributionId", "id"):
        value = response.get(key)
        if isinstance(value, str) and value:
            return value
    raise MorganLloydUnavailable(
        "Morgan Lloyd recorded the contribution, but confirmation could not be saved. "
        "Contact support."
    )


def _require_client() -> Any:
    """The vendor client, or the 503 that says why it is absent."""
    if not configured():
        raise unconfigured("MORGAN_LLOYD_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET")
    from morgan_lloyd.client import MorganLloydClient

    return MorganLloydClient


async def fetch_status(contribution: dict[str, Any]) -> int | None:
    """Read ``cam_status`` back for one contribution, or None if ML no longer lists it.

    There is no "get one contribution" action, so this lists the scheme's applications and
    picks ours out by GUID. That is the vendor's shape, not a shortcut: the documented reads
    are ``cam_getpensioncontributionapplications`` (per scheme) and
    ``cam_getreceivedcontributions`` (a *different* entity with a different option set — see
    the module docstring), and only the first returns the record this GUID identifies.
    """
    scheme = contribution.get("ml_scheme_ref")
    contribution_id = contribution.get("ml_contribution_id")
    if not scheme or not contribution_id:
        raise ValueError(
            "This contribution has not been recorded in Morgan Lloyd, so it has no status "
            "there yet."
        )
    client = _require_client()
    try:
        async with client(settings=_settings()) as api:
            applications = await api.get_pension_contribution_applications(str(scheme))
    except (MorganLloydError, httpx.HTTPError) as exc:
        raise MorganLloydUnavailable(_explain(exc)) from exc

    wanted = str(contribution_id).lower()
    for application in applications:
        if str(application.get("cam_contributionid") or "").lower() == wanted:
            status = application.get("cam_status")
            return int(status) if status is not None else None
    return None


async def update_application(
    contribution: dict[str, Any], *, amount: float | Decimal | None = None
) -> dict[str, Any]:
    """Amend a Morgan Lloyd Contribution Application's amount.

    Refuses unless the record is at Application — see :class:`NotAnApplication`. The status is
    re-read from Morgan Lloyd first rather than trusted from our mirror: the mirror is only as
    fresh as the last refresh, and the whole point of the gate is that somebody else may have
    moved the record on since.
    """
    if amount is None:
        raise ValueError("Nothing to update — give a new amount.")
    if _money(amount) <= 0:
        raise ValueError("Enter an amount greater than zero.")

    current = await fetch_status(contribution)
    if current is None:
        raise MorganLloydUnavailable(
            "Morgan Lloyd no longer lists this contribution application against the scheme."
        )
    if current != STATUS_APPLICATION:
        raise NotAnApplication(
            f"Morgan Lloyd has this contribution at "
            f"'{STATUS_LABELS.get(current, current)}'. Only an application can be amended — "
            "stop it and record a new one instead."
        )

    client = _require_client()
    try:
        async with client(settings=_settings()) as api:
            response = await api.update_contribution_application(
                str(contribution["ml_contribution_id"]), {"cam_amount": _money(amount)}
            )
    except (MorganLloydError, httpx.HTTPError) as exc:
        raise MorganLloydUnavailable(_explain(exc)) from exc
    return {"ml_status": STATUS_APPLICATION, "response": response}


async def stop_application(contribution: dict[str, Any]) -> dict[str, Any]:
    """Stop a contribution at Morgan Lloyd (``cam_status`` -> Stopped).

    Two different vendor calls do this depending on where the record is, and sending the wrong
    one fails: an *application* is stopped through ``cam_updatecontributionapplication`` (the
    only documented status value it accepts is Stopped), while a live contribution is stopped
    by PATCHing the entity. The status is read first so the right one is chosen.
    """
    contribution_id = contribution.get("ml_contribution_id")
    if not contribution_id:
        raise ValueError("This contribution has not been recorded in Morgan Lloyd.")

    current = await fetch_status(contribution)
    if current == STATUS_STOPPED:
        return {"ml_status": STATUS_STOPPED, "response": {"already_stopped": True}}

    client = _require_client()
    try:
        async with client(settings=_settings()) as api:
            if current == STATUS_APPLICATION:
                response: Any = await api.update_contribution_application(
                    str(contribution_id), {"cam_status": STATUS_STOPPED}
                )
            else:
                response = await api.update_pension_contribution_status(
                    str(contribution_id), STATUS_STOPPED
                )
    except (MorganLloydError, httpx.HTTPError) as exc:
        raise MorganLloydUnavailable(_explain(exc)) from exc
    return {"ml_status": STATUS_STOPPED, "response": response}
