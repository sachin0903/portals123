"""Lodge a payment expectation at SEI as an Anticipated Cash Receipt.

A thin seam over ``pkgs/sei`` so routers never import the vendor client directly and
"is it configured?" is answered in one place — the same shape as
``app/integrations/morgan_lloyd``.

**What an ACR is.** SEI has no ``receiptType = ACR`` field. An anticipated receipt is a cash
receipt scheduled for a future ``dueDate`` with no ``payerInfo``: nothing is collected, SEI
simply expects the money by that date and reconciles the credit against it when it lands.
Adding a payer profile is what turns the same call into a BACS Direct Debit — out of scope,
because a payer profile is data we do not hold.

**What SEI needs and we can't derive.** Three values are external unknowns:

- ``accountNumber`` / ``portfolioId`` — the account's identifiers *at SEI*. Held as
  ``platform.links`` rows (provider ``sei``, link types ``primary`` and
  ``portfolio``). Enrolment's portfolio value is a global id
  (``3108-SWP-I31-59103919``); the receipt takes the trailing entity id.
- ``descriptionTypeCode`` / ``treatmentTypeCode`` — firm-configured reference data that
  classifies the movement and its tax treatment at SEI's end. SEI validates the pair against
  the transaction type *and* the caller's role, and publishes no lookup service, so they are
  configuration on the tenant's SEI provider row.
- ``firmId`` — required because our SEI users are Processing-Organisation level.

Rather than guess any of them, :func:`missing_fields` names what is unset and the caller
answers 422. Instructing a custodian to expect money on a client's account is not somewhere
to substitute a plausible default.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.integrations.sei import reference

#: Tenant provider config keys (``platform.tenant_platforms.config``) holding the SEI
#: reference data above. Named here so the router, the error message and the admin UI agree.
CONFIG_FIRM_ID = "firm_id"
CONFIG_DESCRIPTION_CODE = "contribution_description_code"
CONFIG_TREATMENT_CODES = "contribution_treatment_codes"
#: A transfer is not a contribution. Reusing the contribution codes would tell SEI a pension
#: transfer-in was a personal contribution, and SEI validates the pair against the caller's role.
CONFIG_TRANSFER_DESCRIPTION_CODE = "transfer_description_code"
CONFIG_TRANSFER_TREATMENT_CODE = "transfer_treatment_code"
CONFIG_PLATFORM_USER = "platform_user"

#: Graphene contribution category -> the config key holding its SEI treatment code. The
#: treatment code is what actually encodes "personal vs employer" at SEI, so it is per
#: category, not one value for the firm.
TREATMENT_CODE_FOR: dict[str, str] = {
    "personal_contribution": "personal",
    "employer_contribution": "employer",
    "third_party_contribution": "third_party",
}


class SeiNotConfigured(RuntimeError):
    """Raised when a SEI call is attempted without usable credentials."""


class SeiUnavailable(RuntimeError):
    """SEI refused the call, or the answer was lost. Message is safe to show an adviser.

    ``ambiguous`` means the payment may already have been taken: no HTTP status,
    or a 5xx. A 4xx is a refusal, and the instruction can be sent again.

    The call's evidence (``status_code``, the ``request`` body we sent, the ``response`` SEI gave,
    the ``url``) rides on the error so the money leaf can record it on the attempt — the run detail
    then shows a refused call, not a blank. The full response body is also in ``audit.api_log``.
    """

    def __init__(
        self,
        message: str,
        *,
        ambiguous: bool = True,
        status_code: int | None = None,
        url: str | None = None,
        request: Any = None,
        response: Any = None,
    ) -> None:
        super().__init__(message)
        self.ambiguous = ambiguous
        self.status_code = status_code
        self.url = url
        self.request = request
        self.response = response


def response_was_lost(status: int | None) -> bool:
    """No status, or a 5xx. SEI may have accepted the payment."""
    return status is None or status >= 500


def _as_payload(value: Any) -> Any:
    """A JSON-ready copy of a request/response body for the attempt evidence (best-effort)."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(by_alias=True, exclude_none=True)
        except Exception:  # noqa: BLE001 - evidence must never fail the call
            return None
    return value


def _api_error_body(exc: Any) -> Any:
    """What SEI said, for the attempt evidence: its ``errors[]`` when it parsed, else the message.
    The full (often HTML) response body stays in ``audit.api_log``."""
    errors = getattr(exc, "errors", None)
    return {"errors": errors} if errors else {"detail": str(exc)}


def money_out_held(data: object) -> bool:
    """This instruction already went to SEI and the answer did not come back."""
    return isinstance(data, dict) and data.get("sei_inflight") is True


def _settings(environment: str | None = None) -> Any:
    """The vendor settings, reading the API's dotenv pair rather than the package default.

    ``SeiSettings`` declares ``env_file=".env"``; the API's convention is
    ``(".env", ".env.local")`` with secrets in the latter, so pass that explicitly —
    otherwise ``SEI_I31_APPKEY`` placed where every other API secret lives is ignored.
    """
    try:
        from sei.settings import SeiSettings
    except ImportError as exc:  # pragma: no cover - only when the package is missing
        raise SeiNotConfigured("The sei client is not installed in this environment.") from exc

    from app.config import settings as app_settings

    env = environment or app_settings.sei_environment or "i31"
    resolved = SeiSettings.for_environment(env)
    # `for_environment` reads os.environ directly, which is right for the per-environment
    # SEI_{ENV}_* names but blind to the API's .env/.env.local pair. Fill any gap from a
    # pydantic-settings read of those files, so SEI_APPKEY placed where every other API
    # secret lives is honoured.
    fallback = SeiSettings(_env_file=(".env", ".env.local"), environment=env)
    for field in (
        # `pfx_base64`, not `pfx_path` — the path form is gone (Aug 2026), and while it existed
        # this list named it, so a `SEI_PFX_BASE64` placed in the API's own `.env.local` was
        # never picked up. The same divergence as `SeiSettings.configured`, one layer up.
        "pfx_base64",
        "pfx_password",
        "oauth_username",
        "oauth_password",
        "appkey",
        "appsecret",
        "base_url",
    ):
        if not getattr(resolved, field):
            setattr(resolved, field, getattr(fallback, field))
    return resolved


def configured(environment: str | None = None) -> bool:
    """True when the mTLS certificate, OAuth user and AppKey are all present."""
    try:
        return bool(_settings(environment).configured)
    except SeiNotConfigured:
        return False


def missing_fields(context: dict[str, Any]) -> list[str]:
    """What SEI needs for this account/contribution that we do not hold.

    ``context`` carries the resolved SEI ids and the tenant's provider config; see the module
    docstring for why each is external.
    """
    absent: list[str] = []
    if not context.get("sei_account_number"):
        absent.append("the account's SEI account number (links, provider 'sei')")
    if not context.get("sei_portfolio_id"):
        absent.append("the account's SEI portfolio id (links, link type 'portfolio')")
    if not context.get("firm_id"):
        absent.append(f"the tenant's SEI firm id (provider config '{CONFIG_FIRM_ID}')")
    if not context.get("description_code"):
        absent.append(f"a SEI description code (provider config '{CONFIG_DESCRIPTION_CODE}')")
    if not context.get("treatment_code"):
        product = context.get("product_type")
        if product and not reference.contribution_classifies(
            str(product), is_pension=context.get("is_pension")
        ):
            absent.append(f"a SEI description and treatment for this {product} account")
        else:
            category = str(context.get("category") or "")
            if category in TREATMENT_CODE_FOR:
                absent.append(
                    "a SEI treatment code for "
                    f"{category} (provider config '{CONFIG_TREATMENT_CODES}')"
                )
            else:
                absent.append(
                    "the payment's contribution category (personal, employer, or third party), "
                    f"which selects the treatment code (provider config '{CONFIG_TREATMENT_CODES}')"
                )
    return absent


def explain_missing(context: dict[str, Any]) -> str | None:
    """The 422 body: what is missing, plus the codes SEI's own Reference report publishes.

    Naming the codes is the whole difference between a blocker and a task. Before this, the
    refusal said only that a treatment code was unset, and nobody could find out what it should
    be — ``docs/contributions.md`` §4 carried it as the standing gap for months. The codes are
    still not filled in automatically: SEI validates the description/treatment pair against the
    caller's role, and a wrong pair is a rejection on a client's pension.
    """
    absent = missing_fields(context)
    if not absent:
        return None
    detail = "SEI requires these details before a payment expectation can be lodged: " + ", ".join(
        absent
    )
    category = str(context.get("category") or "")
    if not context.get("treatment_code"):
        detail += reference.suggestion_for(category)
    return detail


def resolve_transfer_codes(config: dict[str, Any] | None) -> dict[str, Any]:
    """The firm's SEI codes for a transfer, which are not the contribution ones."""
    config = config or {}
    return {
        "firm_id": config.get(CONFIG_FIRM_ID),
        "description_code": config.get(CONFIG_TRANSFER_DESCRIPTION_CODE),
        "treatment_code": config.get(CONFIG_TRANSFER_TREATMENT_CODE),
        "platform_user": config.get(CONFIG_PLATFORM_USER),
    }


def missing_transfer_fields(context: dict[str, Any]) -> list[str]:
    """What an anticipated cash receipt for a transfer needs and this record has not got."""
    absent: list[str] = []
    if not context.get("sei_account_number"):
        absent.append("the account's SEI account number (links, provider 'sei')")
    if not context.get("sei_portfolio_id"):
        absent.append("the account's SEI portfolio id (links, link type 'portfolio')")
    if not context.get("firm_id"):
        absent.append(f"the tenant's SEI firm id (provider config '{CONFIG_FIRM_ID}')")
    if not context.get("description_code"):
        absent.append(
            "a SEI description code for transfers "
            f"(provider config '{CONFIG_TRANSFER_DESCRIPTION_CODE}')"
        )
    if not context.get("treatment_code"):
        absent.append(
            "a SEI treatment code for transfers "
            f"(provider config '{CONFIG_TRANSFER_TREATMENT_CODE}')"
        )
    return absent


def explain_missing_transfer(context: dict[str, Any]) -> str | None:
    absent = missing_transfer_fields(context)
    if not absent:
        return None
    return (
        "SEI requires these details before a transfer's cash receipt can be lodged: "
        + ", ".join(absent)
    )


def resolve_codes(
    config: dict[str, Any] | None,
    category: str,
    *,
    product_type: str | None = None,
    is_pension: bool | None = None,
) -> dict[str, Any]:
    """Pull the firm's SEI reference codes out of its provider config.

    A pension selects the treatment from the contribution category. An ISA, JISA or GIA
    does not have that category: the Reference report's wrapper pair is the receipt.
    """
    config = config or {}
    if product_type is not None or is_pension is not None:
        if not reference.contribution_classifies(product_type, is_pension=is_pension):
            description, treatment = reference.receipt_pair(product_type)
            return {
                "firm_id": config.get(CONFIG_FIRM_ID),
                "description_code": description,
                "treatment_code": treatment,
            }
    treatments = config.get(CONFIG_TREATMENT_CODES)
    treatments = treatments if isinstance(treatments, dict) else {}
    key = TREATMENT_CODE_FOR.get(category)
    return {
        "firm_id": config.get(CONFIG_FIRM_ID),
        "description_code": config.get(CONFIG_DESCRIPTION_CODE),
        "treatment_code": treatments.get(key) if key else None,
    }


def fee_overrides(amount: object, description_id: object) -> list[Any] | None:
    """The receipt's ``feesAndCharges`` when this payment carries an adviser fee.

    A zero fee is omitted. A fee without the firm's fee type is omitted here; the
    caller refuses the lodge rather than send the money and drop the charge.
    """
    from sei.models import FeeOverride, money

    if isinstance(amount, bool) or not isinstance(amount, int | float | str):
        return None
    try:
        charged = float(amount)
    except ValueError:
        return None
    if charged <= 0 or not description_id:
        return None
    return [
        FeeOverride(
            fee_description_id=str(description_id),
            overriden_fee_amount=money(charged),
        )
    ]


def _portfolio_id(value: object) -> str:
    """The numeric id on the wire. A global enrolment id keeps only its entity id."""
    return reference.portfolio_entity_id(value) or str(value)


def build_receipt(context: dict[str, Any]) -> Any:
    """Map the resolved context onto SEI's ``Attributes`` payload.

    ``externalReference`` carries the contribution's uid so a credit landing at SEI can be
    tied back without depending on amount+date heuristics.
    """
    from sei.enums import ReceiptAmountType, ReceiptMethod
    from sei.models import Attributes, Once, ProcessingScheduleDetails, ReceiptInstructions, money

    due = context.get("due_date")
    product = str(context.get("product_type") or "").upper()
    amount = money(context["amount"])
    charges = fee_overrides(context.get("adviser_fee"), context.get("fee_description_id"))
    instructions = ReceiptInstructions(
        currency_code=context.get("currency") or "GBP",
        # BACS is the UK rail. For an ACR nothing is collected, so this describes how the
        # money is expected to arrive, not how it is taken.
        receipt_method=ReceiptMethod.BACS,
        description_type_code=str(context["description_code"]),
        treatment_type_code=str(context["treatment_code"]),
        treatment_code_tax_year=context.get("tax_year"),
        receipt_amount=amount,
        receipt_amount_type=ReceiptAmountType.GROSS_VALUE,
        # ISA and JISA only, and only on a subscription treatment. The amount is the
        # subscription SEI counts against the allowance.
        isa_contribution=amount if product in {"ISA", "JISA"} else None,
        external_reference=context.get("reference"),
        fees_and_charges=charges,
    )
    schedule = None
    if isinstance(due, date):
        schedule = ProcessingScheduleDetails(once=Once.on(due))
    return Attributes(
        account_number=str(context["sei_account_number"]),
        portfolio_id=_portfolio_id(context["sei_portfolio_id"]),
        firm_id=str(context["firm_id"]),
        receipt_instructions=instructions,
        processing_schedule_details=schedule,
    )


async def create_transfer_receipt(
    context: dict[str, Any], *, environment: str | None = None
) -> dict[str, Any]:
    """Lodge a transfer's cash as an anticipated receipt, using the transfer reference codes."""
    return await _lodge(context, explain_missing_transfer(context), environment)


def missing_disbursement_fields(context: dict[str, Any]) -> list[str]:
    """What a one-off BACS withdrawal needs and this account has not got."""
    absent: list[str] = []
    if not context.get("sei_account_number"):
        absent.append("the account's SEI account number (links, provider 'sei')")
    if not context.get("sei_portfolio_id"):
        absent.append("the account's SEI portfolio id (links, link type 'portfolio')")
    if not context.get("firm_id"):
        absent.append(f"the tenant's SEI firm id (provider config '{CONFIG_FIRM_ID}')")
    if not context.get("pay_to_profile_id"):
        absent.append("a SEI pay-to profile on this account")
    if not context.get("description_code") or not context.get("treatment_code"):
        absent.append("a SEI description and treatment for this withdrawal")
    return absent


def explain_missing_disbursement(context: dict[str, Any]) -> str | None:
    absent = missing_disbursement_fields(context)
    if not absent:
        return None
    return "SEI requires these details before a withdrawal can be sent: " + ", ".join(absent)


def resolve_disbursement_codes(
    config: dict[str, Any] | None, *, product_type: str | None
) -> dict[str, Any]:
    """The wrapper's withdrawal pair. A withdrawal is not a contribution category."""
    config = config or {}
    description, treatment = reference.disbursement_pair(product_type)
    return {
        "firm_id": config.get(CONFIG_FIRM_ID),
        "description_code": description,
        "treatment_code": treatment,
    }


def _payment_type(context: dict[str, Any], reference_text: str) -> dict[str, Any]:
    """The rail the pay-to profile was opened on.

    BACS is method 5, Faster Payment is method 10, SWIFT is method 7. The
    profile already holds the account, so the disbursement names the rail and
    the profile id.
    """
    rail = str(context.get("payment_method") or "BACS")
    note = reference_text or "Withdrawal"
    if rail == "FASTERPAYMENT":
        return {
            "fasterPayment": {
                "fasterPaymentPaymentMethod": 10,
                "fasterPaymentDetails": {
                    "paymentInformation": note[:140],
                    "payeeBankInstructions": "Withdrawal",
                },
            }
        }
    if rail == "SWIFT":
        return {"swift": 7}
    return {
        "bacs": {
            "bacsPaymentMethod": 5,
            "bacsDetails": {
                "destinationStatementText": "Withdrawal",
                "originationStatementText": note[:18],
            },
        }
    }


def _disbursement_fee(context: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The disbursement's ``feesAndChargesDetails`` when the withdrawal carries an adviser fee.

    v12 added this to the ad-hoc CurrencyDisbursement so an advice fee rides the money out, the
    way ``fee_overrides`` rides it on a receipt. The fee names a SEI fee TYPE (``feeTypeId``, the
    firm's initial-advice fee, resolved by ``_attach_adviser_fee``) and overrides the amount to
    exactly what the adviser charged. A zero fee, or one with no resolved fee type, is omitted —
    the caller refuses the send rather than drop the charge, the same contract as a payment.

    NB the request schema types this property as an array; SEI's own worked example wraps it as
    ``{"feesAndCharges": [...]}``. We send the typed array form — verify against the sandbox.
    """
    from sei.models import money

    amount = context.get("adviser_fee")
    fee_id = context.get("fee_description_id")
    if isinstance(amount, bool) or not isinstance(amount, int | float | str):
        return None
    try:
        charged = float(amount)
    except ValueError:
        return None
    if charged <= 0 or not fee_id:
        return None
    return [{"feeTypeId": str(fee_id), "overrideFeeAmount": money(charged), "isOverridden": "1"}]


def build_disbursement(context: dict[str, Any]) -> dict[str, Any]:
    """A one-off ``CurrencyDisbursement`` to the account's pay-to profile.

    UK amount is ``grossValue`` only. The schedule is omitted: this is an ad hoc
    payment, and a one-time schedule is a different instruction. An adviser fee, when the
    withdrawal carries one, rides as ``feesAndChargesDetails``; ``raise_cash`` (the adviser's
    Auto-disinvest switch) sells assets to fund the money out before it pays.
    """
    from sei.models import money

    reference_text = str(context.get("reference") or "Graphene")[:65]
    body: dict[str, Any] = {
        "accountNumber": str(context["sei_account_number"]),
        "portfolioId": _portfolio_id(context["sei_portfolio_id"]),
        "descriptionTypeCode": str(context["description_code"]),
        "treatmentTypeCode": str(context["treatment_code"]),
        "currencyCode": context.get("currency") or "GBP",
        "amountType": {"grossValue": money(context["amount"])},
        "paymentType": _payment_type(context, reference_text),
        "payToDetails": {"payToProfileId": str(context["pay_to_profile_id"])},
        "externalReferenceNumber": reference_text,
    }
    fee = _disbursement_fee(context)
    if fee is not None:
        body["feesAndChargesDetails"] = fee
    if context.get("raise_cash"):
        # Raise cash = sell assets to fund the money out, waiting for the raise to release before
        # it pays. The adviser's "Auto disinvest" switch maps to this (v12 raiseCashMethodOptions).
        body["processingDateOptions"] = {
            "raiseCashMethodOptions": {
                "requiredRaiseCashYes": {
                    "waitForRaiseCashToCompleteDetails": {"releaseCashImmediate": True}
                }
            }
        }
    return body


async def create_cash_disbursement(
    context: dict[str, Any], *, environment: str | None = None
) -> dict[str, Any]:
    """Pay the booked withdrawal and return what to record against it."""
    detail = explain_missing_disbursement(context)
    if detail:
        raise ValueError(detail)
    rail = str(context.get("payment_method") or "BACS")
    if rail not in {"BACS", "FASTERPAYMENT", "SWIFT"}:
        raise ValueError(f"A SEI withdrawal on the {rail} rail is not wired yet.")
    settings = _settings(environment)
    if not settings.configured:
        raise SeiNotConfigured("SEI is unavailable for this firm. Contact support.")
    from sei import SeiApiError, SeiAuthError, SeiClient, SeiConfigError

    disbursement = build_disbursement(context)
    request_body = _as_payload(disbursement)
    url = "/currencydisbursementservices/createcashdisbursement"
    try:
        async with SeiClient(settings) as api:
            result = await api.create_disbursement(str(context["firm_id"]), disbursement)
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
        detail = (
            f"SEI could not accept this request (HTTP {code})."
            if code
            else "SEI did not confirm this payment."
        )
        raise SeiUnavailable(
            detail,
            ambiguous=response_was_lost(code),
            status_code=code,
            url=url,
            request=request_body,
            response=_api_error_body(exc),
        ) from exc
    activity = _disbursement_id(result)
    if str(result.get("status") or "").upper() == "FAILURE" and not activity:
        raise SeiUnavailable(
            "SEI refused this withdrawal.", ambiguous=False, url=url, request=request_body
        )
    return {
        "activity_id": activity,
        "event_id": result.get("eventId"),
        "warnings": [
            {"code": w.get("code"), "message": w.get("message")}
            for w in result.get("warnings") or []
            if isinstance(w, dict) and (w.get("code") or w.get("message"))
        ],
        "request": disbursement,
    }


def _disbursement_id(result: dict[str, Any]) -> str | None:
    """The activity id, whether SEI left it on the details block or the unwrapped body."""
    details = result.get("createCurrencyDisbursementDetails")
    sources = [result]
    if isinstance(details, dict):
        sources.insert(0, details)
    for source in sources:
        value = source.get("currencyDisbursementId") or source.get("activityId")
        if value:
            return str(value)
    return None


async def create_anticipated_receipt(
    context: dict[str, Any], *, environment: str | None = None
) -> dict[str, Any]:
    """Lodge the expectation at SEI and return what to record against it.

    Raises ``ValueError`` when required details are missing (caller turns that into a 422),
    ``SeiNotConfigured`` without credentials (503), ``SeiUnavailable`` when SEI refuses (502).
    """
    return await _lodge(context, explain_missing(context), environment)


async def _lodge(
    context: dict[str, Any], detail: str | None, environment: str | None
) -> dict[str, Any]:
    if detail:
        raise ValueError(detail)
    settings = _settings(environment)
    if not settings.configured:
        raise SeiNotConfigured("SEI is unavailable for this firm. Contact support.")
    from sei import SeiApiError, SeiAuthError, SeiClient, SeiConfigError

    attributes = build_receipt(context)
    request_body = _as_payload(attributes)
    url = "/currencyreceiptservices/createcurrencyreceipt"
    try:
        async with SeiClient(settings) as api:
            result = await api.create_currency_receipt(attributes)
    except SeiConfigError as exc:
        raise SeiNotConfigured("SEI is unavailable for this firm. Contact support.") from exc
    except SeiAuthError as exc:
        # Auth/entitlement is a configuration refusal, not a lost answer: never anticipate twice.
        raise SeiUnavailable(
            "SEI could not accept this request.",
            ambiguous=False,
            url=url,
            request=request_body,
            response=_api_error_body(exc),
        ) from exc
    except SeiApiError as exc:
        # A 4xx is a definite refusal (ambiguous=False → park); a 5xx or lost answer is transient
        # (ambiguous=True → retry), the same split the disbursement path makes.
        status = getattr(exc, "status", None)
        code = status if isinstance(status, int) else None
        detail = (
            f"SEI could not accept this request (HTTP {code})."
            if code
            else "SEI could not accept this request. Try again later."
        )
        raise SeiUnavailable(
            detail,
            ambiguous=response_was_lost(code),
            status_code=code,
            url=url,
            request=request_body,
            response=_api_error_body(exc),
        ) from exc

    return {
        "activity_id": result.activity_id,
        "event_id": result.event_id,
        # A 200 with warnings means SEI accepted the receipt but changed something (an
        # approval step, an ignored instruction). Surfaced, not swallowed.
        "warnings": [
            {"code": w.code, "message": w.message} for w in result.warnings if w.code or w.message
        ],
        "request": attributes.model_dump(by_alias=True, exclude_none=True),
    }


def _explain(exc: Any) -> str:
    """One safe line from a SEI rejection, preferring its own error codes.

    SEI answers a bad payload with ``errors[]`` whose ``code`` is the actionable part — 1001
    is a missing firm id, 1006 a description/treatment code it does not recognise. Those are
    configuration problems an operator can fix, so they are worth naming precisely instead of
    reporting "HTTP 400".
    """
    from sei.enums import ERROR_CODES

    errors = getattr(exc, "errors", None)
    if isinstance(errors, list) and errors:
        parts: list[str] = []
        for error in errors:
            if not isinstance(error, dict):
                continue
            code = str(error.get("code") or "")
            detail = error.get("detail") or error.get("title") or ERROR_CODES.get(code) or ""
            parts.append(f"{code}: {detail}".strip(": ") if code else str(detail))
        if parts:
            return "SEI rejected the payment expectation — " + "; ".join(parts)
    return f"SEI could not be reached: {exc}"
