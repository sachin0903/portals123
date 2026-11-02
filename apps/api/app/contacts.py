"""Contact (client) management — the firm self-service WRITE path (platform portal).

Reads go through RLS (routers/crm.py on ``tenant_session``); writes land here on a
service_role ``firm_session``, so every statement is EXPLICITLY scoped to the caller's own
``tenant_id`` (never a client-supplied one) — the same discipline as ``app.provisioning``.
The contact IS the client (Decision 8): a person/org party that owns accounts, linked to
logins via ``crm.contact_users`` and to staff via ``crm.client_advisers``. The DML audit
triggers (0002) capture every write here into ``audit.log``.
"""

import json
import re
from datetime import date, datetime
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import bank_check, national_insurance
from app.ids import new_id

ServiceType = Annotated[str, Field(pattern="^(advised|execution_only|discretionary)$")]
SubType = Annotated[str, Field(pattern="^(trust|company|charity)$")]

# The values every custodian can store. The portal's field types (`title`, `gender`,
# `marital`, `employment` in pkgs/ui forms/vocabularies.ts) offer the same lists, and
# 0235 is the CHECK. Employment is the eight Seccl and Morgan Lloyd both accept; SEI
# publishes no employment field, so it does not narrow this one. Title stays free text:
# a synced title outside the six still has to round-trip.
Gender = Literal["male", "female"]
MaritalStatus = Literal["single", "married", "divorced", "widowed"]
EmploymentStatus = Literal[
    "employed",
    "self_employed",
    "retired",
    "unemployed",
    "education",
    "care_under_16s",
    "care_over_16",
    "other",
]

# A client's team (`crm.client_advisers.role`, 0245). One `adviser` per client — the partial
# unique index `client_advisers_one_adviser_key` — and any number of the others. The CHECK
# `client_advisers_role_known` holds the same four words.
ClientTeamRole = Literal["adviser", "paraplanner", "administrator", "support"]
CLIENT_TEAM_ROLES: tuple[str, ...] = get_args(ClientTeamRole)
ADVISER_ROLE = "adviser"
# The role for staff who are on a team but were never given one: a replaced adviser (kept on the
# team, so they keep their access until the firm removes them), an adviser whose `is_adviser` is
# switched off, and a non-adviser who creates a client.
SUPPORT_ROLE = "support"


class UpdateContact(BaseModel):
    service_type: ServiceType | None = None
    sub_type: SubType | None = None
    title: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    surname: str | None = None
    name: str | None = None
    email: str | None = None
    enabled: bool | None = None
    address: dict[str, object] | None = None
    # The pension and identity facts (0052-0053). Morgan Lloyd requires gender and takes the
    # rest; onboarding collects marital status, employment status and retirement age. Vulnerability
    # is deliberately NOT here — it has its own validated endpoint.
    gender: Gender | None = None
    marital_status: MaritalStatus | None = None
    employment_status: EmploymentStatus | None = None
    retirement_age: int | None = Field(default=None, ge=50, le=75)
    # Two plain numbers. `mobile` is the one every custodian can store. `phone` is the
    # other number: Morgan Lloyd's `telephone1`. Seccl and SEI enrolment have no second slot.
    mobile: str | None = None
    phone: str | None = None
    source_of_wealth: str | None = None
    flexi_access_taken: bool | None = None
    # 0116 — the KYC answers onboarding has always collected and had nowhere to put. Free text
    # where the list is presentational and grows (occupation, source of funds, place of birth);
    # a Literal only for `tax_relief`, whose four values are all Morgan Lloyd will accept.
    #
    # `occupation` is NOT `employment_status`: what they do vs whether they work.
    occupation: str | None = None
    occupation_detail: str | None = None
    country_of_birth: str | None = None
    town_of_birth: str | None = None
    source_of_funds: str | None = None
    source_of_funds_detail: str | None = None
    tax_relief: (
        Literal[
            "uk_relevant_earnings",
            "uk_resident_this_year",
            "overseas_crown_employment_no_uk_earnings",
            "spouse_overseas_crown_employment",
        ]
        | None
    ) = None

    # ── The identity facts the record could not edit (0127) ─────────────────────────────────────
    #
    # THIS MODEL IS THE REAL ALLOWLIST, and `contacts._UPDATABLE` is the second one. Pydantic's
    # default is `extra='ignore'`, so a field absent HERE is dropped silently on the way in — it
    # never reaches `_UPDATABLE` to be refused, the PATCH answers 200, and the record reads back
    # unchanged. That is not hypothetical: `employer_name` and `relevant_uk_earnings` were added to
    # `_UPDATABLE` alone, so the client record's Employment fields posted, answered 200, and saved
    # nothing. Anything added to one list has to be added to both.
    #
    # `dob` and the identifier/residency block were never editable at all: onboarding wrote them and
    # nothing could correct them, so a date of birth typed wrong on a fact-find needed SQL — on the
    # column a pension projection and the over-75 relief check both read.
    dob: date | None = None
    national_identifier: str | None = None
    national_identifier_type: str | None = None
    # ISO codes from `ref.countries` (0050). Both are lists: a person may have more than one
    # nationality and may genuinely be tax-resident in more than one country.
    nationality: list[str] | None = None
    tax_residency: list[str] | None = None
    # The W-8/W-9 self-certification (0121), required by SEI enrolment and by nobody else. ASKED,
    # never derived from `tax_residency`: a signed declaration to a tax authority is the
    # `tax_relief` rule exactly. `fatca_status` is SEI's code set — 1 = W-9, 2 = W-8 — derived from
    # the answer at the boundary rather than asked twice.
    us_person: bool | None = None
    fatca_status: str | None = None
    fatca_certified_at: datetime | None = None
    # The passport SEI enrolment asks for and publishes straight back
    # (`COUNTRY_OF_CITIZENSHIP_FIRST_PASSPORT`), so the round trip exists. 0129 gave both columns
    # and neither an editor nor a reader, which is the failure 0127's whole restructure was about:
    # a field nobody can see is a field nobody fills in, and this one is a prerequisite for
    # enrolling a client at the custodian.
    passport_number: str | None = None
    # ISO 3166-1 alpha-2, joining `ref.countries` like `nationality` and `tax_residency` — NOT a
    # display name, which is why the read resolves it the same way.
    passport_country: str | None = None
    # Employment (0126 + 0127). `relevant_uk_earnings` is the HMRC relievable-limit input, shown
    # as "Annual earnings"; `employer_contributions` gates the Employer section and is asked
    # rather than derived from the ledger.
    relevant_uk_earnings: float | None = None
    employer_name: str | None = None
    employer_contributions: bool | None = None
    employer_registration_number: str | None = None
    employer_address: dict[str, object] | None = None
    # The employer's own bank account (0271): the scheme paying employer contributions in. The same
    # shape as `bank_account` and the same normaliser and sort-code directory, on the Employer card.
    employer_bank_account: dict[str, object] | None = None
    # WHEN, which is a different fact from WHETHER: the date decides which tax year the £10,000
    # MPAA first applies to, so a contribution booked for the year before is unaffected.
    flexi_access_date: date | None = None
    source_of_wealth_detail: str | None = None

    # Transport only, NOT a column: the reason recorded when a firm's four-eye policy holds the edit
    # for review. Excluded from `_UPDATABLE` below so it never reaches the SET builder.
    justification: str | None = None

    @field_validator("first_name", "surname")
    @classmethod
    def _validate_name_chars(cls, value: str | None) -> str | None:
        if value and not _is_person_name(value):
            raise ValueError(NAME_CHARS_ERROR)
        return value

    @field_validator("mobile", "phone")
    @classmethod
    def _validate_phone(cls, value: str | None) -> str | None:
        return validate_contact_phone(value)

    @model_validator(mode="after")
    def _national_insurance_number(self) -> "UpdateContact":
        """A National Insurance number is stored compact, and only when HMRC could have issued it.

        Other identifier types (a passport, CONCAT) are free text. The type is the regulator's
        name or the code; either counts.
        """
        if not national_insurance.is_nino_type(self.national_identifier_type):
            return self
        if self.national_identifier is None or not str(self.national_identifier).strip():
            return self
        compact = national_insurance.normalise(self.national_identifier)
        if compact is None:
            raise ValueError(national_insurance.FIELD_MESSAGE)
        self.national_identifier = compact
        return self


# COLUMNS A PATCH MAY SET, DERIVED FROM THE MODEL ABOVE — one list, not two.
#
# Whitelisted so the dynamic SET clause is built only from known identifiers, never from a client
# string, which is what keeps this injection-free.
#
# It was written out by hand beside `UpdateContact`, and the two lists were kept in step by a
# set-equality test in both directions. That detected the drift instead of preventing it, and paid
# for the duplication twice: once typing every new field in two files, once writing the test to
# catch what had already shipped. And only one half fails loudly — Pydantic's default is
# `extra='ignore'`, so a column named HERE and not on the model is dropped on the way IN, never
# reaches this allowlist to be refused, and the PATCH answers 200 with the record unchanged. That
# is exactly how 0126's `employer_name` and `relevant_uk_earnings` came to post, report success and
# save nothing.
#
# `bank_account` is the ONE deliberate extra: it has no field on `UpdateContact` because it goes
# through `PUT …/bank-account`'s own validated door (bank data gets one door, and the audit row it
# writes is then unambiguous about what changed), but it is written through this same SET builder.
_UPDATABLE = (frozenset(UpdateContact.model_fields) - {"justification"}) | {"bank_account"}

# `address`/`bank_account` are jsonb — they need an explicit cast + a JSON-encoded
# value (see the SET builder).
_JSONB = frozenset({"address", "bank_account", "employer_address", "employer_bank_account"})

# Columns a PATCH may NOT set to NULL, because the table forbids it (`enabled boolean NOT NULL`,
# 0002:125). An explicit `{"enabled": null}` would otherwise reach the UPDATE and 500 on the
# not-null constraint; it is dropped instead, exactly as an omitted field would be.
_NOT_NULLABLE = frozenset({"enabled"})

# ---------------------------------------------------------------------------------------------
# A CONTACT'S PHONE, DEFENDED AGAINST A PROVIDER OBJECT THAT REACHED THE COLUMN.
#
# Seccl publishes both of its numbers as OBJECTS -- {"number", "isMobile", "locale"} -- and the
# conformed model now extracts `->> 'number'` (services/seccl/models/platform/clients.sql). But
# `crm.contacts.mobile` (and `phone`) are text columns that accepted the whole object without
# complaint for as long as they did, so any deployment whose book was merged before that fix still
# holds rows reading `{"number": "07715215412", "isMobile": true, "locale": "en-GB"}` where a
# number belongs. Both columns are read through `phone_expr`.
#
# Fixing the data alone would not make us robust: the column is text, three providers write it, and
# the failure is SILENT -- nothing raises, the value simply appears on a client's record, in a
# client-facing email, and in the payload we send to Morgan Lloyd. So the read side extracts too.
#
# A REGEX, not a `::jsonb` cast. The cast is the obvious approach and it throws on every normal
# value in the column, so it would trade a cosmetic defect for a 500 on the clients listing.
# `substring` returns NULL when the pattern does not match, which is exactly the fallback wanted:
# a plain number matches nothing, coalesces to itself, and is returned untouched. Malformed JSON,
# a truncated object, an empty string and NULL are all handled by the same property -- there is
# nothing to parse, so there is nothing to fail.
_PHONE_NUMBER_RE = r'"number"\s*:\s*"([^"]*)"'


def phone_expr(column: str) -> str:
    """SQL that reads `column` as a phone number, whatever shape reached it.

    `column` is a literal from our own source at every call site -- never a request value -- so it
    is interpolated. Kept as one expression rather than copied per projection: it is used by the
    clients listing, the client record, the outbound Morgan Lloyd payload and the email/document
    merge context, and those four disagreeing is how a fix like this half-lands.
    """
    # A CASE, not a bare COALESCE, because `substring` answers NULL for "no match" and '' for
    # "matched, but the number was blank" — and COALESCE cannot tell those apart, so a blank
    # number fell through to the raw column and the whole object came back as the phone. The `~`
    # test asks whether this even IS the provider object, and only then extracts; `NULLIF` then
    # maps a blank number to NULL, because a blank is not a number. NULL is also the better answer
    # downstream: the portals render `phone ?? '—'`, which shows the dash for NULL and an empty
    # cell for ''.
    return (
        # TRIPLE-quoted rf-string, and both parts of that matter. `r` because `\s` is a REGEX
        # escape for Postgres, not a Python one — without it this raises a SyntaxWarning and works
        # only because Python still passes an unknown escape through unchanged, which is scheduled
        # to become a SyntaxError. Triple-quoted because in a RAW string `\'` is a literal
        # backslash followed by a quote, not an escaped quote, so the single-quoted form emits
        # `~ \'"number"...` and Postgres answers `syntax error at or near "\"`.
        rf"""CASE WHEN {column} ~ '"number"\s*:\s*"' """
        f"THEN NULLIF(substring({column} from '{_PHONE_NUMBER_RE}'), '') "
        f"ELSE {column} END"
    )


def normalise_phone(value: object) -> str | None:
    """The same rule for a value already in hand (a dict from a provider, a row read elsewhere).

    Accepts the object itself as well as its text form, because a caller reading a provider payload
    holds the former and a caller reading the book holds the latter.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        number = value.get("number")
        return str(number) if number not in (None, "") else None
    text_value = str(value)
    # The SAME pattern serves the SQL and this: a POSIX regex in a Postgres string literal
    # and a Python regex want identical text, so there is nothing to translate between them.
    match = re.search(_PHONE_NUMBER_RE, text_value)
    if match is None:
        return text_value
    # PARENTHESISED, and it matters. `match.group(1) or None if match else text_value` binds as
    # `(match.group(1) or None) if match else ...`, which is the same answer by accident and reads
    # as if the `or None` were the fallback for a failed match. Written out, the two questions are
    # separate: did the pattern match, and did it capture anything.
    return match.group(1) or None


# ITU-T E.164: at most 15 digits, no leading zero on the country code. A national number is
# accepted too (7–15 digits), because an overseas number is valid and we are not in a position to
# know its length.
#
# A `+44` NUMBER IS THE ONE WE DO KNOW, so it is checked: 11 digits nationally (07700 900123,
# 01234 567890), which is 10 after the +44. Without this a nine-digit number saved happily and only
# failed when somebody tried to ring it — the length is the one thing a form can catch at the point
# the mistake is made. The handful of legacy 10-digit UK ranges (0500, 016977) would be refused;
# they are vanishingly rare in a client book and this is the one place the rule lives if that
# changes.
_PHONE_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_PHONE_NATIONAL = re.compile(r"^\d{7,15}$")
_UK_NATIONAL_DIGITS = 10
PHONE_FORMAT_ERROR = (
    "Enter a phone number in international format (for example +447700900123) "
    "or a national number of 7–15 digits."
)
UK_PHONE_LENGTH_ERROR = "A UK phone number is 11 digits — for example 07700 900123 or 01234 567890."


# A UK number written with the trunk prefix is `0` plus 10 digits (07812 345678, 020 7946 0958).
# `00` is the international prefix, not the trunk: `0044…` is the same number dialled from abroad.
# A shorter leading `0` is left alone — `0612345678` is a French mobile, not a short UK one.
_UK_TRUNK = re.compile(r"^0[123789]\d{9}$")
_UK_TRUNK_RANGES = ("01", "02", "03", "07", "08", "09")


def _standardise_uk(compact: str) -> str:
    """`07812345678`, `00447812345678` and `+4407812345678` are one number: `+447812345678`."""
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    elif _UK_TRUNK.fullmatch(compact):
        compact = "+44" + compact[1:]
    # +44 written with the trunk 0 still in it.
    if compact.startswith("+440") and len(compact) == 14:
        compact = "+44" + compact[4:]
    return compact


def _incomplete_uk_trunk(compact: str) -> bool:
    """A trunk-0 number in a UK range that was not a full 11 digits, so it was not rewritten."""
    return (
        not compact.startswith("+")
        and not compact.startswith("00")
        and compact.startswith(_UK_TRUNK_RANGES)
        and not _UK_TRUNK.fullmatch(compact)
    )


def _uk_national(compact: str) -> str | None:
    """The national significant number of a `+44` value, or None when it is not one."""
    return compact[3:] if compact.startswith("+44") else None


def validate_contact_phone(value: object) -> str | None:
    """Normalise then refuse junk. Empty is None (clear).

    Whitespace counts as empty. Spaces typed into the box are a CLEARED field, not a malformed
    number, and answering "enter a number in international format" to someone who just emptied it
    is a refusal they cannot act on — they did the thing it is asking for.
    """
    number = normalise_phone(value)
    number = number.strip() if number else number
    if not number:
        return None
    compact = _standardise_uk(re.sub(r"[\s().-]", "", number))
    if _incomplete_uk_trunk(compact):
        raise ValueError(UK_PHONE_LENGTH_ERROR)
    if not (_PHONE_E164.fullmatch(compact) or _PHONE_NATIONAL.fullmatch(compact)):
        raise ValueError(PHONE_FORMAT_ERROR)
    # A bare number with no `+` and no leading `0` is neither an international number nor a national
    # one in any plan this book uses — `1111111` cleared E.164's seven-digit floor as though it were
    # somebody's country code plus five digits, and stored happily as a number nobody could ring.
    if not compact.startswith("+") and not compact.startswith("0"):
        raise ValueError(PHONE_FORMAT_ERROR)
    national = _uk_national(compact)
    if national is not None and len(national) != _UK_NATIONAL_DIGITS:
        raise ValueError(UK_PHONE_LENGTH_ERROR)
    return compact


def contact_phone(value: object) -> str | None:
    """One stored number, including a leftover labelled-list answer from before 0236.

    The fact-find used to post ``[{"label", "number"}]`` under ``other_numbers``. A case that
    still holds that shape writes the first number into ``phone`` rather than failing the step.
    """
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                number = validate_contact_phone(item.get("number"))
            else:
                number = validate_contact_phone(item)
            if number:
                return number
        return None
    if isinstance(value, dict):
        return validate_contact_phone(value.get("number"))
    return validate_contact_phone(value)


_RETURN = (
    "id, uid, type, service_type, sub_type, enabled, title, first_name, middle_name, "
    "surname, name, email, mobile, phone, address, bank_account, employer_bank_account"
)


# ---- bank details -----------------------------------------------------------------------
# `crm.contacts.bank_account` is free-form jsonb, which for BANK data is not good enough: a sort
# code keyed as "12 34 5" or an account number with a stray letter is stored happily and only fails
# when someone tries to pay against it. So the shape is normalised and validated on the way in, and
# this is the only door — `update_contact` runs every write through it, whichever field list the
# caller sent.
#
# Nothing here ever puts a digit into an exception message or a log line: an error names the field,
# not its value.
_BANK_FIELDS = ("name", "bank", "sort_code", "account_number", "iban", "bic")
_DIGITS = re.compile(r"[^0-9]")
_NOT_ALNUM = re.compile(r"[\s-]")
_IBAN_COMPACT = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
_BIC = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?$")

NAME_CHARS_ERROR = "Use letters, spaces, hyphens or apostrophes only — no numbers or other symbols."


def _is_person_name(value: str) -> bool:
    """A person's name: letters, spaces, hyphens or apostrophes only. `str.isalpha()` is
    Unicode-aware, so an accented name (François, Müller) still passes — an ASCII-only check
    would not. Shared by the contact's own name fields and the bank account holder's name;
    an organisation's `name` is deliberately NOT run through this (a firm can be "AT&T" or
    "3M UK Ltd")."""
    return all(ch.isalpha() or ch in " '-" for ch in value)


def _compact_alnum(raw: str) -> str:
    return _NOT_ALNUM.sub("", raw).upper()


def _iban_is_valid(iban: str) -> bool:
    """ISO 13616: rearrange, map A=10…Z=35, then mod 97 equals 1. Length 15–34."""
    if not _IBAN_COMPACT.fullmatch(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in rearranged)
    return int(numeric) % 97 == 1


#: The UK rail only — an international row always reaches SEI over SWIFT (having an IBAN already
#: says so), so there is nothing to choose there. BACS is free and is the default a firm need
#: never state; Faster Payment is the one some firms pay withdrawals by instead.
_BANK_METHODS = frozenset({"bacs", "faster_payment"})


def normalise_bank_account(value: object) -> dict[str, str] | None:
    """Validate + canonicalise one set of bank details, or ``None`` to clear them.

    Two rails, one object. UK (BACS) returns ``{name, sort_code, account_number}`` — sort code as
    ``12-34-56``, account number as 8 digits (a 7-digit number is left-padded, the Bacs convention)
    — plus ``method`` (``"faster_payment"``) when the firm has chosen that over BACS; BACS is the
    default and is never written, the same convention ``bank`` already follows. International
    returns ``{name, iban, bic}`` — IBAN compact uppercase with a mod-97 check, SWIFT/BIC 8 or 11
    characters — and carries no method, because an international payment always reaches SEI over
    SWIFT. ``scheme`` on the payload chooses the rail when the form has switched; stored rows do
    not carry it, because the fields are enough. An empty/blank object clears the details, so a
    form that blanks every field of the active rail removes them rather than storing ``{}``.

    Raises ``ValueError`` naming the offending field. Deliberately strict: a sort code that is not
    six digits, an IBAN that fails mod-97, is a keying slip, and a payment instruction built on it
    fails somewhere far less visible than this. Nothing here ever puts a digit into the message."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Bank details must be an object.")
    raw = {k: str(value.get(k) or "").strip() for k in _BANK_FIELDS}
    scheme = str(value.get("scheme") or "").strip().lower()
    if scheme not in ("", "bacs", "iban"):
        raise ValueError("Bank details must be UK or international.")
    # NOT validated here: the account-level door (`bank_accounts.normalise_entry`) calls this
    # function for the core fields alone and carries a WIDER method vocabulary of its own (CHAPS,
    # SWIFT, Direct Debit…) through the same `value` dict, validating it itself afterwards. Only
    # the one choice this shared core ever WRITES — Faster Payment — is read here; the strict
    # bacs-or-faster-payment check for the client-level door is `set_bank_account`'s.
    method = str(value.get("method") or "").strip().lower()
    if not any(raw.values()):
        return None  # every field blank => clear, not `{}`

    has_iban = bool(raw["iban"] or raw["bic"])
    has_bacs = bool(raw["sort_code"] or raw["account_number"])
    use_iban = scheme == "iban" or (has_iban and not has_bacs and scheme != "bacs")

    if use_iban:
        if not (raw["name"] or raw["iban"] or raw["bic"]):
            return None
        iban = _compact_alnum(raw["iban"])
        if not iban:
            raise ValueError("Enter an IBAN.")
        if not _iban_is_valid(iban):
            raise ValueError("Enter a valid IBAN.")
        bic = _compact_alnum(raw["bic"])
        if not bic:
            raise ValueError("Enter a SWIFT/BIC.")
        if not _BIC.fullmatch(bic):
            raise ValueError("Enter an 8 or 11-character SWIFT/BIC.")
        if not raw["name"]:
            raise ValueError("Enter the account holder's name.")
        if not _is_person_name(raw["name"]):
            raise ValueError(NAME_CHARS_ERROR)
        return {"name": raw["name"], "iban": iban, "bic": bic}

    if not any(raw[k] for k in ("name", "bank", "sort_code", "account_number")):
        return None

    sort_digits = _DIGITS.sub("", raw["sort_code"])
    if len(sort_digits) != 6:
        raise ValueError("Enter a 6-digit sort code.")
    acct_digits = _DIGITS.sub("", raw["account_number"])
    if len(acct_digits) not in (7, 8):
        raise ValueError("Enter an 8-digit account number.")
    if not raw["name"]:
        raise ValueError("Enter the account holder's name.")
    if not _is_person_name(raw["name"]):
        raise ValueError(NAME_CHARS_ERROR)

    out = {
        "name": raw["name"],
        "sort_code": f"{sort_digits[0:2]}-{sort_digits[2:4]}-{sort_digits[4:6]}",
        "account_number": acct_digits.rjust(8, "0"),
    }
    if raw["bank"]:
        out["bank"] = raw["bank"]
    if method == "faster_payment":
        out["method"] = method
    return out


#: The two bank columns on a contact: the client's own, and the employer's (0271). Both go through
#: the same normaliser and directory check; `column` picks which stored pair a save is compared to.
_BANK_COLUMNS = frozenset({"bank_account", "employer_bank_account"})


async def confirm_uk_bank(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    bank: dict[str, str] | None,
    column: str = "bank_account",
) -> dict[str, str] | None:
    """``bank`` after the sort-code directory has seen a new UK pair.

    The same pair is not asked again, and a name the directory already gave is kept when
    the save does not restate it. An unreachable directory leaves the details as they are.
    Raises ``BankDetailsRejected`` when the directory says the pair is not an account.
    """
    if bank is None or not bank_check.uk_pair(bank):
        return bank
    if column not in _BANK_COLUMNS:
        raise ValueError(f"Not a bank column: {column}")
    stored = (
        await session.execute(
            text(
                f"SELECT {column} FROM crm.contacts "  # noqa: S608 - column is allowlisted above
                "WHERE id = :c AND tenant_id = :t AND deleted_at IS NULL"
            ),
            {"c": contact_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if bank_check.same_pair(stored, bank):
        if isinstance(stored, dict) and stored.get("bank") and not bank.get("bank"):
            return {**bank, "bank": str(stored["bank"])}
        return bank
    return await bank_check.verified_bank(bank, user_id=await bank_check.acting_user(session))


async def _refuse_duplicate_national_insurance(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    fields: dict[str, object],
) -> None:
    """Store a National Insurance number compact, and refuse one another client already holds."""
    if "national_identifier" not in fields and "national_identifier_type" not in fields:
        return
    identifier = fields.get("national_identifier") if "national_identifier" in fields else None
    id_type = (
        fields.get("national_identifier_type") if "national_identifier_type" in fields else None
    )
    if "national_identifier" not in fields or "national_identifier_type" not in fields:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT national_identifier, national_identifier_type FROM crm.contacts "
                        "WHERE id = :c AND tenant_id = :t"
                    ),
                    {"c": contact_id, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return
        if "national_identifier" not in fields:
            identifier = row["national_identifier"]
        if "national_identifier_type" not in fields:
            id_type = row["national_identifier_type"]
    if not national_insurance.is_nino_type(id_type):
        return
    if identifier is None or not str(identifier).strip():
        return
    compact = national_insurance.normalise(identifier)
    if compact is None:
        raise ValueError(national_insurance.FIELD_MESSAGE)
    if "national_identifier" in fields:
        fields["national_identifier"] = compact
    taken = (
        await session.execute(
            text(
                "SELECT 1 FROM crm.contacts "
                "WHERE tenant_id = :t "
                "  AND upper(translate(coalesce(national_identifier, ''), ' -', '')) = :ni "
                "  AND id <> :c "
                "LIMIT 1"
            ),
            {"t": tenant_id, "ni": compact, "c": contact_id},
        )
    ).first()
    if taken:
        raise ValueError(national_insurance.IN_USE)


async def _contact_in_tenant(session: AsyncSession, tenant_id: int, contact_id: int) -> bool:
    return bool(
        (
            await session.execute(
                text("SELECT 1 FROM crm.contacts WHERE id = :c AND tenant_id = :t"),
                {"c": contact_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
    )


async def _user_in_tenant(session: AsyncSession, tenant_id: int, user_id: str) -> bool:
    return bool(
        (
            await session.execute(
                text("SELECT 1 FROM auth.users WHERE id = :u AND tenant_id = :t"),
                {"u": user_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
    )


async def _is_adviser(session: AsyncSession, tenant_id: int, user_id: str) -> bool:
    """Whether this login is one the firm has said may hold a book (`auth.users.type`, 0124).

    NOT an access check — `type` is never an authorisation input and the caller's own permission
    (`contacts:write`) is checked separately by the router. This asks a different question: of the
    logins in this firm, which ones are ADVISERS.

    Nothing asked it before, and the gap had two mouths. `_user_in_tenant` alone means any login in
    the firm could be assigned to a client's book — including a CLIENT'S OWN LOGIN, since a client
    with portal access is a row in the same table. And once the SEI sync began landing a firm's
    whole employee directory as placeholder logins (0123; 546 rows for one firm in the p04 corpus),
    every one of those became assignable too.

    `crm.client_advisers` cannot answer it, which is why this is a column rather than a derivation:
    that table records who HAS a book, and the question here is who may be GIVEN one — so the
    answer for somebody about to be assigned their first client is always no.

    `is_adviser` (0125) rather than a member of `type`: advising is a capability WITHIN staff, and
    `users_adviser_is_staff` makes an adviser who is not staff unrepresentable. No custodian can
    propose it — SEI's `DSR_TYPE_NAME` has seven 'Adviser' codes and zero accounts use one, Seccl's
    `userType` is `Machine | User`, Morgan Lloyd names one adviser contact per firm — so it is
    always an operator's statement.

    A live login only, as `_is_staff` asks: after 0245 the adviser is the ONE `role = 'adviser'`
    row, and a deleted one would be named by every listing while Morgan Lloyd and the AML team
    list, which filter `deleted_at`, showed no adviser at all.
    """
    return bool(
        (
            await session.execute(
                text(
                    "SELECT 1 FROM auth.users "
                    "WHERE id = :u AND tenant_id = :t AND is_adviser AND deleted_at IS NULL"
                ),
                {"u": user_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
    )


async def _is_staff(session: AsyncSession, tenant_id: int, user_id: str) -> bool:
    """Whether this login is a live member of the firm's STAFF — who may sit on a client's team in
    a role other than adviser. A client's own login is a row in the same table, and so is every
    placeholder the SEI directory sync lands (`type = '{}'`); neither belongs on a team."""
    return bool(
        (
            await session.execute(
                text(
                    "SELECT 1 FROM auth.users "
                    "WHERE id = :u AND tenant_id = :t AND 'staff' = ANY(type) "
                    "  AND deleted_at IS NULL"
                ),
                {"u": user_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
    )


async def create_contact(
    session: AsyncSession,
    *,
    tenant_id: int,
    type: str,
    service_type: str | None = None,
    sub_type: str | None = None,
    title: str | None = None,
    first_name: str | None = None,
    middle_name: str | None = None,
    surname: str | None = None,
    name: str | None = None,
    email: str | None = None,
    mobile: str | None = None,
    dob: date | None = None,
    marital_status: str | None = None,
    retirement_age: int | None = None,
    created_by_workos: str | None = None,
) -> dict[str, object]:
    """Create a client (contact) in the caller's firm.

    ``created_by_workos`` (the caller's verified WorkOS id) puts the creator on the new client's
    book in the SAME transaction. Without it a book-scoped adviser creates a client and it is
    immediately invisible to them: ``crm.contacts`` RLS admits a row via ``contacts:read_all`` OR
    ``auth.contact_scope()`` (0002:457), and ``contact_scope`` derives from ``crm.client_advisers``
    / ``crm.contact_users`` (0002:382-386) — so the form succeeded, the toast fired, and the client
    vanished. Ignored when the id doesn't belong to a member of this firm (an operations caller
    provisioning on a firm's behalf is not one of its advisers)."""
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO crm.contacts "  # noqa: S608 - only the fixed _RETURN list is interpolated
                    "(tenant_id, uid, type, service_type, sub_type, title, first_name, "
                    " middle_name, surname, name, email, mobile, dob, marital_status, "
                    " retirement_age) "
                    "VALUES (:t, :uid, :type, :st, :sub, :title, :fn, "
                    "        :mn, :sn, :name, :email, :mobile, :dob, :marital, :retirement) "
                    f"RETURNING {_RETURN}"
                ),
                {
                    "t": tenant_id,
                    "uid": new_id(),
                    "type": type,
                    "st": service_type,
                    "sub": sub_type,
                    "title": title,
                    "fn": first_name,
                    "mn": middle_name,
                    "sn": surname,
                    "name": name,
                    "email": email,
                    # Settable at creation, which it was not before: the drawer asked for a number,
                    # `CreateContact` had no mobile field, so the one number a firm types when it
                    # adds a client landed in the landline column and the header read "No mobile".
                    "mobile": validate_contact_phone(mobile),
                    "dob": dob,
                    "marital": marital_status,
                    "retirement": retirement_age,
                },
            )
        )
        .mappings()
        .one()
    )
    if created_by_workos:
        # Same transaction as the INSERT: either the client and its book row both exist, or
        # neither does. Tenant-scoped on both sides, so a WorkOS id from outside this firm
        # simply matches nothing.
        #
        # The creator is the ADVISER only if they are one. Anyone else with `contacts:create`
        # (a paraplanner, an administrator) still joins the team — otherwise a book-scoped
        # creator loses the client they just made — but as support, so the client's adviser is
        # never somebody who may not hold a book.
        await session.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "SELECT :t, :c, u.id, "
                "       CASE WHEN u.is_adviser THEN :adviser ELSE :support END "
                "FROM auth.users u "
                "WHERE u.workos_user_id = :w AND u.tenant_id = :t "
                "ON CONFLICT (contact_id, adviser_id) DO NOTHING"
            ),
            {
                "t": tenant_id,
                "c": row["id"],
                "w": created_by_workos,
                "adviser": ADVISER_ROLE,
                "support": SUPPORT_ROLE,
            },
        )
    return dict(row)


async def update_contact(
    session: AsyncSession, *, tenant_id: int, contact_id: int, fields: dict[str, object]
) -> dict[str, object]:
    """Patch a client. Only whitelisted fields are changed; tenant-scoped.

    ``fields`` must be the fields the caller actually SENT (routers/firm.py builds it with
    ``model_dump(exclude_unset=True)``), because presence is what distinguishes "leave this alone"
    from "clear this". A present ``None`` NULLs the column: clearing a stale phone, email, title or
    the whole address used to be a silent no-op that still toasted "Client updated", because every
    None was filtered out of the SET clause. The two halves have to move together — filtering on
    presence alone, while the router still sent every unset field as ``None``, would wipe a client's
    name and address on a partial PATCH.

    ``bank_account`` is normalised + validated here (``ValueError``) rather than at whichever
    endpoint happens to carry it, so there is exactly one door onto the column. A UK pair the
    sort-code directory rejects is refused the same way."""
    fields = dict(fields)
    await _refuse_duplicate_national_insurance(
        session, tenant_id=tenant_id, contact_id=contact_id, fields=fields
    )
    for bank_column in ("bank_account", "employer_bank_account"):
        if bank_column not in fields:
            continue
        normalised_bank = normalise_bank_account(fields[bank_column])
        if isinstance(normalised_bank, dict):
            fields[bank_column] = await confirm_uk_bank(
                session,
                tenant_id=tenant_id,
                contact_id=contact_id,
                bank=normalised_bank,
                column=bank_column,
            )
        else:
            fields[bank_column] = normalised_bank
    for number_field in ("mobile", "phone"):
        if number_field in fields:
            fields[number_field] = validate_contact_phone(fields[number_field])
    cols = {
        k: (json.dumps(v) if k in _JSONB and v is not None else v)
        for k, v in fields.items()
        if k in _UPDATABLE and not (v is None and k in _NOT_NULLABLE)
    }
    if not cols:
        row = (
            (
                await session.execute(
                    text(f"SELECT {_RETURN} FROM crm.contacts WHERE id = :c AND tenant_id = :t"),  # noqa: S608 - fixed _RETURN column list, not user input
                    {"c": contact_id, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError("Contact not found")
        return dict(row)

    # keys are whitelisted identifiers; jsonb columns get an explicit cast from their JSON string
    set_sql = ", ".join(
        f"{k} = CAST(:{k} AS jsonb)" if k in _JSONB else f"{k} = :{k}" for k in cols
    )
    row = (
        (
            await session.execute(
                text(
                    f"UPDATE crm.contacts SET {set_sql} WHERE id = :c AND tenant_id = :t "  # noqa: S608 - set_sql keys are whitelisted (_UPDATABLE); values are bound params
                    f"RETURNING {_RETURN}"
                ),
                {**cols, "c": contact_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Contact not found")
    if "fatca_status" in cols:
        # A journey that asked the client for their signed W-9 / W-8 stops asking once the answer
        # is neither. Imported here: `app.fatca` reaches the flow engine, whose handlers import
        # this module.
        from app import fatca

        await fatca.withdraw_unneeded_requests(
            session, tenant_id=tenant_id, contact_id=contact_id, status=cols["fatca_status"]
        )
    return dict(row)


async def snapshot_contact_fields(
    session: AsyncSession, *, tenant_id: int, contact_id: int, fields: list[str]
) -> dict[str, object]:
    """The current values of the given whitelisted contact fields, JSON-normalised.

    The four-eye stale guard: a snapshot taken when a change is proposed compares cleanly with one
    taken when it is applied (``json.dumps(default=str)`` so a date or jsonb column round-trips to
    the same shape the stored snapshot has). Only ``_UPDATABLE`` columns are read, so the dynamic
    column list is never caller text."""
    cols = sorted(f for f in fields if f in _UPDATABLE)
    if not cols:
        return {}
    select = ", ".join(cols)
    row = (
        (
            await session.execute(
                text(
                    f"SELECT {select} FROM crm.contacts "  # noqa: S608 - cols are _UPDATABLE-whitelisted
                    "WHERE id = :c AND tenant_id = :t AND deleted_at IS NULL"
                ),
                {"c": contact_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return {}
    normalised: dict[str, object] = json.loads(json.dumps(dict(row), default=str))
    return normalised


async def accept_bank_account(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    bank_account: object,
    directory: bool = True,
) -> dict[str, str] | None:
    """The canonical details, or ``None`` to clear them. Does not write.

    The strict ``method`` check is HERE, not in ``normalise_bank_account``: that shared core also
    serves the account-level door (`bank_accounts.normalise_entry`), which carries a wider payment
    vocabulary of its own (CHAPS, SWIFT, Direct Debit…) through the same fields and validates it
    itself — a person's own bank details offer only the two the UI does.

    ``directory`` is false only when this payload was already confirmed, so a save does not pay
    the directory twice. A review's approval leaves it true and asks again.
    """
    if isinstance(bank_account, dict):
        method = str(bank_account.get("method") or "").strip().lower()
        if method and method not in _BANK_METHODS:
            raise ValueError("Payment method must be BACS or Faster Payment.")
    normalised = normalise_bank_account(bank_account)
    if directory and isinstance(normalised, dict):
        confirmed = await confirm_uk_bank(
            session, tenant_id=tenant_id, contact_id=contact_id, bank=normalised
        )
        return confirmed
    return normalised


async def set_bank_account(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    bank_account: object,
    directory: bool = True,
) -> dict[str, object]:
    """Set (or clear, with ``None``) a client's bank details. Tenant-scoped.

    Its own function rather than a field on the general PATCH because it is bank data: one endpoint,
    one validation path (``accept_bank_account``), and a return value that carries only the
    canonical details back — never a wider row than the caller asked about."""
    normalised = await accept_bank_account(
        session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        bank_account=bank_account,
        directory=directory,
    )
    row = (
        (
            await session.execute(
                text(
                    "UPDATE crm.contacts SET bank_account = CAST(:b AS jsonb) "
                    "WHERE id = :c AND tenant_id = :t AND deleted_at IS NULL "
                    "RETURNING id, bank_account"
                ),
                {
                    "b": json.dumps(normalised) if normalised is not None else None,
                    "c": contact_id,
                    "t": tenant_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Contact not found")
    return dict(row)


async def archive_contact(
    session: AsyncSession, *, tenant_id: int, contact_id: int
) -> dict[str, object]:
    """Close a client: set ``deleted_at`` so they leave the live book and AUM while their
    history (documents, notes, audit) stays. The record itself stays loadable — Closed is a
    status on the header, not a 404.

    Close is offered only when there is nothing left open: no accounts, or every account is
    worth zero. A client with money still on the book is not closeable from here. Also refuses
    while they still have PENDING instructions (``ValueError`` → 409): closing a client with
    money in flight would hide the row the instruction list joins, and there is no settlement
    path that would ever clear it."""
    if not await _contact_in_tenant(session, tenant_id, contact_id):
        raise LookupError("Contact not found")
    valued = (
        await session.execute(
            text(
                "SELECT count(*) FROM platform.accounts "
                "WHERE tenant_id = :t AND contact_id = :c AND coalesce(value, 0) <> 0"
            ),
            {"t": tenant_id, "c": contact_id},
        )
    ).scalar_one()
    if valued:
        raise ValueError(
            "This client still has accounts with a balance. Close is only available when they "
            "hold no accounts, or every account is worth zero."
        )
    pending = (
        await session.execute(
            text(
                "SELECT count(*) FROM platform.instructions "
                "WHERE tenant_id = :t AND contact_id = :c AND status = 'pending'"
            ),
            {"t": tenant_id, "c": contact_id},
        )
    ).scalar_one()
    if pending:
        raise ValueError(
            f"This client has {pending} pending instruction(s). Cancel or complete them before "
            "closing."
        )
    row = (
        (
            await session.execute(
                text(
                    "UPDATE crm.contacts SET deleted_at = now() "  # noqa: S608 - fixed _RETURN column list, not user input
                    "WHERE id = :c AND tenant_id = :t AND deleted_at IS NULL "
                    f"RETURNING {_RETURN}, deleted_at"
                ),
                {"c": contact_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:  # in-tenant but already archived — idempotent, not an error
        return {"id": contact_id, "already_archived": True}
    return dict(row)


async def restore_contact(
    session: AsyncSession, *, tenant_id: int, contact_id: int
) -> dict[str, object]:
    """Reopen a closed client (tenant-scoped). Closing must be reversible: without this the
    only way back would be a hand-written UPDATE against the database."""
    row = (
        (
            await session.execute(
                text(
                    "UPDATE crm.contacts SET deleted_at = NULL "  # noqa: S608 - fixed _RETURN column list, not user input
                    "WHERE id = :c AND tenant_id = :t AND deleted_at IS NOT NULL "
                    f"RETURNING {_RETURN}"
                ),
                {"c": contact_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("No closed contact with that id")
    return dict(row)


async def assign_adviser(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    adviser_id: str,
    role: str = ADVISER_ROLE,
) -> dict[str, object]:
    """Put a member of staff on a client's team in ``role``, or change the role they hold there.
    Both the contact and the person must belong to the caller's firm.

    One ``adviser`` per client (0245): naming a new one moves the current adviser to ``support``
    rather than removing them, so nobody loses a client they were working on without the firm
    saying so. Every other role takes any number of staff."""
    if role not in CLIENT_TEAM_ROLES:
        raise ValueError(f"unknown team role {role!r}; expected one of {list(CLIENT_TEAM_ROLES)}")
    if not await _contact_in_tenant(session, tenant_id, contact_id):
        raise LookupError("Contact not found")
    if not await _user_in_tenant(session, tenant_id, adviser_id):
        raise LookupError("Adviser is not a member of this firm")
    # SEPARATE REFUSALS, because they need different actions. "Not in this firm" is a wrong id;
    # "not an adviser" is a real colleague nobody has said advises, and the fix is to mark them as
    # one — so the message names it rather than reading as "that person does not exist".
    if role == ADVISER_ROLE:
        if not await _is_adviser(session, tenant_id, adviser_id):
            raise LookupError(
                "That person is not marked as an adviser. Turn on 'Adviser' on their team page "
                "first, or give them another role."
            )
        # Demote before the upsert: `client_advisers_one_adviser_key` would refuse a second
        # adviser row, and it is also what makes two concurrent assignments fail rather than
        # both land.
        await session.execute(
            text(
                "UPDATE crm.client_advisers SET role = :support "
                "WHERE tenant_id = :t AND contact_id = :c AND role = :adviser "
                "  AND adviser_id <> CAST(:a AS uuid)"
            ),
            {
                "t": tenant_id,
                "c": contact_id,
                "a": adviser_id,
                "adviser": ADVISER_ROLE,
                "support": SUPPORT_ROLE,
            },
        )
    elif not await _is_staff(session, tenant_id, adviser_id):
        raise LookupError(
            "That person is not a member of staff, so they cannot be on a client's team."
        )
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                    "VALUES (:t, :c, :a, :r) "
                    "ON CONFLICT (contact_id, adviser_id) DO UPDATE SET role = EXCLUDED.role "
                    "RETURNING id, contact_id, adviser_id, role"
                ),
                {"t": tenant_id, "c": contact_id, "a": adviser_id, "r": role},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def unassign_adviser(
    session: AsyncSession, *, tenant_id: int, contact_id: int, adviser_id: str
) -> dict[str, object]:
    """Remove a staff member from a client's book (tenant-scoped)."""
    deleted = (
        await session.execute(
            text(
                "DELETE FROM crm.client_advisers "
                "WHERE tenant_id = :t AND contact_id = :c AND adviser_id = :a RETURNING id"
            ),
            {"t": tenant_id, "c": contact_id, "a": adviser_id},
        )
    ).scalar_one_or_none()
    if deleted is None:
        raise LookupError("Adviser is not assigned to this contact")
    return {"ok": True, "contact_id": contact_id, "adviser_id": adviser_id}


async def link_user(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    user_id: str,
    capacity: str = "self",
) -> dict[str, object]:
    """Link a login to a client (the party they may act for), with a capacity."""
    if not await _contact_in_tenant(session, tenant_id, contact_id):
        raise LookupError("Contact not found")
    if not await _user_in_tenant(session, tenant_id, user_id):
        raise LookupError("User is not a member of this firm")
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                    "VALUES (:t, :c, :u, :cap) "
                    "ON CONFLICT (contact_id, user_id) DO UPDATE SET capacity = EXCLUDED.capacity "
                    "RETURNING id, contact_id, user_id, capacity"
                ),
                {"t": tenant_id, "c": contact_id, "u": user_id, "cap": capacity},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def delete_note(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    note_uid: str,
    author_workos_id: str,
) -> None:
    """Delete a note — **the author's own, and nobody else's**.

    The authorship test is in the WHERE clause rather than in a read-then-check, which matters for
    the reason every other write here is explicitly tenant-scoped: this runs as `service_role`, so
    RLS is not going to catch a mistake. A note somebody else wrote is a `LookupError` (404) rather
    than a 403, because whether a note exists is itself something a caller who may not touch it has
    no business learning.

    A HARD delete, and the narrow scope is what makes that defensible: a staff note is one person's
    own remark about a conversation, so its author withdrawing it is not the destruction of a
    record — where an adviser deleting a COLLEAGUE'S note would be. `crm.contact_notes` carries the
    audit trigger, so the deletion itself is still answerable.
    """
    removed = (
        await session.execute(
            text(
                "DELETE FROM crm.contact_notes n "
                "USING auth.users u "
                "WHERE n.uid = :n AND n.tenant_id = :t AND n.contact_id = :c "
                "  AND u.id = n.author_id AND u.workos_user_id = :w "
                "RETURNING n.id"
            ),
            {"n": note_uid, "t": tenant_id, "c": contact_id, "w": author_workos_id},
        )
    ).scalar_one_or_none()
    if removed is None:
        raise LookupError("Note not found")


async def add_note(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    author_workos_id: str,
    body: str,
) -> dict[str, object]:
    """Append a staff note to a client (tenant-scoped). Author resolved from the caller's
    verified WorkOS id; notes are staff-internal (RLS keeps them off the client portal)."""
    if not await _contact_in_tenant(session, tenant_id, contact_id):
        raise LookupError("Contact not found")
    author_id = (
        await session.execute(
            text("SELECT id FROM auth.users WHERE workos_user_id = :w"),
            {"w": author_workos_id},
        )
    ).scalar_one_or_none()
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO crm.contact_notes (tenant_id, contact_id, author_id, body) "
                    "VALUES (:t, :c, :a, :b) RETURNING id, uid, body, created_at"
                ),
                {"t": tenant_id, "c": contact_id, "a": author_id, "b": body},
            )
        )
        .mappings()
        .one()
    )
    # The contact-detail read joins `auth.users.name`. The insert must return the same
    # display field so a portal can prepend the note without waiting for that load.
    author = (
        await session.execute(
            text("SELECT COALESCE(NULLIF(name, ''), email) FROM auth.users WHERE id = :a"),
            {"a": author_id},
        )
    ).scalar_one_or_none()
    payload = dict(row)
    payload["author"] = author
    created = payload.get("created_at")
    if isinstance(created, datetime):
        payload["created_at"] = created.isoformat()
    return payload


async def unlink_user(
    session: AsyncSession, *, tenant_id: int, contact_id: int, user_id: str
) -> dict[str, object]:
    """Unlink a login from a client (tenant-scoped)."""
    deleted = (
        await session.execute(
            text(
                "DELETE FROM crm.contact_users "
                "WHERE tenant_id = :t AND contact_id = :c AND user_id = :u RETURNING id"
            ),
            {"t": tenant_id, "c": contact_id, "u": user_id},
        )
    ).scalar_one_or_none()
    if deleted is None:
        raise LookupError("User is not linked to this contact")
    return {"ok": True, "contact_id": contact_id, "user_id": user_id}


# ── Declared vocabularies ────────────────────────────────────────────────────────────────────
# The same Literals the patch model accepts, so a bad value is a 422 naming the field
# rather than a CHECK violation surfaced as a 500. Gender and marital status are 0235;
# employment is still the 0053 list.
GENDERS: tuple[str, ...] = get_args(Gender)
MARITAL_STATUSES: tuple[str, ...] = get_args(MaritalStatus)
EMPLOYMENT_STATUSES: tuple[str, ...] = get_args(EmploymentStatus)
# The onboarding question's own bounds (flows/onboarding_seed.py) and the 0053 CHECK.
RETIREMENT_AGE_RANGE = (50, 75)

# `cam_taxrelief` (0116). The member's own DECLARATION about their tax position, which is why it
# is answered rather than derived from `relevant_uk_earnings` — holding a figure is not the client
# having said anything. The slugs are `morgan_lloyd.enums.TaxRelief`'s member names, lowercased,
# and the labels are shortened from the vendor's full first-person wording (in
# `TAX_RELIEF_DESCRIPTIONS`) because a radio label cannot be three lines long.
TAX_RELIEFS: tuple[str, ...] = (
    "uk_relevant_earnings",
    "uk_resident_this_year",
    "overseas_crown_employment_no_uk_earnings",
    "spouse_overseas_crown_employment",
)

TAX_RELIEF_LABELS: dict[str, str] = {
    "uk_relevant_earnings": "Has relevant UK earnings chargeable to income tax this tax year",
    "uk_resident_this_year": "Is or will be UK resident at some time during this year",
    "overseas_crown_employment_no_uk_earnings": (
        "In overseas Crown employment, with no UK-taxable earnings this tax year"
    ),
    "spouse_overseas_crown_employment": (
        "Spouse has UK-taxable earnings from overseas Crown employment this tax year"
    ),
}


# ── Vulnerability ────────────────────────────────────────────────────────────────────────────
# The FCA's four drivers (FG21/1). Their vocabulary, not ours: a file review, a consumer-duty
# report and this column should all say the same word, and it is the one advisers are trained in.
VULNERABILITY_REASONS: tuple[str, ...] = ("health", "life_event", "resilience", "capability")

_REASON_LABELS = {
    "health": "Health",
    "life_event": "Life event",
    "resilience": "Resilience",
    "capability": "Capability",
}


def reason_label(reason: str) -> str:
    """The adviser-facing name for a driver. Falls back to the stored value so an unmapped
    reason is visible rather than blank."""
    return _REASON_LABELS.get(reason, reason)


# The 24 CHARACTERISTICS beneath those four drivers (FG21/1, 0116) — and exactly the set Morgan
# Lloyd's `cam_vulnerabilitycharacteristics` accepts, so the regulator's vocabulary is the
# vocabulary on both sides and the mapping is a lookup rather than a judgement.
#
# GROUPED BY DRIVER, and the grouping is PRESENTATIONAL ONLY. It is FG21/1's own table and the
# vendor's option list is in the same order, which is good evidence — but it is evidence, not a
# contract, so nothing enforces it: a characteristic is recordable whichever drivers are ticked
# (0116 says why at length). If the grouping were slightly wrong the only consequence would be a
# checkbox under the wrong heading, rather than an answer nobody can record.
CHARACTERISTICS_BY_DRIVER: dict[str, tuple[str, ...]] = {
    "health": (
        "physical_disability",
        "long_term_illness",
        "hearing_impairment",
        "visual_impairment",
        "mental_health",
        "addiction",
        "low_mental_capacity",
    ),
    "life_event": (
        "retirement",
        "bereavement",
        "income_shock",
        "relationship_breakdown",
        "domestic_abuse",
        "caring_responsibilities",
        "other_circumstances",
    ),
    "resilience": (
        "erratic_income",
        "over_indebtedness",
        "low_savings",
        "low_emotional_resilience",
    ),
    "capability": (
        "low_financial_knowledge",
        "poor_literacy_numeracy",
        "poor_english",
        "poor_digital_skills",
        "learning_difficulties",
        "no_access_to_support",
    ),
}

#: Flattened in driver order, which is the order the 0116 CHECK lists and the order a stored
#: array is normalised into — so two records with the same characteristics compare equal and a
#: badge row does not reshuffle between saves (the rule the drivers already follow).
VULNERABILITY_CHARACTERISTICS: tuple[str, ...] = tuple(
    slug for driver in VULNERABILITY_REASONS for slug in CHARACTERISTICS_BY_DRIVER[driver]
)

_CHARACTERISTIC_LABELS: dict[str, str] = {
    "physical_disability": "Physical disability",
    "long_term_illness": "Severe or long-term illness",
    "hearing_impairment": "Hearing impairment",
    "visual_impairment": "Visual impairment",
    "mental_health": "Mental health condition or disability",
    "addiction": "Addiction",
    "low_mental_capacity": "Low mental capacity or cognitive disability",
    "retirement": "Retirement",
    "bereavement": "Bereavement",
    "income_shock": "Income shock",
    "relationship_breakdown": "Relationship breakdown",
    "domestic_abuse": "Domestic abuse (including economic control)",
    "caring_responsibilities": "Caring responsibilities",
    "other_circumstances": "Other circumstances affecting their experience of financial services",
    "erratic_income": "Inadequate (outgoings exceed income) or erratic income",
    "over_indebtedness": "Over-indebtedness",
    "low_savings": "Low savings",
    "low_emotional_resilience": "Low emotional resilience",
    "low_financial_knowledge": "Low knowledge or confidence in managing finances",
    "poor_literacy_numeracy": "Poor literacy or numeracy skills",
    "poor_english": "Poor English language skills",
    "poor_digital_skills": "Poor or non-existent digital skills",
    "learning_difficulties": "Learning difficulties",
    "no_access_to_support": "No or low access to help or support",
}


def characteristic_label(slug: str) -> str:
    """The adviser-facing name for a characteristic, falling back to the stored value."""
    return _CHARACTERISTIC_LABELS.get(slug, slug)


def normalise_vulnerability(payload: object) -> dict[str, object]:
    """Validate a vulnerability block, or refuse it with a message naming the problem.

    Clearing is `is_vulnerable = false`, which drops the reasons and the temporary flag with it —
    the 0054 CHECK enforces that pairing at the database, and doing it here too means the adviser
    is never shown a constraint-violation traceback for what is a coherent intention.

    A vulnerable client with no driver recorded is refused. The flag on its own is what 0053
    had, and it does not support the judgement the policy actually asks for: an adviser reading
    "vulnerable" with no reason cannot tell whether to slow the conversation down, involve a
    third party, or re-check comprehension.
    """
    if not isinstance(payload, dict):
        raise ValueError("Expected a vulnerability object")

    if not payload.get("is_vulnerable"):
        return {
            "is_vulnerable": False,
            "reasons": None,
            "characteristics": None,
            "is_temporary": None,
            "notes": None,
            "recorded_at": None,
        }

    raw = payload.get("reasons") or []
    if isinstance(raw, str):  # a single-select form posts a bare string
        raw = [raw]
    if not isinstance(raw, list | tuple):
        raise ValueError("Reasons must be a list")
    reasons = [str(r).strip().lower() for r in raw if str(r).strip()]
    unknown = sorted(set(reasons) - set(VULNERABILITY_REASONS))
    if unknown:
        raise ValueError(
            "Unknown vulnerability reason(s): "
            + ", ".join(unknown)
            + ". Expected any of: "
            + ", ".join(_REASON_LABELS[r] for r in VULNERABILITY_REASONS)
        )
    if not reasons:
        raise ValueError("Record at least one reason: health, life event, resilience or capability")

    # The characteristics beneath those drivers (0116). OPTIONAL where a driver is required: the
    # driver is the judgement an adviser must make, and FG21/1's characteristics are the detail
    # that supports it — a flag raised in a first conversation often has the driver before it has
    # the specifics. Requiring one would push whoever records it into picking a specific it does
    # not yet know.
    raw_chars = payload.get("characteristics") or []
    if isinstance(raw_chars, str):
        raw_chars = [raw_chars]
    if not isinstance(raw_chars, list | tuple):
        raise ValueError("Characteristics must be a list")
    characteristics = [str(c).strip().lower() for c in raw_chars if str(c).strip()]
    unknown_chars = sorted(set(characteristics) - set(VULNERABILITY_CHARACTERISTICS))
    if unknown_chars:
        # Named, not dropped: a characteristic silently discarded is a client record quietly less
        # true than the conversation it came from, and it is also what Morgan Lloyd is sent.
        raise ValueError(
            "Unknown vulnerability characteristic(s): "
            + ", ".join(unknown_chars)
            + ". Expected any of: "
            + ", ".join(characteristic_label(c) for c in VULNERABILITY_CHARACTERISTICS)
        )

    temporary = payload.get("is_temporary")
    if temporary is not None and not isinstance(temporary, bool):
        raise ValueError("is_temporary must be true, false, or omitted")

    notes = payload.get("notes")
    notes = str(notes).strip() or None if notes is not None else None
    return {
        "is_vulnerable": True,
        # Deduplicated and ordered by the FCA's own listing, so two records with the same
        # drivers compare equal and a badge row does not reshuffle between saves.
        "reasons": [r for r in VULNERABILITY_REASONS if r in set(reasons)],
        # Same normalisation as the drivers, in the same declared order, for the same reason.
        "characteristics": [c for c in VULNERABILITY_CHARACTERISTICS if c in set(characteristics)]
        or None,
        "is_temporary": temporary,
        "notes": notes,
        "recorded_at": payload.get("recorded_at"),
    }


async def set_vulnerability(
    session: AsyncSession, *, tenant_id: int, contact_id: int, vulnerability: object
) -> dict[str, object]:
    """Record or clear a client's vulnerability. Tenant-scoped.

    `vulnerability_recorded_at` is stamped here rather than taken from the caller: it is the
    review clock for a temporary vulnerability, and a client-supplied timestamp could park it
    in the past. Re-recording refreshes it, which is the intended behaviour — re-affirming a
    temporary flag restarts its review window.
    """
    checked = normalise_vulnerability(vulnerability)
    # normalise_vulnerability's return is dict[str, object]; the reasons branch is the only
    # value we index into, so narrow it here rather than loosening the signature.
    reasons = checked["reasons"]
    reason_list = list(reasons) if isinstance(reasons, list | tuple) else None
    characteristics = checked["characteristics"]
    characteristic_list = (
        list(characteristics) if isinstance(characteristics, list | tuple) else None
    )
    row = (
        (
            await session.execute(
                text(
                    "UPDATE crm.contacts SET "
                    "  is_vulnerable = :v, "
                    "  vulnerability_reasons = CAST(:r AS text[]), "
                    "  vulnerability_characteristics = CAST(:ch AS text[]), "
                    "  vulnerability_is_temporary = :tmp, "
                    "  vulnerability_notes = :n, "
                    "  vulnerability_recorded_at = CASE WHEN :v THEN now() ELSE NULL END "
                    "WHERE id = :c AND tenant_id = :t AND deleted_at IS NULL "
                    "RETURNING id, is_vulnerable, vulnerability_reasons, "
                    "          vulnerability_characteristics, "
                    "          vulnerability_is_temporary, vulnerability_notes, "
                    "          vulnerability_recorded_at"
                ),
                {
                    "v": bool(checked["is_vulnerable"]),
                    "r": reason_list or None,
                    # Cleared with the rest of the block when the flag goes off — 0116's own
                    # CHECK enforces that, and `normalise_vulnerability` has already nulled it.
                    "ch": characteristic_list or None,
                    "tmp": checked["is_temporary"],
                    "n": checked["notes"],
                    "c": contact_id,
                    "t": tenant_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Contact not found")
    return dict(row)
