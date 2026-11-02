"""``lookup`` — the inbound half: call an allowlisted endpoint and bring the answer back.

``webhook_out`` tells someone else something. This is its mirror, and the mirror is the harder
direction: what comes back is not a receipt, it is DATA A LATER BRANCH WILL DECIDE ON. A journey
that routes a client to enhanced due diligence because a third party said so has taken the third
party's word for it, so every control here is about bounding what that word can be.

**Why this module exists rather than another block in ``effects``.** Everything below is
pure or HTTP — mapping grammar, path extraction, the request itself — and none of it touches the
database. Kept apart, it is unit-testable without a Postgres and without the executor's import
graph; ``effects.lookup`` is then twenty lines of "resolve, call, remember". The refusal type
is local (``LookupRefused``) precisely so nothing here imports the executor: the executor
translates it into its own ``ActionRefused`` at the one seam.

The decisions, and why they went the way they did:

**1. Synchronous, not queued — and that is what a lookup IS.** ``webhook_out`` enqueues because
nothing waits on it; a lookup exists so that the NEXT node can read the answer, so there is
nothing to hand the case on to until it arrives. Deferring it would mean inventing a resumable
wait node for a value the contract declares as an action's output, and a case parked on an outbox
row nobody can see is worse than one blocked with a reason on it.

**What that costs, honestly, and it is not the same on both paths.** On the FLOW-NODE path it is
one case's step: ``orchestrator`` advances a case in its own transaction, so a slow endpoint holds
that case open for at most ``TIMEOUT_S`` (the contract's ``safety.lookupTimeoutMs``) and nothing
else waits on it. On the RULE path it is head-of-line blocking for the whole estate, and an
earlier draft of this note claimed otherwise — it said the automations loop settles each firing in
its own savepoint, which is true and is not the point: a savepoint is not a transaction.
``automation.consume_once`` wraps an entire ``BATCH_LIMIT`` batch in ONE transaction holding
``FOR UPDATE`` on the consumer cursor, so a batch of rule firings that each call a dead endpoint
is ``BATCH_LIMIT × TIMEOUT_S`` of open transaction with every tenant's automations stalled behind
it. The arithmetic is deliberately kept inside the worker's stall allowance for that loop and
pinned by ``tests/test_flows_lookup.py::test_a_full_batch_of_lookups_stays_inside_the_watchdog`` —
if a future change to the batch size or the timeout breaks that, the healthcheck starts killing
the container mid-batch and the cursor never advances.

The failure path is therefore made RETRYABLE rather than terminal, which is the part that matters:
every refusal below travels back as ``ActionRefused`` → the action step latches ``failed`` → the
case derives to ``blocked``, which ``orchestrator.retry_step`` accepts. A third party being down at
3am parks a case an operator can re-run in the morning; it never wedges one and never latches the
case's terminal ``failed``.

**2. A separate allowlist (``AUTOMATION_LOOKUP_ENDPOINTS``).** Not the webhook list — see the
comment on the setting. Resolved from the LIVE configuration at dispatch, exactly as
``dispatcher._handle_webhook`` re-checks its own: a published flow can outlive the deploy that
allowlisted its endpoint, and an endpoint an operator has withdrawn must fail, not be called from
a definition written before they withdrew it.

**3. SSRF and the response.** The allowlist is the primary control — an author names a key, never
a URL, so there is no attacker-controlled destination to begin with. On top of it:
*redirects are not followed* (a 302 is exactly how an allowlisted endpoint becomes an
unallowlisted one, and following one silently would hand the control back); *the body is read
under a byte cap*, streamed, so an enormous answer is refused rather than buffered; *the content
type must be JSON*, because a mapping applied to something else can only produce nonsense; and
*there are two deadlines* — httpx's per-operation timeouts and an overall ``asyncio.timeout``,
because a server dribbling one byte every second satisfies a read timeout for ever.

**4. The mappings are small and the paths are shallow.** ``request_mapping`` and
``response_mapping`` are both ``{destination: source}`` — the same direction as
``fill_form.config.remember`` — and both are capped at ``MAX_MAPPING_KEYS``, the bound and the
reasoning of ``effects.MAX_PAYLOAD_KEYS`` (an unbounded key list is how a rule ships the whole
context to a third party by accident). A response path is dotted, at most
``MAX_PATH_SEGMENTS`` deep, and addresses object keys and list indices only. No wildcards, no
filters, no expressions: a full JSONPath is a language, and a language authored in a no-code
builder and evaluated in the worker is the thing this codebase keeps declining to build
(``app/flows/interpolation.py``'s header, for the same reason).

**5. The response body is never logged, never audited, never in a refusal message.** It may carry
a client's identity data. Refusals name the endpoint KEY and the shape of the problem ("did not
answer with JSON"), never a fragment of the answer and never the URL — the URL is the operator's
configuration, not the author's business. The audit event records that a lookup ran, against which
key, and which memory keys it wrote; the values are the one thing it must not carry.

**6. A lookup needs a case**, because case memory is where its answer goes — enforced in the
executor, where the case is resolved.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from typing import Any

import httpx

from app.config import settings
from app.flows import memory

#: The contract's ``safety.lookupTimeoutMs``, in seconds. The OVERALL deadline for one lookup —
#: connect, send, and read the whole body — pinned against the JSON by the tests.
TIMEOUT_S = 10.0

#: The connect half of it. Separate and short: a host that will not accept a connection in four
#: seconds is down, and spending the whole budget discovering that leaves none for a slow-but-alive
#: endpoint that would have answered.
CONNECT_TIMEOUT_S = 4.0

#: How much answer we will read. A lookup's answer is a handful of fields; anything at this scale
#: is a misconfigured endpoint (a report, an export, a whole book) and parsing it would put an
#: unbounded allocation in the worker on a third party's say-so. Measured on the DECODED stream,
#: because a compressed bomb is small on the wire and large in memory.
MAX_RESPONSE_BYTES = 256 * 1024

#: How many fields may be sent, and how many remembered. The same number and the same reasoning as
#: ``effects.MAX_PAYLOAD_KEYS`` (pinned equal by a test): a bound, not a policy.
MAX_MAPPING_KEYS = 30

#: How deep a response path may address. ``data.applicant.risk.band`` is four; five is already past
#: anything a no-code author means, and the cap is what keeps extraction a total function over
#: caller-supplied JSON rather than a walk of arbitrary depth.
MAX_PATH_SEGMENTS = 5

#: One segment of a response path, or one field name in the request body. Deliberately permissive
#: about case and hyphens (the far end names its own fields) and deliberately closed to dots,
#: brackets and stars — those are the syntax of the JSONPath we are not implementing, and silently
#: treating one as a literal key would leave an author sure they had written a filter.
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: What we will parse. ``application/json`` and the ``+json`` structured-suffix family; anything
#: else (an HTML error page from a proxy, a CSV, a PDF) is a refusal, because a mapping applied to
#: it can only produce silent nulls that a branch then treats as facts.
_JSON_CONTENT_TYPES = ("application/json", "+json", "text/json")


class LookupRefused(Exception):
    """A lookup that cannot be performed as authored, or an answer we will not accept.

    Carries a reason fit to show a firm's author. The executor turns it into ``ActionRefused``, so
    it lands on the step as a recorded refusal with the reason attached — retryable, not latched.
    """


# ---------------------------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------------------------


def endpoint(key: str) -> str | None:
    """The configured URL for a lookup endpoint key, or None when this deployment has no such
    endpoint. Read from ``settings`` at every call — never cached and never carried in a stored
    definition — so a withdrawn endpoint stops being called the moment it is withdrawn."""
    return settings.automation_lookup_endpoints.get(key)


def known_keys() -> str:
    """The configured keys, for a refusal message that tells an author what they may name."""
    return ", ".join(sorted(settings.automation_lookup_endpoints)) or "none"


# ---------------------------------------------------------------------------------------------
# The mappings (pure)
# ---------------------------------------------------------------------------------------------


def _mapping(config: Mapping[str, Any], name: str) -> dict[str, str]:
    """The shared shape check: an object of ``{destination: source}``, both text, bounded.

    A JSON OBJECT STRING is accepted as well as an object, for ``action_config``'s reason: the
    builder's config fields are text areas, and whether a given inspector parses before it saves is
    a decision being made in another file at the same time as this one. Tolerating both costs four
    lines and removes "the flow published fine and never remembered anything" from the seam.
    """
    raw = config.get(name)
    if raw is None:
        return {}
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise LookupRefused(
                f"'{name}' is not a readable set of fields — write it as "
                '{"field": "where it comes from"}'
            ) from exc
    if not isinstance(raw, Mapping):
        raise LookupRefused(f"'{name}' must be a set of named fields")
    if len(raw) > MAX_MAPPING_KEYS:
        raise LookupRefused(f"a lookup's '{name}' may carry at most {MAX_MAPPING_KEYS} fields")
    out: dict[str, str] = {}
    for destination, source in raw.items():
        if not isinstance(destination, str) or not isinstance(source, str) or not source.strip():
            raise LookupRefused(f"every entry in '{name}' must name a field")
        out[destination.strip()] = source.strip()
    return out


def parse_request_mapping(config: Mapping[str, Any]) -> dict[str, str]:
    """``{field_to_send: context_field}`` — what this lookup ASKS with.

    The value is a path into the same fact context the entry conditions were evaluated against
    (plus the case's memory), resolved by ``conditions.resolve_field`` so one grammar reads
    ``contact.email`` and ``memory.policy_number`` alike. May be empty: "what is today's rate" is a
    lookup with no inputs, and demanding one would only invite a meaningless field.
    """
    mapping = _mapping(config, "request_mapping")
    for field in mapping:
        if not _SEGMENT_RE.match(field):
            raise LookupRefused(
                f"'{field}' is not a usable request field name — letters, numbers, hyphens and "
                "underscores only"
            )
    return mapping


def parse_response_mapping(config: Mapping[str, Any]) -> dict[str, str]:
    """``{memory_key: response_path}`` — what this lookup REMEMBERS.

    Same direction as ``fill_form.config.remember``, on purpose: an author who has written one has
    written both. Required and non-empty — a lookup that remembers nothing has called a third party
    for no reason, and the journey it sits in cannot be branching on anything it fetched.
    """
    mapping = _mapping(config, "response_mapping")
    if not mapping:
        raise LookupRefused(
            "a lookup names no 'response_mapping', so nothing it fetched would be remembered — "
            "map at least one field of the answer to a memory name"
        )
    for key, path in mapping.items():
        if not memory.valid_key(key):
            raise LookupRefused(
                f"'{key}' is not a usable memory name — use lower-case letters, numbers and "
                f"underscores (up to {memory.MAX_KEY_LENGTH} characters)"
            )
        segments = path.split(".")
        if len(segments) > MAX_PATH_SEGMENTS:
            raise LookupRefused(
                f"'{path}' looks {len(segments)} levels deep; a lookup reads at most "
                f"{MAX_PATH_SEGMENTS}"
            )
        if not all(_SEGMENT_RE.match(segment) for segment in segments):
            raise LookupRefused(
                f"'{path}' is not a plain path into the answer — use names and list positions "
                "separated by dots (results.0.score), not filters or wildcards"
            )
    return mapping


# ---------------------------------------------------------------------------------------------
# Reading the answer (pure)
# ---------------------------------------------------------------------------------------------

#: "This path addressed nothing." Distinct from a JSON ``null``, which is an answer.
MISSING = object()


def extract(payload: Any, path: str) -> Any:
    """The value at a dotted path, or ``MISSING``. Total — never raises, never guesses.

    A numeric segment indexes a list (``results.0.score``) and names a key in an object; that
    ambiguity is resolved by what is actually there, which is the only reading that can be right
    for both an object keyed ``"0"`` and a two-element array.
    """
    node: Any = payload
    for segment in path.split("."):
        if isinstance(node, Mapping) and segment in node:
            node = node[segment]
            continue
        if isinstance(node, list) and segment.isdigit():
            index = int(segment)
            if index >= len(node):
                return MISSING
            node = node[index]
            continue
        return MISSING
    return node


def select(payload: Any, mapping: Mapping[str, str]) -> dict[str, Any]:
    """Apply a response mapping. Absent paths are SKIPPED, not written as null.

    ``memory.promotions``' rule, for its reason: "the endpoint had no view on this" and "the
    endpoint said no" are different facts, and only the author knows which their branch means. A
    lookup that fills a key with null on a partial answer would have a branch quietly take the
    ``isEmpty`` arm on a day the far end simply omitted a field.
    """
    out: dict[str, Any] = {}
    for key, path in mapping.items():
        value = extract(payload, path)
        if value is not MISSING:
            out[key] = value
    return out


# ---------------------------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------------------------


def _build_client(
    *, timeout: float = TIMEOUT_S, connect: float = CONNECT_TIMEOUT_S
) -> httpx.AsyncClient:
    """The house policy for an outbound HTTP client to an ALLOWLISTED endpoint, in one place.

    ``follow_redirects=False`` is httpx's default in 0.28 and is set anyway: it is a control, not a
    default we are content to inherit, and a future default flip must not silently widen the
    allowlist. The timeout is per-operation (httpx has no total-deadline option, which is why
    ``fetch`` also wraps the call in ``asyncio.timeout``).

    Shared with ``dispatcher._handle_webhook`` rather than duplicated (module-private but one
    package: this is the seam, not an export). The two directions differ in what they do with the
    answer — a webhook reads a status code, a lookup believes a body — but not in how they must
    reach an operator-allowlisted URL, and the outbound half is exactly where a control gets set on
    one call site and forgotten on the other. It was: the webhook client inherited
    ``follow_redirects`` from httpx's default while this one argued, in a comment, about why
    inheriting it is not good enough.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=connect),
        follow_redirects=False,
    )


def _is_json(content_type: str) -> bool:
    base = content_type.split(";", 1)[0].strip().lower()
    return any(marker in base for marker in _JSON_CONTENT_TYPES)


async def fetch(key: str, url: str, body: Mapping[str, Any]) -> Any:
    """POST ``body`` to an allowlisted endpoint, returning its parsed JSON — or ``LookupRefused``.

    **POST with a JSON body, not GET with a query string**, and that is a privacy decision rather
    than a REST one: a lookup's request is assembled out of client facts, and a query string is the
    part of a request that lands in every access log, proxy log and error tracker between here and
    the far end. The body is the only place those facts belong.

    Every failure — refused connection, timeout, redirect, non-2xx, wrong content type, oversized
    body, unparseable JSON — comes back as one refusal type naming the endpoint KEY, so the step's
    recorded reason is something an operator can act on and never a fragment of a client's data or
    the operator's own URL.
    """
    try:
        async with asyncio.timeout(TIMEOUT_S):
            async with _build_client() as client:
                async with client.stream(
                    "POST",
                    url,
                    json=dict(body),
                    headers={"Accept": "application/json"},
                ) as response:
                    return _parse(key, await _read(key, response))
    except TimeoutError as exc:
        raise LookupRefused(
            f"the '{key}' endpoint did not answer within {int(TIMEOUT_S)} seconds"
        ) from exc
    except httpx.HTTPError as exc:
        # The exception's own text can carry the URL; the class name carries everything an author
        # needs (ConnectError, ReadError) and nothing an operator would rather not publish.
        raise LookupRefused(
            f"the '{key}' endpoint could not be reached ({type(exc).__name__})"
        ) from exc


async def _read(key: str, response: httpx.Response) -> bytes:
    """The response body, under the cap, or a refusal. Streamed: the cap must bite BEFORE the bytes
    are in memory, which a ``response.content`` would not."""
    if response.is_redirect:
        # Not followed. An allowlist that a 302 can walk out of is not an allowlist, and the
        # operator who allowlisted this key did not allowlist wherever it points today.
        raise LookupRefused(
            f"the '{key}' endpoint redirected, which a lookup will not follow — an operator "
            "should point the endpoint at its final address"
        )
    if response.status_code >= 400:
        raise LookupRefused(f"the '{key}' endpoint answered {response.status_code}")
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise LookupRefused(
            f"the '{key}' endpoint's answer is larger than the {MAX_RESPONSE_BYTES} bytes a "
            "lookup will read"
        )
    content_type = response.headers.get("content-type", "")
    if not _is_json(content_type):
        # Named without the value: a content type is the far end's own configuration, not data.
        raise LookupRefused(
            f"the '{key}' endpoint did not answer with JSON "
            f"({content_type.split(';', 1)[0].strip() or 'no content type'})"
        )
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise LookupRefused(
                f"the '{key}' endpoint's answer is larger than the {MAX_RESPONSE_BYTES} bytes a "
                "lookup will read"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _parse(key: str, raw: bytes) -> Any:
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        # Deliberately not `str(exc)`: a decoder error quotes the input, and the input is the
        # client's data.
        raise LookupRefused(f"the '{key}' endpoint's answer was not valid JSON") from exc
    if not isinstance(payload, Mapping | list):
        raise LookupRefused(
            f"the '{key}' endpoint answered with a single value rather than an object, so there "
            "is nothing for a response mapping to address"
        )
    return payload


# ---------------------------------------------------------------------------------------------
# Authoring
# ---------------------------------------------------------------------------------------------


def preflight_issue(config: Mapping[str, Any]) -> str | None:
    """Why this lookup could never run in this deployment, or ``None``. Static checks only.

    Called from ``effects.preflight``, so an author publishing a flow that names an endpoint
    this deployment does not have is told at the screen rather than at 3am — and told what to ask
    an operator for by name, since only an operator can fix it.
    """
    key = config.get("endpoint_key")
    if not isinstance(key, str) or not key.strip():
        return "the action names no 'endpoint_key'"
    if endpoint(key.strip()) is None:
        return (
            f"'{key.strip()}' is not a lookup endpoint this deployment knows (configured: "
            f"{known_keys()}) — ask an operator to add it to AUTOMATION_LOOKUP_ENDPOINTS, so that "
            "a flow never names a URL"
        )
    try:
        parse_request_mapping(config)
        parse_response_mapping(config)
    except LookupRefused as exc:
        return str(exc)
    return None
