"""``lookup`` — the inbound half, proved without a network and without a database.

Everything in ``app/flows/lookup.py`` is pure or HTTP, which is exactly why it was split out of the
executor: the controls that matter here are testable in isolation, and each one below is a
CONTROL rather than a behaviour —

* the allowlist is the primary SSRF defence, so an unknown key must never become a call;
* a redirect must not be followed, because a 302 is how an allowlisted endpoint stops being one;
* the body is read under a cap and only if it is JSON, because a mapping applied to a report or an
  HTML error page produces silent nulls a branch then treats as facts;
* there are two deadlines, because httpx's are per-operation and a dribbling server satisfies a
  read timeout for ever;
* no refusal message may carry a fragment of the answer — it is the client's data.

The network is faked at ``httpx.MockTransport``, httpx's own seam, so the REAL client, streaming
and timeout configuration are exercised and only the socket is not. The DB-backed half (what a
lookup writes into case memory, and what the audit event says) lives in
``test_flows_action_steps.py`` and ``test_effects.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import settings
from app.flows import dispatcher, effects
from app.flows import lookup as lookup_mod

CONTRACT = Path(__file__).resolve().parents[3] / (
    "pkgs/ui/src/lib/components/book/journeys/flow-contract.json"
)


# ---------------------------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------------------------


def _serve(handler: Any, monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Point ``fetch`` at a MockTransport, keeping the real client's policy. Returns the requests.

    ``_build_client`` exists so the house policy (timeouts, no redirects) lives in one place; here
    it is where the socket is replaced, so a test that passes proves the policy the production
    call carries rather than one a fake invented.
    """
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def _client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(_record),
            timeout=httpx.Timeout(lookup_mod.TIMEOUT_S, connect=lookup_mod.CONNECT_TIMEOUT_S),
            follow_redirects=False,
        )

    monkeypatch.setattr(lookup_mod, "_build_client", _client)
    return seen


def _json_response(payload: Any, **kwargs: Any) -> httpx.Response:
    return httpx.Response(200, json=payload, **kwargs)


# ---------------------------------------------------------------------------------------------
# The contract mirror
# ---------------------------------------------------------------------------------------------


@pytest.mark.pkgs_ui_pin
def test_the_timeout_is_the_contracts_lookup_timeout() -> None:
    """``safety.lookupTimeoutMs`` is the shared number; a Python constant that drifted from it
    would mean the builder promising an author one budget and the engine enforcing another."""
    safety = json.loads(CONTRACT.read_text(encoding="utf-8"))["safety"]
    assert lookup_mod.TIMEOUT_S * 1000 == safety["lookupTimeoutMs"]
    assert lookup_mod.CONNECT_TIMEOUT_S < lookup_mod.TIMEOUT_S


def test_the_mapping_cap_matches_the_webhook_payload_cap() -> None:
    """Same bound, same reasoning (``effects.MAX_PAYLOAD_KEYS``): an unbounded key list is how
    a rule ships the whole context somewhere by accident."""
    assert lookup_mod.MAX_MAPPING_KEYS == effects.MAX_PAYLOAD_KEYS


@pytest.mark.pkgs_ui_pin
def test_the_config_keys_are_the_contracts() -> None:
    from app.flows import graph as graph_mod

    declared = json.loads(CONTRACT.read_text(encoding="utf-8"))["actionTypes"]["lookup"]["config"]
    assert graph_mod.ACTION_CONFIG_KEYS["lookup"] == set(declared)


# ---------------------------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------------------------


def test_an_unknown_endpoint_key_resolves_to_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "automation_lookup_endpoints", {"risk": "https://r.example/x"})
    assert lookup_mod.endpoint("risk") == "https://r.example/x"
    assert lookup_mod.endpoint("whatever") is None


def test_the_lookup_allowlist_is_not_the_webhook_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE separation: an endpoint an operator was content to notify is not thereby one a client's
    journey may take its facts from. Configuring one must not configure the other."""
    monkeypatch.setattr(settings, "automation_webhook_endpoints", {"crm": "https://crm.example/h"})
    monkeypatch.setattr(settings, "automation_lookup_endpoints", {})
    assert lookup_mod.endpoint("crm") is None
    assert effects.webhook_endpoint("crm") == "https://crm.example/h"


# ---------------------------------------------------------------------------------------------
# The mapping grammar
# ---------------------------------------------------------------------------------------------


def test_a_request_mapping_names_fields_to_send_from_context_paths() -> None:
    assert lookup_mod.parse_request_mapping(
        {"request_mapping": {"email": " contact.email ", "band": "memory.risk_band"}}
    ) == {"email": "contact.email", "band": "memory.risk_band"}
    # Absent is fine: "what is today's rate" is a lookup with no inputs.
    assert lookup_mod.parse_request_mapping({}) == {}


def test_a_mapping_may_arrive_as_a_json_object_string() -> None:
    """The seam with a builder whose config fields are text areas — ``action_config``'s tolerance,
    for its reason. Blank text is an absent mapping, not a broken one."""
    assert lookup_mod.parse_request_mapping({"request_mapping": '{"email": "contact.email"}'}) == {
        "email": "contact.email"
    }
    assert lookup_mod.parse_request_mapping({"request_mapping": "   "}) == {}
    with pytest.raises(lookup_mod.LookupRefused, match="not a readable set of fields"):
        lookup_mod.parse_request_mapping({"request_mapping": "email = contact.email"})


def test_a_request_mapping_refuses_junk() -> None:
    with pytest.raises(lookup_mod.LookupRefused, match="must be a set of named fields"):
        lookup_mod.parse_request_mapping({"request_mapping": ["contact.email"]})
    with pytest.raises(lookup_mod.LookupRefused, match="must name a field"):
        lookup_mod.parse_request_mapping({"request_mapping": {"email": ""}})
    with pytest.raises(lookup_mod.LookupRefused, match="not a usable request field name"):
        lookup_mod.parse_request_mapping({"request_mapping": {"e mail": "contact.email"}})
    too_many = {f"f{i}": "contact.email" for i in range(lookup_mod.MAX_MAPPING_KEYS + 1)}
    with pytest.raises(lookup_mod.LookupRefused, match="at most"):
        lookup_mod.parse_request_mapping({"request_mapping": too_many})


def test_a_response_mapping_is_memory_key_to_path() -> None:
    assert lookup_mod.parse_response_mapping(
        {"response_mapping": {"risk_band": "data.risk.band", "score": "results.0.score"}}
    ) == {"risk_band": "data.risk.band", "score": "results.0.score"}


def test_a_response_mapping_is_required() -> None:
    """A lookup that remembers nothing has called a third party for no reason — and the journey it
    sits in cannot be branching on anything it fetched."""
    with pytest.raises(lookup_mod.LookupRefused, match="nothing it fetched would be remembered"):
        lookup_mod.parse_response_mapping({})


def test_a_response_mapping_refuses_bad_memory_names_and_clever_paths() -> None:
    with pytest.raises(lookup_mod.LookupRefused, match="not a usable memory name"):
        lookup_mod.parse_response_mapping({"response_mapping": {"Risk Band": "a"}})
    # A dotted memory name is ambiguous against the nested lookup every reader performs.
    with pytest.raises(lookup_mod.LookupRefused, match="not a usable memory name"):
        lookup_mod.parse_response_mapping({"response_mapping": {"risk.band": "a"}})
    with pytest.raises(lookup_mod.LookupRefused, match="levels deep"):
        lookup_mod.parse_response_mapping({"response_mapping": {"x": "a.b.c.d.e.f"}})
    for clever in ("results[0].score", "results.*.score", "$.data", "results..score"):
        with pytest.raises(lookup_mod.LookupRefused, match="not a plain path"):
            lookup_mod.parse_response_mapping({"response_mapping": {"x": clever}})


# ---------------------------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------------------------


def test_extract_walks_objects_and_list_positions() -> None:
    payload = {"data": {"risk": {"band": "high"}}, "results": [{"score": 7}, {"score": 9}]}
    assert lookup_mod.extract(payload, "data.risk.band") == "high"
    assert lookup_mod.extract(payload, "results.1.score") == 9
    assert lookup_mod.extract(payload, "results.9.score") is lookup_mod.MISSING
    assert lookup_mod.extract(payload, "data.risk.grade") is lookup_mod.MISSING
    assert lookup_mod.extract(payload, "data.risk.band.deeper") is lookup_mod.MISSING


def test_extract_distinguishes_a_null_answer_from_no_answer() -> None:
    """``null`` is the endpoint saying "no value"; MISSING is it not saying anything. A branch
    testing ``isEmpty`` means the first and not the second, which is why ``select`` skips one and
    keeps the other."""
    payload = {"band": None}
    assert lookup_mod.extract(payload, "band") is None
    assert lookup_mod.select(payload, {"risk_band": "band", "score": "score"}) == {
        "risk_band": None
    }


# ---------------------------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_happy_path_posts_a_json_body_and_parses_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The facts go in the BODY, never the query string: a query string lands in every access log
    between here and the far end, and a lookup's request is assembled out of client facts."""
    seen = _serve(lambda _r: _json_response({"band": "high"}), monkeypatch)
    payload = await lookup_mod.fetch("risk", "https://r.example/x", {"email": "ada@example.com"})

    assert payload == {"band": "high"}
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.query == b""
    assert json.loads(seen[0].content) == {"email": "ada@example.com"}
    assert seen[0].headers["accept"] == "application/json"


@pytest.mark.asyncio
async def test_a_redirect_is_refused_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE SSRF control's second half: an allowlist a 302 can walk out of is not an allowlist."""
    seen = _serve(
        lambda _r: httpx.Response(302, headers={"location": "https://elsewhere.example/x"}),
        monkeypatch,
    )
    with pytest.raises(lookup_mod.LookupRefused, match="redirected"):
        await lookup_mod.fetch("risk", "https://r.example/x", {})
    assert len(seen) == 1  # the redirect target was never called


@pytest.mark.asyncio
async def test_a_non_2xx_answer_is_a_refusal_naming_the_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(lambda _r: httpx.Response(503, text="the database is on fire"), monkeypatch)
    with pytest.raises(lookup_mod.LookupRefused) as refusal:
        await lookup_mod.fetch("risk", "https://r.example/x", {})
    assert "503" in str(refusal.value)
    assert "on fire" not in str(refusal.value)  # the far end's body is never quoted back


@pytest.mark.asyncio
async def test_a_non_json_answer_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A proxy's HTML error page parses as nothing, and a mapping applied to it would write silent
    nulls a branch then treats as facts."""
    _serve(
        lambda _r: httpx.Response(200, text="<html>gateway timeout</html>"),
        monkeypatch,
    )
    with pytest.raises(lookup_mod.LookupRefused, match="did not answer with JSON"):
        await lookup_mod.fetch("risk", "https://r.example/x", {})


@pytest.mark.asyncio
async def test_json_that_is_not_json_is_refused_without_quoting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(
        lambda _r: httpx.Response(
            200,
            content=b'{"client_reference": "NI-QQ123456C", truncated',
            headers={"content-type": "application/json"},
        ),
        monkeypatch,
    )
    with pytest.raises(lookup_mod.LookupRefused) as refusal:
        await lookup_mod.fetch("risk", "https://r.example/x", {})
    assert "not valid JSON" in str(refusal.value)
    assert "QQ123456C" not in str(refusal.value)


@pytest.mark.asyncio
async def test_a_bare_scalar_answer_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(lambda _r: _json_response("high"), monkeypatch)
    with pytest.raises(lookup_mod.LookupRefused, match="single value"):
        await lookup_mod.fetch("risk", "https://r.example/x", {})


@pytest.mark.asyncio
async def test_an_oversized_answer_is_refused_by_the_declared_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cheapest first: a content-length over the cap is refused before a byte of body is read."""
    _serve(
        lambda _r: httpx.Response(
            200,
            content=b"{}",
            headers={
                "content-type": "application/json",
                "content-length": str(lookup_mod.MAX_RESPONSE_BYTES + 1),
            },
        ),
        monkeypatch,
    )
    with pytest.raises(lookup_mod.LookupRefused, match="larger than"):
        await lookup_mod.fetch("risk", "https://r.example/x", {})


@pytest.mark.asyncio
async def test_an_oversized_streamed_answer_is_refused_mid_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap that actually matters: a chunked answer declares no length, so the only place to
    stop it is while it arrives — before an unbounded allocation on a third party's say-so."""

    async def _chunks() -> Any:
        for _ in range(8):
            yield b"x" * (lookup_mod.MAX_RESPONSE_BYTES // 4)

    def _stream(_request: httpx.Request) -> httpx.Response:
        # An async iterable, so httpx builds a real streaming response with NO content-length —
        # the shape that makes the mid-stream cap the only one that can bite.
        return httpx.Response(200, content=_chunks(), headers={"content-type": "application/json"})

    _serve(_stream, monkeypatch)
    with pytest.raises(lookup_mod.LookupRefused, match="larger than"):
        await lookup_mod.fetch("risk", "https://r.example/x", {})


@pytest.mark.asyncio
async def test_a_slow_endpoint_times_out_rather_than_holding_the_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OVERALL deadline. httpx's timeouts are per-operation, so a server that dribbles a byte
    inside every read window satisfies them for ever; ``asyncio.timeout`` is what bounds the whole
    call — and therefore the transaction the case is waiting in."""

    async def _slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return _json_response({"band": "high"})

    monkeypatch.setattr(lookup_mod, "TIMEOUT_S", 0.05)
    _serve(_slow, monkeypatch)
    with pytest.raises(lookup_mod.LookupRefused, match="did not answer within"):
        await lookup_mod.fetch("risk", "https://r.example/x", {})


@pytest.mark.asyncio
async def test_an_unreachable_endpoint_is_a_refusal_naming_neither_the_url_nor_a_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 61] Connection refused to 10.0.0.7")

    _serve(_refuse, monkeypatch)
    with pytest.raises(lookup_mod.LookupRefused) as refusal:
        await lookup_mod.fetch("risk", "https://r.example/x", {})
    assert "ConnectError" in str(refusal.value)
    assert "10.0.0.7" not in str(refusal.value)


# ---------------------------------------------------------------------------------------------
# Authoring
# ---------------------------------------------------------------------------------------------


def test_preflight_refuses_an_endpoint_this_deployment_does_not_have(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The author cannot fix this and the operator can, so the refusal says whose job it is and
    names the setting they need to add it to."""
    monkeypatch.setattr(settings, "automation_lookup_endpoints", {"risk": "https://r.example/x"})
    issue = lookup_mod.preflight_issue(
        {"endpoint_key": "credit", "response_mapping": {"band": "band"}}
    )
    assert issue is not None
    assert "AUTOMATION_LOOKUP_ENDPOINTS" in issue
    assert "risk" in issue  # what IS configured, so the author can pick one


def test_preflight_refuses_a_lookup_with_no_endpoint_and_passes_a_good_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "automation_lookup_endpoints", {"risk": "https://r.example/x"})
    assert "names no 'endpoint_key'" in (lookup_mod.preflight_issue({}) or "")
    assert (
        lookup_mod.preflight_issue(
            {
                "endpoint_key": "risk",
                "request_mapping": {"email": "contact.email"},
                "response_mapping": {"risk_band": "data.band"},
            }
        )
        is None
    )


def test_preflight_catches_the_mapping_mistakes_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole runtime symptom of a bad mapping is a journey that never learns anything, which is
    exactly the class of mistake preflight exists to catch at the screen."""
    monkeypatch.setattr(settings, "automation_lookup_endpoints", {"risk": "https://r.example/x"})
    assert "nothing it fetched" in (lookup_mod.preflight_issue({"endpoint_key": "risk"}) or "")
    assert "not a plain path" in (
        lookup_mod.preflight_issue(
            {"endpoint_key": "risk", "response_mapping": {"band": "results[0].band"}}
        )
        or ""
    )


# ---------------------------------------------------------------------------------------------
# The head-of-line bound the module's own note now admits to
# ---------------------------------------------------------------------------------------------


def test_a_full_batch_of_lookups_stays_inside_the_watchdog() -> None:
    """A lookup is synchronous, and on the RULE path that is head-of-line blocking for the estate.

    ``automation.consume_once`` wraps a whole ``BATCH_LIMIT`` batch in ONE transaction holding the
    consumer cursor ``FOR UPDATE`` — a savepoint per firing does not change that — so a batch of
    rule firings against a dead endpoint costs ``BATCH_LIMIT × TIMEOUT_S`` with every tenant's
    automations behind it. That has to fit inside the worker's stall allowance for the loop, and
    the failure if it does not is not a slow minute: the watchdog fails the healthcheck, the
    container restarts, the transaction rolls back with the cursor un-advanced, and the same batch
    is read again for ever.

    Three independently tunable numbers, in three different modules, with nothing but this
    assertion between them and that outage.
    """
    from app.flows.automation import BATCH_LIMIT
    from app.worker import AUTOMATIONS_LOOP, LOOP_STALL_SECONDS

    worst_case = BATCH_LIMIT * lookup_mod.TIMEOUT_S
    allowance = LOOP_STALL_SECONDS[AUTOMATIONS_LOOP]
    assert worst_case < allowance, (
        f"a full batch of lookups can take {worst_case:.0f}s, past the {allowance:.0f}s stall "
        f"allowance — lower BATCH_LIMIT or lookup.TIMEOUT_S, or raise the allowance knowing why"
    )


# ---------------------------------------------------------------------------------------------
# The outbound half — the webhook client carries the same controls
# ---------------------------------------------------------------------------------------------


def _serve_webhook(handler: Any, monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Point the OUTBOX dispatcher's webhook at a MockTransport, keeping the shared house policy.

    Patched at ``lookup._build_client`` — the one factory both directions now go through — with
    ``follow_redirects`` left to whatever that factory sets, so a test that passes proves the
    production policy rather than one the fake invented.
    """
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    real = lookup_mod._build_client

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        client = real(**kwargs)
        client._transport = httpx.MockTransport(_record)
        return client

    monkeypatch.setattr(lookup_mod, "_build_client", _client)
    return seen


_WEBHOOK_PAYLOAD = {"endpoint_key": "crm", "body": {"a": 1}}


@pytest.mark.asyncio
async def test_the_webhook_does_not_follow_a_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same control ``lookup`` sets explicitly, on the call site that was inheriting it from an
    httpx default. A 302 is how an allowlisted endpoint becomes an unallowlisted one, and a
    redirect is not a delivery — so it must not pass the old "< 400" success test either."""
    monkeypatch.setattr(settings, "automation_webhook_endpoints", {"crm": "https://crm.test/hook"})
    seen = _serve_webhook(
        lambda _r: httpx.Response(302, headers={"location": "https://elsewhere.test/steal"}),
        monkeypatch,
    )

    with pytest.raises(RuntimeError, match="redirected"):
        await dispatcher._handle_webhook(1, dict(_WEBHOOK_PAYLOAD))

    # One request, to the allowlisted host — the redirect target was never called.
    assert [str(r.url) for r in seen] == ["https://crm.test/hook"]


@pytest.mark.asyncio
async def test_the_webhook_reply_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing here reads a webhook's body — but ``client.post`` buffers all of it before this code
    sees a header, so an allowlisted endpoint answering with an enormous body was an unbounded
    allocation in the worker. The inbound half has always streamed under a cap; this one now does
    too, and the cap bites mid-stream rather than after the fact."""
    monkeypatch.setattr(settings, "automation_webhook_endpoints", {"crm": "https://crm.test/hook"})
    oversized = b"x" * (dispatcher.WEBHOOK_MAX_RESPONSE_BYTES + 1024)
    _serve_webhook(lambda _r: httpx.Response(200, content=oversized), monkeypatch)

    with pytest.raises(RuntimeError, match="bytes"):
        await dispatcher._handle_webhook(1, dict(_WEBHOOK_PAYLOAD))


@pytest.mark.asyncio
async def test_the_webhook_still_delivers_normally(monkeypatch: pytest.MonkeyPatch) -> None:
    """The controls above must not have turned an ordinary delivery into a failure: a 200 with a
    small body is a success, and the payload still arrives intact."""
    monkeypatch.setattr(settings, "automation_webhook_endpoints", {"crm": "https://crm.test/hook"})
    seen = _serve_webhook(lambda _r: httpx.Response(200, json={"ok": True}), monkeypatch)

    await dispatcher._handle_webhook(
        7, {"endpoint_key": "crm", "automation_uid": "aut_1", "body": {"client": "abc"}}
    )

    assert len(seen) == 1
    body = json.loads(seen[0].content)
    assert body["tenant_id"] == 7
    assert body["data"] == {"client": "abc"}
    assert body["automation"]["uid"] == "aut_1"


@pytest.mark.asyncio
async def test_the_webhook_reports_a_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transient, so it goes back on backoff — and the message names the endpoint KEY rather than
    the operator's URL, the rule the inbound half already follows."""
    monkeypatch.setattr(settings, "automation_webhook_endpoints", {"crm": "https://crm.test/hook"})
    _serve_webhook(lambda _r: httpx.Response(503), monkeypatch)

    with pytest.raises(RuntimeError, match="'crm' returned 503"):
        await dispatcher._handle_webhook(1, dict(_WEBHOOK_PAYLOAD))
