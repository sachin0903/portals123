"""Postcoder address autocomplete and UK bank checks — the sole holder of ``POSTCODER_API_KEY``.

The portals keep a same-origin ``/api/postcoder/{action}`` BFF so the browser never sees a bearer
token or the upstream key; those routes forward here. The upstream takes the key as a query
parameter (and, for a bank check, in the URL path), so it must never leave this process —
including on error bodies, which Postcoder sometimes echoes.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app import bank_check, ratelimit
from app.auth import Principal, get_current_principal
from app.config import settings

router = APIRouter(prefix="/ref/postcoder", tags=["ref"])

UPSTREAM = "https://ws.postcoder.com/pcw/autocomplete"
Action = Literal["find", "retrieve"]
_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def for_postcoder_country(code: str) -> str:
    """Postcoder wants a lowercase code, and calls the United Kingdom ``uk`` rather than ``gb``."""
    normalised = (code or "").lower().strip()
    return "uk" if normalised == "gb" else normalised


@router.get("/{action}")
async def postcoder(
    action: Action,
    *,
    query: str | None = Query(None),
    id: str | None = Query(None),
    country: str = Query("gb", min_length=2, max_length=2),
    pathfilter: str | None = Query(None),
    principal: Principal = Depends(get_current_principal),
) -> Any:
    """Proxy ``find`` / ``retrieve`` to Postcoder. Any signed-in portal user may call it."""
    # Per user, before the key is even looked at: every call here is paid upstream, and the
    # portals' BFFs forward a keystroke each — a stuck key or a scripted session is the shape.
    await ratelimit.enforce(
        ratelimit.POSTCODER,
        ratelimit.per_user_limits(
            ratelimit.POSTCODER,
            user_id=principal.user_id,
            per_minute=settings.rate_limit_postcoder_per_minute,
        ),
        detail="Too many address lookups. Wait a moment, or enter the address manually.",
    )
    api_key = settings.postcoder_api_key.strip()
    if not api_key:
        # A missing key is a deployment problem, not a user one. Say so plainly rather than letting
        # the upstream answer 401 and the component render it as "no addresses found".
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Address lookup is unavailable. Enter the address manually.",
        )

    params: dict[str, str] = {
        "apikey": api_key,
        "country": for_postcoder_country(country),
        "format": "json",
    }

    if action == "find":
        q = (query or "").strip()
        if not q:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Enter part of an address.",
            )
        params["query"] = q
        params["maximumresults"] = "10"
        if pathfilter:
            params["pathfilter"] = pathfilter
    else:
        suggestion_id = (id or "").strip()
        if not suggestion_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Choose an address from the search results.",
            )
        params["id"] = suggestion_id
        # Postcoder's addressline fields preserve the postal order used by each country. For
        # international lookups, excluding the separately-returned locality fields from those
        # lines avoids duplicating the city/region/postcode when the form renders both.
        params["lines"] = "3"
        if for_postcoder_country(country) not in {"uk", "gg", "je", "im", "ie"}:
            params["exclude"] = "posttown,county,postcode,country"
        # Carried through so Postcoder can rank within the original search.
        if query:
            params["query"] = query

    url = f"{UPSTREAM}/{action}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(
                url,
                params=params,
                headers={"accept": "application/json", "user-agent": "graphene-api"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the address lookup service.",
        ) from exc

    if not response.is_success:
        # The upstream's own body can contain the key on some error paths, so it is never
        # forwarded — only the status is.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Address lookup is temporarily unavailable. Enter the address manually.",
        )
    return response.json()


class BankLookup(BaseModel):
    sort_code: str
    account_number: str


class BankSupportsOut(BaseModel):
    bacs: bool
    faster_payments: bool
    chaps: bool
    direct_debits: bool


class BankCheckOut(BaseModel):
    valid: bool
    state: bank_check.BankState
    sort_code: str
    account_number: str
    bank_name: str
    branch_name: str
    bank_bic: str
    supports: BankSupportsOut
    message: str


def _bank_out(check: bank_check.BankCheck) -> BankCheckOut:
    return BankCheckOut(
        valid=check.valid,
        state=check.state,
        sort_code=check.sort_code,
        account_number=check.account_number,
        bank_name=check.bank_name,
        branch_name=check.branch_name,
        bank_bic=check.bank_bic,
        supports=BankSupportsOut(
            bacs=check.supports.bacs,
            faster_payments=check.supports.faster_payments,
            chaps=check.supports.chaps,
            direct_debits=check.supports.direct_debits,
        ),
        message=check.message,
    )


@router.post("/bank", response_model=BankCheckOut, name="check-bank-account")
async def check_bank(
    body: BankLookup,
    principal: Principal = Depends(get_current_principal),
) -> BankCheckOut:
    """Check a UK sort code and account number against the sort code directory.

    Any signed-in user may call it. A pair the directory accepts comes back with the
    bank, the branch and the payment rails that account can take. The key never leaves
    this process.
    """
    # The cap lives in ``check_uk_account``, which every other caller of the directory
    # (a contact save, an account save, a fact-find write-back) goes through too. Counting
    # it here as well would charge this route twice.
    api_key = settings.postcoder_api_key.strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bank details can't be checked right now.",
        )
    sort = "".join(ch for ch in body.sort_code if ch.isdigit())
    number = "".join(ch for ch in body.account_number if ch.isdigit())
    if len(sort) != 6 or len(number) != 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter a 6-digit sort code and an 8-digit account number.",
        )
    check = await bank_check.check_uk_account(sort, number, user_id=principal.user_id)
    if check is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't check these bank details.",
        )
    return _bank_out(check)
