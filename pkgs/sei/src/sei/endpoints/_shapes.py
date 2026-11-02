"""Response-shape normalisers, shared by every endpoint module.

SEI is inconsistent across its ~200 services about how a payload is wrapped: some answer
JSON:API-ish (``{"data": {"type": …, "attributes": {…}}}``), some answer an array of those,
some answer a bare object, and the older SOAP-derived services answer a single-key envelope
named after the operation (``{"createCashDisbursementResponse": {…}}``).

This is worth centralising rather than repeating, because of **how guessing wrong fails**: a
caller that reaches for ``body["data"]["attributes"]`` on a service that answers a bare object
gets ``{}`` or ``[]``, not an exception — which reads as "SEI has nothing for this firm"
rather than as a parsing bug. Every function here therefore tries the known shapes in order
and returns the outermost thing it did recognise, so an unexpected envelope degrades to the
raw body a caller can still inspect.
"""

from __future__ import annotations

from typing import Any

from .._base import SeiApiError


def attributes(body: Any) -> dict[str, Any]:
    """``data.attributes`` when SEI wraps it; ``data`` when it wraps only that far; the body
    itself otherwise. Also unwraps a single-key ``*Response`` envelope."""
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        inner = data.get("attributes")
        if isinstance(inner, dict):
            return inner
        return _unwrap_response(data)
    return _unwrap_response(body)


def rows(body: Any, *, key: str | None = None) -> list[dict[str, Any]]:
    """A collection, whichever way this service spells one.

    Tried in order: a bare array; ``data[]`` (each entry unwrapped to its ``attributes`` when
    it has one); then, for ``data`` as an object, the first list-of-objects value inside it —
    which is how the retrieval services publish a collection (``listOfTransactionDetails``,
    ``ownedAccounts``, ``listOfpayToRecipients``…), each under its own name.

    **PASS `key` WHENEVER THE RESPONSE CARRIES MORE THAN ONE COLLECTION.** The fallback returns
    whichever appears first in the JSON, and the case that bites is the real collection being
    EMPTY: it then fails the non-empty test and the NEXT list wins. Read off the checked-in
    `spec/sei.openapi.json`, four responses put an emptiable collection before a non-data one —

      * ``realtimeholdings``: ``holdingsDetails`` then ``warning``, so an account with no
        positions returns SEI's own ``{code, title, detail}`` warning as one holding;
      * ``fee-package-details/retrieve``: ``periodicFeesPkgDetails`` then ``realTimeFeeDetails``,
        so a firm with no periodic fees gets a DEALING charge where
        ``platform.fee_packages.external_id`` expects a ``feePackageProfileId``;
      * ``accountsHistoricMarketValues``: per-account then per-ROLLUP, so a household total is
        returned as one account's value with nothing on the row saying so;
      * ``freereceiptstatus``: ``activityDetail`` then ``exceptionDetails``, so a FAILED custody
        transfer is returned as a status row, at HTTP 200, with ``status: "FAILURE"`` discarded
        because this helper never looks at it.

    Each of those names its key. An UNDECLARED response carrying two candidate collections RAISES
    rather than guessing, which is what turns the next one into a failure instead of a wrong
    answer — the same posture `_paths.py` takes about Seccl's two URL conventions.
    """
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if not isinstance(body, dict):
        return []
    data = body.get("data", body)
    if isinstance(data, list):
        out: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            inner = row.get("attributes")
            out.append(inner if isinstance(inner, dict) else row)
        return out
    if isinstance(data, dict):
        inner = data.get("attributes")
        source = inner if isinstance(inner, dict) else data
        if key is not None:
            # DECLARED: an absent or empty key is an empty answer, which is a real one. Falling
            # back to the scan here would put the guess back for exactly the responses that asked
            # not to be guessed at.
            named = source.get(key)
            if isinstance(named, list):
                return [row for row in named if isinstance(row, dict)]
            return []
        candidates = [
            name
            for name, value in source.items()
            if isinstance(value, list) and value and all(isinstance(row, dict) for row in value)
        ]
        if len(candidates) > 1:
            raise SeiApiError(
                "ambiguous response shape: more than one collection "
                f"({', '.join(sorted(candidates))}) — pass `key=` for this endpoint"
            )
        if candidates:
            return list(source[candidates[0]])
        # A single-record retrieve: the object IS the row. Not a fabrication — the responses that
        # would be one (a warning or exception block standing in for absent data) are the four
        # that name their key above.
        return [source]
    return []


def _unwrap_response(body: dict[str, Any]) -> dict[str, Any]:
    """Peel one ``{"<operation>Response": {…}}`` envelope, and the ``{"<Thing>": {…}}`` layer
    inside it. The SOAP-derived cash services nest twice and name both layers after the
    operation, so a caller looking for ``status`` finds it two levels down."""
    if len(body) != 1:
        return body
    (only,) = body.values()
    if not isinstance(only, dict):
        return body
    if len(only) == 1:
        (nested,) = only.values()
        if isinstance(nested, dict):
            return nested
    return only
