"""Benefits / drawdown against the Morgan Lloyd Dynamics API.

Drawdown itself is CTC's DCube journey, but we never talk to CTC for it: Morgan Lloyd creates
the benefit application and hands back a one-time ``ssoUrl`` with the loginId + ssoToken already
embedded. So this module needs Morgan Lloyd credentials only -- there are no CTC credentials in
the drawdown path at all.

``illustrationTemplate`` is optional in the vendor OpenAPI and the mint still succeeds without
it. DCube loads it after SSO (``docs/morgan-lloyd/workflows.md`` step 3). The payload is the
same shape ``services/morgan-lloyd/scripts/generate_illustrations.py`` always sent. A 500 on
the SSO URL itself is CTC ``SSOLogin`` (``docs/gotchas.md``), not a missing template.

``morgan_lloyd`` is imported lazily. It is a uv workspace member (so it is present in the shared
venv) but is not yet a declared dependency of apps/api -- see the note in pyproject.toml. The
lazy import also keeps the API importable on a machine where the package is absent: the failure
then surfaces as a clean "not configured" rather than an ImportError at startup.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from morgan_lloyd import MorganLloydError

from app.integrations.ctc.xml_builder import projection_exit_age

logger = logging.getLogger(__name__)


class MorganLloydNotConfigured(RuntimeError):
    """Raised when a Morgan Lloyd call is attempted without usable credentials."""


class MorganLloydUnavailable(RuntimeError):
    """Morgan Lloyd (or Azure AD) refused the call. Message is safe to show an adviser."""


# Azure returns its diagnosis as an AADSTSnnnnn code inside a long HTML/JSON blob. Pull the code
# and the first sentence out; the rest is trace ids and a help link.
_AADSTS = re.compile(r"(AADSTS\d+): ([^.]+\.)")

# Conditional Access blocks token issuance by network location among other things, so it is the
# expected failure from a developer laptop even with correct credentials -- exactly like SEI's
# source-IP allowlist. Worth naming so it isn't mistaken for a rotated secret.
_CONDITIONAL_ACCESS = "AADSTS53003"


def _explain(exc: Exception) -> str:
    """A one-line, secret-free explanation of a vendor HTTP failure."""
    body = ""
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text
    match = _AADSTS.search(body)
    if match is None:
        return f"Morgan Lloyd could not be reached: {exc}"
    code, sentence = match.group(1), match.group(2).strip()
    if code == _CONDITIONAL_ACCESS:
        return (
            "Azure AD blocked this token request by Conditional Access policy "
            f"({code}). The credentials are accepted; the request has to come from an "
            "allowlisted network — run it from the deployed host, not a laptop."
        )
    return f"Azure AD rejected the Morgan Lloyd credentials ({code}): {sentence}"


def _settings() -> Any:
    """The vendor settings, but reading the API's dotenv pair rather than its own default.

    The two-file convention (``.env`` then ``.env.local``, later winning) now lives on
    ``MorganLloydSettings`` itself. It used to be passed here and ONLY here, which meant every
    other construction site -- ``sync.py``, ``platform_actions``, the client's own default --
    silently read an unconfigured object from credentials sitting exactly where
    ``.env.example`` says to put them.
    """
    try:
        from morgan_lloyd.settings import MorganLloydSettings
    except ImportError as exc:  # pragma: no cover - only when the package is missing
        raise MorganLloydNotConfigured(
            "The morgan-lloyd client is not installed in this environment."
        ) from exc
    return MorganLloydSettings()


UNAVAILABLE = "Morgan Lloyd is unavailable for this firm. Contact support."

# CTC's own vocabularies (xml_builder.SEXES / MARITAL_STATUSES). Unmapped values stay None.
_CTC_SEX = {"male": "Male", "female": "Female"}
_CTC_MARITAL = {
    "single": "Single",
    "married": "Married",
    "civil_partner": "Civil Partnership",
    "divorced": "Divorced",
    "cohabiting": "Partnered",
    "widowed": "Widowed",
}

# Charges on the seed illustrations that produced walkable DCube URLs. This template is what
# DCube loads on the first page, not a client-facing projection the adviser signed off.
_FUND_CHARGE_PCT = 0.45
_ADVISER_FEE_PCT = 0.75


def _xml_child(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, tag, attrs)
    if text is not None:
        element.text = text
    return element


def _iso_date(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text[:10] if text else None


def drawdown_illustration_template(
    *,
    title: str | None,
    first_name: str | None,
    surname: str | None,
    gender: str | None,
    marital_status: str | None,
    date_of_birth: object,
    retirement_age: int | None,
    fund_value: float,
    output_reference: str,
    agent_name: str,
    effective_date: str | None = None,
) -> str:
    """CTC Bespoke XML v1.6 (CTC1) flexi-access drawdown request for DCube.

    Matches ``generate_illustrations.build_illustration_xml`` except ``quotation_type`` is
    ``Flexi Access Drawdown`` (plain ``Drawdown`` is capped, closed to new members) and there is
    no XML declaration (the vendor example has none; Python's uses single quotes).
    """
    as_of = effective_date or date.today().isoformat()
    # Known identity only. Inventing a date of birth or sex (the old 1960-01-01 / Male
    # fallbacks) would load DCube on a made-up member after SSO. CTC marks those elements
    # optional; omitting them is honest, a guess is not.
    dob = _iso_date(date_of_birth)
    sex = _CTC_SEX.get(str(gender or "").lower())
    marital = _CTC_MARITAL.get(str(marital_status or "").lower()) or "Unknown"
    exit_age = int(retirement_age) if retirement_age else 65
    # The same term the pension-illustration form uses. A client already past retirement —
    # ordinary for a pension in payment — still gets a quote, running to a future age rather
    # than to a maturity date that has already happened. A date we cannot read leaves the
    # recorded age; there is nothing to measure a term from.
    if dob:
        try:
            born = date.fromisoformat(dob)
            as_of_date = date.fromisoformat(as_of)
        except ValueError:
            pass
        else:
            exit_age = projection_exit_age(
                retirement_age=exit_age,
                born=born,
                effective=as_of_date,
                drawdown=True,
            )
    fund = max(float(fund_value or 0), 0.0)
    monthly = round(fund * 0.04 / 12, 2) if fund else 1.0
    if monthly <= 0:
        monthly = 1.0

    message = ET.Element("message")
    header = _xml_child(message, "header")
    _xml_child(header, "message_version", "CTC1")
    _xml_child(header, "created_date", f"{as_of} 09:00:00")
    _xml_child(header, "mode", "Request")

    content = _xml_child(message, "content")
    quotations = _xml_child(content, "quotations")
    _xml_child(quotations, "effective_date", as_of)
    _xml_child(quotations, "output_required", "Yes")
    _xml_child(quotations, "output_reference", output_reference[:45])
    _xml_child(quotations, "agent_name", agent_name[:130] or "Graphene Platforms")
    _xml_child(quotations, "client_type", "Individual")

    investor = _xml_child(quotations, "investor")
    _xml_child(investor, "title", (title or "Mx")[:20])
    _xml_child(investor, "forename", (first_name or "Member")[:50])
    _xml_child(investor, "surname", (surname or "Unknown")[:50])
    if sex:
        _xml_child(investor, "sex", sex)
    _xml_child(investor, "life_type", "Applicant")
    _xml_child(investor, "marital_status", marital)
    if dob:
        _xml_child(investor, "date_of_birth", dob)
    _xml_child(investor, "exit_age", str(exit_age))

    quotation = _xml_child(
        quotations,
        "quotation",
        product_ref="SIPPEN",
        product_type="Self Invested Personal Pension",
    )
    _xml_child(quotation, "quotation_type", "Flexi Access Drawdown")

    stream = _xml_child(quotation, "plan_stream", plan_stream_key="FUND1")
    _xml_child(stream, "contribution_type", "Existing Fund Value")
    _xml_child(stream, "amount", f"{fund:.2f}")
    _xml_child(stream, "frequency", "Single")
    _xml_child(stream, "crystallised", "No")

    premium_split = _xml_child(quotation, "premium_split")
    _xml_child(premium_split, "stream_applicable", plan_stream_key="FUND1")
    fund_split = _xml_child(premium_split, "fund_split")
    _xml_child(fund_split, "fund_ref", "GRAPHENE-GENERIC")
    _xml_child(fund_split, "fund_name", "Balanced Portfolio (generic test fund)")
    _xml_child(fund_split, "split_per", "100")
    _xml_child(fund_split, "rate_of_return", "5.0")
    _xml_child(fund_split, "management_charge", f"{_FUND_CHARGE_PCT:.2f}")
    _xml_child(fund_split, "generic_fund", "Yes")

    withdrawals = _xml_child(quotation, "withdrawals")
    _xml_child(withdrawals, "basis", "Fixed")
    _xml_child(withdrawals, "amount", f"{monthly:.2f}")
    _xml_child(withdrawals, "frequency", "Monthly")
    _xml_child(withdrawals, "start_date", as_of)
    _xml_child(withdrawals, "drawdown_type", "Flexible")

    crystallisation = _xml_child(quotation, "crystallisation")
    _xml_child(crystallisation, "drawdown_type", "Flexible")
    _xml_child(crystallisation, "amount", f"{fund:.2f}")
    _xml_child(crystallisation, "frequency", "Single")
    _xml_child(crystallisation, "antytxfcsh", type="Maximum")

    adviser_fees = _xml_child(quotation, "adviser_fees")
    adviser_fee = _xml_child(adviser_fees, "adviser_fee")
    _xml_child(adviser_fee, "stream_applicable", plan_stream_key="FUND1")
    _xml_child(adviser_fee, "fee_basis", "Ongoing")
    _xml_child(adviser_fee, "fee_frequency", "Monthly")
    _xml_child(adviser_fee, "fee_timing", "In Arrears")
    _xml_child(adviser_fee, "fee_value_rate", "Per Annum")
    fee_start = _xml_child(adviser_fee, "fee_start")
    _xml_child(fee_start, "start_type", "Immediately")
    fee_end = _xml_child(adviser_fee, "fee_end")
    _xml_child(fee_end, "end_type", "Maturity")
    _xml_child(adviser_fee, "percentage", f"{_ADVISER_FEE_PCT:.2f}")
    _xml_child(adviser_fee, "plus_vat", "No")

    ET.indent(message)
    # OpenAPI's example has no XML declaration and uses CRLF. Python's declaration uses
    # single quotes (`encoding='utf-8'`), which is the first thing a strict parser rejects.
    return ET.tostring(message, encoding="unicode").replace("\n", "\r\n")


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def drawdown_kwargs(account: Mapping[str, Any], *, agent_name: str) -> dict[str, Any]:
    """Vendor arguments for :func:`drawdown_session` from an account-and-client row.

    Cash and crystallised valuations are omitted when the book has no figure — they are
    optional on ``cam_setmemberdetails``. Inventing a percentage of the pot is not a valuation.
    """
    asset = _optional_float(account.get("value")) or 0.0
    cash = _optional_float(account.get("cash_value"))
    crystallised = _optional_float(account.get("crystallised_value"))
    params: dict[str, Any] = {
        "scheme_ref": str(account["ml_scheme_ref"]) if account.get("ml_scheme_ref") else None,
        "asset_valuation": asset,
        "illustration_template": drawdown_illustration_template(
            title=str(account["title"]) if account.get("title") else None,
            first_name=str(account["first_name"]) if account.get("first_name") else None,
            surname=str(account["surname"]) if account.get("surname") else None,
            gender=str(account["gender"]) if account.get("gender") else None,
            marital_status=(
                str(account["marital_status"]) if account.get("marital_status") else None
            ),
            date_of_birth=account.get("dob"),
            retirement_age=(
                int(account["retirement_age"]) if account.get("retirement_age") else None
            ),
            fund_value=asset,
            output_reference=f"DD{account['uid']}"[:45],
            agent_name=agent_name or "Graphene Platforms",
        ),
        "policy_number": str(account["ml_policy_ref"]) if account.get("ml_policy_ref") else None,
    }
    if cash is not None:
        params["cash_valuation"] = cash
    if crystallised is not None:
        params["crystallised_value"] = crystallised
    return params


def sso_url_with_policy(sso_url: str | None, policy_number: str | None) -> str | None:
    """DCube's SSO form reads ``policyNumber`` from the hash query. ML's ``ssoUrl`` omits it.

    The SPA posts ``DrawdownSetup/User/SSOLogin`` with
    ``{providerSSO, loginId, ssoToken, policyNumber}``. The policy number is the one
    ``cam_createwrapper`` minted (this account's uid).
    """
    if not sso_url or not policy_number:
        return sso_url
    parsed = urlparse(sso_url)
    fragment = parsed.fragment
    if fragment:
        path, _, query = fragment.partition("?")
        params = dict(parse_qsl(query, keep_blank_values=True))
        params.setdefault("policyNumber", policy_number)
        return urlunparse(parsed._replace(fragment=f"{path}?{urlencode(params)}"))
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params.setdefault("policyNumber", policy_number)
    return urlunparse(parsed._replace(query=urlencode(params)))


def unconfigured(reason: str) -> MorganLloydNotConfigured:
    """The 503 for a missing setting, with the setting LOGGED rather than shown.

    A firm reads this message and cannot act on it: a missing credential is Graphene's to put
    right, so naming ``MORGAN_LLOYD_TENANT_ID`` at an adviser tells them nothing they can use.
    Dropping the name outright is the other way to be wrong, because "unavailable" alone is
    indistinguishable from a vendor outage — so it goes to the log, which is where whoever can
    fix it is looking.
    """
    logger.warning("Morgan Lloyd call refused, not configured: %s", reason)
    return MorganLloydNotConfigured(UNAVAILABLE)


def configured() -> bool:
    """True when the Azure client-credentials triple is present.

    Credentials come from ``MORGAN_LLOYD_*`` env vars read by the vendor settings class, not
    from ``app.config`` -- one definition of the connection, shared with services/morgan-lloyd.
    """
    try:
        settings = _settings()
    except MorganLloydNotConfigured:
        return False
    return bool(settings.tenant_id and settings.client_id and settings.client_secret)


async def drawdown_session(
    contact_id: str,
    *,
    advised: bool = True,
    scheme_ref: str | None = None,
    asset_valuation: float | None = None,
    cash_valuation: float | None = None,
    crystallised_value: float | None = None,
    illustration_template: str | None = None,
    policy_number: str | None = None,
) -> dict[str, Any]:
    """Create a benefit application and return its one-time drawdown SSO link.

    ``POST cam_setmemberdetails``. The response key is ``ssoUrl`` in the example JSON but
    ``ssoURL`` in the docs table, so both are accepted -- whichever the tenant's instance
    actually returns. The token is single-use: never cache the result.

    ``illustrationTemplate`` is what DCube loads after SSO. Minting without it still returns
    a URL; the journey then has no illustration document.

    Raises ``MorganLloydNotConfigured`` when credentials are absent, so callers can answer 503
    rather than leaking a vendor auth error.
    """
    if not configured():
        raise unconfigured("MORGAN_LLOYD_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET")
    from morgan_lloyd.client import MorganLloydClient

    application: dict[str, Any] = {"contactId": contact_id, "advised": advised}
    if scheme_ref:
        application["schemeRef"] = scheme_ref
    if asset_valuation is not None:
        application["assetValuation"] = asset_valuation
    if cash_valuation is not None:
        application["cashValuation"] = cash_valuation
    if crystallised_value is not None:
        application["crystallisedValue"] = crystallised_value
    if illustration_template:
        application["illustrationTemplate"] = illustration_template

    try:
        async with MorganLloydClient(settings=_settings()) as api:
            response = await api.create_benefit_application(application)
    # THE CLIENT DOES NOT RAISE httpx. `morgan_lloyd._base` wraps every transport and status
    # error into `MorganLloydApiError`, which descends from `MorganLloydError` and NOT from
    # `httpx.HTTPError` — so an `except httpx.HTTPError` here caught nothing and a rejected vendor
    # call, or an Azure Conditional Access block, escaped as an unhandled 500 with a stack trace
    # instead of the 502 this route's docstring promises. httpx stays in the tuple for anything
    # raised outside the client.
    except (MorganLloydError, httpx.HTTPError) as exc:
        # Token or API failure. Never let the vendor's raw body (which echoes request details)
        # reach the portal; _explain reduces it to one safe line.
        raise MorganLloydUnavailable(_explain(exc)) from exc

    sso_url = response.get("ssoUrl") or response.get("ssoURL")
    return {
        "sso_url": sso_url_with_policy(
            sso_url if isinstance(sso_url, str) else None, policy_number
        ),
        "benefit_application_id": response.get("benefitApplicationId"),
    }
