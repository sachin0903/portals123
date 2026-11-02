"""A lodged SEI activity is read back onto the Graphene row, without posting it."""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations.sei.fees import choose_fee_type
from app.integrations.sei.receipt_status import activity_snapshot, refresh_submitted

pytestmark = pytest.mark.anyio


def test_an_activity_read_keeps_seis_own_status_and_state() -> None:
    snapshot = activity_snapshot(
        {
            "activityId": "1012160685",
            "activityStatus": 7,
            "activityStatusDescription": "In Process",
            "activityState": 307,
            "activityStateDescription": "Waiting for Matching",
        }
    )
    assert snapshot == {
        "activity_id": "1012160685",
        "activity_status": "7",
        "activity_status_description": "In Process",
        "activity_state": "307",
        "activity_state_description": "Waiting for Matching",
    }


def test_one_initial_advice_fee_type_is_used() -> None:
    assert choose_fee_type(["84521", "84521"]) == "84521"


def test_two_fee_types_are_not_guessed() -> None:
    assert choose_fee_type(["84521", "84522"]) is None


def test_an_ad_hoc_fee_is_a_direct_charge_on_the_portfolio() -> None:
    from datetime import date

    from app.integrations.sei.fees import build_one_time_fee

    body = build_one_time_fee(
        {
            "firm_id": "3108",
            "sei_account_number": "AIO00213",
            "sei_portfolio_id": "3108-SWP-I31-59103919",
            "currency": "GBP",
            "amount": 99,
            "fee_type": "84522",
            "due_date": date(2026, 10, 16),
        }
    )
    assert body == {
        "firmId": "3108",
        "accountNumber": "AIO00213",
        "portfolioId": "59103919",
        "collectionMethod": "1",
        "currencyCode": "GBP",
        "feeAmount": "99.00",
        "feeType": "84522",
        "collectionDate": "10/16/2026",
    }


def test_an_ad_hoc_fee_without_a_date_is_collected_today() -> None:
    from datetime import date

    from app.integrations.sei.fees import build_one_time_fee

    body = build_one_time_fee(
        {
            "firm_id": "3108",
            "sei_account_number": "AIO00213",
            "sei_portfolio_id": "59103919",
            "amount": "5.1",
            "fee_type": "84522",
        },
        today=date(2026, 10, 16),
    )
    assert body["collectionDate"] == "10/16/2026"
    assert body["feeAmount"] == "5.10"


def test_an_ad_hoc_fee_names_what_sei_still_needs() -> None:
    from app.integrations.sei.fees import explain_missing_one_time_fee

    detail = explain_missing_one_time_fee({"firm_id": "3108"})
    assert detail is not None
    assert "account number" in detail
    assert "portfolio" in detail
    assert "fee type" in detail


def test_a_blank_activity_field_is_left_out() -> None:
    assert "activity_state" not in activity_snapshot({"activityId": "1", "activityState": ""})


async def test_refresh_does_nothing_when_sei_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.integrations.sei.receipt_status.configured", lambda: False)

    class Session:
        async def execute(self, *_a: Any, **_k: Any) -> None:
            raise AssertionError("no query when SEI is not configured")

    assert await refresh_submitted(Session()) == 0  # type: ignore[arg-type]
