"""The three promises this module makes, pinned.

It is 141 lines with no dependencies, which makes it look like it does not need tests. It does:
every one of its guarantees is the kind that fails silently and is discovered later, from the
wrong side.

  1. **Credentials never reach the recorder.** `audit.api_log` is append-only, so a leaked
     token cannot be cleaned up afterwards — the module's own docstring says so.
  2. **A call that raised is still recorded.** That is the one an incident is reconstructed
     from, and the one a naive `recorder(call)` after the request would miss.
  3. **Recording never breaks the caller.** Failing an adviser's transfer because the audit
     insert deadlocked would be the worse outcome.
"""

from __future__ import annotations

from typing import Any

import pytest

import api_log
from api_log import (
    ApiCall,
    clear_default_recorder,
    default_recorder,
    record,
    redact,
    set_default_recorder,
)


class TestRedact:
    @pytest.mark.parametrize(
        "key",
        [
            "authorization",
            "Authorization",
            "AUTHORIZATION",
            "auth",
            "token",
            "access_token",
            "refresh_token",
            "secret",
            "client_secret",
            "password",
            "apikey",
            "ApiKey",
            "x-api-key",
            "X-API-KEY",
            "api_key",
            "api key",
            "client-secret",
            "x-auth-token",
            "credentials",
        ],
    )
    def test_every_credential_spelling_is_stripped(self, key: str) -> None:
        """Matched on a lower-cased SUBSTRING, so header-case and prefixed variants all go."""
        assert redact({key: "hunter2"}) == {key: "[redacted]"}

    def test_the_value_is_replaced_not_merely_masked(self) -> None:
        # A partial mask ("hun***") still leaks length and prefix.
        assert redact({"token": "eyJhbGciOi"})["token"] == "[redacted]"

    def test_non_secret_keys_survive(self) -> None:
        """Bodies are KEPT on purpose — a redacted audit trail answers no questions."""
        body = {"amount": 1250.0, "schemeReference": "SR-000123", "cam_status": 809130003}
        assert redact(body) == body

    def test_nested_secrets_are_reached(self) -> None:
        payload = {"headers": {"Authorization": "Bearer x"}, "body": {"amount": 10}}
        assert redact(payload) == {
            "headers": {"Authorization": "[redacted]"},
            "body": {"amount": 10},
        }

    def test_secrets_inside_lists_are_reached(self) -> None:
        payload = {"calls": [{"apikey": "k1"}, {"apikey": "k2"}]}
        assert redact(payload) == {"calls": [{"apikey": "[redacted]"}, {"apikey": "[redacted]"}]}

    def test_lists_are_capped(self) -> None:
        """A 10k-element body would otherwise be copied whole into an append-only table."""
        assert len(redact([{"n": i} for i in range(500)])) == 50

    def test_recursion_is_bounded(self) -> None:
        """Depth-capped rather than unbounded: a cyclic-ish nested payload must not blow the
        stack inside a `finally` block, where the exception would mask the real one."""
        deep: dict[str, Any] = {"token": "leak"}
        for _ in range(50):
            deep = {"inner": deep}
        redact(deep)  # must not raise

    def test_a_secret_below_the_depth_cap_is_still_stripped(self) -> None:
        payload = {"a": {"b": {"c": {"authorization": "Bearer x"}}}}
        assert payload["a"]["b"]["c"]["authorization"] == "Bearer x"
        assert redact(payload)["a"]["b"]["c"]["authorization"] == "[redacted]"

    @pytest.mark.parametrize("value", [None, "plain", 42, 1.5, True])
    def test_scalars_pass_through(self, value: Any) -> None:
        assert redact(value) == value


class TestApiCallRedacted:
    def test_carries_every_field_across(self) -> None:
        """A field added to ApiCall and forgotten in `redacted()` is silently dropped from the
        audit trail — nothing else would catch it."""
        call = ApiCall(
            provider="sei",
            service="ctc",
            transport="http",
            method="POST",
            url="https://example.test/x",
            operation="create_currency_receipt",
            request={"amount": "1250.00"},
            response={"activityId": "857458"},
            status_code=200,
            ok=True,
            error=None,
            duration_ms=12,
            entity_type="account",
            entity_id=7,
            # So the response survives too, and every field can be compared in one sweep. The
            # retention rule itself is pinned by TestResponseRetention.
            keep_response=True,
            extra={"tenant": "firm-a"},
        )
        copy = call.redacted()
        for name in ApiCall.__dataclass_fields__:
            assert getattr(copy, name) == getattr(call, name), f"{name} lost in redacted()"

    def test_strips_credentials_from_request_response_and_extra(self) -> None:
        call = ApiCall(
            provider="morgan_lloyd",
            request={"Authorization": "Bearer secret", "amount": 10},
            response={"client_secret": "s"},
            extra={"apikey": "k"},
        )
        copy = call.redacted()
        assert copy.request == {"Authorization": "[redacted]", "amount": 10}
        assert copy.response == {"client_secret": "[redacted]"}
        assert copy.extra == {"apikey": "[redacted]"}

    def test_does_not_mutate_the_original(self) -> None:
        """The caller keeps using `call` after recording; redaction must not blank its own view."""
        call = ApiCall(provider="seccl", request={"token": "t"})
        call.redacted()
        assert call.request == {"token": "t"}


class TestRecord:
    def test_records_a_successful_call(self) -> None:
        seen: list[ApiCall] = []
        with record(seen.append, ApiCall(provider="sei")) as call:
            call.status_code = 200
            call.ok = True
        assert len(seen) == 1
        assert seen[0].ok is True and seen[0].status_code == 200

    def test_records_a_call_that_raised_and_re_raises(self) -> None:
        seen: list[ApiCall] = []
        with pytest.raises(TimeoutError):
            with record(seen.append, ApiCall(provider="sei")):
                raise TimeoutError("upstream went away")
        assert len(seen) == 1
        assert seen[0].ok is False
        assert seen[0].error == "TimeoutError: upstream went away"

    def test_an_error_the_client_already_set_is_not_overwritten(self) -> None:
        """The client's diagnosis is better than the exception's repr — e.g. Morgan Lloyd's
        responseKey=1 envelope, which arrives as HTTP 200."""
        seen: list[ApiCall] = []
        with pytest.raises(ValueError):
            with record(seen.append, ApiCall(provider="morgan_lloyd")) as call:
                call.error = "responseKey=1: Scheme not found"
                raise ValueError("boom")
        assert seen[0].error == "responseKey=1: Scheme not found"

    def test_the_recorder_receives_the_redacted_copy(self) -> None:
        seen: list[ApiCall] = []
        with record(seen.append, ApiCall(provider="sei")) as call:
            call.request = {"Authorization": "Bearer secret"}
        assert seen[0].request == {"Authorization": "[redacted]"}

    def test_duration_is_always_stamped(self) -> None:
        seen: list[ApiCall] = []
        with record(seen.append, ApiCall(provider="sei")):
            pass
        assert seen[0].duration_ms is not None and seen[0].duration_ms >= 0

    def test_duration_is_stamped_even_when_the_call_raised(self) -> None:
        seen: list[ApiCall] = []
        with pytest.raises(RuntimeError):
            with record(seen.append, ApiCall(provider="sei")):
                raise RuntimeError("x")
        assert seen[0].duration_ms is not None

    def test_a_failing_recorder_never_breaks_the_caller(self) -> None:
        """The whole point of the swallow: a deadlocked audit insert must not fail a transfer."""

        def explode(call: ApiCall) -> None:
            raise RuntimeError("audit insert deadlocked")

        with record(explode, ApiCall(provider="sei")) as call:
            call.ok = True
        # Reaching here is the assertion.

    def test_a_failing_recorder_does_not_mask_the_callers_own_exception(self) -> None:
        """A raise inside the `finally` would replace the real failure with the audit's."""

        def explode(call: ApiCall) -> None:
            raise RuntimeError("audit insert deadlocked")

        with pytest.raises(TimeoutError):
            with record(explode, ApiCall(provider="sei")):
                raise TimeoutError("the real problem")

    def test_no_recorder_is_a_no_op(self) -> None:
        """A sync worker or a script has nowhere to write; the client must still work."""
        with record(None, ApiCall(provider="sei")) as call:
            call.ok = True
        assert call.ok is True


class TestResponseRetention:
    """A bare success keeps no response body; a failure and a create both do.

    The rule exists because sync volume makes success bodies expensive and they are usually an
    echo of the request. The two exceptions are the whole of its subtlety, so both are pinned.
    """

    def test_a_successful_response_is_dropped(self) -> None:
        seen: list[ApiCall] = []
        with record(seen.append, ApiCall(provider="seccl")) as call:
            call.ok = True
            call.response = {"data": [1, 2, 3]}
        assert seen[0].response is None

    def test_a_failed_response_is_kept(self) -> None:
        """The vendor's message is what an incident is reconstructed from."""
        seen: list[ApiCall] = []
        with record(seen.append, ApiCall(provider="morgan_lloyd")) as call:
            call.ok = False
            call.response = {"message": "Scheme MLSIPP/00001 not found"}
        assert seen[0].response == {"message": "Scheme MLSIPP/00001 not found"}

    def test_a_successful_create_keeps_its_response(self) -> None:
        """`cam_createcontact` answers with the new contactid, and after `platform.links` that
        id lives nowhere else — discarding it is how the same person gets created twice."""
        seen: list[ApiCall] = []
        with record(seen.append, ApiCall(provider="morgan_lloyd", keep_response=True)) as call:
            call.ok = True
            call.response = {"response": {"contactid": "8eac9382-15f6-f011-8406-0022481a4023"}}
        assert seen[0].response == {
            "response": {"contactid": "8eac9382-15f6-f011-8406-0022481a4023"}
        }

    def test_entity_type_alone_does_not_keep_a_response(self) -> None:
        """`entity_type` means "what this was ABOUT" — a READ sets it too. Keying retention off
        it was the first attempt and it was wrong in both directions: it kept every successful
        read of a known entity, and kept nothing for the ML client, which sets it nowhere and is
        the one whose create ids most need keeping."""
        seen: list[ApiCall] = []
        with record(seen.append, ApiCall(provider="seccl", entity_type="account")) as call:
            call.ok = True
            call.response = {"echo": True}
        assert seen[0].response is None

    def test_the_request_is_kept_whatever_the_outcome(self) -> None:
        """Dropping responses must not quietly become dropping evidence of what we sent."""
        seen: list[ApiCall] = []
        with record(seen.append, ApiCall(provider="sei")) as call:
            call.ok = True
            call.request = {"amount": 100}
            call.response = {"echo": True}
        assert seen[0].request == {"amount": 100}

    def test_a_kept_response_is_still_redacted(self) -> None:
        """The exception is about volume, never about secrecy."""
        seen: list[ApiCall] = []
        with record(seen.append, ApiCall(provider="sei")) as call:
            call.ok = False
            call.response = {"token": "shh", "detail": "nope"}
        assert seen[0].response == {"token": "[redacted]", "detail": "nope"}


class TestDefaultRecorder:
    """The process-wide default — the piece whose absence left `audit.api_log` empty.

    Every client accepted a `recorder` and no caller ever passed one, so the table, its drain,
    its endpoints and its admin page all existed with no producer. These pin the mechanism that
    replaced thirty-five call-site changes with four installs.
    """

    def teardown_method(self) -> None:
        clear_default_recorder()

    def test_absent_by_default(self) -> None:
        """A script or an out-of-monorepo SDK consumer has no database; None is correct."""
        clear_default_recorder()
        assert default_recorder() is None

    def test_installed_then_returned(self) -> None:
        # Bound to a name first: `seen.append` builds a NEW bound-method object on every
        # attribute access, so `default_recorder() is seen.append` can never hold.
        seen: list[ApiCall] = []
        recorder = seen.append
        set_default_recorder(recorder)
        assert default_recorder() is recorder

    def test_the_last_install_wins(self) -> None:
        first: list[ApiCall] = []
        second: list[ApiCall] = []
        first_recorder, second_recorder = first.append, second.append
        set_default_recorder(first_recorder)
        set_default_recorder(second_recorder)
        assert default_recorder() is second_recorder

    def test_clearing_restores_absence(self) -> None:
        seen: list[ApiCall] = []
        set_default_recorder(seen.append)
        clear_default_recorder()
        assert default_recorder() is None

    def test_record_does_not_reach_for_it_itself(self) -> None:
        """`record(None, …)` stays a no-op: resolving the default is the CLIENT's job, done per
        call, so that an explicit recorder can still win and a test can pass None deliberately."""
        seen: list[ApiCall] = []
        set_default_recorder(seen.append)
        with record(None, ApiCall(provider="sei")) as call:
            call.ok = True
        assert seen == []


# ---- the operation name -------------------------------------------------------------------
# It was derived three different ways, once per vendor client, and only Morgan Lloyd's was
# tested — the one that had already been fixed the hard way. `operation` is what the log groups
# by, filters on, and what the outbound console joins its tabs by, so a derivation that varies
# per record does not merely look untidy: it fragments every count and makes an exact-match
# filter miss the call.


class TestOperationOf:
    def test_a_query_string_is_not_part_of_the_operation(self) -> None:
        """Morgan Lloyd's scar: OData reads landed one distinct operation per `$select`."""
        assert api_log.operation_of("cam_schemes?$select=cam_name") == "cam_schemes"

    def test_a_key_predicate_is_not_part_of_the_operation(self) -> None:
        assert api_log.operation_of("contacts(00000000-0000-0000-0000-000000000001)") == "contacts"

    def test_a_version_prefix_is_not_the_operation(self) -> None:
        """Seccl recorded `v1` for `/v1/fee-structures/{firm}` — its one versioned endpoint, and
        the name told a reader nothing about the call."""
        assert api_log.operation_of("/v1/fee-structures/SELCT") == "fee-structures"

    def test_the_ordinary_seccl_shape_is_unchanged(self) -> None:
        assert api_log.operation_of("/client/SELCT") == "client"

    def test_take_last_names_the_operation_after_its_service(self) -> None:
        """SEI's SWP paths are `service/operation`; the service is recorded separately."""
        assert (
            api_log.operation_of("/currencyreceiptservices/createCurrencyReceipt", take="last")
            == "createCurrencyReceipt"
        )

    def test_an_identifier_is_why_last_is_not_the_default(self) -> None:
        """A trailing id under `take="last"` would record one operation per RECORD — the same
        fragmentation the query string caused, from the other end. Stated here so the choice is
        made per vendor rather than inherited."""
        assert api_log.operation_of("/accountservice/accounts/12345", take="last") == "12345"
        assert api_log.operation_of("/accountservice/accounts/12345") == "accountservice"

    def test_an_empty_path_names_nothing(self) -> None:
        assert api_log.operation_of("") is None
        assert api_log.operation_of("/") is None

    def test_segments_strip_query_and_predicate_together(self) -> None:
        assert api_log.path_segments("/a(1)/b?x=1") == ("a", "b")


class TestBodyOf:
    def test_json_comes_back_as_json(self) -> None:
        class _R:
            def json(self) -> dict[str, int]:
                return {"a": 1}

        assert api_log.body_of(_R()) == {"a": 1}

    def test_a_non_json_body_is_truncated_not_dropped(self) -> None:
        """A rogue endpoint answering with a 5MB HTML error page must not put 5MB into an
        append-only table once per retry cycle — but the first few KB is what an incident is
        reconstructed from, so it is truncated rather than discarded."""

        class _R:
            text = "x" * 10_000

            def json(self) -> dict[str, int]:
                raise ValueError("not json")

        assert len(api_log.body_of(_R())) == 4000

    def test_an_empty_body_is_none(self) -> None:
        class _R:
            text = ""

            def json(self) -> dict[str, int]:
                raise ValueError("not json")

        assert api_log.body_of(_R()) is None
