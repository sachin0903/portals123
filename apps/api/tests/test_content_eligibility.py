"""Audience / service-type / sub-type gates for published content."""

from datetime import UTC, datetime, timedelta

from app.content import ContactAudience, eligible

NOW = datetime(2026, 9, 9, tzinfo=UTC)
INDIVIDUAL = ContactAudience(type="individual", service_type="execution_only", sub_type=None)
ADVISED = ContactAudience(type="individual", service_type="advised", sub_type=None)
ORG = ContactAudience(type="organisation", service_type="advised", sub_type="trust")
COMPANY = ContactAudience(type="organisation", service_type="advised", sub_type="company")


def _ok(**overrides: object) -> bool:
    base: dict[str, object] = {
        "status": "published",
        "audience": "all",
        "service_types": [],
        "sub_types": [],
        "published_at": NOW - timedelta(days=1),
        "expires_at": None,
        "contact": INDIVIDUAL,
        "now": NOW,
    }
    base.update(overrides)
    return eligible(**base)  # type: ignore[arg-type]


def test_draft_and_archived_never_show() -> None:
    assert _ok(status="draft") is False
    assert _ok(status="archived") is False


def test_all_audience_matches_anyone() -> None:
    assert _ok(audience="all", contact=INDIVIDUAL) is True
    assert _ok(audience="all", contact=ORG) is True


def test_individual_audience_hides_from_orgs() -> None:
    assert _ok(audience="individual", contact=INDIVIDUAL) is True
    assert _ok(audience="individual", contact=ORG) is False


def test_organisation_audience_hides_from_individuals() -> None:
    assert _ok(audience="organisation", contact=ORG) is True
    assert _ok(audience="organisation", contact=INDIVIDUAL) is False


def test_unknown_contact_type_reads_as_individual() -> None:
    unknown = ContactAudience(type=None, service_type="advised", sub_type=None)
    assert _ok(audience="individual", contact=unknown) is True
    assert _ok(audience="organisation", contact=unknown) is False


def test_service_type_empty_means_all() -> None:
    assert _ok(service_types=[], contact=INDIVIDUAL) is True
    assert _ok(service_types=["execution_only"], contact=INDIVIDUAL) is True
    assert _ok(service_types=["execution_only"], contact=ADVISED) is False


def test_sub_type_only_applies_to_organisations() -> None:
    assert _ok(sub_types=["trust"], contact=INDIVIDUAL) is True
    assert _ok(sub_types=["trust"], contact=ORG) is True
    assert _ok(sub_types=["trust"], contact=COMPANY) is False


def test_future_publish_and_expiry() -> None:
    assert _ok(published_at=NOW + timedelta(days=1)) is False
    assert _ok(expires_at=NOW - timedelta(hours=1)) is False
    assert _ok(expires_at=NOW + timedelta(days=1)) is True
