"""Conditions — the ONE condition evaluator the flow engine uses everywhere (pure, unit-tested).

v3 asks the same authored grammar three questions in three different places:

1. **Trigger entry conditions** — should this event start/advance this flow at all?
2. **Runtime branch nodes** — which outgoing branch does this case take now?
3. **Plan-time step visibility** — does this step apply to this participant/account? (the original
   call site, ``app/flows/visibility.py``, which is now a thin delegation to this module).

They were never three different questions, so they are not three different implementations: one
vocabulary, one evaluator, one set of semantics an author has to learn once. The shared grammar is
pinned cross-language by ``pkgs/ui/.../book/journeys/flow-contract.json`` (its ``conditions`` block)
— the API container ships WITHOUT pkgs/ui, so the operator set and the field registry are mirrored
here as Python literals and a pin test in ``tests/test_flows_conditions.py`` fails the moment the
two drift. Change the contract and this module together, never one.

Grammar
-------
A **condition** is either a leaf ``{field, operator, value}`` or a **group**
``{logic: 'and'|'or', conditions: [<condition>, ...]}``; groups nest arbitrarily (up to
``MAX_GROUP_DEPTH``). Absent/empty ⇒ True.

Two total-function rules that look inconsistent but are not — the asymmetry is deliberate:

- **An unknown/unparseable OPERATOR (or structurally broken condition) evaluates True.** This is
  the legacy "don't hide" rule from ``visibility.py``: a step whose condition the engine cannot
  understand must still be shown to the client rather than silently vanish from their journey, and
  a branch must still be takeable rather than stalling the case. Malformed authoring is caught by
  ``condition_issues`` at publish, which is the gate that stops it reaching runtime at all.
- **An unknown/missing FIELD resolves to ``None`` and is then tested normally** — so ``isEmpty``
  matches it, ``equals 'x'`` does not. A mistyped field must NOT quietly satisfy every test (that
  would fire automations at everybody); it must behave like "we have no value for this", which is
  the truth. Never an exception either way.

``evaluate`` NEVER raises. A condition that 500s the worker loop stalls every firm's automations,
so every operator is written to be type-safe against untrusted author input and the whole thing
sits behind a last-resort guard.

Operator semantics, in one place
--------------------------------
- ``equals``/``notEquals``/``in``/``notIn``/``contains``/``notContains``/``isEmpty``/``isNotEmpty``
  — v1/v2, frozen. ``in`` needs a list value (a non-list can never match). ``contains`` is type-safe
  (a string contains only a string). "Empty" is ``None``/``''``/``[]``/``{}`` compared by ``==``,
  so ``0`` and ``False`` are NOT empty.
- ``matches``/``notMatches`` — wildcard text. Both sides are normalised first: casefolded, and every
  run of non-word characters becomes one space, so "E-Mail", "e mail" and "e_mail!" are one thing.
  ``*`` then matches exactly ONE word, ``**`` matches any text (including none, and mid-word), and
  the match is whole-string — a pattern with no wildcard is normalised equality. A list actual
  matches if any string member does (tags). Anything else matches nothing.
- ``greaterThan``/``lessThan`` — numeric. int/float/Decimal/numeric string; booleans, NaN, ±inf and
  junk are not numbers, and a non-number on either side is False. Not complements: equality is
  False for both.
- ``before``/``after`` — temporal, against an ISO date/datetime or a ``{field: …}`` reference.
- ``withinDays``/``olderThanDays`` — temporal, against a number of days measured back from
  ``time.now`` in the context (else wall-clock UTC). Exact complements whenever both sides resolve;
  a future timestamp is within every window and older than none.
- All four temporal operators read a **naive timestamp as UTC** (see ``_as_datetime``) — a
  naive/aware comparison raises ``TypeError`` in Python, and this is the engine's hot loop.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------
# The contract mirror (pinned against flow-contract.json → conditions / caps)
# ---------------------------------------------------------------------------------------------

#: Every operator the builder may author. The first eight are v1/v2 and their semantics are frozen
#: (definitions in the wild use them); the last eight are v3's additions.
OPERATORS: frozenset[str] = frozenset(
    {
        "equals",
        "notEquals",
        "in",
        "notIn",
        "contains",
        "notContains",
        "isEmpty",
        "isNotEmpty",
        "matches",
        "notMatches",
        "greaterThan",
        "lessThan",
        "before",
        "after",
        "withinDays",
        "olderThanDays",
    }
)

#: The field registry the builder renders as a picker, by scope. Entries ending in ``*`` are OPEN
#: namespaces (authored keys — case memory and an event's own subject payload); everything else is
#: closed, so a typo in it is a publish issue.
FIELD_SCOPES: dict[str, tuple[str, ...]] = {
    "contact": (
        "contact.type",
        "contact.service_type",
        "contact.sub_type",
        "contact.enabled",
        "contact.tags",
        "contact.nationality",
        "contact.has_login",
    ),
    "application": (
        "application.custody",
        "application.status",
        "application.has_pension_product",
        "application.has_sipp_product",
        "application.has_transfer",
    ),
    "account": (
        "account.product",
        "account.custody",
        "account.status",
        "account.has_transfer",
        "account.value",
        "account.discretionary",
    ),
    "case": (
        "case.status",
        "case.flow_code",
        "case.tags",
        "case.started_at",
    ),
    "memory": ("memory.*",),
    "event": (
        "event.type",
        "event.actor_id",
        "event.subject.*",
    ),
    "time": (
        "time.hour",
        "time.weekday",
        "time.since_step_completed",
    ),
}

#: caps.maxConditionsPerGroup — a group with more than this is refused at publish (pinned).
MAX_CONDITIONS_PER_GROUP = 20

#: How deep groups may nest. The contract caps group WIDTH but not depth; this is ours, because
#: "and(or(and(or(and(…)))))" is unreadable in any builder long before it is expensive to run, and
#: an unbounded recursive walk over author-supplied JSON is a stack overflow waiting to happen.
MAX_GROUP_DEPTH = 5

#: Operator spellings that are not in the contract but exist in stored v1/v2 definitions. They are
#: accepted verbatim (and never emitted).
_ALIASES: dict[str, str] = {
    "==": "equals",
    "eq": "equals",
    "!=": "notEquals",
    "ne": "notEquals",
}

#: Pre-v3 authored field paths. The planner still builds ``applicant.*`` contexts (participant type
#: and roles, see planning.py), and pre-v3 contexts were flat un-dotted keys (``service_type``), so
#: neither may be reported as an unknown field. Everything with a dotted scope prefix outside this
#: list and the registry is a typo.
_LEGACY_PREFIXES: tuple[str, ...] = ("applicant.",)

_KNOWN_FIELDS: frozenset[str] = frozenset(
    f for fields in FIELD_SCOPES.values() for f in fields if not f.endswith("*")
)
_OPEN_PREFIXES: tuple[str, ...] = (
    tuple(f[:-1] for fields in FIELD_SCOPES.values() for f in fields if f.endswith("*"))
    + _LEGACY_PREFIXES
)

#: The most segments a field path may carry (``event.subject.something`` is three).
_MAX_FIELD_SEGMENTS = 12

# The legacy emptiness set, unchanged: membership is tested with `==`, so 0/False are NOT empty.
_EMPTY: tuple[object, ...] = (None, "", [], {})

_NEEDS_LIST = ("in", "notIn")
_NEEDS_NUMBER = ("greaterThan", "lessThan")
_NEEDS_DAYS = ("withinDays", "olderThanDays")
_NEEDS_DATE = ("before", "after")
_NEEDS_TEXT = ("matches", "notMatches")
_TEMPORAL = _NEEDS_DATE + _NEEDS_DAYS


# ---------------------------------------------------------------------------------------------
# Field resolution
# ---------------------------------------------------------------------------------------------


def resolve_field(field: Any, ctx: Mapping[str, Any]) -> Any:
    """Resolve a dotted path against the context. ``None`` for anything we cannot resolve — never
    an exception.

    The **longest key wins at every level**, because the planner's contexts are flat dicts with
    literal dotted keys (``{"application.custody": "offshore"}``, planning.py) while the
    trigger/branch contexts are nested (``{"event": {"subject": {"contact_id": 7}}}``). Trying the
    whole remaining path as a key before descending means one grammar reads both shapes — and a
    mixed one (``{"event.subject": {...}}``) too. Iterative and segment-capped, so a pathological
    ``a.a.a.…`` path can neither blow the stack nor turn into a quadratic walk.
    """
    if not isinstance(field, str) or not field:
        return None
    parts = field.split(".")
    if len(parts) > _MAX_FIELD_SEGMENTS:
        return None  # no real field is this deep; refuse rather than grind
    node: Any = ctx
    while parts:
        if not isinstance(node, Mapping):
            return None
        for cut in range(len(parts), 0, -1):
            head = ".".join(parts[:cut])
            if head in node:
                node, parts = node[head], parts[cut:]
                break
        else:
            return None
    return node


def _deref(value: Any, ctx: Mapping[str, Any]) -> Any:
    """Resolve a ``{field: …}`` value reference ("is this account worth more than the last one we
    saw") against the context. Only the v3 comparison operators dereference: the eight legacy
    operators must keep treating a dict value as a literal to compare, or a stored definition would
    change meaning under us."""
    if isinstance(value, Mapping) and "field" in value:
        return resolve_field(value.get("field"), ctx)
    return value


# ---------------------------------------------------------------------------------------------
# Value coercion (total — every one of these returns None rather than raising)
# ---------------------------------------------------------------------------------------------


def _as_number(value: Any) -> float | None:
    """A finite float, or None. Booleans are deliberately NOT numbers (``True > 0`` is a nonsense
    an author never means), and NaN/±inf are rejected so comparisons stay meaningful."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, Decimal):  # lake/OLTP money arrives as Decimal
        try:
            f = float(value)
        except (ValueError, OverflowError, InvalidOperation):
            return None
        return f if math.isfinite(f) else None
    if isinstance(value, str):
        try:
            f = float(value.strip())
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def _as_datetime(value: Any) -> datetime | None:
    """An ISO date/datetime (or a real date/datetime) as a UTC-aware datetime, else None.

    **Naive means UTC.** Everything we store is ``timestamptz`` serialised as UTC, and a comparison
    between a naive and an aware datetime raises ``TypeError`` in Python — so rather than let an
    author's ``"2026-07-01"`` (naive by nature) take down the worker, every parsed value is stamped
    UTC. A date with no time is midnight UTC.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.endswith(("Z", "z")):  # fromisoformat handles this on 3.11+, be explicit anyway
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _now(ctx: Mapping[str, Any]) -> datetime:
    """Now, in UTC — or the ``time.now`` the context pins. Triggers evaluate against the event's own
    timestamp rather than wall-clock (a replayed/deferred event must decide the same way it would
    have when it happened), and tests get determinism for free."""
    pinned = _as_datetime(resolve_field("time.now", ctx))
    return pinned if pinned is not None else datetime.now(UTC)


# ---------------------------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------------------------


def _contains(actual: Any, value: Any) -> bool:
    """Type-safe ``value in actual``. A string actual only contains a **string** value (``5 in
    "advised"`` would otherwise raise ``TypeError`` and 500 instantiate); a list contains any value;
    anything else contains nothing. Author-supplied ``value`` is untrusted, so never let a type
    mismatch escape as an exception."""
    if isinstance(actual, str):
        return isinstance(value, str) and value in actual
    if isinstance(actual, list):
        return value in actual
    return False


# Wildcard matching (Crisp's grammar). Everything that is not a word character or a wildcard is a
# separator, so "e-mail", "e mail" and "E—Mail!" are the same text to an author.
_SEPARATORS = re.compile(r"[^\w*]+")
_MAX_PATTERN_CHARS = 500
_MAX_WILDCARDS = 12
_MAX_SUBJECT_CHARS = 4096


def _normalise_text(text: str) -> str:
    """Case- and punctuation-insensitive normal form: punctuation runs collapse to a single space
    and the rest is casefolded. Wildcards survive — the only punctuation that means anything."""
    return _SEPARATORS.sub(" ", text).casefold().strip()


# ---- the wildcard engine: an NFA, NOT a regex -------------------------------------------------
#
# ``matches`` used to compile to a regex — ``*`` to ``[^ ]+``, ``**`` to ``.*`` — and that was a
# denial of service an author could type. A backtracking engine explores every way of splitting the
# subject between adjacent variable-length groups, so ``*a*a*a*a*a*a*a*a*a*a*a*b`` (twelve stars,
# exactly ON the cap, so it published clean) against a run of ``a``s cost ~4.6x per four subject
# characters: 0.008s at 30, 5.8s at 46, and the subject cap allows 4096. That is synchronous CPU
# inside the worker's asyncio loop, so it does not stall one firm's automations — it stalls timers,
# the outbox, the notifier and AML sync for EVERY tenant, until the stall watchdog restarts the
# container onto the same unconsumed event, for ever.
#
# The caps were the guard and they were the wrong guard: they bound the pattern, and the cost is
# exponential in the SUBJECT. (The old docstring's reasoning aimed at ``.*`` — the ``**`` form —
# which CPython actually optimises; the lethal construct was the single star.)
#
# So the grammar is matched by the algorithm it actually describes. There is no regex feature in
# use here beyond "any run of characters", which is a plain NFA, and an NFA simulated by carrying
# the whole set of live states forward one character at a time cannot backtrack: the work is one
# pass over the subject, full stop. The state set is a bitmask in a Python int, so a step is a
# handful of big-int operations rather than a Python loop over states.
#
# The caps stay — they are pinned by ``condition_issues``'s message and by the tests, and "500
# characters of pattern" is a sane authoring bound — but they are no longer load-bearing for
# safety. Anything added to this grammar later (a ``?``, a character class) extends the token set
# and inherits the same guarantee for free, which a regex would not have given.

#: One position in the compiled NFA. ``*`` compiles to ONE_WORD followed by WORD_RUN ("one non-space
#: and then any number more"), ``**`` to ANY_RUN, and every other character to a literal.
_LITERAL, _ONE_WORD, _WORD_RUN, _ANY_RUN = 0, 1, 2, 3


class _Wildcard:
    """A compiled wildcard pattern, as bitmasks over NFA positions.

    Bit ``j`` of a state set means "positions 0..j-1 have been matched, position ``j`` is next";
    bit ``size`` therefore means "the whole pattern is matched". ``literals`` maps a character to
    the positions that accept it; the other three masks are the positions of each wildcard token.
    """

    __slots__ = ("any_run", "literals", "one_word", "run", "size", "start")

    def __init__(self, tokens: list[tuple[int, str]]) -> None:
        self.size = len(tokens)
        literals: dict[str, int] = {}
        one_word = any_run = word_run = 0
        for j, (kind, char) in enumerate(tokens):
            bit = 1 << j
            if kind == _LITERAL:
                literals[char] = literals.get(char, 0) | bit
            elif kind == _ONE_WORD:
                one_word |= bit
            elif kind == _WORD_RUN:
                word_run |= bit
            else:
                any_run |= bit
        self.literals = literals
        self.one_word = one_word
        self.any_run = any_run
        #: The tokens that may consume ZERO characters, and so can be stepped over for free.
        self.run = word_run | any_run
        self.start = self._closure(1)

    def _closure(self, states: int) -> int:
        """Add every position reachable without consuming a character. A ``*_RUN`` token matches
        the empty string, so being before one is also being after it. Bounded: each pass moves at
        least one bit left, and the mask is ``size`` bits wide."""
        while True:
            grown = states | ((states & self.run) << 1)
            if grown == states:
                return states
            states = grown

    def match(self, subject: str) -> bool:
        """Whole-string match, in one pass over ``subject``."""
        states = self.start
        done = 1 << self.size
        for char in subject:
            if char == " ":
                # A space ends a word: only a literal space and the unrestricted ``**`` accept it.
                advanced = (states & self.literals.get(" ", 0)) << 1
                stayed = states & self.any_run
            else:
                advanced = (states & (self.literals.get(char, 0) | self.one_word)) << 1
                stayed = states & self.run
            states = advanced | stayed
            if not states:
                return False  # no live state — nothing later in the subject can revive one
            states = self._closure(states)
        return bool(states & done)


@lru_cache(maxsize=512)
def _wildcard_nfa(pattern: str) -> _Wildcard | None:
    """Compile a normalised wildcard pattern. ``**`` is any text (including none), ``*`` is exactly
    one word. Whole-string match, so a pattern with no wildcard is normalised equality.

    Returns None — meaning "no match, ever" — for a pattern too long or too wildcard-heavy to be
    author intent. These are authoring bounds, not the safety guard (see the note above): matching
    is linear in the subject whatever they are set to, and ``condition_issues`` reports the same
    limit at publish so the refusal is explained rather than mysterious.
    """
    if len(pattern) > _MAX_PATTERN_CHARS:
        return None
    pattern = re.sub(r"\*{3,}", "**", pattern)  # ***… is a typo for **
    if pattern.count("*") > _MAX_WILDCARDS:
        return None
    tokens: list[tuple[int, str]] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            tokens.append((_ANY_RUN, ""))
            i += 2
        elif pattern[i] == "*":
            # One word — after normalisation, space is the only separator.
            tokens.append((_ONE_WORD, ""))
            tokens.append((_WORD_RUN, ""))
            i += 1
        else:
            tokens.append((_LITERAL, pattern[i]))
            i += 1
    return _Wildcard(tokens)


def _pattern_for(pattern: str) -> _Wildcard | None:
    """Normalise + compile, keeping oversized author input OUT of the compile cache (the cache is
    keyed by the pattern, and a megabyte of pasted text would sit in it for the process's life)."""
    normalised = _normalise_text(pattern)
    if len(normalised) > _MAX_PATTERN_CHARS:
        return None
    return _wildcard_nfa(normalised)


def _match_text(actual: str, pattern: str) -> bool:
    subject = _normalise_text(actual)
    if len(subject) > _MAX_SUBJECT_CHARS:
        return False  # matching a novel is not what this operator is for
    nfa = _pattern_for(pattern)
    return nfa is not None and nfa.match(subject)


def _matches(actual: Any, value: Any) -> bool:
    """``matches``: wildcard text. A list actual matches if ANY of its string members does (tags),
    which keeps ``contact.tags matches 'vip **'`` meaning what an author expects. A non-string
    pattern, or an actual that is neither string nor list, matches nothing."""
    if not isinstance(value, str):
        return False
    if isinstance(actual, list):
        return any(isinstance(item, str) and _match_text(item, value) for item in actual)
    return isinstance(actual, str) and _match_text(actual, value)


def _numeric(actual: Any, value: Any, ctx: Mapping[str, Any], *, greater: bool) -> bool:
    """``greaterThan``/``lessThan``. Either side non-numeric ⇒ False (never a raise), so these two
    are NOT complements of each other: at equality, and against junk, both are False."""
    a = _as_number(actual)
    b = _as_number(_deref(value, ctx))
    if a is None or b is None:
        return False
    return a > b if greater else a < b


def _temporal(actual: Any, value: Any, ctx: Mapping[str, Any], op: str) -> bool:
    """``before``/``after`` (against an ISO date or a ``{field: …}`` reference) and
    ``withinDays``/``olderThanDays`` (against a number of days, measured from ``_now``).

    ``withinDays N`` is "at or after the cutoff" and ``olderThanDays N`` is "before the cutoff", so
    for a resolvable timestamp they are exact complements — including for a FUTURE timestamp, which
    is within any window and older than none. Junk on either side ⇒ both False.
    """
    a = _as_datetime(actual)
    if a is None:
        return False
    if op in _NEEDS_DATE:
        b = _as_datetime(_deref(value, ctx))
        if b is None:
            return False
        return a < b if op == "before" else a > b
    days = _as_number(_deref(value, ctx))
    if days is None or days < 0:
        return False
    try:
        cutoff = _now(ctx) - timedelta(days=days)
    except (OverflowError, ValueError):
        return False  # "within 10^9 days" — an author typo, not a window
    return a >= cutoff if op == "withinDays" else a < cutoff


def _eval_leaf(cond: Mapping[str, Any], ctx: Mapping[str, Any]) -> bool:
    field = cond.get("field")
    raw_op = str(cond.get("operator") or cond.get("op") or "equals")
    op = _ALIASES.get(raw_op, raw_op)
    value = cond.get("value")
    actual = resolve_field(field, ctx)
    match op:
        case "equals":
            # bool(): a context value may carry an exotic __eq__ (Decimal, a pandas scalar from the
            # lake) whose result is not a plain bool, and every call site expects one.
            return bool(actual == value)
        case "notEquals":
            return bool(actual != value)
        case "in":
            return isinstance(value, list) and actual in value
        case "notIn":
            return not (isinstance(value, list) and actual in value)
        case "contains":
            return _contains(actual, value)
        case "notContains":
            return not _contains(actual, value)
        case "isEmpty":
            return actual in _EMPTY
        case "isNotEmpty":
            return actual not in _EMPTY
        case "matches":
            return _matches(actual, value)
        case "notMatches":
            return not _matches(actual, value)
        case "greaterThan":
            return _numeric(actual, value, ctx, greater=True)
        case "lessThan":
            return _numeric(actual, value, ctx, greater=False)
        case "before" | "after" | "withinDays" | "olderThanDays":
            return _temporal(actual, value, ctx, op)
        case _:
            return True  # unknown operator ⇒ don't hide (see the module docstring)


# ---------------------------------------------------------------------------------------------
# Groups + the public evaluator
# ---------------------------------------------------------------------------------------------


def _is_group(node: Mapping[str, Any]) -> bool:
    conditions = node.get("conditions")
    return isinstance(conditions, list) and bool(conditions)


def _eval_node(node: Any, ctx: Mapping[str, Any], depth: int) -> bool:
    if not isinstance(node, Mapping) or not node:
        return True
    if _is_group(node):
        if depth >= MAX_GROUP_DEPTH:
            return True  # refused at publish by condition_issues; fail open rather than recurse
        logic = str(node.get("logic") or "and").lower()
        conjunction = logic == "and"  # anything that isn't 'and' is 'or' (legacy)
        seen = False
        for member in node.get("conditions", []):
            if not isinstance(member, Mapping) or not member:
                # Not a condition at all — skip it, contributing NOTHING to either logic.
                #
                # A non-object member has always been skipped (legacy). An EMPTY object now is
                # too, and that is a fix rather than a tidy-up: it used to fall through to the leaf
                # evaluator as `actual=None equals value=None`, which is True — so one `{}` in an
                # `or` group made the group unconditionally true (a branch arm that swallows every
                # case, an entry condition that fires for everybody), and it published clean.
                #
                # "Skip" rather than "False" because the identity element differs by logic: an
                # absent condition must not change an `and` group's verdict either. Note this is
                # the truly-empty test, not "has no field" — `{operator: isEmpty}` is a real
                # condition and still evaluates against a null actual (see the bare-leaf note
                # below). `condition_issues` reports an empty member so it never reaches here from
                # anything published.
                continue
            seen = True
            if _is_group(member):
                result = _eval_node(member, ctx, depth + 1)
            else:
                result = _eval_leaf(member, ctx)
            if result is not conjunction:
                return result  # short-circuit: first False under and / first True under or
        return True if not seen else conjunction
    # A bare leaf. Legacy quirk kept on purpose: at the TOP level a condition with no `field` is
    # "no condition" (⇒ True), while inside a group it is evaluated with a null actual. Definitions
    # in the wild lean on the first; the second is what makes `{operator: isEmpty}` usable at all.
    if node.get("field"):
        return _eval_leaf(node, ctx)
    return True


def evaluate(condition: Any, ctx: Mapping[str, Any]) -> bool:
    """Is this condition met in this context? Absent/empty/unintelligible ⇒ True ("don't hide").

    Total by construction and by guard: this runs inside the worker's dispatch loop and inside
    instantiate, and an exception in either stalls automations for a whole firm.
    """
    try:
        return _eval_node(condition, ctx if isinstance(ctx, Mapping) else {}, 0)
    except Exception as exc:  # noqa: BLE001 — the total-function guarantee, deliberately broad
        # Never log the condition itself: authored values carry client data.
        _log.warning("condition evaluation failed (%s) — treating as met", type(exc).__name__)
        return True


# ---------------------------------------------------------------------------------------------
# Authoring issues (publish preflight + the builder's live badges)
# ---------------------------------------------------------------------------------------------


def _field_issue(field: Any, issues: list[str]) -> str:
    """Report an unknown/missing field and return a label to use in later messages."""
    if not isinstance(field, str) or not field.strip():
        issues.append("A condition is missing the field it tests.")
        return "?"
    if field in _KNOWN_FIELDS or field.startswith(_OPEN_PREFIXES):
        return field
    if "." in field:
        # Only a dotted path names a scope we can check. A bare key is a pre-v3 flat context key
        # (`service_type`), and we have no registry to check those against — so we say nothing
        # rather than cry wolf over every legacy definition.
        issues.append(
            f"“{field}” isn’t a field the engine knows. Pick one from the list, "
            "or use a memory.* key."
        )
    return field


def _leaf_issues(cond: Mapping[str, Any], issues: list[str]) -> None:
    label = _field_issue(cond.get("field"), issues)
    raw_op = str(cond.get("operator") or cond.get("op") or "equals")
    op = _ALIASES.get(raw_op, raw_op)
    if op not in OPERATORS:
        issues.append(f"The condition on “{label}” uses a test we don’t know (“{raw_op}”).")
        return
    value = cond.get("value")
    referenced = isinstance(value, Mapping) and "field" in value
    days = _as_number(value)
    if op in _NEEDS_LIST and not isinstance(value, list):
        issues.append(f"The “{op}” test on “{label}” needs a list of values.")
    elif op in _NEEDS_NUMBER and not referenced and _as_number(value) is None:
        issues.append(f"The “{op}” test on “{label}” needs a number.")
    elif op in _NEEDS_DAYS and not referenced and (days is None or days < 0):
        issues.append(f"The “{op}” test on “{label}” needs a number of days.")
    elif op in _NEEDS_DATE and not referenced and _as_datetime(value) is None:
        issues.append(
            f"The “{op}” test on “{label}” needs a date, or another field to compare against."
        )
    elif op in _NEEDS_TEXT:
        if not isinstance(value, str) or not value.strip():
            issues.append(
                f"The “{op}” test on “{label}” needs some text to match "
                "(* for a word, ** for any text)."
            )
        elif _pattern_for(value) is None:
            issues.append(
                f"The “{op}” pattern on “{label}” is too long, or uses more than "
                f"{_MAX_WILDCARDS} wildcards."
            )


def _node_issues(node: Any, depth: int, issues: list[str]) -> None:
    if node is None:
        return
    if not isinstance(node, Mapping):
        issues.append("A condition must be an object with a field and a test.")
        return
    if _is_group(node):
        if depth > MAX_GROUP_DEPTH:
            issues.append(
                f"Condition groups are nested more than {MAX_GROUP_DEPTH} deep. Flatten them."
            )
            return  # don't walk further: the whole point of the cap is bounded recursion
        logic = node.get("logic")
        if logic is not None and str(logic).lower() not in ("and", "or"):
            issues.append(f"A condition group’s logic must be “and” or “or” (got “{logic}”).")
        members = node.get("conditions", [])
        if len(members) > MAX_CONDITIONS_PER_GROUP:
            issues.append(
                f"A condition group has {len(members)} conditions — the most allowed is "
                f"{MAX_CONDITIONS_PER_GROUP}. Split it into nested groups."
            )
        for member in members:
            _node_issues(member, depth + 1, issues)
        return
    if node.get("field") is None and node.get("operator") is None and node.get("op") is None:
        # Nothing authored here — and what that MEANS depends on where it sits.
        #
        # At the root (depth 1) it is "no condition", a legitimate way to author "always": stored
        # definitions rely on it, and `graph.py` normalises a branch arm's `{}` to the else for the
        # same reason. Inside a GROUP it is a half-written row, and the two layers have to agree
        # about that — the builder deliberately stores an unfinished arm as `{}` rather than null
        # so it cannot become a second fallback (`flow-editor/inspectors/branch.svelte`), so
        # letting one publish silently is the layer disagreeing with itself. Unreported, an `{}`
        # in an `or` group is a rule that fires for everybody.
        if depth > 1:
            issues.append(
                "A condition inside a group is unfinished. Give it a field and a test, "
                "or remove the row."
            )
        return
    _leaf_issues(node, issues)


def explain(condition: Any, ctx: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate a condition tree leaf by leaf and report WHICH parts held, with the value each one
    actually read.

    ``evaluate`` answers yes/no for the whole tree, which is the wrong granularity for an author
    staring at four rows wondering which one is wrong. This walks the same structure and calls the
    same evaluator on each node, so the verdict can never disagree with the engine's — the
    alternative (a second, TypeScript-side evaluator) is two implementations of one semantics and
    the exact drift this module exists to prevent.

    It lives here, beside the evaluator, because three surfaces need the identical shape: the rule
    tier's dry run (``POST /automations/{uid}/test``), a flow branch's arms and a step's plan-time
    visibility (``app/flows/simulate.py``). The returned tree is rendered by ``ExplainNode`` in
    ``@graphene/ui/automations``.
    """
    passed = evaluate(condition, ctx)
    if not isinstance(condition, Mapping) or not condition:
        return {"kind": "always", "passed": passed}
    if "logic" in condition or "conditions" in condition:
        members = condition.get("conditions")
        return {
            "kind": "group",
            "logic": str(condition.get("logic") or "and").lower(),
            "passed": passed,
            "conditions": [
                explain(member, ctx) for member in (members if isinstance(members, list) else [])
            ],
        }
    field_name = condition.get("field")
    return {
        "kind": "leaf",
        "field": field_name,
        "operator": condition.get("operator") or condition.get("op") or "equals",
        "value": condition.get("value"),
        "actual": resolve_field(field_name, ctx),
        "passed": passed,
    }


def condition_issues(condition: Any) -> list[str]:
    """Human-readable reasons an authored condition is not publishable: an unknown test, a field
    outside the registry (the ``memory.*``/``event.subject.*`` namespaces stay open), a value of the
    wrong shape for its test, an oversized or over-nested group. Empty ⇒ good to publish.

    Deduplicated in document order — twenty copies of the same message is a worse badge than one.
    """
    issues: list[str] = []
    _node_issues(condition, 1, issues)
    seen: set[str] = set()
    unique: list[str] = []
    for issue in issues:
        if issue not in seen:
            seen.add(issue)
            unique.append(issue)
    return unique
