"""Case memory — the handful of facts a running journey carries between its nodes.

A v3 flow is a graph, and a graph needs variables: something for a branch to test, something for
a message to print, somewhere for a lookup's answer to land. That is all this is — a small,
typed, per-case key-value store (schema: ``0046_case_memory``).

**Three ways a value gets in**, and only three:

1. **Injected at start** — ``orchestrator.start_execution(..., memory={...})``, fed by the
   automation that fired the journey or by the adviser's intake. Seeded into every case of the
   execution, because at kickoff there is no smaller scope that is true.
2. **Promoted from a completed form** — by an AUTHORED mapping on the step
   (``fill_form.config.remember``: ``{"risk_band": "attitude_to_risk"}``, or
   ``{"risk_band": "band"}`` when ``band`` is a scoring output rather than an answer). Never by
   dumping the answer set, and never by dumping every output. A fact-find has hundreds of
   fields; memory is for the handful a later branch or message actually needs, and the
   difference between those two designs is whether a firm's client data quietly accumulates
   in a second place.
3. **Written by an action node** as the journey runs (``lookup``'s response mapping, Phase D).

**Why it is scoped to the case and not the execution.** A tiered onboarding fans ONE flow into a
profile case per participant plus a case per account. A branch resolves inside one of them, and
"this applicant is offshore" is a fact about that applicant — hoisting it to the execution would
let one participant's answer decide another's route. Journey-wide values are seeded into every
case at start, which is the same thing said honestly.

**The caps, and why these numbers.** Memory is read on the hot path (every branch evaluation
builds a context from it) and printed into client-facing copy, so it is bounded in three
independent directions: ``MAX_KEYS`` per case, ``MAX_VALUE_BYTES`` per value (also a CHECK on the
table — the Python cap is a promise, the CHECK is a fact), and ``MAX_TOTAL_BYTES`` per case so a
hundred keys just under the per-value cap cannot add up to something nobody meant. Over any of
them is a **refusal**, not a truncation: a silently shortened value that a branch then tests is
worse than a write that says no.

**Values are JSON, and only JSON.** Scalars, lists and dicts of scalars. Not because jsonb could
not hold more, but because everything downstream (a condition operator, an interpolated string, a
webhook payload) treats these as data — and the moment a value can be anything else, "data" stops
being a guarantee.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("flow_memory")

#: The authored name, WITHOUT the ``memory.`` prefix. Mirrors the CHECK on ``flows.case_memory.key``
#: — dotless on purpose, because a dotted key would be ambiguous against the nested lookup both
#: ``conditions.resolve_field`` and ``interpolation._lookup`` perform to reach it.
KEY_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_]*[a-z0-9])?$")
MAX_KEY_LENGTH = 64

#: The three caps. See the module header for why there are three rather than one.
MAX_KEYS = 100
MAX_VALUE_BYTES = 4_096
MAX_TOTAL_BYTES = 64_000

#: How a value got here (mirrors the CHECK on ``flows.case_memory.source``).
SOURCES: frozenset[str] = frozenset({"start", "answers", "action", "engine"})

#: The namespace every reader sees these values under (``memory.risk_band``). One constant so the
#: context builders, the condition registry and the docs cannot drift.
NAMESPACE = "memory"

#: How deep a container value may nest. A value is a fact, not a document; five levels is already
#: past anything a no-code author means, and an unbounded walk over caller-supplied JSON is a
#: stack overflow with a friendly name.
MAX_DEPTH = 5


class MemoryRefused(ValueError):
    """A write case memory will not perform, with a reason fit to show an author.

    Raised rather than swallowed: a branch that tests a value which was silently dropped is a
    journey that goes the wrong way with nothing anywhere saying why.
    """


# ---------------------------------------------------------------------------------------------
# Validation (pure)
# ---------------------------------------------------------------------------------------------


def valid_key(key: Any) -> bool:
    return isinstance(key, str) and len(key) <= MAX_KEY_LENGTH and bool(KEY_RE.match(key))


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    """The value as something ``json.dumps`` will accept, or a refusal.

    Deliberately NOT a coercion pass: ``str(value)`` on an unexpected object would store the
    repr of a SQLAlchemy row and let a condition compare against it. Dates and Decimals are the
    two exceptions, because they arrive from the OLTP/lake layers constantly and their ISO/decimal
    text forms are exactly what the temporal and numeric operators already parse.
    """
    if depth > MAX_DEPTH:
        raise MemoryRefused("this value is nested too deeply to remember")
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        # NaN/±inf serialise as bare `NaN`/`Infinity`, which is not JSON and which every
        # downstream comparison would read as nonsense.
        if not math.isfinite(value):
            raise MemoryRefused("this value is not a finite number")
        return value
    if isinstance(value, Decimal):
        # Money and lake numerics arrive as Decimal. `float` matches what conditions._as_number
        # already does with one, so a remembered value compares the same way an unremembered one
        # would.
        as_float = float(value)
        if not math.isfinite(as_float):
            raise MemoryRefused("this value is not a finite number")
        return as_float
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_json_safe(v, depth=depth + 1) for v in value]
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise MemoryRefused("a remembered object needs text keys")
            out[k] = _json_safe(v, depth=depth + 1)
        return out
    raise MemoryRefused(f"a {type(value).__name__} is not something a journey can remember")


def encode(key: Any, value: Any) -> tuple[str, str]:
    """Validate one (key, value) and return the key plus its JSON text. Raises ``MemoryRefused``."""
    if not valid_key(key):
        raise MemoryRefused(
            f"'{key}' is not a usable memory name — use lower-case letters, numbers and "
            f"underscores (up to {MAX_KEY_LENGTH} characters)"
        )
    encoded = json.dumps(_json_safe(value))
    if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
        raise MemoryRefused(
            f"the value for '{key}' is larger than the {MAX_VALUE_BYTES} bytes a journey will "
            "remember — put the detail in a document and remember the reference"
        )
    return str(key), encoded


# ---------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------


async def read(session: AsyncSession, case_id: int) -> dict[str, Any]:
    """Every value this case carries, by key. ``{}`` for a case that has never written one."""
    rows = (
        (
            await session.execute(
                text("SELECT key, value FROM flows.case_memory WHERE case_id = :c ORDER BY key"),
                {"c": case_id},
            )
        )
        .mappings()
        .all()
    )
    # The value comes back already decoded: the async dialect registers a jsonb codec, so a stored
    # `"high"` arrives as the str `high`. Deliberately NOT `json.loads` on a str — a JSON string and
    # the text form of a JSON document are indistinguishable once decoded, and guessing would turn
    # a client's answer of `[1,2]` into a list.
    return {str(row["key"]): row["value"] for row in rows}


async def context(session: AsyncSession, case_id: int) -> dict[str, Any]:
    """This case's memory shaped for the two resolvers that read it.

    Returns ``{"memory": {...}}`` — NESTED, and one shape rather than two. Both
    ``conditions.resolve_field`` (longest-key-first, then descend) and
    ``interpolation._lookup`` (exact key, then descend) walk into a nested mapping, so
    ``memory.risk_band`` resolves in a branch condition and ``{{ memory.risk_band || "unknown" }}``
    renders in a task title from the SAME object. Emitting flat ``memory.x`` keys as well would be
    two sources of truth for one value, and the day they disagree is the day a branch and the email
    announcing its outcome say different things.
    """
    return {NAMESPACE: await read(session, case_id)}


async def context_for_case_uid(
    session: AsyncSession, *, tenant_id: int, case_uid: str
) -> dict[str, Any]:
    """The same nested ``{"memory": {...}}``, resolved from the case's UID in ONE query.

    The rule tier only ever holds the uid (``AutomationContext.case_uid``); resolving the id first
    and then reading would put two round-trips on the dispatch path of every action, and that path
    already runs per firing. The join carries the ``tenant_id`` predicate rather than trusting the
    uid alone — the caller is the service_role worker, where RLS is not the boundary.

    A uid that names no case returns the empty namespace, not a refusal: a firing whose case has
    since been deleted must still render its copy with fallbacks rather than fault.
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT m.key, m.value FROM flows.case_memory m "
                    "JOIN flows.cases c ON c.id = m.case_id "
                    "WHERE c.uid = :u AND c.tenant_id = :t ORDER BY m.key"
                ),
                {"u": case_uid, "t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return {NAMESPACE: {str(row["key"]): row["value"] for row in rows}}


# ---------------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------------


async def _usage(session: AsyncSession, case_id: int) -> tuple[int, int]:
    """(number of keys, total serialised bytes) for one case — the cap check's input."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT count(*) AS keys, "
                    "       COALESCE(sum(octet_length(value::text)), 0) AS bytes "
                    "FROM flows.case_memory WHERE case_id = :c"
                ),
                {"c": case_id},
            )
        )
        .mappings()
        .one()
    )
    return int(row["keys"]), int(row["bytes"])


async def write(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_id: int,
    key: str,
    value: Any,
    source: str = "engine",
    node_id: str | None = None,
) -> None:
    """Set one value. Upsert by ``(case_id, key)`` — memory is a variable, not a log.

    Raises ``MemoryRefused`` for a bad name, an unstorable value, or a case that has reached one of
    the caps. The caps are checked against what is ALREADY stored, so overwriting an existing key
    never trips the key-count cap (the common case for a value a loop-ish journey keeps refreshing).
    """
    stored_key, encoded = encode(key, value)
    if source not in SOURCES:
        raise MemoryRefused(f"'{source}' is not a memory source")
    keys, total = await _usage(session, case_id)
    existing = (
        await session.execute(
            text(
                "SELECT octet_length(value::text) FROM flows.case_memory "
                "WHERE case_id = :c AND key = :k"
            ),
            {"c": case_id, "k": stored_key},
        )
    ).scalar_one_or_none()
    if existing is None and keys >= MAX_KEYS:
        raise MemoryRefused(
            f"this journey already remembers {MAX_KEYS} values, which is as many as it may. "
            "Remove one before adding another."
        )
    projected = total - int(existing or 0) + len(encoded.encode("utf-8"))
    if projected > MAX_TOTAL_BYTES:
        raise MemoryRefused(
            f"this journey's memory would exceed {MAX_TOTAL_BYTES} bytes. Remember references, "
            "not documents."
        )
    await session.execute(
        text(
            "INSERT INTO flows.case_memory (tenant_id, case_id, key, value, source, node_id) "
            "VALUES (:t, :c, :k, CAST(:v AS jsonb), :s, :n) "
            "ON CONFLICT (case_id, key) DO UPDATE SET "
            "  value = EXCLUDED.value, source = EXCLUDED.source, node_id = EXCLUDED.node_id"
        ),
        {
            "t": tenant_id,
            "c": case_id,
            "k": stored_key,
            "v": encoded,
            "s": source,
            "n": node_id,
        },
    )


async def write_many(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_id: int,
    values: Mapping[str, Any],
    source: str = "engine",
    node_id: str | None = None,
    strict: bool = False,
) -> list[str]:
    """Set several values. Returns the keys actually written, in authored order.

    ``strict=False`` (the default) is the RUNTIME posture: one unstorable value must not fail a
    step transition or abandon the other values with it, so the refusal is recorded against that
    key and the rest are written. ``strict=True`` is for a caller that can still tell a human —
    an API start, where "this variable is too big" belongs in the response, not a log line.
    """
    written: list[str] = []
    for key, value in values.items():
        try:
            await write(
                session,
                tenant_id=tenant_id,
                case_id=case_id,
                key=key,
                value=value,
                source=source,
                node_id=node_id,
            )
        except MemoryRefused:
            if strict:
                raise
            _log_refusal(case_id, key)
            continue
        written.append(str(key))
    return written


def _log_refusal(case_id: int, key: Any) -> None:
    logger.warning(
        "a journey could not remember a value; the rest were kept",
        extra={"event": "flow_memory_refused", "case_id": case_id, "memory_key": str(key)[:64]},
    )


def promotions(mapping: Any, answers: Any, outputs: Any = None) -> dict[str, Any]:
    """The values an authored ``remember`` mapping promotes — pure.

    The mapping is ``{memory_key: source_key}``: authored, small, and reviewed at publish. The
    source key is read from ``outputs`` first and from ``answers`` after that. Outputs win so a
    posted ``band`` cannot be remembered in place of the band the server computed
    (``risk_band`` ← ``band``). The inverse — dumping every answer, or every output, into
    memory — is the design this exists instead of. A fact-find carries hundreds of fields, most
    of them the client's most sensitive, and copying them into a second store that a branch and
    an email both read is a data-protection decision nobody made. Scoring writes four keys;
    only the ones this mapping names are promoted.

    A source key that is absent from both is simply not promoted, so a partially-answered form
    leaves the memory key unset rather than setting it to nothing: ``isEmpty`` and "was never
    asked" are different questions and only the author knows which they meant. A key that is
    present and null (a score that matched no band) is promoted: that null is the result.
    """
    if not isinstance(mapping, Mapping):
        return {}
    answer_map = answers if isinstance(answers, Mapping) else {}
    output_map = outputs if isinstance(outputs, Mapping) else {}
    if not answer_map and not output_map:
        return {}
    out: dict[str, Any] = {}
    for memory_key, source_key in mapping.items():
        if not isinstance(source_key, str):
            continue
        if source_key in output_map:
            out[str(memory_key)] = output_map[source_key]
        elif source_key in answer_map:
            out[str(memory_key)] = answer_map[source_key]
    return out


async def promote_from_answers(
    session: AsyncSession,
    *,
    tenant_id: int,
    case_id: int,
    mapping: Any,
    answers: Any,
    outputs: Any = None,
    node_id: str | None = None,
) -> list[str]:
    """Apply a step's authored ``remember`` mapping to its answers and scoring outputs.

    Never raises. ``outputs`` is the server-computed ``{band, score, label, definition}``; a
    mapping entry that names one of those keys stores the computed value, not a posted one.
    """
    values = promotions(mapping, answers, outputs)
    if not values:
        return []
    return await write_many(
        session,
        tenant_id=tenant_id,
        case_id=case_id,
        values=values,
        source="answers",
        node_id=node_id,
    )


async def copy_case(
    session: AsyncSession, *, tenant_id: int, from_case_id: int, to_case_id: int
) -> int:
    """Carry one case's memory into another — ``start_flow``'s ``carry_memory``. Returns the count.

    A copy, not a link: the child journey is a separate case that may go on to overwrite what it
    was handed, and a shared store would let it rewrite the parent's history. Existing keys on the
    destination win, because whatever seeded the child deliberately is more specific than whatever
    it inherited.
    """
    # RETURNING + a count rather than `rowcount`: on an async driver the attribute is not part of
    # the typed Result surface, and counting the returned ids is exact under any driver.
    copied = (
        await session.execute(
            text(
                "INSERT INTO flows.case_memory (tenant_id, case_id, key, value, source, node_id) "
                "SELECT :t, :to, key, value, source, node_id FROM ("
                "  SELECT key, value, source, node_id FROM flows.case_memory "
                "  WHERE case_id = :from ORDER BY key LIMIT :cap"
                ") src "
                "ON CONFLICT (case_id, key) DO NOTHING "
                "RETURNING id"
            ),
            {"t": tenant_id, "to": to_case_id, "from": from_case_id, "cap": MAX_KEYS},
        )
    ).all()
    return len(copied)
