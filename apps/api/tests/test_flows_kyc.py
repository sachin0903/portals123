"""Unit tests for the person-scoped KYC rules — validity + dedup selection.

These are not shelf-ware: ``ReviewHandler`` calls ``pick_valid`` for its reuse decision and
``compute_valid_until`` for the horizon it stamps, so what is asserted here is what ships.
"""

from datetime import UTC, datetime, timedelta

from app.flows.kyc import (
    KIND_AML,
    KIND_IDENTITY,
    KIND_TERMS,
    LAPSE_DAYS,
    KycCheck,
    compute_valid_until,
    is_valid,
    pick_valid,
)

_NOW = datetime(2026, 7, 24, tzinfo=UTC)


def test_is_valid_requires_a_pass_within_any_horizon() -> None:
    assert is_valid(KycCheck(1, KIND_TERMS, "passed", valid_until=_NOW + timedelta(days=1)), _NOW)
    # past its horizon
    assert not is_valid(
        KycCheck(1, KIND_TERMS, "passed", valid_until=_NOW - timedelta(days=1)), _NOW
    )
    # not passed
    assert not is_valid(KycCheck(1, KIND_IDENTITY, "referred"), _NOW)
    # an identity or AML pass has no horizon, and stands
    assert is_valid(KycCheck(1, KIND_IDENTITY, "passed"), _NOW)
    assert is_valid(KycCheck(1, KIND_AML, "passed"), _NOW)


def test_pick_valid_prefers_a_pass_with_no_horizon_then_the_furthest() -> None:
    checks = [
        KycCheck(1, KIND_IDENTITY, "passed", valid_until=_NOW + timedelta(days=10)),
        KycCheck(1, KIND_IDENTITY, "passed"),
        KycCheck(1, KIND_AML, "passed", valid_until=_NOW + timedelta(days=5)),
        KycCheck(1, KIND_IDENTITY, "failed"),
    ]
    best = pick_valid(checks, KIND_IDENTITY, _NOW)
    assert best is not None and best.valid_until is None
    dated = [
        checks[0],
        KycCheck(1, KIND_IDENTITY, "passed", valid_until=_NOW + timedelta(days=200)),
    ]
    assert pick_valid(dated, KIND_IDENTITY, _NOW).valid_until == _NOW + timedelta(days=200)  # type: ignore[union-attr]
    assert pick_valid(checks, KIND_AML, _NOW).kind == KIND_AML  # type: ignore[union-attr]
    # no valid check of a kind not present
    assert pick_valid([], KIND_IDENTITY, _NOW) is None


def test_only_a_terms_acceptance_lapses() -> None:
    assert compute_valid_until(_NOW, KIND_IDENTITY) is None
    assert compute_valid_until(_NOW, KIND_AML) is None
    assert LAPSE_DAYS == {KIND_TERMS: 3650}
    assert compute_valid_until(_NOW, KIND_TERMS) == _NOW + timedelta(days=3650)
