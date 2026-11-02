"""The anchor account at SEI — **found, never created, and never defaulted.**

The Morgan Lloyd and Seccl suites both find-or-create their subject. This one cannot, and the
reason is structural rather than a gap: **SEI has no "create an account" call.** An account
arrives through ENROLMENT (`sei.endpoints.enrollment` — create, enrich, submit, add owners), which
is a journey with a `clientList[]` array, depth 8 and 48 leaves, and is deliberately not offered as
a form anywhere in this estate (`docs/sei.md`). Driving it from a test would be building the
onboarding path, not testing the money rail.

So the account is CONFIGURATION: `SEI_INTEGRATION_ACCOUNT`, with `SEI_INTEGRATION_PORTFOLIO` when
the account has more than one portfolio.

**There is deliberately no default, and that is a safety decision rather than tidiness.** A real
account number is available locally — the synced drop in `.data/sei.duckdb` carries thousands, and
`FIRM_ID` 3102 matches the firm these credentials are for. Hardcoding one would make the suite
zero-config and would also mean that anyone running it with writes enabled sends **instructions
about a real person's money**: a cash receipt they did not expect, a disbursement out of their
account, a fee. The environment is a test one and the accounts in it are not obviously fictional.
A skip that names the variable is the safe failure; a default is not.

**Use a GIA.** `SEI_INTEGRATION_ACCOUNT` should name an `ACCOUNT_TYPE` 157 *Investment Account*
with no pension classification — the wrapper with the fewest preconditions on it, and the one this
environment has most of (6,935 of 17,521). An ISA carries subscription rules and a JISA carries a
registered-contact rule, both of which fail a money instruction for reasons that have nothing to do
with the API. A House Account (176) is the firm's own, not a client product.

**Do not point it at a SIPP**, and the reason is not squeamishness: a SIPP at SEI is a custody
account whose SCHEME is administered elsewhere (Morgan Lloyd, in this estate), so an instruction
raised against it out of band is a real pension movement that the administrator does not know
about. `refuse_if_pension` is the guard, and it runs before every money instruction.

Getting a number: the warehouse (`raw.accounts`/`platform.accounts` for the firm — filter
`ACCOUNT_TYPE = '157' AND ACCOUNT_CLASSIFICATION_NAME IS DISTINCT FROM 'Pension'`), or SEI's own
`GET /externalentityelasticsearch/external/entities?searchType=Account` (in the spec, not wrapped
by this client, and currently un-entitled for this AppKey).

**Creating one is the enrolment journey, and the account template is unreachable.** The
`clientTemplateId` and account-template id an enrolment body carries come from
`ReferenceApi.client_templates` / `account_templates`. On i31 both answer for Select
(`3106`): the account-template proxy is the lowercase `enrollmentaccounttemplates` — the
spec's camelCase 404s. DSRs are not read from here; the nightly drop already has them.

**A portfolio is not an account and the difference bites.** An account can hold a Capital and an
Income portfolio (`PORTFOLIO_TYPE` 189/190), each on its OWN model — so "the account's model" is
not a question SEI answers, and every model and disbursement call takes a portfolio. And SEI's
numeric `accountId` is a THIRD identifier, distinct from the account NUMBER: fee assignment takes
the id and refuses the number.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

import pytest

from sei.client import SeiClient


def _first(payload: Any, *keys: str) -> str:
    """The first of `keys` present anywhere in a nested SEI answer, as a string.

    SEI's envelopes vary by service — `data[].attributes`, a bare object, a list — and the
    client's `_attributes`/`_rows` helpers already normalise the common ones. This walks whatever
    is left rather than asserting a shape, because the identifier being findable is what matters
    and a per-service accessor would be four spellings of the same thing.
    """
    stack: list[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key in keys:
                value = node.get(key)
                if value not in (None, "", []):
                    return str(value)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return ""


def configured_account() -> str:
    """The account under test, or a skip naming how to set one."""
    account = (os.environ.get("SEI_INTEGRATION_ACCOUNT") or "").strip()
    if not account:
        pytest.skip(
            "set SEI_INTEGRATION_ACCOUNT to an account number on this test environment. There is "
            "deliberately no default: SEI's writes are instructions about money, and the accounts "
            "on i31 are not obviously fictional. See this module's docstring."
        )
    return account


def configured_portfolio() -> str | None:
    """An explicit portfolio, when the account has more than one."""
    return (os.environ.get("SEI_INTEGRATION_PORTFOLIO") or "").strip() or None


async def portfolio_for(api: SeiClient, firm_id: str, account_number: str) -> str:
    """The portfolio to act on — configured, else the account's own.

    Discovered rather than assumed, because a two-portfolio account has no single answer and
    picking the first silently would put a fee or a disbursement on the Income portfolio of an
    account whose owner meant Capital.
    """
    if (explicit := configured_portfolio()) is not None:
        return explicit
    details = await api.account_details(firm_id, account_number)
    found = _first(details, "portfolioId", "portfolio_id", "PORTFOLIO_ID")
    if not found:
        pytest.skip(
            f"no portfolioId on {account_number} — set SEI_INTEGRATION_PORTFOLIO. Every model, "
            "fee and disbursement call takes one."
        )
    return found


async def numeric_account_id(api: SeiClient, firm_id: str, account_number: str) -> str:
    """SEI's numeric `accountId` — **not the account number.**

    `FeesApi.assign_package_to_account` takes this and refuses the number, which is the kind of
    wrong-identifier mistake that answers "not found" and reads as "the account does not exist"
    (the `cam_schemerefs` shape, in SEI's costume).
    """
    details = await api.account_details(firm_id, account_number)
    found = _first(details, "accountId", "account_id", "ACCOUNT_ID")
    if not found:
        pytest.skip(
            f"no numeric accountId on {account_number}; fee assignment cannot be addressed "
            f"(keys seen: {sorted(details)[:12] if isinstance(details, dict) else type(details)})"
        )
    return found


async def refuse_if_pension(api: SeiClient, firm_id: str, account_number: str) -> None:
    """**Stop before instructing money on a pension account.**

    A SIPP at SEI is not a distinct wrapper — it is an `ACCOUNT_TYPE` 157 *Investment Account*
    carrying `ACCOUNT_CLASSIFICATION_NAME = 'Pension'` and, usually,
    `ACCOUNT_SUB_CLASSIFICATION_NAME = 'Pension - HMRC Approved - SIPP'`. On the i31 drop that is
    934 accounts, 830 of them SIPPs, and every one of them looks like a GIA if you read only the
    wrapper. `services/sei/models/platform/accounts.sql` had exactly that bug.

    Which means a misconfigured `SEI_INTEGRATION_ACCOUNT` does not look wrong: it looks like an
    Investment Account. The cost of getting it wrong is not a failed test — it is a cash receipt
    or a disbursement on somebody's pension, made without the SCHEME ADMINISTRATOR (Morgan Lloyd
    here) knowing, which is a reportable event rather than a tidy-up.

    A read that cannot be made is not a pass. If the account is unreadable the caller sees the
    vendor's own error through the conftest hook — an un-entitled or degraded service skips with
    its reason, which is the honest outcome.
    """
    details = await api.account_details(firm_id, account_number)
    classification = _first(details, "accountClassificationName", "ACCOUNT_CLASSIFICATION_NAME")
    sub = _first(details, "accountSubClassificationName", "ACCOUNT_SUB_CLASSIFICATION_NAME")
    qualifier = _first(details, "accountQualifierName", "ACCOUNT_QUALIFIER_NAME")
    if "pension" in f"{classification} {sub}".lower() or qualifier.upper() == "SIPP":
        pytest.fail(
            f"SEI_INTEGRATION_ACCOUNT ({account_number}) is a PENSION account "
            f"(classification={classification!r} sub={sub!r} qualifier={qualifier!r}). Point it "
            "at a GIA: an instruction here is a real pension movement, and the scheme "
            "administrator would not know about it. See this module's docstring."
        )


def future_date(days: int = 30) -> dt.date:
    """A settlement date comfortably in the future.

    Far enough out that a run is never near a cut-off, and far enough that a receipt raised by a
    test is visibly not this week's real business.
    """
    return dt.date.today() + dt.timedelta(days=days)


def reference(what: str) -> str:
    """An `externalReference` that says what raised it and when.

    SEI has no idempotency mechanism on these services, so this is what makes a duplicate
    recognisable after the fact — the same job `_reference` does in the Seccl suite.
    """
    return f"ZZINT-{what}-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}"
