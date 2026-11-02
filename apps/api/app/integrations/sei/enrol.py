"""Open a client and an account at SEI in one enrolment.

SEI takes both in ``POST /accountservice/accounts``. The call is sent only when
the firm has configured the ids SEI will not derive: the client template for
this client's type, the account template, the classification, and the account's
org. A missing one is named. A servicing team is a team the firm named
(`dsr_ids`, a custody's own list, and a model's list when the account uses
that model). The relationship id is the one that
team's scope already uses. A bare
team id is left off — SEI rejects it, because both ids are mandatory.
An adviser is named on an employee relationship only when the book already
grants them that relationship. A SEI employee id on its own is not a grant.
Nothing here is filled with another firm's number.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.actions.attempt import OperationError
from app.integrations.sei.grants import (
    Grant,
    ResolvedTeams,
    account_teams,
    client_teams,
    custody_dsr_lists,
    modal_employees,
    model_dsr_lists,
    representative_grant_day,
    team_defaults,
)
from app.national_insurance import is_british, is_nino_type

# Inverse of the type map the book already reads back from SEI. These codes are
# platform-wide (a blank firm id on ACCT_TYPE), not a firm's own numbering.
# A SIPP is not a further code: every SIPP in the drop is 157 Investment Account,
# and the pension is the enrolment template plus the classification.
_ACCOUNT_TYPES = {"GIA": "157", "ISA": "172", "JISA": "173", "SIPP": "157"}
# SEI's own codes for firmInvestmentCapacityId, stated on the enrolment operation.
# The account-details field firmCapacity is a different value (example "Custodian")
# and is not this code. The enrolment schema types the id as an integer.
_CAPACITY = {"discretionary": 3, "advisory": 4, "execution_only": 5}
# Enrolment genderId and the profile-update gender field. 1 male, 2 female.
# SEI documents genderId as no longer shown; the profile update is the write that stores it.
_GENDER = {"male": 1, "female": 2}
# Profile-update initialSourceOfFunds. SEI's list is four codes. A book label
# with no code is left off. Source of wealth is not this list.
_FUNDS = {
    "personal savings": "1",
    "sale of investments (e.g., shares, bonds)": "1",
    "salary or employment income": "2",
    "business income": "2",
    "pension or retirement funds": "2",
    "inheritance": "3",
    "gift from family or friends": "3",
}
_FATCA = {"w9": "1", "w8": "2", "w8_cure": "3", "not_certified": "6"}


def _resolve_fatca(client: dict[str, Any]) -> str | None:
    """The SEI FATCA compliance code for a client, or None when SEI cannot certify one yet.

    An explicit ``fatca_status`` wins. Otherwise a client who has declared they are NOT a US person
    is certified under a W-8 (0134: "2 = W-8, everyone else"), so the us_person answer stands in for
    a separate W-8 selection — SEI needs a code, and "not a US person" is one. A US person (or one
    who has not answered) must still name their W-9, so nothing is derived for them.
    """
    status = str(client.get("fatca_status") or "")
    if status in _FATCA:
        return _FATCA[status]
    if client.get("us_person") is False:
        return _FATCA["w8"]
    return None


# Enrolment titleId is system code 451. The profile update names the first five;
# Professor is the file-drop code for the same list.
_TITLES = {"mr": 1, "mrs": 2, "ms": 3, "dr": 4, "miss": 21, "prof": 12, "professor": 12}
# Enrolment maritalStatusId. The book has one widowed value; 10 is Widowed
# (9 is Widower). Single is SEI's Never Married.
_MARITAL = {"married": 1, "single": 2, "divorced": 8, "widowed": 10}
# ComplyCube overall → SEI clientAMLRiskRating. very_high has no further code.
_AML = {"low": 1, "medium": 2, "high": 3, "very_high": 3}
# SEI rejects a review date after its own current date. The platform clock is
# US Eastern, so a review just after midnight in the UK is still yesterday there.
_SEI_CLOCK = ZoneInfo("America/New_York")
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def sei_email(value: object) -> str:
    """The address SEI will store.

    The enrolment BPM rejects a plus-address (``E-mail is not valid``). The tag
    after ``+`` is a routing hint on the same mailbox, so the address SEI takes
    is the part before it.
    """
    text = str(value or "").strip()
    local, sep, domain = text.partition("@")
    if not sep:
        return text
    return f"{local.split('+', 1)[0]}@{domain}"


_SETTLED = frozenset({"Validation Failed", "Enrollment Completed"})


def allowable_currencies(allowed: Sequence[str] | None, base: str | None) -> list[str]:
    """Currencies to add after SEI has opened the account.

    A firm with one currency already has it as ``accountBaseCurrency``. SEI refuses a
    code the account already allows, so the base is not sent again. Fewer than two
    distinct codes means there is nothing further to permit.
    """
    seen: list[str] = []
    held: set[str] = set()
    for code in allowed or []:
        text = str(code or "").strip().upper()
        if len(text) != 3 or text in held:
            continue
        held.add(text)
        seen.append(text)
    if len(seen) < 2:
        return []
    base_code = str(base or "").strip().upper()
    return [code for code in seen if code != base_code]


def currency_unavailable(message: str) -> bool:
    """SEI refused this code on the allowable-currency call.

    The firm's list can name a real ISO currency the platform will not add
    (``Invalid iso currency code : ALL``). One such code fails the whole batch,
    so the caller sends each code alone and leaves the refused ones off.
    """
    return "invalid iso currency code" in message.lower()


def enrolment_settled(status: dict[str, Any]) -> bool:
    """Whether the process has finished validating, or has already minted ids.

    Minted ids finish the enrolment step. They do not mean the account can take
    a pay-to: SEI still reports Work in Progress, and refuses that call, until
    the account itself is Open.
    """
    if str(status.get("businessProcessStatus") or "") in _SETTLED:
        return True
    client, accounts = opened_identifiers(status)
    return bool(client or accounts)


def account_status(details: dict[str, Any]) -> str:
    """``accountOverviewDetail.accountStatus``, or empty when the read has none."""
    overview = details.get("accountOverviewDetail")
    if not isinstance(overview, dict):
        return ""
    return str(overview.get("accountStatus") or "").strip()


def account_open_for_pay_to(details: dict[str, Any]) -> bool:
    """Whether a pay-to may be sent.

    The live account read reports ``Open``. The account-details schema also
    names ``Active``. Work in Progress is neither, and a pay-to sent then is
    refused.
    """
    return account_status(details).casefold() in {"open", "active"}


def pay_to_should_wait(status: str) -> bool:
    """Whether this account status is still inside enrolment, so the pay-to waits.

    A closed or otherwise finished account is not a wait: sending later will not
    make it Open.
    """
    text = status.strip().casefold()
    if text == "open":
        return False
    if not text:
        return True
    return any(
        token in text
        for token in (
            "progress",
            "incomplete",
            "pending",
            "enroll",
            "in process",
            "inprocess",
            # "WIP Submitted" is still inside enrolment. It contains none of the
            # words above, and failing it on the first read sends no pay-to.
            "wip",
        )
    )


def still_inside_enrolment(message: str) -> bool:
    """SEI refused the call because the party is still inside the enrolment.

    Field 1193: the account status is Work in Progress, so a pay-to cannot be
    created yet. The next read may be Open.
    """
    text = message.casefold()
    return "still being enrolled" in text or "work in progress" in text


def enrolment_outcome(status: dict[str, Any]) -> str | None:
    """Why this status must not be written onto the book, or None when it may.

    The POST is accepted before the BPM finishes. An in-progress process has no
    client yet. Storing our own uid as that party makes the next attempt address
    a client SEI does not have.
    """
    refused = enrolment_refusal(status)
    if refused:
        return refused
    if not enrolment_settled(status):
        return "SEI has not finished this enrolment. Nothing was recorded."
    return None


def resume_business_key(error: str | None, response: Any) -> str | None:
    """The enrolment still open at SEI, so a retry polls it instead of posting another.

    A refusal is not resumed: the record was fixed and the next send is a new enrolment.
    A failure while adding allowable currencies, or while creating the pay-to
    profile, is resumed, because the account is already open and a second
    enrolment would open another one.
    """
    if not error:
        return None
    resumable = (
        "has not finished this enrolment" in error
        or "allowable currencies" in error
        or "pay-to" in error
    )
    if not resumable:
        return None
    body = response
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return None
    if isinstance(body, dict) and body.get("businessKey"):
        return str(body["businessKey"])
    return None


def enrolment_refusal(status: dict[str, Any]) -> str | None:
    """The BPM's refusal, once the create call has returned a handle.

    The POST is accepted before validation finishes. ``Validation Failed`` is
    still a refusal, and the details name the field.
    """
    process = str(status.get("businessProcessStatus") or "")
    errors = status.get("validationErrors") or []
    if process != "Validation Failed" and not errors:
        return None
    details = [
        str(item.get("detail") or item.get("title") or "").strip()
        for item in errors
        if isinstance(item, dict)
    ]
    details = [item for item in details if item]
    return "; ".join(details) or process or "SEI refused the enrolment"


def pair_by_name(names: list[str], named: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Pair opened accounts to ours by the name SEI stored.

    The identifier list is not in the order the request was sent. A repeated
    name is not a pair: one SEI account cannot be written onto two of ours.
    """
    if len(names) != len(set(names)):
        return []
    by_name: dict[str, dict[str, Any]] = {}
    for name, item in named:
        if not name:
            continue
        if name in by_name:
            return []
        by_name[name] = item
    if any(name not in by_name for name in names):
        return []
    return [by_name[name] for name in names]


def opened_identifiers(status: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    """The SEI client id and the opened accounts, once the process has them.

    The status schema spells the client id ``primeryEndClientId``.
    """
    data = status.get("enrollmentData")
    if not isinstance(data, dict):
        return None, []
    identifiers = data.get("enrollmentIdentifiers")
    if not isinstance(identifiers, dict):
        return None, []
    client = identifiers.get("primeryEndClientId") or identifiers.get("primaryEndClientId")
    accounts = identifiers.get("accounts")
    opened = (
        [item for item in accounts if isinstance(item, dict)] if isinstance(accounts, list) else []
    )
    return (str(client) if client else None), opened


def sei_date(value: object) -> str | None:
    """SEI's profile-update date, ``06-DEC-1978``. Enrolment itself takes ISO."""
    if isinstance(value, date):
        day = value
    elif isinstance(value, str) and len(value) >= 10:
        try:
            day = date.fromisoformat(value[:10])
        except ValueError:
            return None
    else:
        return None
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def _gender_id(value: object) -> int | None:
    return _GENDER.get(str(value or "").strip().lower())


def initial_source_of_funds(value: object) -> str | None:
    """SEI's source-of-funds code for a book label, when the label has one."""
    return _FUNDS.get(str(value or "").strip().casefold())


def client_profile_facts(client: dict[str, Any]) -> dict[str, Any]:
    """Gender, occupation and source of funds, on the profile-update numbering.

    Enrolment also sends ``genderId``. SEI documents that field as no longer
    shown, so this block is the write that stores gender. Occupation is free
    text, at most 100 characters. Source of funds is SEI's four-code list.
    Town of birth is a tax-residence field, and source of wealth has no SEI
    field. Neither is sent here.
    """
    body: dict[str, Any] = {}
    gender = _gender_id(client.get("gender"))
    if gender is not None:
        body["gender"] = gender
    occupation = str(client.get("occupation") or "").strip()[:100]
    if occupation:
        body["occupation"] = occupation
    funds = initial_source_of_funds(client.get("source_of_funds"))
    if funds:
        body["initialSourceOfFunds"] = funds
    return body


def profile_update(client: dict[str, Any], *, part: str) -> dict[str, Any]:
    """The ``clientBasicsUpdateData`` block for one slice of the client."""
    if part == "details":
        body: dict[str, Any] = client_profile_facts(client)
        born = sei_date(client.get("dob"))
        if born:
            body["dateOfBirth"] = born
        if not body:
            raise OperationError(
                "SEI still needs a date of birth, a gender, an occupation, "
                "or a source of funds it accepts.",
                permanent=True,
            )
        return body
    if part == "vulnerability":
        return {"vulnerableClientFlag": bool(client.get("is_vulnerable"))}
    if part == "circumstances":
        body = {}
        if client.get("marital_status"):
            body["maritalStatus"] = str(client["marital_status"])
        funds = initial_source_of_funds(client.get("source_of_funds"))
        if funds:
            body["initialSourceOfFunds"] = funds
        if not body:
            raise OperationError(
                "SEI has no source-of-wealth field. This client has no marital status "
                "or a source of funds SEI accepts.",
                permanent=True,
            )
        return body
    if part == "bank":
        raise OperationError(
            "SEI keeps a bank account as a pay-to profile on the account (money out), "
            "not on the client. A payer profile is the direct-debit source, and a pay-for "
            "is the client themselves. The sync copies profiles SEI already holds.",
            permanent=True,
        )
    raise OperationError("SEI has no update for that part of the client.", permanent=True)


def _missing_config(config: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not config.get("firm_id"):
        missing.append("the firm's SEI firm id")
    # The client template is chosen per client, once the type is known. A firm
    # gap here would name "a client template" for an organisation that needs
    # its own.
    if not config.get("individual_classification_id"):
        missing.append("the SEI account classification Individual for this firm")
    # Servicing relationships are 0..N. A tenant that selected DSR teams sends
    # those. Otherwise a firm whose book has one grant is filled from that grant.
    # An empty list is not a gap — SEI names a tag only when that firm has made
    # one mandatory. The adviser's own id is an employee relationship, not a
    # substitute for the team's grant.
    return missing


def _teams(ids: list[Any]) -> list[dict[str, Any]]:
    """A grant is a relationship type plus a team.

    A bare id has neither, and SEI rejects the item (code 2196: team id and
    servicing relationship id are both mandatory). Those ids are left off.
    """
    sent: list[dict[str, Any]] = []
    for team in ids:
        if not isinstance(team, dict):
            continue
        if team.get("servicingRelationshipId") is None or team.get("teamId") is None:
            continue
        sent.append(
            {
                "servicingRelationshipId": int(team["servicingRelationshipId"]),
                "teamId": int(team["teamId"]),
            }
        )
    return sent


def catalogue_defaults(
    config: dict[str, Any],
    *,
    client_templates: list[str],
    individual_codes: list[str],
    grants: Sequence[Grant | tuple[Any, ...]],
    pension_codes: list[str] | None = None,
    sipp_codes: list[str] | None = None,
    employee_grants: Sequence[Grant | tuple[Any, ...]] = (),
) -> tuple[dict[str, Any], ResolvedTeams]:
    """Fill what the firm has exactly one of. Several answers stay for an operator to pick.

    The returned teams are the custody resolutions. They are not copied onto the
    config: that dict is the firm's saved settings plus the scalar catalogue fills.
    Employee relationships are the same kind of fill, on the client and on the
    account: the person the book already uses for each one. The journey's
    adviser replaces that person only when they already hold the relationship.
    """
    filled = dict(config)
    # One client template covers both kinds. Several stay for the operator: an
    # individual and an organisation are different templates, and the other
    # kind's choice is not reused.
    if len(client_templates) == 1:
        only = client_templates[0]
        for key in (
            "client_template_id",
            "client_template_individual_id",
            "client_template_organisation_id",
        ):
            if not filled.get(key):
                filled[key] = only
    if not filled.get("individual_classification_id") and len(individual_codes) == 1:
        filled["individual_classification_id"] = individual_codes[0]
    if not filled.get("pension_classification_id") and pension_codes and len(pension_codes) == 1:
        filled["pension_classification_id"] = pension_codes[0]
    if not filled.get("sipp_subclassification_id") and sipp_codes and len(sipp_codes) == 1:
        filled["sipp_subclassification_id"] = sipp_codes[0]
    # Firm-wide pairs land on the in-memory config because the body already sends
    # those lists. Custody pairs stay on the returned teams: which account they
    # apply to is only known when the body is built.
    firm_teams, resolved = team_defaults(filled, grants)
    filled.update(firm_teams)
    for scope in ("client", "account"):
        pairs, holders = modal_employees(employee_grants, scope=scope)
        if pairs and not filled.get(f"{scope}_employee_ids"):
            filled[f"{scope}_employee_ids"] = pairs
            filled[f"{scope}_employee_holders"] = holders
    return filled, resolved


_FIRM_FILE_GRANTS = """
WITH latest AS (
  SELECT max(dt) AS dt FROM raw.client_account_link WHERE "FIRM_ID" = ?
),
current AS (
  SELECT l.* FROM raw.client_account_link l
  JOIN latest ON l.dt = latest.dt
  WHERE l."FIRM_ID" = ?
),
slots AS (
  SELECT 'client' AS scope,
         "CLIENT_DSR_TEAM_TAG_1" AS type_id,
         "CLIENT_DSR_TEAM_ID_1" AS team_id
  FROM current
  UNION ALL SELECT 'client', "CLIENT_DSR_TEAM_TAG_2", "CLIENT_DSR_TEAM_ID_2" FROM current
  UNION ALL SELECT 'client', "CLIENT_DSR_TEAM_TAG_3", "CLIENT_DSR_TEAM_ID_3" FROM current
  UNION ALL SELECT 'client', "CLIENT_DSR_TEAM_TAG_4", "CLIENT_DSR_TEAM_ID_4" FROM current
  UNION ALL SELECT 'client', "CLIENT_DSR_TEAM_TAG_5", "CLIENT_DSR_TEAM_ID_5" FROM current
  UNION ALL SELECT 'client', "CLIENT_DSR_TEAM_TAG_6", "CLIENT_DSR_TEAM_ID_6" FROM current
  UNION ALL SELECT 'client', "CLIENT_DSR_TEAM_TAG_7", "CLIENT_DSR_TEAM_ID_7" FROM current
  UNION ALL SELECT 'client', "CLIENT_DSR_TEAM_TAG_8", "CLIENT_DSR_TEAM_ID_8" FROM current
  UNION ALL SELECT 'client', "CLIENT_DSR_TEAM_TAG_9", "CLIENT_DSR_TEAM_ID_9" FROM current
  UNION ALL SELECT 'client', "CLIENT_DSR_TEAM_TAG_10", "CLIENT_DSR_TEAM_ID_10" FROM current
  UNION ALL SELECT 'account', "ACCOUNT_DSR_TEAM_TAG_1", "ACCOUNT_DSR_TEAM_ID_1" FROM current
  UNION ALL SELECT 'account', "ACCOUNT_DSR_TEAM_TAG_2", "ACCOUNT_DSR_TEAM_ID_2" FROM current
  UNION ALL SELECT 'account', "ACCOUNT_DSR_TEAM_TAG_3", "ACCOUNT_DSR_TEAM_ID_3" FROM current
  UNION ALL SELECT 'account', "ACCOUNT_DSR_TEAM_TAG_4", "ACCOUNT_DSR_TEAM_ID_4" FROM current
  UNION ALL SELECT 'account', "ACCOUNT_DSR_TEAM_TAG_5", "ACCOUNT_DSR_TEAM_ID_5" FROM current
  UNION ALL SELECT 'account', "ACCOUNT_DSR_TEAM_TAG_6", "ACCOUNT_DSR_TEAM_ID_6" FROM current
  UNION ALL SELECT 'account', "ACCOUNT_DSR_TEAM_TAG_7", "ACCOUNT_DSR_TEAM_ID_7" FROM current
  UNION ALL SELECT 'account', "ACCOUNT_DSR_TEAM_TAG_8", "ACCOUNT_DSR_TEAM_ID_8" FROM current
  UNION ALL SELECT 'account', "ACCOUNT_DSR_TEAM_TAG_9", "ACCOUNT_DSR_TEAM_ID_9" FROM current
  UNION ALL SELECT 'account', "ACCOUNT_DSR_TEAM_TAG_10", "ACCOUNT_DSR_TEAM_ID_10" FROM current
)
SELECT s.scope, s.type_id, s.team_id, count(*), max(t.dsr_type)
FROM slots s
LEFT JOIN ref.dsr_types t
  ON t.dsr_type_id = s.type_id AND t.firm_id = ?
WHERE COALESCE(s.type_id, '') <> '' AND COALESCE(s.team_id, '') <> ''
GROUP BY 1, 2, 3
"""


_GRANT_DAY_COUNTS = """
SELECT dt, count(*)
FROM raw.client_account_link
WHERE "FIRM_ID" = ?
  AND dt >= (
    SELECT max(dt) - INTERVAL 21 DAY
    FROM raw.client_account_link
    WHERE "FIRM_ID" = ?
  )
GROUP BY dt
"""

# One day's grants, team and employee. The conformed model keeps only the
# warehouse's newest file, and that file is sometimes a thin extract of this
# firm. Enrolment asks for a day itself so it can skip that extract.
_GRANTS_ON_DAY = """
WITH current AS (
  SELECT * FROM raw.client_account_link
  WHERE dt = ? AND "FIRM_ID" = ?
),
slots AS (
  SELECT g.scope, g.grantee, g.type_id, g.team_id
  FROM current c,
  UNNEST([
    {'scope':'account','grantee':'team','type_id':c."ACCOUNT_DSR_TEAM_TAG_1",'team_id':c."ACCOUNT_DSR_TEAM_ID_1"},
    {'scope':'account','grantee':'team','type_id':c."ACCOUNT_DSR_TEAM_TAG_2",'team_id':c."ACCOUNT_DSR_TEAM_ID_2"},
    {'scope':'account','grantee':'team','type_id':c."ACCOUNT_DSR_TEAM_TAG_3",'team_id':c."ACCOUNT_DSR_TEAM_ID_3"},
    {'scope':'account','grantee':'team','type_id':c."ACCOUNT_DSR_TEAM_TAG_4",'team_id':c."ACCOUNT_DSR_TEAM_ID_4"},
    {'scope':'account','grantee':'team','type_id':c."ACCOUNT_DSR_TEAM_TAG_5",'team_id':c."ACCOUNT_DSR_TEAM_ID_5"},
    {'scope':'account','grantee':'team','type_id':c."ACCOUNT_DSR_TEAM_TAG_6",'team_id':c."ACCOUNT_DSR_TEAM_ID_6"},
    {'scope':'account','grantee':'team','type_id':c."ACCOUNT_DSR_TEAM_TAG_7",'team_id':c."ACCOUNT_DSR_TEAM_ID_7"},
    {'scope':'account','grantee':'team','type_id':c."ACCOUNT_DSR_TEAM_TAG_8",'team_id':c."ACCOUNT_DSR_TEAM_ID_8"},
    {'scope':'account','grantee':'team','type_id':c."ACCOUNT_DSR_TEAM_TAG_9",'team_id':c."ACCOUNT_DSR_TEAM_ID_9"},
    {'scope':'account','grantee':'team','type_id':c."ACCOUNT_DSR_TEAM_TAG_10",'team_id':c."ACCOUNT_DSR_TEAM_ID_10"},
    {'scope':'client','grantee':'team','type_id':c."CLIENT_DSR_TEAM_TAG_1",'team_id':c."CLIENT_DSR_TEAM_ID_1"},
    {'scope':'client','grantee':'team','type_id':c."CLIENT_DSR_TEAM_TAG_2",'team_id':c."CLIENT_DSR_TEAM_ID_2"},
    {'scope':'client','grantee':'team','type_id':c."CLIENT_DSR_TEAM_TAG_3",'team_id':c."CLIENT_DSR_TEAM_ID_3"},
    {'scope':'client','grantee':'team','type_id':c."CLIENT_DSR_TEAM_TAG_4",'team_id':c."CLIENT_DSR_TEAM_ID_4"},
    {'scope':'client','grantee':'team','type_id':c."CLIENT_DSR_TEAM_TAG_5",'team_id':c."CLIENT_DSR_TEAM_ID_5"},
    {'scope':'client','grantee':'team','type_id':c."CLIENT_DSR_TEAM_TAG_6",'team_id':c."CLIENT_DSR_TEAM_ID_6"},
    {'scope':'client','grantee':'team','type_id':c."CLIENT_DSR_TEAM_TAG_7",'team_id':c."CLIENT_DSR_TEAM_ID_7"},
    {'scope':'client','grantee':'team','type_id':c."CLIENT_DSR_TEAM_TAG_8",'team_id':c."CLIENT_DSR_TEAM_ID_8"},
    {'scope':'client','grantee':'team','type_id':c."CLIENT_DSR_TEAM_TAG_9",'team_id':c."CLIENT_DSR_TEAM_ID_9"},
    {'scope':'client','grantee':'team','type_id':c."CLIENT_DSR_TEAM_TAG_10",'team_id':c."CLIENT_DSR_TEAM_ID_10"},
    {'scope':'account','grantee':'employee','type_id':c."ACCOUNT_DSR_EMP_TAG_1",'team_id':c."ACCOUNT_DSR_EMP_ID_1"},
    {'scope':'account','grantee':'employee','type_id':c."ACCOUNT_DSR_EMP_TAG_2",'team_id':c."ACCOUNT_DSR_EMP_ID_2"},
    {'scope':'account','grantee':'employee','type_id':c."ACCOUNT_DSR_EMP_TAG_3",'team_id':c."ACCOUNT_DSR_EMP_ID_3"},
    {'scope':'account','grantee':'employee','type_id':c."ACCOUNT_DSR_EMP_TAG_4",'team_id':c."ACCOUNT_DSR_EMP_ID_4"},
    {'scope':'account','grantee':'employee','type_id':c."ACCOUNT_DSR_EMP_TAG_5",'team_id':c."ACCOUNT_DSR_EMP_ID_5"},
    {'scope':'account','grantee':'employee','type_id':c."ACCOUNT_DSR_EMP_TAG_6",'team_id':c."ACCOUNT_DSR_EMP_ID_6"},
    {'scope':'account','grantee':'employee','type_id':c."ACCOUNT_DSR_EMP_TAG_7",'team_id':c."ACCOUNT_DSR_EMP_ID_7"},
    {'scope':'account','grantee':'employee','type_id':c."ACCOUNT_DSR_EMP_TAG_8",'team_id':c."ACCOUNT_DSR_EMP_ID_8"},
    {'scope':'account','grantee':'employee','type_id':c."ACCOUNT_DSR_EMP_TAG_9",'team_id':c."ACCOUNT_DSR_EMP_ID_9"},
    {'scope':'account','grantee':'employee','type_id':c."ACCOUNT_DSR_EMP_TAG_10",'team_id':c."ACCOUNT_DSR_EMP_ID_10"},
    {'scope':'client','grantee':'employee','type_id':c."CLIENT_DSR_EMP_TAG_1",'team_id':c."CLIENT_DSR_EMP_ID_1"},
    {'scope':'client','grantee':'employee','type_id':c."CLIENT_DSR_EMP_TAG_2",'team_id':c."CLIENT_DSR_EMP_ID_2"},
    {'scope':'client','grantee':'employee','type_id':c."CLIENT_DSR_EMP_TAG_3",'team_id':c."CLIENT_DSR_EMP_ID_3"},
    {'scope':'client','grantee':'employee','type_id':c."CLIENT_DSR_EMP_TAG_4",'team_id':c."CLIENT_DSR_EMP_ID_4"},
    {'scope':'client','grantee':'employee','type_id':c."CLIENT_DSR_EMP_TAG_5",'team_id':c."CLIENT_DSR_EMP_ID_5"},
    {'scope':'client','grantee':'employee','type_id':c."CLIENT_DSR_EMP_TAG_6",'team_id':c."CLIENT_DSR_EMP_ID_6"},
    {'scope':'client','grantee':'employee','type_id':c."CLIENT_DSR_EMP_TAG_7",'team_id':c."CLIENT_DSR_EMP_ID_7"},
    {'scope':'client','grantee':'employee','type_id':c."CLIENT_DSR_EMP_TAG_8",'team_id':c."CLIENT_DSR_EMP_ID_8"},
    {'scope':'client','grantee':'employee','type_id':c."CLIENT_DSR_EMP_TAG_9",'team_id':c."CLIENT_DSR_EMP_ID_9"},
    {'scope':'client','grantee':'employee','type_id':c."CLIENT_DSR_EMP_TAG_10",'team_id':c."CLIENT_DSR_EMP_ID_10"}
  ]) AS u(g)
  WHERE COALESCE(g.type_id, '') <> '' AND COALESCE(g.team_id, '') <> ''
)
SELECT s.scope, s.grantee, s.type_id, s.team_id, count(*), max(t.dsr_type)
FROM slots s
LEFT JOIN ref.dsr_types t
  ON t.dsr_type_id = s.type_id AND t.firm_id = ?
GROUP BY 1, 2, 3, 4
"""


def _grants_on_firm_file(conn: Any, firm: str) -> list[Grant]:
    """Team grants on this firm's own latest client/account link file.

    Ten slots a side, the same unpivot as ``platform.dsr_grants``, but the latest
    file for this firm rather than the newest file in the warehouse.
    """
    rows = conn.execute(_FIRM_FILE_GRANTS, [firm, firm, firm]).fetchall()
    return [Grant.from_row(row) for row in rows]


def _book_grants(conn: Any, firm: str) -> tuple[list[Grant], list[Grant]] | None:
    """Team and employee grants from this firm's latest full client/account file."""
    counts = conn.execute(_GRANT_DAY_COUNTS, [firm, firm]).fetchall()
    day = representative_grant_day([(row[0], int(row[1])) for row in counts])
    if day is None:
        return None
    teams: list[Grant] = []
    employees: list[Grant] = []
    for scope, grantee, type_id, team_id, count, type_name in conn.execute(
        _GRANTS_ON_DAY, [day, firm, firm]
    ).fetchall():
        grant = Grant(str(scope), str(type_id), str(team_id), int(count), str(type_name or ""))
        if grantee == "employee":
            employees.append(grant)
        else:
            teams.append(grant)
    if not teams and not employees:
        return None
    return teams, employees


def employees_for_picker(conn: Any, firm: str) -> list[tuple[str, str | None]] | None:
    """Employee ids on this firm's latest full file, with a name when the reference has one.

    ``None`` means there is no full file, so the caller may use the conformed
    grants. An empty list is a full file that names no employees, and that is
    the list to show.
    """
    loaded = _book_grants(conn, firm)
    if loaded is None:
        return None
    _, employees = loaded
    ids = sorted({grant.team_id for grant in employees if grant.team_id})
    names: dict[str, str] = {}
    for row in conn.execute(
        "SELECT dsr_team_id, dsr_team_description FROM ref.dsr_teams WHERE firm_id = ?",
        [firm],
    ).fetchall():
        if not row[0] or not row[1]:
            continue
        names[str(row[0])] = str(row[1]).strip()
    return [(item, names.get(item) or None) for item in ids]


_FIRM_AML = frozenset({"clientAMLRiskRating", "clientAMLRiskLastReviewDate"})


def without_firm_aml(value: Any) -> Any:
    """The enrolment body as a firm may store it.

    SEI is sent the ComplyCube band. ``compliance.client_risk`` is not a firm
    fact, so the copy kept on the action attempt drops the rating and its date.
    """
    if isinstance(value, dict):
        return {key: without_firm_aml(item) for key, item in value.items() if key not in _FIRM_AML}
    if isinstance(value, list):
        return [without_firm_aml(item) for item in value]
    return value


def _catalogue_satisfied(config: dict[str, Any]) -> bool:
    """The scalar fills and the firm-wide team lists are already on the config."""
    # Both typed templates have to be present. A legacy single id still needs
    # the catalogue, so a firm with one template can fill the side that was
    # left blank.
    has_templates = bool(config.get("client_template_individual_id")) and bool(
        config.get("client_template_organisation_id")
    )
    return bool(
        has_templates
        and config.get("individual_classification_id")
        and config.get("client_team_ids")
        and config.get("account_team_ids")
        and config.get("pension_classification_id")
        and config.get("sipp_subclassification_id")
    )


def fill_from_catalogue(config: dict[str, Any]) -> tuple[dict[str, Any], ResolvedTeams]:
    """Read this firm's client template, Individual classification, and servicing grants.

    Grants come from the firm's latest full client/account file. A thinner file
    that arrives afterwards is not the book. Custody teams and model teams are
    resolved on the way out. A config that already holds every scalar fill still
    hits the warehouse when a custody or a model named teams, because those
    resolutions are not stored on the config.
    """
    firm = str(config.get("firm_id") or "").strip()
    if not firm:
        return config, ResolvedTeams()
    if (
        _catalogue_satisfied(config)
        and not custody_dsr_lists(config)
        and not model_dsr_lists(config)
    ):
        return config, ResolvedTeams()
    try:
        from app.lake import core
    except ImportError:
        return config, ResolvedTeams()
    try:
        conn = core.connect("sei")
    except core.CoreUnavailable:
        return config, ResolvedTeams()
    try:
        templates = [
            str(row[0])
            for row in conn.execute(
                "SELECT template_id FROM ref.enrollment_templates "
                "WHERE firm_id = ? AND scope = 'client'",
                [firm],
            ).fetchall()
        ]
        codes = [
            str(row[0])
            for row in conn.execute(
                "SELECT code FROM ref.codes "
                "WHERE type = 'ACCOUNT_CLASSIFICATION' AND firm_id = ? "
                "AND description = 'Individual'",
                [firm],
            ).fetchall()
        ]
        pensions = [
            str(row[0])
            for row in conn.execute(
                "SELECT code FROM ref.codes "
                "WHERE type = 'ACCOUNT_CLASSIFICATION' AND firm_id = ? "
                "AND description = 'Pension'",
                [firm],
            ).fetchall()
        ]
        sipps = [
            str(row[0])
            for row in conn.execute(
                "SELECT code FROM ref.codes "
                "WHERE type = 'ACCOUNT_SUB_CLASSIFICATION' AND firm_id = ? "
                "AND description = 'Pension - HMRC Approved - SIPP'",
                [firm],
            ).fetchall()
        ]
        loaded = _book_grants(conn, firm)
        if loaded is None:
            # The conformed grants keep only the newest drop. A firm that is not
            # in that file still has its own latest delivery.
            grants = _grants_on_firm_file(conn, firm)
            employees = [
                Grant.from_row(row)
                for row in conn.execute(
                    "SELECT scope, dsr_type_id, dsr_id, count(*), any_value(dsr_type) "
                    "FROM platform.dsr_grants "
                    "WHERE firm_id = ? AND grantee = 'employee' "
                    "AND scope IN ('client', 'account') "
                    "GROUP BY 1, 2, 3",
                    [firm],
                ).fetchall()
            ]
        else:
            grants, employees = loaded
    except Exception:  # noqa: BLE001 — a catalogue miss names the gap; it does not fail the call
        return config, ResolvedTeams()
    finally:
        conn.close()
    return catalogue_defaults(
        config,
        client_templates=templates,
        individual_codes=codes,
        grants=grants,
        pension_codes=pensions,
        sipp_codes=sipps,
        employee_grants=employees,
    )


# SEI organizationTypeId, from the enrolment schema. Our contact subtype is the
# closed list trust / company / charity. Company is SEI's Corporations.
_ORG_TYPES = {"charity": "2", "company": "4", "trust": "14"}


def client_kind(client: dict[str, Any]) -> str:
    """``organisation`` or ``individual``. A contact with no type is a person."""
    if str(client.get("type") or "").strip().casefold() == "organisation":
        return "organisation"
    return "individual"


def client_template_for(config: dict[str, Any], client: dict[str, Any]) -> str | None:
    """The client template for this person's type.

    The type-specific choice wins. A single saved template, from before the
    split, covers both kinds until one of the two is set. The other kind's
    template is not reused.
    """
    kind = client_kind(client)
    specific = (
        "client_template_organisation_id"
        if kind == "organisation"
        else "client_template_individual_id"
    )
    chosen = str(config.get(specific) or "").strip()
    if chosen:
        return chosen
    other = (
        "client_template_individual_id"
        if kind == "organisation"
        else "client_template_organisation_id"
    )
    if str(config.get(other) or "").strip():
        return None
    legacy = str(config.get("client_template_id") or "").strip()
    return legacy or None


def _organization_type_id(client: dict[str, Any]) -> str | None:
    return _ORG_TYPES.get(str(client.get("sub_type") or "").strip().casefold())


def _client_label(client: dict[str, Any]) -> str:
    if client_kind(client) == "organisation":
        name = str(client.get("name") or "").strip()
        return name or str(client.get("uid") or "") or "an organisation"
    return _person_label(client) or str(client.get("uid") or "") or "a client"


def _person_label(client: dict[str, Any]) -> str:
    return " ".join(
        str(part)
        for part in (client.get("title"), client.get("first_name"), client.get("surname"))
        if part
    )


def _servicing(teams: Any) -> list[dict[str, Any]]:
    """The firm's team grants. An adviser is an employee relationship, not one of these."""
    return _teams(list(teams or []))


def _title_id(value: object) -> int | None:
    key = str(value or "").strip().rstrip(".").casefold()
    return _TITLES.get(key)


def _marital_id(value: object) -> int | None:
    return _MARITAL.get(str(value or "").strip().casefold())


def _aml_rating(client: dict[str, Any]) -> int | None:
    return _AML.get(str(client.get("aml_risk") or "").strip().casefold())


def _aml_reviewed(client: dict[str, Any], *, today: date | None = None) -> str | None:
    """The review day, never later than SEI's current day.

    The stored timestamp is UTC. SEI compares the date with US Eastern. Sending
    the UTC day in the hours after midnight in the UK is a future date there,
    and the enrolment is refused.
    """
    raw = str(client.get("aml_risk_reviewed") or "").strip()
    if len(raw) < 10:
        return None
    try:
        reviewed = date.fromisoformat(raw[:10])
    except ValueError:
        return None
    limit = today if today is not None else datetime.now(_SEI_CLOCK).date()
    if reviewed > limit:
        reviewed = limit
    return reviewed.isoformat()


def _employees(
    employee_id: str | None, config: dict[str, Any], *, scope: str
) -> list[dict[str, int]]:
    """Employee relationships for one scope.

    The book's own holder is who SEI will accept on that relationship. The
    journey's adviser replaces them only when this person already holds it.
    An employee id that has never been granted the relationship is left off:
    SEI refuses it (2396 on the client, 2053 on the account). A stored
    decision type still names the adviser directly on the account.
    """
    adviser = str(employee_id or "").strip()
    holders = config.get(f"{scope}_employee_holders") or {}
    sent: list[dict[str, int]] = []
    seen: set[int] = set()
    for pair in config.get(f"{scope}_employee_ids") or []:
        if not isinstance(pair, dict):
            continue
        type_id = pair.get("servicingRelationshipId")
        employee = pair.get("employeeEntityId")
        if type_id is None or employee is None:
            continue
        held = {str(item) for item in holders.get(str(type_id), [])}
        if adviser.isdigit() and adviser in held:
            employee = adviser
        sent.append(
            {
                "servicingRelationshipId": int(type_id),
                "employeeEntityId": int(employee),
            }
        )
        seen.add(int(type_id))
    if scope == "account":
        explicit = str(config.get("investment_decision_type_id") or "")
        if explicit.isdigit() and adviser.isdigit() and int(explicit) not in seen:
            sent.append(
                {
                    "servicingRelationshipId": int(explicit),
                    "employeeEntityId": int(adviser),
                }
            )
    return sent


def _iso2(value: object) -> str | None:
    text = str(value or "").strip().upper()
    return text if len(text) == 2 and text.isalpha() else None


def _first_code(value: object) -> str | None:
    if isinstance(value, list | tuple) and value:
        return _iso2(value[0])
    return _iso2(value)


# The same pension wrappers the contribution path uses. ``PENSION`` in the name
# still catches a label that is not one of these codes. SSAS is a pension and
# does not contain the word.
_PENSION_WRAPPERS = frozenset({"SIPP", "PENSION", "SSAS", "TPPENSION"})


def wrapper_flags(products: Sequence[object]) -> dict[str, bool]:
    """Whether these wrappers need a UK National Insurance number on their own.

    An ISA or a pension always does. A GIA does only when the person is British
    or UK-resident, and that half is decided by the caller. A JISA does not:
    the child is not asked for one.
    """
    names = {str(item or "").strip().upper() for item in products if str(item or "").strip()}
    pension = any(name in _PENSION_WRAPPERS or "PENSION" in name for name in names)
    return {"has_isa_or_pension": "ISA" in names or pension, "has_gia": "GIA" in names}


def _owned_products(client: dict[str, Any], accounts: Sequence[dict[str, Any]]) -> list[object]:
    uid = str(client.get("uid") or "")
    return [
        account.get("product_type")
        for account in accounts
        if any(
            isinstance(owner, dict) and str(owner.get("uid") or "") == uid
            for owner in account.get("owners") or []
        )
    ]


def _needs_uk_nino(client: dict[str, Any], accounts: Sequence[dict[str, Any]]) -> bool:
    """ISA and pension always. A GIA when nationality or tax residency includes the UK."""
    flags = wrapper_flags(_owned_products(client, accounts))
    if flags["has_isa_or_pension"]:
        return True
    uk = is_british(client.get("nationality")) or is_british(client.get("tax_residency"))
    return bool(flags["has_gia"] and uk)


def _nino(client: dict[str, Any]) -> str | None:
    """The UK National Insurance number, once, from whichever question collected it.

    Nationality's own identifier is a NINO when that country asks for one. The
    fact-find asks again only when it does not, and stores that answer separately.
    """
    number = str(client.get("national_identifier") or "").strip()
    if number and is_nino_type(client.get("national_identifier_type")):
        return number
    extra = str(client.get("uk_national_insurance") or "").strip()
    return extra or None


def _tax_identifier(client: dict[str, Any]) -> dict[str, Any] | None:
    """SEI's tax identifier. A NINO is type 7 and its country is GB."""
    number = _nino(client)
    if not number:
        return None
    return {
        "identifierISOCountry": "GB",
        "taxIdTypeId": 7,
        "taxIdNumber": number,
    }


def _local_phone(value: object) -> str | None:
    """The subscriber number SEI stores. The address country supplies the prefix."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if digits.startswith("44") and len(digits) > 10:
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    return digits or None


def _address(client: dict[str, Any]) -> dict[str, Any]:
    country = _iso2(client.get("address_country")) or "GB"
    body: dict[str, Any] = {
        "isoCountry": country,
        "addressLine1": client["address_line1"],
        "city": client["address_city"],
    }
    line2 = str(client.get("address_line2") or "").strip()
    if line2:
        body["addressLine2"] = line2
    postcode = str(client.get("address_postcode") or "").strip()
    if postcode:
        body["postalCode"] = postcode
    return body


def _new_client(
    client: dict[str, Any], config: dict[str, Any], fatca: str, team_ids: Any
) -> dict[str, Any]:
    template = client_template_for(config, client)
    if not template:
        raise OperationError("SEI still needs a client template.", permanent=True)
    organisation = client_kind(client) == "organisation"
    body: dict[str, Any] = {
        "existingThirdParty": False,
        "externalPartyId": client.get("uid"),
        "residingOrgId": str(config["firm_id"]),
        "clientTypeId": "6" if organisation else "5",
        "clientTemplateId": template,
        "address": _address(client),
        "email": {"emailAddress": sei_email(client.get("email"))},
    }
    if organisation:
        body["organizationDetails"] = {
            "organizationName": str(client.get("name") or "").strip(),
            "organizationTypeId": _organization_type_id(client),
            "certifiedFATCAComplianceId": fatca,
        }
    else:
        body["individualDetails"] = {
            "firstName": client["first_name"],
            "lastName": client["surname"],
            "dateOfBirth": str(client["dob"])[:10],
            "mailingLabelName": _person_label(client),
            "certifiedFATCAComplianceId": fatca,
        }
    mobile = _local_phone(client.get("mobile"))
    landline = _local_phone(client.get("phone"))
    if mobile:
        body["phone"] = {"phoneMethodId": 2, "localPhoneNumber": mobile}
    elif landline:
        body["phone"] = {"phoneMethodId": 1, "localPhoneNumber": landline}
    if not organisation:
        birth = _iso2(client.get("country_of_birth"))
        if birth:
            body["individualDetails"]["countryOfBirth"] = birth
        citizen = _iso2(client.get("passport_country")) or _first_code(client.get("nationality"))
        if citizen:
            body["individualDetails"]["countryOfCitizenshipFirstPassport"] = citizen
        title = _title_id(client.get("title"))
        if title is not None:
            body["individualDetails"]["titleId"] = title
        marital = _marital_id(client.get("marital_status"))
        if marital is not None:
            body["individualDetails"]["maritalStatusId"] = marital
        gender = _gender_id(client.get("gender"))
        if gender is not None:
            body["individualDetails"]["genderId"] = gender
        tax = _tax_identifier(client)
        if tax:
            body["taxInformation"] = {"identifier": tax}
    rating = _aml_rating(client)
    if rating is not None:
        body["clientAMLRiskRating"] = rating
        reviewed = _aml_reviewed(client)
        if reviewed:
            body["clientAMLRiskLastReviewDate"] = reviewed
    servicing = _servicing(team_ids)
    if servicing:
        body["teamServicingRelationshipList"] = servicing
    employees = _employees(str(client.get("sei_employee_id") or "") or None, config, scope="client")
    if employees:
        body["employeeServicingRelationshipList"] = employees
    return body


def _client_entry(client: dict[str, Any], config: dict[str, Any], team_ids: Any) -> dict[str, Any]:
    sei_id = str(client.get("sei_id") or "").strip()
    if sei_id.isdigit():
        return {"existingClient": {"clientEntityId": int(sei_id)}}
    if sei_id:
        return {"existingClient": {"clientExternalPartyId": client.get("uid")}}
    fatca = _resolve_fatca(client)
    if fatca is None:  # the readiness check guards this; belt-and-braces for a direct caller
        raise OperationError("SEI cannot certify this client's FATCA status.", permanent=True)
    return {"newClient": _new_client(client, config, fatca, team_ids)}


def enrolment_attributes(
    client: dict[str, Any],
    account: dict[str, Any],
    config: dict[str, Any],
    teams: ResolvedTeams | None = None,
) -> dict[str, Any]:
    """``data.attributes`` for one new individual and one account, or a refusal."""
    owners = account.get("owners") or [client]
    return enrolment_attributes_for([client], [{**account, "owners": owners}], config, teams)


def enrolment_attributes_for(
    clients: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    config: dict[str, Any],
    teams: ResolvedTeams | None = None,
) -> dict[str, Any]:
    """One enrolment: every client once, then every account.

    A joint account names the account's primary owner and the other owners.
    The JISA's owner is the child; a registered contact is a client on the
    application, not a second owner of the JISA.
    """
    missing = _missing_config(config)
    prepared: list[tuple[dict[str, Any], str, str]] = []
    for account in accounts:
        product = str(account.get("product_type") or "").upper()
        account_type = _ACCOUNT_TYPES.get(product)
        label = account.get("name") or product or "this product"
        if account_type is None:
            missing.append(f"a SEI account type for {label}")
        template = str(account.get("scheme_product_id") or "").strip()
        if not template:
            missing.append(f"the SEI enrolment template for {label}")
        if (
            str(account.get("product_type") or "").upper() == "SIPP"
            and config.get("pension_classification_id")
            and not config.get("sipp_subclassification_id")
        ):
            missing.append(f"a SEI pension subclassification for {label}")
        if config.get("investment_decision_type_id") and not any(
            str(owner.get("sei_employee_id") or "").strip().isdigit()
            for owner in account.get("owners") or []
        ):
            missing.append(f"the lead adviser's SEI employee id for {label}")
        org = str(account.get("residing_org_id") or config.get("account_org_id") or "").strip()
        if not org:
            missing.append(f"the onshore SEI org for {label}")
        else:
            account["residing_org_id"] = org
        capacity = _CAPACITY.get(str(account.get("investment_capacity") or "").strip().lower())
        if capacity is None:
            missing.append(f"the investment capacity for {label}")
        else:
            account["firm_investment_capacity_id"] = capacity
        if account_type and template and org and capacity:
            prepared.append((account, account_type, template))
    joint_type = str(config.get("joint_ownership_type_id") or "").strip()
    needs_joint = any(len(account.get("owners") or []) > 1 for account in accounts)
    if needs_joint and not joint_type.isdigit():
        missing.append("the SEI joint ownership type")
    for account in accounts:
        if not account.get("owners"):
            label = account.get("name") or account.get("product_type") or "an account"
            missing.append(f"an owner for {label}")
    ready: list[dict[str, Any]] = []
    seen: set[str] = set()
    identity = (
        ("first name", "first_name"),
        ("surname", "surname"),
        ("email address", "email"),
        ("date of birth", "dob"),
        ("address line 1", "address_line1"),
        ("city", "address_city"),
    )
    for client in clients:
        uid = str(client.get("uid") or "")
        if uid in seen:
            continue
        seen.add(uid)
        if client.get("sei_id"):
            ready.append(client)
            continue
        who = _client_label(client) or uid or "a client"
        fatca = _resolve_fatca(client)
        if fatca is None:
            missing.append(f"a FATCA status SEI can certify for {who}")
        organisation = client_kind(client) == "organisation"
        kind_label = "organisation" if organisation else "individual"
        if not client_template_for(config, client):
            missing.append(f"a SEI {kind_label} client template for {who}")
        required: tuple[tuple[str, str], ...]
        if organisation:
            if not str(client.get("name") or "").strip():
                missing.append(f"{who}'s organisation name")
            if _organization_type_id(client) is None:
                missing.append(f"{who}'s organisation type")
            required = (
                ("email address", "email"),
                ("address line 1", "address_line1"),
                ("city", "address_city"),
            )
        else:
            required = identity
            if not _iso2(client.get("country_of_birth")):
                missing.append(f"{who}'s country of birth")
            citizen = _iso2(client.get("passport_country")) or _first_code(
                client.get("nationality")
            )
            if not citizen:
                missing.append(f"{who}'s citizenship")
        for label, key in required:
            if not client.get(key):
                missing.append(f"{who}'s {label}")
        country = _iso2(client.get("address_country")) or "GB"
        has_postcode = bool(str(client.get("address_postcode") or "").strip())
        if country == "GB" and not has_postcode:
            missing.append(f"{who}'s postcode")
        needs_nino = (not organisation) and _needs_uk_nino(client, accounts)
        if needs_nino and _tax_identifier(client) is None:
            missing.append(f"{who}'s National Insurance number")
        person_ready = organisation or (
            all(client.get(key) for _label, key in identity)
            and _iso2(client.get("country_of_birth"))
            and (_iso2(client.get("passport_country")) or _first_code(client.get("nationality")))
            and (not needs_nino or _tax_identifier(client) is not None)
        )
        named = bool(str(client.get("name") or "").strip())
        organisation_ready = (not organisation) or (
            named and _organization_type_id(client) is not None
        )
        if (
            fatca is not None
            and client_template_for(config, client)
            and person_ready
            and organisation_ready
            and all(client.get(key) for _label, key in required)
            and (country != "GB" or has_postcode)
        ):
            ready.append(client)
    if missing:
        raise OperationError("SEI still needs " + ", ".join(missing) + ".", permanent=True)
    account_list: list[dict[str, Any]] = []
    for account, account_type, template in prepared:
        owners = list(account.get("owners") or [])
        owner_block: dict[str, Any] = {"primaryOwnerExternalId": owners[0].get("uid")}
        if len(owners) > 1:
            owner_block["jointAccountOwner"] = {
                "jointOwnershipTypeId": int(joint_type),
                "jointOwners": [{"jointOwnerExternalId": owner.get("uid")} for owner in owners[1:]],
            }
        product = str(account.get("product_type") or "").upper()
        classification = str(config["individual_classification_id"])
        if product == "SIPP" and config.get("pension_classification_id"):
            classification = str(config["pension_classification_id"])
        portfolio = f"{account.get('name') or product} Capital"
        employee_id = next(
            (
                str(owner.get("sei_employee_id") or "").strip()
                for owner in owners
                if str(owner.get("sei_employee_id") or "").strip()
            ),
            "",
        )
        entry: dict[str, Any] = {
            "accountTypeId": account_type,
            "accountSubTypeId": "1",
            "accountClassificationId": classification,
            "accountTemplateId": template,
            "accountName": account.get("name") or product,
            "accountBaseCurrency": account.get("currency") or "GBP",
            "firmInvestmentCapacityId": account["firm_investment_capacity_id"],
            "residingOrgId": str(account["residing_org_id"]),
            "accountOwner": owner_block,
            "portfolios": [{"principalPortfolio": {"portfolioName": portfolio}}],
        }
        if product == "SIPP" and config.get("sipp_subclassification_id"):
            entry["accountSubClassificationId"] = str(config["sipp_subclassification_id"])
        servicing = _servicing(account_teams(account, config, teams))
        employees = _employees(employee_id or None, config, scope="account")
        if servicing:
            entry["teamServicingRelationshipList"] = servicing
        if employees:
            entry["employeeServicingRelationshipList"] = employees
        registered = list(account.get("registered_contacts") or [])
        if product == "JISA" and registered:
            contact = registered[0]
            pointer: dict[str, Any] = {}
            sei_id = str(contact.get("sei_id") or "").strip()
            if sei_id.isdigit():
                pointer["entityId"] = int(sei_id)
            else:
                pointer["externalPartyId"] = contact.get("uid")
            entry["thirdPartyRegisteredContact"] = {"existingThirdPartyRegisteredContact": pointer}
        account_list.append(entry)
    reference = next(
        (
            item.get("application_uid") or item.get("uid")
            for item in accounts
            if item.get("application_uid") or item.get("uid")
        ),
        "",
    )
    return {
        "firmId": str(config["firm_id"]),
        "usedBy": "Graphene",
        "bypassApproval": True,
        "caseReference": str(reference or ""),
        "clientList": [
            _client_entry(client, config, client_teams(accounts, config, teams)) for client in ready
        ],
        "accountList": account_list,
    }
