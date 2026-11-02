"""ONE retry policy for every vendor HTTP client. `AsyncRetrying(**policy(method))`.

The three clients (`pkgs/morgan-lloyd`, `pkgs/seccl`, `pkgs/sei`) each carried their own copy of
the predicate and the tenacity configuration. The copies had diverged in the way copies do — five
attempts against four, with nothing anywhere saying why the same 502 from two custodians should be
treated differently — and none of the three read `Retry-After`, while the estate's two other
outbound clients both do (`app/aml/client.py`, `sei_sync/api.py`).

It lives beside `api_log` rather than in a fourth package because these three already depend on it
for the audit hook at their single HTTP choke point, and the retry decision belongs at the same
choke point: what gets retried and what gets recorded are two halves of one question about an
attempt. The package's "no dependencies" rule is about a DATABASE and about settings — the reason
the clients emit rather than persist — and this module is a submodule, so `import api_log` still
pulls in neither httpx nor tenacity.

## The rule, and why it is not "retry what failed"

**A retry is safe only when the failure PROVES the vendor did not process the call.** Morgan Lloyd
has no delete, so a retried `cam_createcontact` puts a second human being in the CRM; Seccl's
`create_transaction_group` is "THE MONEY RAIL … sending one twice instructs twice"; SEI's
`create_cash_disbursement` pays cash away from the account. None of the three offers an idempotency
key — `endpoints/cash.py` sends no `externalSystemTxnId` and `enrollment.py`'s docstring records
that SEI has none — so the vendor cannot collapse a duplicate for us.

* **429** — refused BEFORE processing. Retrying cannot repeat an effect, whatever the method, and
  this is the case worth keeping the loop for.
* **5xx** — the vendor may have committed the write and lost the answer on the way back. Retried
  only for an idempotent method.
* **transport error** — nothing distinguishes "never sent" from "sent, answer lost", so the safe
  reading is that it arrived. Retried only for an idempotent method.

A refused retry is not a lost call: the failure is raised, `audit.api_log` keeps the attempt, and
an operator resends deliberately. A duplicated payment is not recoverable that way.

## Retry-After

A 429 is the one status retried for every method, so it is the one where obeying the vendor matters
most — and a rate limit that names five seconds retried after one is three more refusals. The
header wins when present, capped, because an unbounded one (hostile, or `"3600"` by typo) parks a
worker for an hour. The HTTP-date form is not parsed: the point is to wait a sensible amount, not
to obey precisely, so it falls through to the backoff curve (the same decision `aml/client.py`
records).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from tenacity import RetryCallState, retry_if_exception, stop_after_attempt, wait_exponential_jitter

__all__ = [
    "BACKOFF",
    "IDEMPOTENT_METHODS",
    "MAX_ATTEMPTS",
    "RETRY_AFTER_CAP_S",
    "policy",
    "retry_after_seconds",
    "retryable_for",
]

#: Methods where sending the same request twice cannot do the thing twice.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: FOUR, which is the estate's number: `aml/client.py::MAX_RETRIES` and
#: `sei_sync/api.py::RETRY_ATTEMPTS` both chose it deliberately, and the vendor clients' 5 was
#: never argued for anywhere. The difference matters least of anything here — what a retry may
#: touch is the decision, not how many times.
MAX_ATTEMPTS = 4

#: A 429's own `Retry-After`, capped. Above this the header is ignored in favour of the curve.
RETRY_AFTER_CAP_S = 30.0

#: The fallback curve when the vendor states no `Retry-After`. `_wait` looks this up by NAME at
#: call time, which is what lets a test collapse the sleep — the three clients' suites drive real
#: retry loops through httpx transports, and 1s, 2s, 4s, 8s turns each of those into half a minute
#: of waiting (`monkeypatch.setattr("api_log.retry.BACKOFF", wait_none())`).
BACKOFF = wait_exponential_jitter(initial=1, max=30)


def retryable_for(method: str) -> Callable[[BaseException], bool]:
    """Whether to retry, decided by the METHOD as well as by the failure.

    See the module docstring for the rule and for what each failure proves.
    """
    idempotent = method.upper() in IDEMPOTENT_METHODS

    def retryable(exc: BaseException) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 429:
                return True
            return idempotent and status >= 500
        return idempotent and isinstance(exc, httpx.TransportError)

    return retryable


def retry_after_seconds(exc: BaseException | None) -> float | None:
    """The vendor's own `Retry-After` in seconds, capped — or None when it did not state one.

    None for the HTTP-date form too, deliberately: the caller falls back to its backoff curve.
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    raw = exc.response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        stated = float(raw)
    except (TypeError, ValueError):
        return None
    return min(max(stated, 0.0), RETRY_AFTER_CAP_S)


def _wait(state: RetryCallState) -> float:
    stated = retry_after_seconds(state.outcome.exception() if state.outcome else None)
    return stated if stated is not None else float(BACKOFF(state))


def policy(method: str) -> dict[str, Any]:
    """The `AsyncRetrying(**…)` keywords for one request of `method`.

    `reraise=True` because every caller wraps the vendor's own exception into its own error type
    afterwards, and tenacity's `RetryError` would hide the status code they all read.
    """
    return {
        "retry": retry_if_exception(retryable_for(method)),
        "wait": _wait,
        "stop": stop_after_attempt(MAX_ATTEMPTS),
        "reraise": True,
    }
