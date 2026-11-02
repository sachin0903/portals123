"""The pay-to profile the pay-to step creates from the bank details already collected.

SEI will not send a withdrawal without a profile id. The client's bank account is
that destination: it is attached to the party enrolment just opened, not registered
as a second person. A UK account is BACS (or Faster Payment when that was chosen).
An IBAN is SWIFT.
"""

from __future__ import annotations

from typing import Any

_ROLL_NUMBER = 18


def build_pay_to(
    *,
    firm_id: str,
    account_number: str,
    bank: dict[str, Any],
    external_party_id: str | None = None,
    party_id: str | None = None,
) -> dict[str, Any]:
    """The ``CreatePayToRecipientRequest2`` body for this account's own bank.

    ``party_id`` is SEI's client id when the person is already on the platform.
    Otherwise ``external_party_id`` is the uid enrolment registered them under.
    The two are not sent together.
    """
    # SEI validates this body as XML, in the schema's order. byPassApproval is
    # the last element. Putting it earlier makes the next field (the party id)
    # fail with "no child element is expected at this point".
    body: dict[str, Any] = {
        "usedBy": "Graphene",
        "accountNumber": account_number,
        "firmId": str(firm_id),
        "newPartyIndicator": False,
    }
    if party_id and str(party_id).isdigit():
        body["partyId"] = str(party_id)
    elif external_party_id:
        body["externalPartyId"] = str(external_party_id)
    # An existing party is not given a new address. SEI still requires both of
    # these, and rejects a missing indicator as null. False means "do not mark
    # a new address as primary". Home is the address type enrolment already
    # sent for the person. Both sit before the payment method in the schema.
    body["existingPartyAddressPrimaryIndicator"] = False
    body["existingPartyAddressProfileType"] = "Home"
    rail = _rail(bank)
    body["paymentMethod"] = rail
    if rail == "SWIFT":
        iban = _alnum(bank.get("iban"))
        bic = _alnum(bank.get("bic"))
        if not iban or not bic:
            raise ValueError("SEI pay-to needs an IBAN and a BIC.")
        body["swiftInfo"] = _swift(bank)
    else:
        sort_code = _digits(bank.get("sort_code"))
        account_number = _digits(bank.get("account_number"))
        if len(sort_code) != 6 or len(account_number) != 8:
            raise ValueError("SEI pay-to needs a 6-digit sort code and an 8-digit account number.")
        if rail == "FASTERPAYMENT":
            body["fasterInfo"] = _faster(bank)
        else:
            body["bacsInfo"] = _bacs(bank)
    body["byPassApproval"] = True
    return body


def profile_id_of(result: Any) -> str | None:
    """The new profile id, or None when SEI withheld it for approval (``0``)."""
    if not isinstance(result, dict):
        return None
    raw = result.get("profileId")
    if raw in (None, "", 0, "0"):
        for value in result.values():
            if isinstance(value, dict):
                found = profile_id_of(value)
                if found:
                    return found
        return None
    return str(raw)


def already_a_pay_to(exc: BaseException) -> bool:
    """SEI already holds this party, method and bank on the account (code 2004)."""
    errors = getattr(exc, "errors", None)
    if isinstance(errors, list):
        for error in errors:
            if isinstance(error, dict) and str(error.get("code") or "") == "2004":
                return True
    text = str(exc).lower()
    return "duplicate" in text and "pay" in text


def matching_profile(details: dict[str, Any], bank: dict[str, Any]) -> str | None:
    """The existing pay-to whose bank details are this account's."""
    recipients = details.get("listOfpayToRecipients") or []
    if not isinstance(recipients, list):
        return None
    want_account = _digits(bank.get("account_number"))
    want_sort = _digits(bank.get("sort_code"))
    want_iban = _alnum(bank.get("iban"))
    for item in recipients:
        if not isinstance(item, dict):
            continue
        bank_info = item.get("bankInfo")
        info = bank_info if isinstance(bank_info, dict) else {}
        same_account = want_account and _digits(info.get("bankAccountNumber")) == want_account
        same_sort = not want_sort or _digits(info.get("bankSortCode")) == want_sort
        same_iban = want_iban and _alnum(info.get("ibanNumber")) == want_iban
        if (same_account and same_sort) or same_iban:
            found = profile_id_of(item)
            if found:
                return found
    return None


def remembered_entries(
    current: list[dict[str, Any]], entry: dict[str, str]
) -> list[dict[str, Any]]:
    """Put this profile on the bank row a withdrawal will select.

    Onboarding stores the destination with no direction and no profile id.
    Appending a second row leaves that first row in front, so the withdrawal
    reads an empty profile. A payer-only row is a different destination and is
    left alone.
    """
    rows = [dict(item) for item in current if isinstance(item, dict)]
    for item in rows:
        direction = str(item.get("direction") or "").strip().lower()
        if direction not in {"", "pay_to"} or not _same_bank(item, entry):
            continue
        item["provider"] = entry.get("provider") or item.get("provider") or "sei"
        item["provider_ref"] = entry["provider_ref"]
        if direction != "pay_to":
            item["direction"] = "pay_to"
        if entry.get("method") and not item.get("method"):
            item["method"] = entry["method"]
        return rows
    rows.append(dict(entry))
    return rows


def _same_bank(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_iban = _alnum(left.get("iban"))
    right_iban = _alnum(right.get("iban"))
    if left_iban and right_iban:
        return left_iban == right_iban
    account = _digits(left.get("account_number"))
    sort_code = _digits(left.get("sort_code"))
    return bool(
        account
        and account == _digits(right.get("account_number"))
        and sort_code == _digits(right.get("sort_code"))
    )


def pay_to_entry(bank: dict[str, Any], profile_id: str) -> dict[str, str]:
    """The account-level bank entry a later withdrawal resolves."""
    method = str(bank.get("method") or "").strip().lower()
    if not method:
        method = "swift" if bank.get("iban") else "bacs"
    entry = {
        "direction": "pay_to",
        "provider": "sei",
        "provider_ref": str(profile_id),
        "method": method,
    }
    for key in ("name", "sort_code", "account_number", "iban", "bic", "bank"):
        value = str(bank.get(key) or "").strip()
        if value:
            entry[key] = value
    return entry


def _rail(bank: dict[str, Any]) -> str:
    method = str(bank.get("method") or "").strip().lower()
    if bank.get("iban"):
        return "SWIFT"
    if method == "faster_payment":
        return "FASTERPAYMENT"
    return "BACS"


def _bacs(bank: dict[str, Any]) -> dict[str, str]:
    name = str(bank.get("name") or "Account")[:_ROLL_NUMBER]
    return {
        "bacsAccountName": name,
        "bacsBeneficiaryReferenceRollNumber": name,
        "bacsDestinationSortCode": _digits(bank.get("sort_code")),
        "bacsPayToAccountNumber": _digits(bank.get("account_number")),
    }


def _faster(bank: dict[str, Any]) -> dict[str, str]:
    return {
        "fasterInstitutionalSortCode": _digits(bank.get("sort_code")),
        "fasterPayToAccountNumber": _digits(bank.get("account_number")),
    }


def _swift(bank: dict[str, Any]) -> dict[str, str]:
    bic = _alnum(bank.get("bic"))
    return {
        "swiftAccountInstructionBIC": bic,
        "swiftBeneficiaryAccountId": _alnum(bank.get("iban")),
        "swiftBeneficiaryAccountIdType": "IBAN",
        "swiftBeneficiaryIntitutionBIC": bic,
    }


def _digits(value: object) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())


def _alnum(value: object) -> str:
    return "".join(character for character in str(value or "").upper() if character.isalnum())
