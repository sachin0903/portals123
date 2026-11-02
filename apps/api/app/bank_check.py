"""UK bank-account check against the sort code directory.

Postcoder's bank endpoint tests a sort code and account number against the Extended
Industry Sort Code Directory and the bank's own check digits, and returns the bank,
the branch and the rails that account can take. It does not say the account is open
or whose name is on it.

The key travels in the URL path, so a failure is logged as a fixed sentence: the
upstream URL and body are never written down, and never returned to the caller.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Literal

import httpx
from sqlalchemy import text

from app import ratelimit
from app.config import settings

logger = logging.getLogger(__name__)

BankState = Literal["valid", "sort_code_unknown", "account_invalid"]

_UPSTREAM = "https://ws.postcoder.com/pcw"
_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

SORT_CODE_UNKNOWN = "This sort code isn't recognised."
ACCOUNT_INVALID = "This account number isn't valid for that sort code."


class BankDetailsRejected(ValueError):
    """The sort code directory rejected this account. The message names no digits."""


@dataclass(frozen=True)
class BankSupports:
    bacs: bool
    faster_payments: bool
    chaps: bool
    direct_debits: bool


@dataclass(frozen=True)
class BankCheck:
    valid: bool
    state: BankState
    sort_code: str
    account_number: str
    bank_name: str
    branch_name: str
    bank_bic: str
    supports: BankSupports
    message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _digits(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def format_sort_code(digits: str) -> str:
    return f"{digits[0:2]}-{digits[2:4]}-{digits[4:6]}"


def uk_pair(bank: object) -> tuple[str, str] | None:
    """The 6-digit sort code and 8-digit account number, or None when this is not a UK pair."""
    if not isinstance(bank, dict):
        return None
    sort = _digits(bank.get("sort_code") or bank.get("sortCode"))
    number = _digits(bank.get("account_number") or bank.get("accountNumber"))
    if len(sort) == 6 and len(number) == 8:
        return sort, number
    return None


def same_pair(previous: object, bank: object) -> bool:
    """True when both objects are the same UK account, so a save need not ask again."""
    return uk_pair(previous) is not None and uk_pair(previous) == uk_pair(bank)


def rejection_message(check: BankCheck) -> str:
    if check.state == "sort_code_unknown":
        return SORT_CODE_UNKNOWN
    return ACCOUNT_INVALID


def _flag(value: object) -> bool:
    return value is True


def _state_id(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _text(body: dict[str, Any], key: str) -> str:
    raw = body.get(key)
    return str(raw).strip() if isinstance(raw, str) else ""


def parse_response(body: object, *, sort_code: str, account_number: str) -> BankCheck | None:
    """The directory's JSON as a ``BankCheck``, or None when it is not an object.

    ``stateid`` is 0 for a valid pair, -1 when the sort code is not in the directory and -2
    when the account number fails the check for that sort code. A rejection carries only
    ``valid`` and ``stateid``, so its sort code and account number are the ones that were sent.
    """
    if not isinstance(body, dict):
        return None
    valid = body.get("valid") is True
    state_id = _state_id(body.get("stateid"))
    state: BankState
    if valid:
        state = "valid"
    elif state_id == -1:
        state = "sort_code_unknown"
    else:
        state = "account_invalid"
    sort_digits = _digits(body.get("sortcode")) or sort_code
    number_digits = _digits(body.get("accountnumber")) or account_number
    if len(number_digits) < 8:
        number_digits = number_digits.zfill(8)
    check = BankCheck(
        valid=valid,
        state=state,
        sort_code=format_sort_code(sort_digits) if len(sort_digits) == 6 else sort_code,
        account_number=number_digits,
        bank_name=_text(body, "bankname"),
        branch_name=_text(body, "branchname"),
        bank_bic=_text(body, "bankbic"),
        supports=BankSupports(
            bacs=_flag(body.get("bacs")),
            faster_payments=_flag(body.get("fasterpayments")),
            chaps=_flag(body.get("chaps")),
            direct_debits=_flag(body.get("directdebits")),
        ),
        message="",
    )
    if valid:
        return check
    return BankCheck(
        valid=False,
        state=state,
        sort_code=check.sort_code,
        account_number=check.account_number,
        bank_name=check.bank_name,
        branch_name=check.branch_name,
        bank_bic=check.bank_bic,
        supports=check.supports,
        message=rejection_message(check),
    )


async def acting_user(session: Any) -> str | None:
    """The signed-in user on this session, from the claims the request published.

    The bank check is metered per user. A session with no claims (a test, a job) has
    nobody to meter, and the caller decides what that means.
    """
    raw = (
        await session.execute(
            text("SELECT NULLIF(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'")
        )
    ).scalar_one_or_none()
    return str(raw) if raw else None


async def check_uk_account(
    sort_code: str, account_number: str, *, user_id: str | None = None
) -> BankCheck | None:
    """Ask the directory about one pair.

    ``None`` when the key is blank, the service cannot be reached, or it answers
    with something other than a bank record. Never raises.

    ``user_id`` spends that user's Postcoder budget before the paid call. The
    address lookup and ``POST /ref/postcoder/bank`` already did; a contact save,
    an account save and a fact-find write-back did not, so a scripted session
    could ask the directory without the cap. No user means no meter — the HTTP
    route always passes one.
    """
    api_key = settings.postcoder_api_key.strip()
    sort = _digits(sort_code)
    number = _digits(account_number)
    if not api_key or len(sort) != 6 or len(number) != 8:
        return None
    if user_id:
        await ratelimit.enforce(
            ratelimit.POSTCODER,
            ratelimit.per_user_limits(
                ratelimit.POSTCODER,
                user_id=user_id,
                per_minute=settings.rate_limit_postcoder_per_minute,
            ),
            detail="Too many bank checks. Wait a moment.",
        )
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.post(
                f"{_UPSTREAM}/{api_key}/bank",
                params={"format": "json"},
                json={"sortcode": sort, "accountnumber": number},
                headers={"accept": "application/json", "user-agent": "graphene-api"},
            )
    except httpx.HTTPError:
        logger.info("bank check unavailable")
        return None
    if not response.is_success:
        logger.info("bank check unavailable")
        return None
    try:
        payload = response.json()
    except ValueError:
        logger.info("bank check unavailable")
        return None
    return parse_response(payload, sort_code=sort, account_number=number)


def apply_directory_name(bank: dict[str, Any], check: BankCheck) -> dict[str, Any]:
    """``bank`` with the directory's sort code, account number and, when unnamed, its bank.

    The stored key is ``bank``. The directory's own name is not kept beside it.
    """
    out = dict(bank)
    out["sort_code"] = check.sort_code
    out["account_number"] = check.account_number
    if check.bank_name and not str(out.get("bank") or "").strip():
        out["bank"] = check.bank_name
    return out


async def verified_bank(bank: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
    """``bank`` with the directory's sort code, account number and bank name applied.

    Unchanged when the pair is not a complete UK account, or when the directory
    cannot be asked. Raises ``BankDetailsRejected`` when the directory says no.
    """
    pair = uk_pair(bank)
    if pair is None:
        return bank
    check = await check_uk_account(*pair, user_id=user_id)
    if check is None:
        return bank
    if not check.valid:
        raise BankDetailsRejected(check.message or rejection_message(check))
    return apply_directory_name(bank, check)
