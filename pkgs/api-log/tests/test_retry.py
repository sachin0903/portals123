"""Which failures may be retried, for which methods, and for how long.

The predicate used to live three times over — once per vendor client — with diverged attempt
counts and no `Retry-After` in any of them. It is one implementation now, so it is tested once
here and each client's own suite asserts only its WIRING (that every retry loop in it goes through
`policy(method)`), which is the half a behavioural test cannot see: the regression that started
this was a CALL SITE moving onto an existing loop, not the predicate being wrong.
"""

from __future__ import annotations

import httpx
import pytest
from tenacity import AsyncRetrying, RetryCallState

from api_log.retry import (
    IDEMPOTENT_METHODS,
    MAX_ATTEMPTS,
    RETRY_AFTER_CAP_S,
    policy,
    retry_after_seconds,
    retryable_for,
)


def _status(code: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://vendor.test/instruct")
    return httpx.HTTPStatusError(
        "boom", request=request, response=httpx.Response(code, headers=headers)
    )


class TestAWriteIsRetriedOnlyWhenTheCallWasRefused:
    def test_a_429_is_retried(self) -> None:
        """The one failure that PROVES the request was not processed — refused at the gate, before
        any effect — so retrying it cannot instruct twice. This is what the loop is worth keeping
        for on a write."""
        assert retryable_for("POST")(_status(429)) is True

    @pytest.mark.parametrize("code", [500, 502, 503, 504])
    def test_a_5xx_is_not_retried(self, code: int) -> None:
        """The vendor may have committed the write and lost the answer on the way back."""
        assert retryable_for("POST")(_status(code)) is False

    @pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
    def test_a_transport_error_is_not_retried_for_any_write(self, method: str) -> None:
        """Nothing distinguishes "never sent" from "sent, answer lost"."""
        request = httpx.Request(method, "https://vendor.test/instruct")
        assert retryable_for(method)(httpx.ConnectError("reset", request=request)) is False


class TestAReadIsStillRetried:
    @pytest.mark.parametrize("method", sorted(IDEMPOTENT_METHODS))
    def test_a_5xx_and_a_transport_error_are_both_retried(self, method: str) -> None:
        """Reliability is not given up where repeating the call cannot repeat an effect."""
        request = httpx.Request(method, "https://vendor.test/read")
        assert retryable_for(method)(_status(503)) is True
        assert retryable_for(method)(httpx.ConnectError("reset", request=request)) is True

    def test_the_method_is_read_case_insensitively(self) -> None:
        """Two of the three clients pass the method straight through from a caller."""
        assert retryable_for("get")(_status(503)) is True

    def test_a_4xx_is_never_retried(self) -> None:
        """A 400/401/404 is a fact about the request, not a transient condition — the rule
        `app/aml/client.py` states in as many words."""
        for code in (400, 401, 403, 404, 409, 422):
            assert retryable_for("GET")(_status(code)) is False


class TestRetryAfter:
    def test_the_vendors_own_interval_wins(self) -> None:
        """A 429 is the one status retried for every method, so it is the one where obeying the
        vendor matters most: a rate limit naming five seconds, retried after one, is three more
        refusals. None of the three clients read this header before."""
        assert retry_after_seconds(_status(429, {"Retry-After": "5"})) == 5.0

    def test_it_is_capped(self) -> None:
        """A hostile or typo'd value ("3600") would otherwise park a worker for an hour."""
        assert retry_after_seconds(_status(429, {"Retry-After": "3600"})) == RETRY_AFTER_CAP_S

    def test_a_negative_value_does_not_become_a_negative_wait(self) -> None:
        assert retry_after_seconds(_status(429, {"Retry-After": "-5"})) == 0.0

    def test_the_http_date_form_falls_through_to_the_curve(self) -> None:
        """Legal, and not parsed: the point is to wait a sensible amount, not to obey precisely."""
        assert (
            retry_after_seconds(_status(429, {"Retry-After": "Wed, 09 Sep 2026 10:00:00 GMT"}))
            is None
        )

    def test_no_header_and_no_response_are_both_none(self) -> None:
        assert retry_after_seconds(_status(429)) is None
        assert retry_after_seconds(httpx.ConnectError("reset")) is None
        assert retry_after_seconds(None) is None

    def test_the_wait_the_loop_actually_computes_comes_from_the_header(self) -> None:
        """`wait` is a callable over tenacity's `RetryCallState` and reads the exception through
        `state.outcome` — the wiring between the header and the sleep, which a test of
        `retry_after_seconds` alone does not touch."""
        wait = policy("POST")["wait"]
        state = RetryCallState(AsyncRetrying(**policy("POST")), lambda: None, (), {})
        state.outcome = _Outcome(_status(429, {"Retry-After": "7"}))  # type: ignore[assignment]
        assert wait(state) == 7.0

    def test_it_falls_back_to_the_curve_when_the_vendor_states_nothing(self) -> None:
        wait = policy("GET")["wait"]
        state = RetryCallState(AsyncRetrying(**policy("GET")), lambda: None, (), {})
        state.attempt_number = 1
        state.outcome = _Outcome(_status(503))  # type: ignore[assignment]
        assert wait(state) > 0.0


class _Outcome:
    """Minimal stand-in for tenacity's `Future` — `_wait` reads only `.exception()`."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def exception(self) -> BaseException:
        return self._exc


def test_the_policy_is_one_object_the_clients_can_splat() -> None:
    """`AsyncRetrying(**policy(method))` is the whole contract, so the keys are pinned: a client
    that had to name `stop=` itself is a client that can diverge on the count again."""
    keys = policy("GET")
    assert set(keys) == {"retry", "wait", "stop", "reraise"}
    assert keys["reraise"] is True
    assert MAX_ATTEMPTS == 4
