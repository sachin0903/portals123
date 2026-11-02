"""Unit tests for the one condition evaluator (``app/flows/conditions.py``) and the visibility call
site that now delegates to it. No DB.

What this suite is for, in order of how much it would hurt to lose:

1. **The total-function guarantee.** ``evaluate`` runs in the worker's dispatch loop; a raise there
   stalls every firm's automations. The fuzz battery at the bottom throws junk of every shape at it.
2. **Backwards compatibility.** Every v1/v2 definition in the wild uses the original eight
   operators; their semantics are pinned here exactly as ``visibility.py`` had them.
3. **The contract pin.** The operator set and the field registry are mirrored from
   flow-contract.json as Python literals (the API container ships without pkgs/ui) — these fail the
   moment the two sides drift.
4. **Honest negation.** "is / is not" in the builder is only honest if the pairs are exact
   complements; the matrix proves it over a value grid rather than a handful of examples.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.flows import conditions, visibility

# ---------------------------------------------------------------------------------------------
# The contract pin (flow-contract.json is the single cross-language source of truth)
# ---------------------------------------------------------------------------------------------

# tests/ -> apps/api -> apps -> the repo root.
_CONTRACT_PATH = (
    Path(__file__).resolve().parents[3]
    / "pkgs"
    / "ui"
    / "src"
    / "lib"
    / "components"
    / "book"
    / "journeys"
    / "flow-contract.json"
)
_DRIFT = "the Python side drifted from flow-contract.json — change them together"


def _contract() -> dict[str, Any]:
    data = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.mark.pkgs_ui_pin
def test_contract_operators_match() -> None:
    assert conditions.OPERATORS == frozenset(_contract()["conditions"]["operators"]), _DRIFT


@pytest.mark.pkgs_ui_pin
def test_contract_field_scopes_match() -> None:
    scopes = {k: tuple(v) for k, v in _contract()["conditions"]["fieldScopes"].items()}
    assert conditions.FIELD_SCOPES == scopes, _DRIFT


@pytest.mark.pkgs_ui_pin
def test_contract_group_cap_matches() -> None:
    assert conditions.MAX_CONDITIONS_PER_GROUP == _contract()["caps"]["maxConditionsPerGroup"], (
        _DRIFT
    )


@pytest.mark.parametrize("op", sorted(conditions.OPERATORS))
def test_every_operator_is_implemented(op: str) -> None:
    """No operator may sit in the contract with no branch behind it: an unimplemented one falls
    into the "unknown ⇒ True" catch-all and silently matches EVERYBODY, which is exactly the
    automation-fires-at-the-whole-book failure. An operator that can return False for some input
    provably has its own branch (the catch-all never can)."""
    probes: list[tuple[Any, Any]] = [
        ("advised", "advised"),
        ("advised", "other"),
        ("", None),
        (5, 1),
        (1, 5),
        (["isa"], ["isa"]),
        ("isa", ["isa"]),
        ("2026-07-01", "2026-07-02"),
        ("2026-07-01", 7),
        (None, "**"),
    ]
    results = [
        conditions.evaluate({"field": "x", "operator": op, "value": value}, {"x": actual})
        for actual, value in probes
    ]
    assert False in results, f"{op} never returns False — is it implemented?"


# ---------------------------------------------------------------------------------------------
# Backwards compatibility — the eight legacy operators, exactly as visibility.py had them
# ---------------------------------------------------------------------------------------------


def _ev(cond: Any, ctx: dict[str, Any] | None = None) -> bool:
    return conditions.evaluate(cond, ctx or {})


def test_legacy_single_condition_match() -> None:
    vis = {"field": "service_type", "operator": "equals", "value": "advised"}
    assert _ev(vis, {"service_type": "advised"}) is True
    assert _ev(vis, {"service_type": "execution_only"}) is False


def test_legacy_absent_is_visible() -> None:
    assert _ev(None) is True
    assert _ev({}, {"service_type": "advised"}) is True
    assert _ev("", {}) is True
    assert _ev([], {}) is True


def test_legacy_group_and_or() -> None:
    conds = [
        {"field": "service_type", "operator": "equals", "value": "advised"},
        {"field": "type", "operator": "equals", "value": "individual"},
    ]
    ctx = {"service_type": "advised", "type": "organisation"}
    assert _ev({"logic": "and", "conditions": conds}, ctx) is False
    assert _ev({"logic": "or", "conditions": conds}, ctx) is True


def test_legacy_group_defaults_to_and_and_unknown_logic_is_or() -> None:
    conds = [
        {"field": "a", "operator": "equals", "value": 1},
        {"field": "b", "operator": "equals", "value": 2},
    ]
    ctx = {"a": 1, "b": 99}
    assert _ev({"conditions": conds}, ctx) is False  # no logic ⇒ and
    assert _ev({"logic": "AND", "conditions": conds}, ctx) is False  # case-insensitive
    assert _ev({"logic": "nonsense", "conditions": conds}, ctx) is True  # not 'and' ⇒ or (legacy)


def test_legacy_group_with_no_usable_members_is_visible() -> None:
    assert _ev({"logic": "and", "conditions": ["nope", 5, None]}, {}) is True


def test_legacy_empty_operators() -> None:
    is_set = {"field": "email", "operator": "isNotEmpty"}
    assert _ev(is_set, {"email": "a@b.com"}) is True
    assert _ev(is_set, {"email": ""}) is False
    assert _ev({"field": "email", "operator": "isEmpty"}, {}) is True
    # The emptiness set is compared with ==, so 0 and False are NOT empty (regression guard: a
    # `0` fund value must not read as "not provided").
    assert _ev({"field": "v", "operator": "isEmpty"}, {"v": 0}) is False
    assert _ev({"field": "v", "operator": "isEmpty"}, {"v": False}) is False
    assert _ev({"field": "v", "operator": "isEmpty"}, {"v": []}) is True
    assert _ev({"field": "v", "operator": "isEmpty"}, {"v": {}}) is True


def test_legacy_unknown_operator_is_visible() -> None:
    assert _ev({"field": "x", "operator": "wat", "value": 1}, {"x": 2}) is True


def test_legacy_contains_is_type_safe() -> None:
    # Regression (S4): `contains` with a non-string value against a string actual must NOT raise
    # (`5 in "advised"` → TypeError → 500 at instantiate). It returns False instead.
    assert _ev({"field": "s", "operator": "contains", "value": 5}, {"s": "advised"}) is False
    assert _ev({"field": "s", "operator": "contains", "value": "vis"}, {"s": "advised"}) is True
    tags = {"t": ["isa", "gia"]}
    assert _ev({"field": "t", "operator": "contains", "value": "isa"}, tags) is True
    assert _ev({"field": "t", "operator": "notContains", "value": 5}, {"t": "advised"}) is True
    # Anything that is neither string nor list contains nothing.
    assert _ev({"field": "n", "operator": "contains", "value": 1}, {"n": {"a": 1}}) is False


def test_legacy_in_requires_a_list_value() -> None:
    assert _ev({"field": "p", "operator": "in", "value": ["ISA", "GIA"]}, {"p": "ISA"}) is True
    assert _ev({"field": "p", "operator": "in", "value": ["ISA"]}, {"p": "SIPP"}) is False
    # A non-list value can never satisfy `in` — and `notIn` is its exact complement, so it is True.
    assert _ev({"field": "p", "operator": "in", "value": "ISA"}, {"p": "ISA"}) is False
    assert _ev({"field": "p", "operator": "notIn", "value": "ISA"}, {"p": "ISA"}) is True


def test_legacy_operator_aliases() -> None:
    """v1 definitions wrote `==`/`eq`/`!=`/`ne`, and some wrote the key as `op` rather than
    `operator`."""
    for spelling in ("equals", "==", "eq"):
        assert _ev({"field": "x", "operator": spelling, "value": 1}, {"x": 1}) is True
    for spelling in ("notEquals", "!=", "ne"):
        assert _ev({"field": "x", "operator": spelling, "value": 1}, {"x": 1}) is False
    assert _ev({"field": "x", "op": "equals", "value": 1}, {"x": 1}) is True
    # No operator at all defaults to equals (legacy).
    assert _ev({"field": "x", "value": 1}, {"x": 1}) is True


def test_legacy_top_level_leaf_without_a_field_is_visible() -> None:
    """The quirk kept on purpose: at the top level "no field" means "no condition", but the same
    object INSIDE a group is evaluated with a null actual (which is what makes isEmpty usable)."""
    assert _ev({"operator": "isNotEmpty"}, {}) is True
    assert _ev({"logic": "and", "conditions": [{"operator": "isNotEmpty"}]}, {}) is False


def test_visibility_module_delegates() -> None:
    """visibility.evaluate keeps its signature and its behaviour — it is now a call site."""
    vis = {"field": "applicant.type", "operator": "equals", "value": "individual"}
    assert visibility.evaluate(vis, {"applicant.type": "individual"}) is True
    assert visibility.evaluate(vis, {"applicant.type": "organisation"}) is False
    assert visibility.evaluate(None, {}) is True


# ---------------------------------------------------------------------------------------------
# Field resolution
# ---------------------------------------------------------------------------------------------


def test_field_resolution_flat_dotted_keys() -> None:
    """The planner's contexts are FLAT dicts with literal dotted keys — the shape every stored
    definition was authored against."""
    ctx = {"application.custody": "offshore", "applicant.type": "individual"}
    assert conditions.resolve_field("application.custody", ctx) == "offshore"
    assert _ev({"field": "application.custody", "operator": "equals", "value": "offshore"}, ctx)


def test_field_resolution_nested_and_mixed() -> None:
    nested = {"event": {"subject": {"contact_id": 7}}, "contact": {"type": "individual"}}
    assert conditions.resolve_field("event.subject.contact_id", nested) == 7
    assert conditions.resolve_field("contact.type", nested) == "individual"
    mixed = {"event.subject": {"contact_id": 7}}
    assert conditions.resolve_field("event.subject.contact_id", mixed) == 7


def test_field_resolution_misses_are_none_never_raises() -> None:
    ctx = {"contact": {"type": "individual"}, "n": 5}
    assert conditions.resolve_field("contact.nope", ctx) is None
    assert conditions.resolve_field("nope.nope.nope", ctx) is None
    assert conditions.resolve_field("n.deeper", ctx) is None  # descend into a non-mapping
    assert conditions.resolve_field("", ctx) is None
    assert conditions.resolve_field(None, ctx) is None
    assert conditions.resolve_field(42, ctx) is None
    assert conditions.resolve_field("a." * 5000 + "b", ctx) is None  # iterative, no stack blowup


def test_unknown_field_is_null_not_true() -> None:
    """The documented asymmetry: an unknown OPERATOR is visible (don't hide), an unknown FIELD is
    just null. A mistyped field must never quietly satisfy every test."""
    assert _ev({"field": "typo.here", "operator": "equals", "value": "x"}, {"a": 1}) is False
    assert _ev({"field": "typo.here", "operator": "isEmpty"}, {"a": 1}) is True
    assert _ev({"field": "typo.here", "operator": "notEquals", "value": "x"}, {"a": 1}) is True
    assert _ev({"field": "typo.here", "operator": "contains", "value": "x"}, {"a": 1}) is False


# ---------------------------------------------------------------------------------------------
# matches / notMatches — the wildcard grammar
# ---------------------------------------------------------------------------------------------

# (pattern, subject, expected)
_WILDCARD_CASES: list[tuple[str, str, bool]] = [
    # No wildcard ⇒ whole-string equality, case- and punctuation-insensitive.
    ("hello world", "Hello, world!", True),
    ("hello world", "hello world extra", False),
    ("e mail", "E-Mail", True),
    ("e-mail", "e mail", True),  # punctuation in the PATTERN is a separator too
    ("gia", "GIA", True),
    ("gia", "a GIA account", False),
    # `*` is exactly one word.
    ("hello *", "hello world", True),
    ("hello *", "hello", False),
    ("hello *", "hello big world", False),
    ("* world", "hello world", True),
    ("*", "hello", True),
    ("*", "hello world", False),
    ("*", "", False),
    # `**` is any text, including none and part of a word.
    ("**", "", True),
    ("**", "anything at all", True),
    ("hello **", "hello", False),  # the space is literal, so something must follow it
    ("hello**", "hello", True),
    ("**world**", "hello world today", True),
    ("**wor**", "hello world", True),  # substring, not word-bounded
    ("*wor*", "hello world", False),  # `*` cannot match part of a word next to literals
    ("sipp **", "SIPP transfer in progress", True),
    ("** transfer **", "urgent transfer today", True),
]


@pytest.mark.parametrize(("pattern", "subject", "expected"), _WILDCARD_CASES)
def test_matches_wildcard_grammar(pattern: str, subject: str, expected: bool) -> None:
    cond = {"field": "s", "operator": "matches", "value": pattern}
    assert _ev(cond, {"s": subject}) is expected


@pytest.mark.parametrize(("pattern", "subject", "expected"), _WILDCARD_CASES)
def test_not_matches_is_the_exact_complement(pattern: str, subject: str, expected: bool) -> None:
    cond = {"field": "s", "operator": "notMatches", "value": pattern}
    assert _ev(cond, {"s": subject}) is (not expected)


def test_matches_against_a_list_matches_any_member() -> None:
    ctx = {"contact.tags": ["VIP", "needs-review"]}
    assert _ev({"field": "contact.tags", "operator": "matches", "value": "vip"}, ctx) is True
    assert _ev({"field": "contact.tags", "operator": "matches", "value": "needs review"}, ctx)
    assert _ev({"field": "contact.tags", "operator": "matches", "value": "gone"}, ctx) is False
    # Non-string members are skipped, not coerced.
    assert _ev({"field": "t", "operator": "matches", "value": "5"}, {"t": [5]}) is False


def test_matches_non_text_never_matches() -> None:
    for actual in (None, 5, True, {"a": 1}):
        assert _ev({"field": "s", "operator": "matches", "value": "**"}, {"s": actual}) is False
    for value in (None, 5, ["**"], {"a": 1}):
        assert _ev({"field": "s", "operator": "matches", "value": value}, {"s": "x"}) is False


def test_matches_refuses_a_pathological_pattern() -> None:
    """An over-wildcarded or over-long pattern simply never matches (and condition_issues explains
    why at publish). These are AUTHORING bounds — the safety guarantee is the matcher itself, which
    cannot backtrack; see the ReDoS regression below."""
    evil = " ".join(["**"] * 40)
    assert _ev({"field": "s", "operator": "matches", "value": evil}, {"s": "a " * 400}) is False
    assert _ev({"field": "s", "operator": "matches", "value": "x" * 900}, {"s": "x" * 900}) is False
    # …and a subject far too long to be author intent is refused rather than scanned.
    assert _ev({"field": "s", "operator": "matches", "value": "**"}, {"s": "a" * 50_000}) is False


# The shape that used to be lethal: a single `*` compiled to `[^ ]+`, and alternating one of those
# with a literal makes a backtracking engine try every way of splitting the subject between them.
# Exactly `_MAX_WILDCARDS` stars is ON the cap, so `condition_issues` returned [] and the condition
# PUBLISHED CLEAN. Measured against the old regex: 0.008s at 30 subject chars, 0.30s at 38, 1.4s at
# 42, 5.8s at 46 — ~4.6x per four characters, and the subject cap allows 4096. This is synchronous
# CPU inside the worker's asyncio loop, so it stalls timers, the outbox, the notifier and AML sync
# for EVERY tenant until the watchdog restarts the container onto the same unconsumed event.
_REDOS_PATTERNS = [
    "*a" * (conditions._MAX_WILDCARDS - 1) + "*b",  # the reviewer's original
    "*" + "a*" * (conditions._MAX_WILDCARDS - 1) + "b",
    "**a" * 4 + "**b",  # the `.*` form, for completeness
    " ".join(["*"] * conditions._MAX_WILDCARDS) + " b",
    ("*a" * 6) + ("**b" * 3) + "*c",  # both wildcard kinds interleaved
]


@pytest.mark.parametrize("pattern", _REDOS_PATTERNS)
@pytest.mark.parametrize("length", [64, 512, conditions._MAX_SUBJECT_CHARS])
def test_matches_is_linear_against_an_adversarial_subject(pattern: str, length: int) -> None:
    """A wildcard match is bounded WORK, not merely a bounded return value.

    The bound is deliberately generous (a second is ~1000x the real cost and ~6x the old
    implementation's cost at a subject length of 46) so this fails on the defect and not on a busy
    CI box. The old matcher does not finish the 4096-character cases this side of the heat death of
    the universe.
    """
    subject = "a" * length
    cond = {"field": "s", "operator": "matches", "value": pattern}
    conditions._wildcard_nfa.cache_clear()  # the compile is part of the cost being bounded
    start = time.perf_counter()
    result = conditions.evaluate(cond, {"s": subject})
    elapsed = time.perf_counter() - start
    assert result is False
    assert elapsed < 1.0, f"{pattern!r} against {length} chars took {elapsed:.3f}s"


def test_matches_publishes_the_shape_that_used_to_hang() -> None:
    """The cap did not even catch it: exactly `_MAX_WILDCARDS` stars is ON the limit, so the
    pattern that took six seconds to fail was publishable. It still is — because it is now cheap,
    not because the limit moved."""
    pattern = "*a" * (conditions._MAX_WILDCARDS - 1) + "*b"
    assert pattern.count("*") == conditions._MAX_WILDCARDS
    assert (
        conditions.condition_issues({"field": "s", "operator": "matches", "value": pattern}) == []
    )


def test_matches_stays_linear_past_the_wildcard_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The caps bound AUTHORING, not cost — which is the whole point of moving off the regex.

    Lifting `_MAX_WILDCARDS` far past its configured value must not reintroduce the hang. If this
    ever gets slow again, the safety of `matches` has quietly gone back to depending on a number in
    a constant, which is where this defect came from.
    """
    monkeypatch.setattr(conditions, "_MAX_WILDCARDS", 400)
    conditions._wildcard_nfa.cache_clear()
    pattern = "*a" * 99 + "*b"
    start = time.perf_counter()
    assert conditions._match_text("a" * conditions._MAX_SUBJECT_CHARS, pattern) is False
    elapsed = time.perf_counter() - start
    conditions._wildcard_nfa.cache_clear()  # don't leave an over-cap compile in the shared cache
    assert elapsed < 1.0, f"200 wildcards took {elapsed:.3f}s"


# ---------------------------------------------------------------------------------------------
# greaterThan / lessThan
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actual", "value", "gt", "lt"),
    [
        (10, 5, True, False),
        (5, 10, False, True),
        (5, 5, False, False),  # equality satisfies neither — they are not complements
        (10.5, "10", True, False),
        ("10", 10.5, False, True),  # numeric strings work either side
        (Decimal("250000.50"), 250000, True, False),
        ("not a number", 1, False, False),
        (None, 1, False, False),
        (1, None, False, False),
        (True, 0, False, False),  # booleans are deliberately not numbers
        (float("nan"), 1, False, False),
        (float("inf"), 1, False, False),
        ([1], 1, False, False),
    ],
)
def test_numeric_comparisons(actual: Any, value: Any, gt: bool, lt: bool) -> None:
    ctx = {"account.value": actual}
    assert _ev({"field": "account.value", "operator": "greaterThan", "value": value}, ctx) is gt
    assert _ev({"field": "account.value", "operator": "lessThan", "value": value}, ctx) is lt
    assert not (gt and lt)  # mutually exclusive by construction


def test_numeric_comparison_against_another_field() -> None:
    ctx = {"account": {"value": 100.0}, "memory": {"threshold": 50}}
    cond = {
        "field": "account.value",
        "operator": "greaterThan",
        "value": {"field": "memory.threshold"},
    }
    assert _ev(cond, ctx) is True
    assert _ev(cond, {"account": {"value": 10}, "memory": {"threshold": 50}}) is False


def test_legacy_operators_do_not_dereference_a_field_value() -> None:
    """Only the v3 comparison operators resolve a `{field: …}` value. `equals` must keep treating
    it as a literal, or a stored definition comparing against an object changes meaning."""
    ctx = {"x": {"field": "y"}, "y": "resolved"}
    assert _ev({"field": "x", "operator": "equals", "value": {"field": "y"}}, ctx) is True


# ---------------------------------------------------------------------------------------------
# Temporal operators
# ---------------------------------------------------------------------------------------------

_NOW = "2026-08-03T12:00:00+00:00"


def _tctx(actual: Any) -> dict[str, Any]:
    """A context with `now` pinned, so the day-window operators are deterministic."""
    return {"t": actual, "time.now": _NOW}


@pytest.mark.parametrize(
    ("actual", "value", "before", "after"),
    [
        ("2026-07-01T11:00:00Z", "2026-07-01T12:00:00Z", True, False),
        ("2026-07-01T13:00:00Z", "2026-07-01T12:00:00Z", False, True),
        ("2026-07-01T12:00:00Z", "2026-07-01T12:00:00Z", False, False),  # equal ⇒ neither
        # Naive is UTC — the whole point of the rule, since a naive/aware comparison raises.
        ("2026-07-01T11:00:00", "2026-07-01T12:00:00Z", True, False),
        ("2026-07-01T12:00:00Z", "2026-07-01T11:00:00", False, True),
        # 'Z' and '+00:00' are the same instant; a real offset is honoured.
        ("2026-07-01T13:00:00+02:00", "2026-07-01T12:00:00Z", True, False),
        # A date with no time is midnight UTC.
        ("2026-07-01", "2026-07-01T00:00:01Z", True, False),
        ("2026-07-02", "2026-07-01", False, True),
        # Junk on either side ⇒ both False, never a raise.
        ("yesterday", "2026-07-01", False, False),
        ("2026-07-01", "soon", False, False),
        (None, "2026-07-01", False, False),
        (12345, "2026-07-01", False, False),
        ("2026-13-45", "2026-07-01", False, False),
    ],
)
def test_before_after(actual: Any, value: Any, before: bool, after: bool) -> None:
    assert _ev({"field": "t", "operator": "before", "value": value}, _tctx(actual)) is before
    assert _ev({"field": "t", "operator": "after", "value": value}, _tctx(actual)) is after
    assert not (before and after)


def test_before_after_accept_real_datetime_objects() -> None:
    ctx = {"t": datetime(2026, 7, 1, 11, 0, tzinfo=UTC)}
    assert _ev({"field": "t", "operator": "before", "value": "2026-07-01T12:00:00Z"}, ctx) is True
    assert _ev({"field": "t", "operator": "before", "value": date(2026, 7, 2)}, ctx) is True


def test_before_after_against_another_field() -> None:
    ctx = {"case": {"started_at": "2026-07-01"}, "memory": {"deadline": "2026-07-10"}}
    cond = {
        "field": "case.started_at",
        "operator": "before",
        "value": {"field": "memory.deadline"},
    }
    assert _ev(cond, ctx) is True


@pytest.mark.parametrize(
    ("actual", "days", "within", "older"),
    [
        ("2026-08-01T12:00:00Z", 7, True, False),  # 2 days ago
        ("2026-07-01T12:00:00Z", 7, False, True),  # 33 days ago
        ("2026-07-27T12:00:00Z", 7, True, False),  # exactly on the cutoff ⇒ within (>=)
        ("2026-07-27T11:59:59Z", 7, False, True),  # a second the wrong side of it
        ("2026-09-01T12:00:00Z", 7, True, False),  # the future is within any window, older than
        # none — which is what makes the pair total
        ("2026-08-01T12:00:00Z", "7", True, False),  # a numeric string is a number of days
        ("2026-08-01T12:00:00Z", 0, False, True),  # a zero-day window is "since this instant"
    ],
)
def test_within_days_and_older_than_days_are_complements(
    actual: str, days: Any, within: bool, older: bool
) -> None:
    assert _ev({"field": "t", "operator": "withinDays", "value": days}, _tctx(actual)) is within
    assert _ev({"field": "t", "operator": "olderThanDays", "value": days}, _tctx(actual)) is older
    assert within is not older  # exact complements whenever both sides resolve


@pytest.mark.parametrize(
    ("actual", "days"),
    [
        ("nonsense", 7),
        (None, 7),
        ("2026-08-01T12:00:00Z", "seven"),
        ("2026-08-01T12:00:00Z", None),
        ("2026-08-01T12:00:00Z", -1),  # a negative window is a typo, not a future window
        ("2026-08-01T12:00:00Z", 1e18),  # timedelta would OverflowError
    ],
)
def test_day_windows_refuse_junk_on_either_side(actual: Any, days: Any) -> None:
    assert _ev({"field": "t", "operator": "withinDays", "value": days}, _tctx(actual)) is False
    assert _ev({"field": "t", "operator": "olderThanDays", "value": days}, _tctx(actual)) is False


def test_day_windows_fall_back_to_wall_clock() -> None:
    """Without a pinned `time.now` the window is measured from now — the normal runtime path."""
    just_now = datetime.now(UTC).isoformat()
    assert _ev({"field": "t", "operator": "withinDays", "value": 1}, {"t": just_now}) is True
    assert _ev({"field": "t", "operator": "olderThanDays", "value": 1}, {"t": just_now}) is False


# ---------------------------------------------------------------------------------------------
# Negated twins — exact complements over a value matrix
# ---------------------------------------------------------------------------------------------

_COMPLEMENT_PAIRS = [
    ("equals", "notEquals"),
    ("in", "notIn"),
    ("contains", "notContains"),
    ("isEmpty", "isNotEmpty"),
    ("matches", "notMatches"),
]

_ACTUALS: list[Any] = [
    None,
    "",
    "advised",
    "GIA account",
    ["isa", "gia"],
    [],
    0,
    5,
    True,
    False,
    {"a": 1},
    "2026-07-01T12:00:00Z",
]

_VALUES: list[Any] = [
    None,
    "",
    "advised",
    "isa",
    "**isa**",
    "*",
    5,
    ["isa", "gia"],
    {"field": "other"},
    True,
]


@pytest.mark.parametrize(("positive", "negative"), _COMPLEMENT_PAIRS)
def test_negated_twins_are_exact_complements(positive: str, negative: str) -> None:
    """ "is / is not" in the builder is only honest if the negation is total: for every value in the
    grid, exactly one of the pair holds. (greaterThan/lessThan and before/after are NOT twins —
    they share the equality boundary — and are asserted mutually exclusive in their own tests.)"""
    for actual in _ACTUALS:
        for value in _VALUES:
            ctx = {"x": actual, "other": "advised"}
            pos = _ev({"field": "x", "operator": positive, "value": value}, ctx)
            neg = _ev({"field": "x", "operator": negative, "value": value}, ctx)
            assert pos is not neg, f"{positive}/{negative} on {actual!r} vs {value!r}"


# ---------------------------------------------------------------------------------------------
# Nested groups
# ---------------------------------------------------------------------------------------------


def test_nested_groups_evaluate_arbitrarily_deep() -> None:
    """ "offshore AND (pension OR SIPP)" — one level of nesting the old evaluator silently treated
    as an always-true leaf."""
    cond = {
        "logic": "and",
        "conditions": [
            {"field": "application.custody", "operator": "equals", "value": "offshore"},
            {
                "logic": "or",
                "conditions": [
                    {
                        "field": "application.has_pension_product",
                        "operator": "equals",
                        "value": True,
                    },
                    {"field": "application.has_sipp_product", "operator": "equals", "value": True},
                ],
            },
        ],
    }
    base = {"application.custody": "offshore", "application.has_sipp_product": True}
    assert _ev(cond, base) is True
    assert _ev(cond, {**base, "application.has_sipp_product": False}) is False
    assert _ev(cond, {**base, "application.custody": "onshore"}) is False


def test_groups_nest_to_the_cap_then_fail_open() -> None:
    """Up to MAX_GROUP_DEPTH the nesting is evaluated; beyond it the walk stops and the condition
    reads as met (the legacy "don't hide" rule) — publish refuses it before it can get there."""

    def wrap(depth: int, leaf: dict[str, Any]) -> dict[str, Any]:
        node = leaf
        for _ in range(depth):
            node = {"logic": "and", "conditions": [node]}
        return node

    false_leaf = {"field": "x", "operator": "equals", "value": "no"}
    ctx = {"x": "yes"}
    assert _ev(wrap(conditions.MAX_GROUP_DEPTH, false_leaf), ctx) is False
    assert _ev(wrap(conditions.MAX_GROUP_DEPTH + 1, false_leaf), ctx) is True
    assert conditions.condition_issues(wrap(conditions.MAX_GROUP_DEPTH, false_leaf)) == []
    assert conditions.condition_issues(wrap(conditions.MAX_GROUP_DEPTH + 1, false_leaf)) == [
        f"Condition groups are nested more than {conditions.MAX_GROUP_DEPTH} deep. Flatten them."
    ]


def test_an_empty_member_of_an_or_group_does_not_swallow_every_case() -> None:
    """`{}` inside a group is an unfinished row, not a condition that matches everything.

    It used to fall through to the leaf evaluator as `actual=None equals value=None` — True — so
    one empty row in an `or` group made the whole group unconditionally true: a branch arm that
    takes every case, or an entry condition that fires the rule for everybody. And it published
    clean, because `condition_issues` treated an empty object as "no condition, not a broken one" —
    which is right for a root and wrong for a member.
    """
    empty_in_or = {
        "logic": "or",
        "conditions": [
            {"field": "contact.type", "operator": "equals", "value": "nope"},
            {},
        ],
    }
    assert _ev(empty_in_or, {"contact.type": "individual"}) is False
    assert conditions.condition_issues(empty_in_or) == [
        "A condition inside a group is unfinished. Give it a field and a test, or remove the row."
    ]


def test_an_empty_member_is_skipped_rather_than_false() -> None:
    """Skipped, not False — the identity element differs by logic, and an absent condition must not
    flip an `and` group's verdict either."""
    empty_in_and = {
        "logic": "and",
        "conditions": [{"field": "contact.type", "operator": "equals", "value": "individual"}, {}],
    }
    assert _ev(empty_in_and, {"contact.type": "individual"}) is True
    assert _ev(empty_in_and, {"contact.type": "organisation"}) is False
    # A group of nothing but empties has no usable members at all, which is the legacy
    # "no condition ⇒ visible" case.
    assert _ev({"logic": "or", "conditions": [{}, {}]}, {}) is True


def test_an_empty_condition_is_still_no_condition_at_the_ROOT() -> None:
    """The two layers now agree on what `{}` means, and this is the half that must NOT change:
    stored definitions author "always" as an empty object, and `graph.py` normalises a branch arm's
    `{}` to the unconditional else for the same reason."""
    assert _ev({}, {}) is True
    assert conditions.condition_issues({}) == []
    assert conditions.condition_issues({"value": 5}) == []  # nothing authored yet, not broken


def test_an_operator_only_member_is_still_a_real_condition() -> None:
    """The skip is the truly-empty test, not "has no field": `{operator: isEmpty}` is what makes a
    field-less emptiness check usable inside a group, and it is evaluated against a null actual."""
    cond = {"logic": "and", "conditions": [{"operator": "isEmpty"}]}
    assert _ev(cond, {}) is True
    assert _ev({"logic": "and", "conditions": [{"operator": "isNotEmpty"}]}, {}) is False
    assert conditions.condition_issues(cond) == ["A condition is missing the field it tests."]


def test_group_short_circuits_without_changing_the_answer() -> None:
    """A False member under `and` stops the walk; a later broken member cannot resurrect it."""
    cond = {
        "logic": "and",
        "conditions": [
            {"field": "x", "operator": "equals", "value": "no"},
            {"field": "y", "operator": "wat"},  # unknown ⇒ True, but never reached
        ],
    }
    assert _ev(cond, {"x": "yes"}) is False


# ---------------------------------------------------------------------------------------------
# condition_issues
# ---------------------------------------------------------------------------------------------


def test_no_issues_for_a_well_formed_condition() -> None:
    assert conditions.condition_issues(None) == []
    assert conditions.condition_issues({}) == []
    assert (
        conditions.condition_issues(
            {
                "logic": "or",
                "conditions": [
                    {"field": "contact.type", "operator": "equals", "value": "individual"},
                    {"field": "account.value", "operator": "greaterThan", "value": 100000},
                    {"field": "case.started_at", "operator": "olderThanDays", "value": 30},
                    {"field": "memory.chaser_sent", "operator": "isEmpty"},
                    {"field": "event.subject.contact_id", "operator": "isNotEmpty"},
                    {"field": "contact.tags", "operator": "matches", "value": "vip **"},
                ],
            }
        )
        == []
    )


def test_issue_unknown_operator() -> None:
    issues = conditions.condition_issues({"field": "contact.type", "operator": "sortof"})
    assert issues == ["The condition on “contact.type” uses a test we don’t know (“sortof”)."]


def test_issue_unknown_field_but_open_namespaces_and_legacy_paths_pass() -> None:
    issues = conditions.condition_issues({"field": "contact.typ", "operator": "isEmpty"})
    assert issues == [
        "“contact.typ” isn’t a field the engine knows. Pick one from the list, "
        "or use a memory.* key."
    ]
    for ok in (
        "memory.anything_at_all",
        "event.subject.account_id",
        "applicant.type",  # pre-v3 planner context
        "service_type",  # pre-v3 flat key — no scope to check it against, so we stay quiet
        "contact.type",
    ):
        assert conditions.condition_issues({"field": ok, "operator": "isEmpty"}) == [], ok


def test_issue_missing_field() -> None:
    assert conditions.condition_issues({"operator": "isEmpty"}) == [
        "A condition is missing the field it tests."
    ]


def test_issue_wrong_value_type_per_operator() -> None:
    def one(op: str, value: Any) -> str:
        issues = conditions.condition_issues(
            {"field": "account.value", "operator": op, "value": value}
        )
        assert len(issues) == 1, issues
        return issues[0]

    assert one("in", "ISA").endswith("needs a list of values.")
    assert one("notIn", None).endswith("needs a list of values.")
    assert one("greaterThan", "lots").endswith("needs a number.")
    assert one("lessThan", None).endswith("needs a number.")
    assert one("withinDays", "soon").endswith("needs a number of days.")
    assert one("olderThanDays", -3).endswith("needs a number of days.")
    assert one("before", "whenever").endswith("needs a date, or another field to compare against.")
    assert one("matches", 5).endswith("(* for a word, ** for any text).")
    assert one("matches", "   ").endswith("(* for a word, ** for any text).")
    assert "wildcards" in one("matches", " ".join(["**"] * 40))
    # A `{field: …}` reference satisfies the comparison operators' value check.
    for op in ("greaterThan", "before", "withinDays"):
        assert (
            conditions.condition_issues(
                {"field": "account.value", "operator": op, "value": {"field": "memory.x"}}
            )
            == []
        )


def test_issue_group_shape() -> None:
    oversized = {
        "logic": "and",
        "conditions": [
            {"field": "memory.k", "operator": "isEmpty"}
            for _ in range(conditions.MAX_CONDITIONS_PER_GROUP + 1)
        ],
    }
    assert conditions.condition_issues(oversized) == [
        f"A condition group has {conditions.MAX_CONDITIONS_PER_GROUP + 1} conditions — the most "
        f"allowed is {conditions.MAX_CONDITIONS_PER_GROUP}. Split it into nested groups."
    ]
    assert conditions.condition_issues(
        {"logic": "maybe", "conditions": [{"field": "memory.k", "operator": "isEmpty"}]}
    ) == ["A condition group’s logic must be “and” or “or” (got “maybe”)."]
    assert conditions.condition_issues("not an object") == [
        "A condition must be an object with a field and a test."
    ]


def test_issues_are_deduplicated_in_document_order() -> None:
    cond = {
        "logic": "and",
        "conditions": [
            {"field": "contact.typ", "operator": "isEmpty"},
            {"field": "contact.typ", "operator": "isEmpty"},
            {"field": "contact.type", "operator": "sortof"},
        ],
    }
    issues = conditions.condition_issues(cond)
    assert len(issues) == 2
    assert issues[0].startswith("“contact.typ”")
    assert issues[1].startswith("The condition on “contact.type”")


def test_aliases_are_not_reported_as_unknown_operators() -> None:
    for spelling in ("==", "!=", "eq", "ne"):
        cond = {"field": "contact.type", "operator": spelling, "value": "individual"}
        assert conditions.condition_issues(cond) == [], spelling


# ---------------------------------------------------------------------------------------------
# The total-function guarantee — junk in, a bool out, never an exception
# ---------------------------------------------------------------------------------------------


def _self_referential() -> dict[str, Any]:
    node: dict[str, Any] = {"logic": "and"}
    node["conditions"] = [node]  # a cycle an author cannot type but a bad merge can produce
    return node


_JUNK_CONDITIONS: list[Any] = [
    None,
    True,
    0,
    "",
    "a string",
    b"bytes",
    [],
    [{"field": "x"}],
    {"field": None, "operator": None, "value": None},
    {"field": 5, "operator": 5, "value": 5},
    {"field": "x", "operator": ["equals"], "value": object()},
    {"logic": None, "conditions": None},
    {"logic": [], "conditions": {}},
    {"logic": "and", "conditions": [None, 1, "x", [], {}]},
    {"conditions": [{"conditions": [{"conditions": [{"field": "x"}]}]}]},
    _self_referential(),
    {"field": "a." * 2000 + "b", "operator": "equals", "value": "x"},
    {"field": "x", "operator": "matches", "value": "*" * 200},
    {"field": "x", "operator": "contains", "value": {"unhashable": ["deep"]}},
    {"field": "x", "operator": "in", "value": [object(), None, float("nan")]},
    {"field": "x", "operator": "greaterThan", "value": "1e400"},
    {"field": "x", "operator": "withinDays", "value": float("inf")},
    {"field": "x", "operator": "olderThanDays", "value": 10**18},
    {"field": "x", "operator": "before", "value": "9999-99-99"},
    {"field": "x", "operator": "isEmpty", "value": "x" * 100_000},
    # 500 members, deliberately over the publish cap: evaluate still has to cope.
    {
        "logic": "or",
        "conditions": [{"field": "x", "operator": "equals", "value": i} for i in range(500)],
    },
]

_JUNK_CONTEXTS: list[Any] = [
    {},
    None,
    "a string",
    [1, 2, 3],
    42,
    {"x": object()},
    {"x": float("nan")},
    {"x": {"y": {"z": None}}},
    {"x": "x" * 200_000},
    {"x": [None, [None]], "time.now": "not a time"},
]


@pytest.mark.parametrize("cond", _JUNK_CONDITIONS)
@pytest.mark.parametrize("ctx", _JUNK_CONTEXTS)
def test_evaluate_never_raises(cond: Any, ctx: Any) -> None:
    """A condition that 500s the worker loop stalls every firm's automations. Junk of every shape
    must come back as a plain bool."""
    assert isinstance(conditions.evaluate(cond, ctx), bool)


@pytest.mark.parametrize("cond", _JUNK_CONDITIONS)
def test_condition_issues_never_raises(cond: Any) -> None:
    issues = conditions.condition_issues(cond)
    assert isinstance(issues, list)
    assert all(isinstance(i, str) and i for i in issues)


def test_evaluate_survives_a_hostile_context() -> None:
    """A mapping whose lookups explode still resolves to "no value", not a 500 — the last-resort
    guard inside evaluate is what makes the promise unconditional."""

    class Exploding(dict[str, Any]):
        def __contains__(self, key: object) -> bool:
            raise RuntimeError("boom")

    cond = {"field": "x", "operator": "equals", "value": 1}
    assert conditions.evaluate(cond, Exploding()) is True
