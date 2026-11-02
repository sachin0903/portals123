"""Scope-driven extractors: the custom actions that answer only for a given scheme reference or
adviser email.

**These used to land empty tables on every run, twice, for two different reasons.** First they
were scoped by `ML_SYNC_SCHEME_REFS`, which was never set in any environment, so the extractor
looped over an empty list. That was fixed by deriving the scope — and the derivation read the
WRONG ENTITY, so they went on landing empty.

`cam_schemerefs` is **not** a list of scheme references. It is the reference SERIES — one record
per SIPP product line, which is why it carries `cam_schemetype` — and its `cam_name` values
(`QSIPP/00652`, `SSAS/01421`, `HISIPP/00645`) are series identifiers, not schemes. The actual
references live on **`cam_schemes.cam_name`** (`MLSIPP/01417`). Verified against the live org
(Aug 2026): every series id is refused by `cam_getscheme` with "Scheme … not found" and crashes
`cam_getaccounttransactions` with a 400 null-reference, while every real ref answers.

The earlier note here recorded the discrepancy ("7 refs where `cam_schemes` holds 3") and read
it as a superset — a wider list the actions partly reject. It is not a superset; it is a
different population, and the overlap was zero. A per-ref action failing for EVERY ref is
indistinguishable from a firm with no data, which is why this survived two fixes.

So the scope derives from `cam_schemes` (`_scheme_refs`): the configured refs when
`ML_SYNC_SCHEME_REFS` is set, otherwise every scheme the CRM holds. The env var survives as a
NARROWING override for a targeted backfill, not as the thing that makes these work at all.

**Why keep these alongside `raw_odata.py`?** They are not duplicates. The OData collections are
the whole-book baseline read straight off the tables; these are custom actions that return a
different, often richer projection of the same domain — `cam_getscheme` carries the
crystallised/uncrystallised split, and the contribution actions return the application and
receipt views a scheme actually exposes. `raw.bank_transactions` has no collection form at all.
Where both exist, the collection is the spine and the action is the detail.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import pyarrow as pa
from interlace import model
from morgan_lloyd import MorganLloydActionError, MorganLloydClient
from morgan_lloyd.settings import MorganLloydSettings

from ml_sync.records import records_to_table
from ml_sync.settings import SyncSettings

logger = logging.getLogger("ml_sync.scoped")

_DAILY_MORNING = "30 5 * * 2-6"

# The scheme reference lives on `cam_schemes.cam_name` (`MLSIPP/01417`) — verified against the
# live org by calling every scheme-scoped action with both populations. NOT `cam_schemerefs`,
# which is the reference SERIES; see the module docstring.
_SCHEME_SET = "cam_schemes"
_SCHEME_REF_FIELD = "cam_name"


def _stamped(records: list[dict[str, Any]], **scope: str) -> list[dict[str, Any]]:
    """Inject the scope value(s) + the connection's Microsoft tenant (the per-firm
    discriminator the fan-out filters by) into every record."""
    scope.setdefault("mlTenantId", MorganLloydSettings().tenant_id)
    for record in records:
        for key, value in scope.items():
            record.setdefault(key, value)
    return records


async def _one(awaitable: Awaitable[dict[str, Any]]) -> list[dict[str, Any]]:
    """`cam_getscheme` answers with a single record where the others answer with a list."""
    return [await awaitable]


# "No <thing> found / were found / exist" -- the vendor's way of saying "nothing here", which it
# reports by RAISING a failed envelope. Matched on the message because the envelope gives no
# other signal: `responseKey` is 1 both for "no rows" and for "your reference is wrong", which
# are opposite facts and need opposite handling.
#
# Observed live, all four of these shapes:
#   cam_getaccounttransactions: No Financial Transactions were found
#   cam_getadvisercontacts:     No Adviser Found for Given Email Address
#
# Anchored on a leading "no" so it cannot swallow a real failure that merely mentions the word
# -- "Scheme MLSIPP/1 not found" must stay a skip, because a reference we cannot resolve is a
# problem and an entity with no rows is not.
_EMPTY_RESULT = re.compile(r":\s*no\b[^:]*\b(found|exist|exists|records)\b", re.IGNORECASE)


def _is_empty_result(exc: Exception) -> bool:
    return bool(_EMPTY_RESULT.search(str(exc)))


_ADVISER_SPINE: dict[str, pa.DataType] = {
    "contactid": pa.string(),
    "adviser_email": pa.string(),
    "ml_tenant_id": pa.string(),
}


async def _scheme_refs(api: MorganLloydClient) -> list[str]:
    """Which schemes to pull, configured first and enumerated otherwise.

    `ML_SYNC_SCHEME_REFS` is an override for a targeted backfill. When it is unset — which is
    every environment today — the refs come from `cam_schemes` rather than the loop running
    zero times, which is what made every model in this module write an empty table.
    """
    configured = SyncSettings().scheme_ref_list
    if configured:
        return list(configured)
    records = await api.get_collection(f"{_SCHEME_SET}?$select={_SCHEME_REF_FIELD}")
    refs = {
        str(record[_SCHEME_REF_FIELD]).strip()
        for record in records
        if str(record.get(_SCHEME_REF_FIELD) or "").strip()
    }
    return sorted(refs)


async def _per_ref(
    api: MorganLloydClient,
    fetch: Callable[[str], Awaitable[list[dict[str, Any]]]],
    *,
    action: str,
    scope_key: str,
) -> list[dict[str, Any]]:
    """Call one scheme-scoped action for every ref, tolerating the ones it cannot answer.

    A single unrecognised ref must not abandon the run — that would put us straight back to
    writing an empty table, which is the bug this module exists to have fixed. Failures are
    counted and logged; the refs that work still land.

    **An empty answer is not a failure.** Several of these actions report "nothing here" by
    RAISING: `cam_getaccounttransactions` answers `responseKey=1` with "No Financial Transactions
    were found" for a scheme that simply has none. Counting that as a skipped ref would log a
    warning per scheme per run and make a healthy sync look broken — and, worse, would leave the
    genuine failures (a 400, an unrecognised ref) buried among them.
    """
    records: list[dict[str, Any]] = []
    skipped: list[str] = []
    for ref in await _scheme_refs(api):
        try:
            records.extend(_stamped(await fetch(ref), **{scope_key: ref}))
        except MorganLloydActionError as exc:
            if _is_empty_result(exc):
                continue
            skipped.append(ref)
            logger.info("%s: skipping %s (%s)", action, ref, str(exc)[:120])
        except Exception as exc:  # noqa: BLE001 - vendor raises a zoo of action/HTTP errors
            skipped.append(ref)
            logger.info("%s: skipping %s (%s)", action, ref, str(exc)[:120])
    if skipped:
        logger.warning(
            "%s: %d of %d scheme refs did not resolve: %s",
            action,
            len(skipped),
            len(skipped) + len({r.get(scope_key) for r in records}),
            ", ".join(skipped[:10]),
        )
    return records


@model(
    name="raw.adviser_contacts",
    strategy="replace",
    schedule={"cron": _DAILY_MORNING},
    tags=("morgan_lloyd", "raw"),
    description="Adviser books via cam_getadvisercontacts; advisers enumerated from the "
    "Financial Adviser Firm, narrowable with ML_SYNC_ADVISER_EMAILS",
)
async def raw_adviser_contacts() -> pa.Table:
    """Adviser books, scoped by the adviser emails under our Financial Adviser Firm.

    **This landed an empty table on every run for the same reason the scheme-scoped models did:
    it looped over `ML_SYNC_ADVISER_EMAILS`, which is blank in every environment.** The old note
    here said advisers are "NOT self-enumerable" because `cam_getadvisercontacts` takes an email
    and the contacts collection cannot say which rows are advisers. Half right: it cannot, WITHOUT
    the firm GUID — and we hold the firm GUID already, as `MORGAN_LLOYD_ADVISER_FIRM_ID`, because
    `cam_createcontact` cannot attach a client without it.

    So the scope derives itself: list the contacts under the firm, then pull each one's book. The
    env var survives as a NARROWING override, as it does for scheme refs.

    Configuration is still required — but it is configuration we already had to set for client
    creation to work at all, rather than a second list nobody knew to maintain. With neither, the
    model says so rather than writing an empty table that reads as "this firm has no advisers".
    """
    settings = SyncSettings()
    emails = list(settings.adviser_email_list)
    records: list[dict[str, Any]] = []

    async with MorganLloydClient() as api:
        if not emails:
            firm_id = MorganLloydSettings().adviser_firm_id
            if not firm_id:
                logger.warning(
                    "raw.adviser_contacts: neither ML_SYNC_ADVISER_EMAILS nor "
                    "MORGAN_LLOYD_ADVISER_FIRM_ID is set, so no adviser book can be read. "
                    "This table is empty because it is UNCONFIGURED, not because the firm has "
                    "no advisers."
                )
                return records_to_table(records, spine=_ADVISER_SPINE)
            emails = await _adviser_emails(api, firm_id)

        for email in emails:
            try:
                records.extend(_stamped(await api.get_adviser_contacts(email), adviserEmail=email))
            except MorganLloydActionError as exc:
                # An adviser with no clients answers by raising, exactly as the scheme-scoped
                # actions do. Not a failure, and not worth a warning per adviser per run.
                if not _is_empty_result(exc):
                    logger.info("cam_getadvisercontacts: skipping %s (%s)", email, str(exc)[:120])

    return records_to_table(records, spine=_ADVISER_SPINE)


async def _adviser_emails(api: MorganLloydClient, firm_id: str) -> list[str]:
    """Every adviser email under the Financial Adviser Firm.

    `get_financial_adviser_contacts` selects only name and id, so this asks for the email too --
    it is the key `cam_getadvisercontacts` takes, and a contact without one cannot be queried.
    """
    contacts = await api.get_collection(
        "contacts",
        params={
            "$select": "emailaddress1,contactid",
            "$filter": f"_parentcustomerid_value eq {firm_id}",
        },
    )
    emails = sorted(
        {
            str(c["emailaddress1"]).strip()
            for c in contacts
            if str(c.get("emailaddress1") or "").strip()
        }
    )
    logger.info("raw.adviser_contacts: %d adviser email(s) under firm %s", len(emails), firm_id)
    return emails


@model(
    name="raw.scheme_detail",
    strategy="replace",
    schedule={"cron": _DAILY_MORNING},
    tags=("morgan_lloyd", "raw"),
    description="Scheme detail incl. the crystallised/uncrystallised split (cam_getscheme), "
    "per scheme reference. The richer companion to raw.schemes",
)
async def raw_scheme_detail() -> pa.Table:
    # Named `scheme_detail`, not `schemes`: `raw.schemes` is the whole `cam_schemes` collection
    # (311 columns, every scheme), and this is the per-reference action view that carries the
    # crystallised/uncrystallised split. Two names because they are two shapes.
    async with MorganLloydClient() as api:
        records = await _per_ref(
            api,
            lambda ref: _one(api.get_scheme_by_ref(ref)),
            action="cam_getscheme",
            scope_key="schemeReference",
        )
    return records_to_table(
        records, spine={"scheme_reference": pa.string(), "ml_tenant_id": pa.string()}
    )


@model(
    name="raw.contribution_applications",
    strategy="replace",
    schedule={"cron": _DAILY_MORNING},
    tags=("morgan_lloyd", "raw"),
    description="Contribution applications per scheme (cam_getpensioncontributionapplications)",
)
async def raw_contribution_applications() -> pa.Table:
    async with MorganLloydClient() as api:
        records = await _per_ref(
            api,
            api.get_pension_contribution_applications,
            action="cam_getpensioncontributionapplications",
            scope_key="schemeReference",
        )
    return records_to_table(
        records,
        spine={
            "cam_contributionid": pa.string(),
            "scheme_reference": pa.string(),
            "ml_tenant_id": pa.string(),
        },
    )


@model(
    name="raw.received_contributions",
    strategy="replace",
    schedule={"cron": _DAILY_MORNING},
    tags=("morgan_lloyd", "raw"),
    description="Received pension contributions per scheme (cam_getreceivedcontributions)",
)
async def raw_received_contributions() -> pa.Table:
    async with MorganLloydClient() as api:
        records = await _per_ref(
            api,
            api.get_received_contributions,
            action="cam_getreceivedcontributions",
            scope_key="schemeReference",
        )
    return records_to_table(
        records,
        spine={
            "cam_pensioncontributionid": pa.string(),
            "scheme_reference": pa.string(),
            "ml_tenant_id": pa.string(),
        },
    )


@model(
    name="raw.bank_transactions",
    strategy="replace",
    schedule={"cron": _DAILY_MORNING},
    tags=("morgan_lloyd", "raw"),
    description="Scheme bank-account financial transactions (cam_getbankaccounttransactions). "
    "No collection form exists, so the action is the only route",
)
async def raw_bank_transactions() -> pa.Table:
    async with MorganLloydClient() as api:
        records = await _per_ref(
            api,
            api.get_bank_account_transactions,
            action="cam_getaccounttransactions",
            scope_key="schemeRef",
        )
    return records_to_table(
        records,
        spine={
            "financial_transaction_name": pa.string(),
            "scheme_ref": pa.string(),
            "ml_tenant_id": pa.string(),
        },
    )
