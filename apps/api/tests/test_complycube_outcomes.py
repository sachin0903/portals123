"""ComplyCube status mapping: provider → compliance.checks → crm.kyc_checks."""

from __future__ import annotations

import pytest

from app.complycube.outcomes import (
    KycOutcome,
    is_technical_failure,
    map_status,
    monitoring_covers,
    next_attempt,
    partner_status,
    provider_outcome,
    public_status,
    to_kyc_status,
)


def test_provider_status_maps_onto_our_vocabulary() -> None:
    assert map_status("pending", None) == "pending"
    assert map_status(None, None) == "pending"
    assert map_status("complete", "clear") == "clear"
    assert map_status("complete", "attention") == "refer"
    assert map_status("complete", "rejected") == "rejected"
    assert map_status("complete", "match_confirmed") == "refer"
    # An outcome we do not know is never silently clear.
    assert map_status("complete", "something_new") == "refer"
    assert map_status("complete", None) == "refer"


def test_a_technical_failure_is_not_covered_by_monitoring() -> None:
    assert monitoring_covers("clear", True)
    assert monitoring_covers("refer", True)
    assert not monitoring_covers("clear", False)
    assert not monitoring_covers("failed", True)


def test_a_provider_failure_is_a_technical_failure_not_a_rejection() -> None:
    assert map_status("failed", None) == "failed"
    assert map_status("failed", "clear") == "failed"
    assert is_technical_failure("failed")
    assert not is_technical_failure("rejected")


def test_an_issuing_country_outside_the_nationalities_refers_a_clear_identity() -> None:
    assert (
        public_status("complete", "clear", issuing_country="US", nationalities=["GB", "IE"])
        == "refer"
    )
    assert public_status("complete", "clear", issuing_country="gb", nationalities=["GB"]) == "clear"
    assert public_status("complete", "clear", issuing_country=None, nationalities=["GB"]) == "clear"


def test_the_outcome_is_read_from_the_top_level_or_the_result() -> None:
    assert provider_outcome({"outcome": "clear"}) == "clear"
    assert provider_outcome({"result": {"outcome": "attention"}}) == "attention"
    assert provider_outcome({"outcome": "", "result": {"outcome": "rejected"}}) == "rejected"
    assert provider_outcome({}) is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("clear", KycOutcome("passed")),
        ("refer", KycOutcome("referred")),
        ("rejected", KycOutcome("failed")),
        ("failed", KycOutcome("pending", retryable=True)),
        ("pending", KycOutcome("pending")),
    ],
)
def test_to_kyc_status(status: str, expected: KycOutcome) -> None:
    assert to_kyc_status(status) == expected


def test_only_a_verification_failure_counts_as_a_try() -> None:
    assert not to_kyc_status("failed").counts_as_attempt
    assert to_kyc_status("rejected").counts_as_attempt
    assert next_attempt(1, "failed") == 1
    assert next_attempt(1, "rejected") == 2
    assert next_attempt(2, "clear") == 3


def test_an_unknown_status_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown screening status"):
        to_kyc_status("approved")


def test_a_technical_failure_is_never_a_verdict_on_the_kyc_row() -> None:
    assert to_kyc_status("failed").status == "pending"


def test_the_firm_api_vocabulary_has_no_technical_failure() -> None:
    # Not a verdict: the firm reads it as still pending while operations runs it again.
    assert partner_status("failed") == "pending"
    for status in ("pending", "clear", "refer", "rejected"):
        assert partner_status(status) == status
    assert partner_status("anything") == "pending"
