"""Daily valuation push to Morgan Lloyd — the outbound sink.

Morgan Lloyd receive each active policy's valuation daily. Interlace's built-in
exports cover files and attached-database tables, not API pushes, so the sink is
a scheduled Python model: it reads the active book (raw.schemes), pushes via the
client, and MATERIALISES THE PUSH LEDGER as its own table — one row per (policy,
valuation date) with the outcome. That makes the run state first-class: the model
appears on the /platforms/morgan-lloyd page (lineage sink node, Runs tab with
durations), `platform.valuation_runs` gives the per-day summary, and the Query
console reads the row-level ledger.

**The action name is no longer a placeholder.** This sink used to call
`push_scheme_valuation`, whose action `cam_updateschemevaluation` does not exist
in the org — so its first live push would have failed with a vendor error that
reads as a permissions problem. It now calls the documented
`cam_createpolicyvaluation` through `push_policy_valuations`, and
`pkgs/morgan-lloyd/tests/test_actions_exist.py` asserts every action the client
names is in `$metadata`.

Two things still block a real push, and they are why every row is `skipped`:

  - **the POLICY reference.** A valuation is filed against a policy (minted by
    `cam_createwrapper`), not against the `MLSIPP/…` scheme.
    `app/integrations/morgan_lloyd/sync.py` records one now (`link_type='policy'`,
    the account's uid). What is still missing HERE is the join: this sink reads
    `raw.schemes` and the policy number lives on `platform.links` in the portals
    DB. Sending the scheme reference instead is the `cam_schemerefs` mistake in
    another costume: a plausible identifier of the wrong kind, refused as "not
    found" — for a valuation, `Failed Policies: <ref> (missing policy)`, which is
    also what a policy created moments ago answers until Dynamics catches up.
  - **the fund splits.** `cam_createpolicyvaluation` wants per-holding units,
    price and market value, and `platform.valuations` is account × date. The
    splits are in the tenant's lake slice and are not joined to ML policies.

`ML_VALUATIONS_PUSH_ENABLED` gates the send (default off => dry-run: the ledger
records what WOULD be sent). Note a successful push is **not verifiable through
the API** — `cam_policyvaluations` is one of the 85 `cam_*` tables our service
principal cannot read — so this ledger is the only record that it happened.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import pyarrow as pa
from interlace import model
from morgan_lloyd import MorganLloydClient
from morgan_lloyd.endpoints.valuations import PolicyValuation

from ml_sync.records import records_to_table

#: Policies per `cam_createpolicyvaluation` call. The action's whole contract is a BATCH — its
#: parameter is `{"Policies": [...]}` and it reports failures per policy — so the loop used to send
#: one request per scheme carrying a single-element list, which is ~830 full Dataverse round trips
#: a night on the dev book's pension accounts. Chunked rather than sent whole because a vendor
#: error takes its chunk with it, and because a JSON payload of every policy in a firm's book is
#: not a shape this action has been exercised with.
_CHUNK = 100

_SPINE: dict[str, pa.DataType] = {
    "scheme_reference": pa.string(),
    "policy_reference": pa.string(),
    "valuation_date": pa.string(),
    "value": pa.float64(),
    "status": pa.string(),
    "detail": pa.string(),
    "ml_tenant_id": pa.string(),
}


def _push_enabled() -> bool:
    return os.environ.get("ML_VALUATIONS_PUSH_ENABLED", "").lower() in ("1", "true", "yes")


def _provider() -> str:
    """The firm GUID `cam_createpolicyvaluation` calls `provider`.

    The vendor issues ONE value under three field names, so the investment-provider setting
    falls back to the adviser firm id (`docs/morgan-lloyd/README.md`).
    """
    return (
        os.environ.get("MORGAN_LLOYD_INVESTMENT_PROVIDER_ID")
        or os.environ.get("MORGAN_LLOYD_ADVISER_FIRM_ID")
        or ""
    )


def _resolve_valuation(scheme: dict[str, Any], today: str) -> PolicyValuation | None:
    """The valuation to push for a scheme's policy.

    Returns ``None`` while either half is missing (see the module docstring): the policy
    reference, which nothing records, and the fund splits, which live in the tenant's lake
    slice unjoined to ML policies. Both are needed — a policy valuation with no splits is
    accepted by the vendor and lands a figure nobody can reconcile.
    """
    return None


@model(
    name="sink.daily_valuations",
    strategy="merge",
    key=("scheme_reference", "valuation_date"),
    depends_on="raw.schemes",
    schedule={"cron": "30 7 * * 1-5"},  # after the morning price/holdings runs
    tags=("morgan_lloyd", "sink"),
    description="Daily scheme valuations pushed to Morgan Lloyd; this table is the push ledger",
)
async def sink_daily_valuations(raw_schemes: Any) -> pa.Table:
    schemes = raw_schemes.table().to_pylist()
    today = date.today().isoformat()
    push = _push_enabled()

    provider = _provider()
    rows: list[dict[str, Any]] = []
    #: The rows that will actually be sent, paired with their valuation so the response can be
    #: attributed back to each one.
    sending: list[tuple[dict[str, Any], PolicyValuation]] = []

    for scheme in schemes:
        ref = scheme.get("scheme_reference")
        if not ref:
            continue
        row: dict[str, Any] = {
            "schemeReference": ref,
            "policyReference": None,
            "valuationDate": today,
            "mlTenantId": scheme.get("ml_tenant_id"),
        }
        rows.append(row)
        valuation = _resolve_valuation(scheme, today)
        if valuation is None:
            row |= {
                "value": None,
                "status": "skipped",
                "detail": "no policy reference or fund splits",
            }
        elif not provider:
            # Named rather than attempted: without it the vendor refuses in a way that
            # reads as a scheme problem (`docs/morgan-lloyd/README.md`).
            row |= {
                "value": valuation.policy_value,
                "status": "skipped",
                "detail": "MORGAN_LLOYD_INVESTMENT_PROVIDER_ID / _ADVISER_FIRM_ID unset",
            }
        elif not push:
            row |= {
                "policyReference": valuation.policy_reference,
                "value": valuation.policy_value,
                "status": "dry-run",
                "detail": "push disabled",
            }
        else:
            row |= {
                "policyReference": valuation.policy_reference,
                "value": valuation.policy_value,
            }
            sending.append((row, valuation))

    if sending:
        async with MorganLloydClient() as api:
            for start in range(0, len(sending), _CHUNK):
                await _push_chunk(api, provider, sending[start : start + _CHUNK])

    return records_to_table(rows, spine=_SPINE)


async def _push_chunk(
    api: MorganLloydClient, provider: str, chunk: list[tuple[dict[str, Any], PolicyValuation]]
) -> None:
    """One `cam_createpolicyvaluation` call for the whole chunk, attributed back per policy.

    A vendor error takes the whole chunk with it — the action either parsed the payload or did
    not, so there is no per-policy outcome to read — and `_CHUNK` is what bounds how much one
    refusal costs.
    """
    try:
        response = await api.push_policy_valuations(provider, [v for _, v in chunk])
    except Exception as exc:  # noqa: BLE001 — one chunk must not stop the book
        for row, _ in chunk:
            row |= {"status": "failed", "detail": str(exc)[:500]}
        return

    message = str(response.get("message", ""))
    failed = _failed_references(message, [v.policy_reference for _, v in chunk])
    for row, valuation in chunk:
        row |= {
            "status": "failed" if valuation.policy_reference in failed else "pushed",
            # THE WHOLE MESSAGE, on every row of the chunk. The attribution below reads prose, so
            # the ledger keeps the vendor's own words for an operator to check it against.
            "detail": message[:500],
        }


def _failed_references(message: str, references: list[str]) -> set[str]:
    """Which policies this response reports as failed, read out of its message.

    `cam_createpolicyvaluation` answers ONE message for the whole batch and names the policies it
    refused in it — `Failed Policies: <ref> (missing policy)`, quoted in this module's header. The
    delimiter between several is not documented and has not been observed, so this does not parse
    a list: if the message reports a failure at all, any reference it NAMES is failed and the rest
    are not. Conservative in the direction that matters — a policy the vendor did not mention is
    reported as pushed, which is what a batch with no failures answers.
    """
    if "fail" not in message.lower():
        return set()
    return {ref for ref in references if ref and ref in message}
