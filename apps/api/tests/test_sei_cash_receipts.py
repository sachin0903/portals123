"""The SEI seam: what we need before a payment expectation can be lodged, and what we send.

The point of `missing_fields` is that it refuses rather than substitutes. Three of SEI's
required values are things only SEI can tell us (the account's SEI ids, and the firm's
description/treatment reference codes), so the failure mode that matters is naming them
precisely instead of guessing a code and lodging a misclassified receipt on a client's account.
"""

from pathlib import Path

import pytest

from app.integrations import sei

COMPLETE = {
    "sei_account_number": "USB0042817",
    "sei_portfolio_id": "1234567",
    "firm_id": "3102",
    "description_code": "1091",
    "treatment_code": "4402",
    "category": "personal_contribution",
    "amount": 1250.0,
    "currency": "GBP",
    "tax_year": "2026/27",
    "reference": "con_abc",
}


class TestMissingFields:
    def test_a_complete_context_has_nothing_missing(self) -> None:
        assert sei.missing_fields(COMPLETE) == []

    def test_names_the_unlinked_sei_ids(self) -> None:
        absent = sei.missing_fields(
            {**COMPLETE, "sei_account_number": None, "sei_portfolio_id": None}
        )
        assert absent == [
            "the account's SEI account number (links, provider 'sei')",
            "the account's SEI portfolio id (links, link type 'portfolio')",
        ]

    def test_names_the_firm_id_which_a_po_level_caller_must_send(self) -> None:
        absent = sei.missing_fields({**COMPLETE, "firm_id": None})
        assert absent == ["the tenant's SEI firm id (provider config 'firm_id')"]

    def test_names_the_reference_codes_and_where_they_are_configured(self) -> None:
        absent = sei.missing_fields({**COMPLETE, "description_code": None, "treatment_code": None})
        assert sei.CONFIG_DESCRIPTION_CODE in absent[0]
        assert sei.CONFIG_TREATMENT_CODES in absent[1]
        # The treatment code is per category, so the message says which one is unconfigured.
        assert "personal_contribution" in absent[1]

    def test_an_isa_does_not_need_a_contribution_category(self) -> None:
        context = {
            **COMPLETE,
            "category": "",
            "treatment_code": "1401",
            "description_code": "20009",
            "product_type": "ISA",
            "is_pension": False,
        }
        assert sei.missing_fields(context) == []

    def test_a_payment_with_no_category_says_so(self) -> None:
        absent = sei.missing_fields({**COMPLETE, "category": "", "treatment_code": None})
        detail = " ".join(absent)
        assert "contribution category" in detail
        assert "for payment" not in detail

    def test_an_isa_without_codes_does_not_ask_for_a_pension_category(self) -> None:
        absent = sei.missing_fields(
            {
                **COMPLETE,
                "product_type": "ISA",
                "is_pension": False,
                "treatment_code": None,
                "category": "",
            }
        )
        detail = " ".join(absent)
        assert "contribution category" not in detail
        assert "ISA" in detail


class TestResolveCodes:
    def test_pulls_the_per_category_treatment_code(self) -> None:
        config = {
            "firm_id": "3102",
            "contribution_description_code": "1091",
            "contribution_treatment_codes": {
                "personal": "4402",
                "employer": "4403",
                "third_party": "4404",
            },
        }
        assert sei.resolve_codes(config, "employer_contribution")["treatment_code"] == "4403"
        assert sei.resolve_codes(config, "personal_contribution")["treatment_code"] == "4402"

    def test_an_isa_uses_the_subscription_pair_and_ignores_the_pension_map(self) -> None:
        config = {"firm_id": "3108", "contribution_treatment_codes": {"personal": "4958"}}
        resolved = sei.resolve_codes(config, "", product_type="ISA", is_pension=False)
        assert resolved["description_code"] == "20009"
        assert resolved["treatment_code"] == "1401"
        gia = sei.resolve_codes(config, "", product_type="GIA", is_pension=False)
        assert gia["treatment_code"] == "1451"

    def test_a_category_with_no_sei_treatment_resolves_to_none(self) -> None:
        # transfer_in is not a contribution at SEI either; it has no mapped treatment code.
        config = {"contribution_treatment_codes": {"personal": "4402"}}
        assert sei.resolve_codes(config, "transfer_in")["treatment_code"] is None

    def test_an_unconfigured_provider_yields_nothing_rather_than_raising(self) -> None:
        assert sei.resolve_codes(None, "personal_contribution") == {
            "firm_id": None,
            "description_code": None,
            "treatment_code": None,
        }

    def test_a_malformed_treatment_map_is_tolerated(self) -> None:
        # config is operator-edited jsonb; a string where a map belongs must not 500.
        assert (
            sei.resolve_codes({"contribution_treatment_codes": "4402"}, "personal_contribution")[
                "treatment_code"
            ]
            is None
        )


class TestBuildReceipt:
    def test_builds_an_anticipated_receipt(self) -> None:
        pytest.importorskip("sei")
        from datetime import date

        attributes = sei.build_receipt({**COMPLETE, "due_date": date(2026, 9, 1)})
        payload = attributes.model_dump(by_alias=True, exclude_none=True)

        assert payload["accountNumber"] == "USB0042817"
        assert payload["portfolioId"] == "1234567"
        assert payload["firmId"] == "3102"
        instructions = payload["receiptInstructions"]
        assert instructions["currencyCode"] == "GBP"
        assert instructions["descriptionTypeCode"] == "1091"
        assert instructions["treatmentTypeCode"] == "4402"
        assert instructions["treatmentCodeTaxYear"] == "2026/27"
        # Amounts are strings on the wire; SEI rejects a JSON number.
        assert instructions["receiptAmount"] == "1250.00"
        # No payerInfo: that is what makes it anticipated rather than a Direct Debit.
        assert "payerInfo" not in instructions
        assert payload["processingScheduleDetails"]["once"]["dueDate"] == "2026-09-01"

    def test_a_global_portfolio_id_is_sent_as_the_entity_id(self) -> None:
        pytest.importorskip("sei")
        attributes = sei.build_receipt({**COMPLETE, "sei_portfolio_id": "3108-SWP-I31-59103919"})
        payload = attributes.model_dump(by_alias=True, exclude_none=True)
        assert payload["portfolioId"] == "59103919"

    def test_the_contribution_uid_travels_as_the_external_reference(self) -> None:
        """So a credit landing at SEI ties back without amount+date guesswork."""
        pytest.importorskip("sei")
        attributes = sei.build_receipt(COMPLETE)
        payload = attributes.model_dump(by_alias=True, exclude_none=True)
        assert payload["receiptInstructions"]["externalReference"] == "con_abc"

    def test_an_isa_receipt_carries_the_subscription_amount(self) -> None:
        pytest.importorskip("sei")
        attributes = sei.build_receipt({**COMPLETE, "product_type": "ISA", "amount": 100})
        instructions = attributes.model_dump(by_alias=True, exclude_none=True)[
            "receiptInstructions"
        ]
        assert instructions["isaContribution"] == "100.00"
        assert instructions["treatmentTypeCode"] == "4402"

    def test_an_adviser_fee_is_attached_to_the_receipt(self) -> None:
        pytest.importorskip("sei")
        attributes = sei.build_receipt(
            {**COMPLETE, "adviser_fee": 5.12, "fee_description_id": "84521"}
        )
        charges = attributes.model_dump(by_alias=True, exclude_none=True)["receiptInstructions"][
            "feesAndCharges"
        ]
        assert charges == [
            {
                "feeDescriptionId": "84521",
                "feeAmountType": "1",
                "overridenFeeAmount": "5.12",
            }
        ]

    def test_a_payment_without_a_fee_does_not_send_one(self) -> None:
        pytest.importorskip("sei")
        instructions = sei.build_receipt(COMPLETE).model_dump(by_alias=True, exclude_none=True)[
            "receiptInstructions"
        ]
        assert "feesAndCharges" not in instructions

    def test_a_gia_receipt_does_not_send_an_isa_subscription(self) -> None:
        pytest.importorskip("sei")
        attributes = sei.build_receipt({**COMPLETE, "product_type": "GIA"})
        instructions = attributes.model_dump(by_alias=True, exclude_none=True)[
            "receiptInstructions"
        ]
        assert "isaContribution" not in instructions

    def test_no_due_date_means_no_schedule_block(self) -> None:
        pytest.importorskip("sei")
        attributes = sei.build_receipt({**COMPLETE, "due_date": None})
        payload = attributes.model_dump(by_alias=True, exclude_none=True)
        assert "processingScheduleDetails" not in payload


class TestCreateAnticipatedReceipt:
    async def test_refuses_before_reaching_the_network_when_details_are_missing(self) -> None:
        with pytest.raises(ValueError) as exc:
            await sei.create_anticipated_receipt({**COMPLETE, "treatment_code": None})
        assert "SEI requires these details" in str(exc.value)

    async def test_unconfigured_credentials_are_not_a_client_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """503, not 502: no credentials is a deployment state.

        **`chdir` is load-bearing, not tidiness.** `_settings()` fills gaps from a
        pydantic-settings read of `.env`/`.env.local`, and those are RELATIVE paths — so
        deleting the environment variables is not enough to make a developer's real credentials
        invisible, and the working directory decides whether this test is testing anything.

        It was passing for the wrong reason: the gap-fill list named `pfx_path`, so a
        `SEI_PFX_BASE64` sitting in `apps/api/.env.local` (where every other API secret lives)
        was never read at all. Fixing that list turned this into a live call against a real
        firm — which is the failure that found the bug, and the reason the isolation is now
        explicit rather than incidental.
        """
        pytest.importorskip("sei")
        for name in (
            "SEI_PFX_BASE64",
            "SEI_PFX_PASSWORD",
            "SEI_OAUTH_USERNAME",
            "SEI_OAUTH_PASSWORD",
            "SEI_APPKEY",
            "SEI_I31_PFX_BASE64",
            "SEI_I31_PFX_PASSWORD",
            "SEI_I31_OAUTH_USERNAME",
            "SEI_I31_OAUTH_PASSWORD",
            "SEI_I31_APPKEY",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(sei.SeiNotConfigured):
            await sei.create_anticipated_receipt(COMPLETE)

    async def test_a_4xx_is_a_definite_refusal_and_a_5xx_is_ambiguous(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The receipt lodge must split a definite refusal (4xx, ambiguous=False → the payment path
        parks it) from a lost/transient answer (5xx or no status, ambiguous=True → it may retry),
        the same split the disbursement path already makes."""
        pytest.importorskip("sei")
        import sei as sei_pkg

        from app.integrations.sei import cash_receipts as cr

        class _FakeApi:
            def __init__(self, status: int) -> None:
                self._status = status

            async def __aenter__(self) -> "_FakeApi":
                return self

            async def __aexit__(self, *exc: object) -> bool:
                return False

            async def create_currency_receipt(self, attributes: object) -> object:
                raise sei_pkg.SeiApiError("refused", status=self._status)

        class _Settings:
            configured = True

        monkeypatch.setattr(cr, "_settings", lambda environment=None: _Settings())
        for status, ambiguous in ((400, False), (503, True)):
            monkeypatch.setattr(sei_pkg, "SeiClient", lambda _s, st=status: _FakeApi(st))
            with pytest.raises(cr.SeiUnavailable) as exc:
                await cr.create_anticipated_receipt(COMPLETE)
            assert exc.value.ambiguous is ambiguous, status


class TestMoneyOut:
    def test_a_lost_answer_is_not_a_refusal(self) -> None:
        from app.integrations.sei.cash_receipts import (
            SeiUnavailable,
            money_out_held,
            response_was_lost,
        )

        assert response_was_lost(None) is True
        assert response_was_lost(503) is True
        assert response_was_lost(400) is False
        assert SeiUnavailable("down").ambiguous is True
        assert SeiUnavailable("no", ambiguous=False).ambiguous is False
        assert money_out_held({"sei_inflight": True}) is True
        assert money_out_held({}) is False
        assert money_out_held(None) is False


class TestDisbursement:
    def test_a_bacs_withdrawal_names_the_pay_to_and_the_isa_treatment(self) -> None:
        from app.integrations.sei.cash_receipts import build_disbursement

        body = build_disbursement(
            {
                "sei_account_number": "AIO00084",
                "sei_portfolio_id": "58532529",
                "description_code": "20317",
                "treatment_code": "2410",
                "amount": 50,
                "currency": "GBP",
                "pay_to_profile_id": "59720304",
                "reference": "ins_abc",
            }
        )
        assert body["treatmentTypeCode"] == "2410"
        assert body["amountType"] == {"grossValue": "50.00"}
        assert body["payToDetails"] == {"payToProfileId": "59720304"}
        assert body["paymentType"]["bacs"]["bacsPaymentMethod"] == 5
        assert "processingScheduleDetails" not in body

    def test_faster_payment_and_swift_name_their_own_method(self) -> None:
        from app.integrations.sei.cash_receipts import build_disbursement

        shared = {
            "sei_account_number": "AIO00084",
            "sei_portfolio_id": "58532529",
            "description_code": "20317",
            "treatment_code": "2410",
            "amount": 50,
            "pay_to_profile_id": "59720304",
            "reference": "ins_abc",
        }
        faster = build_disbursement({**shared, "payment_method": "FASTERPAYMENT"})
        assert faster["paymentType"]["fasterPayment"]["fasterPaymentPaymentMethod"] == 10
        assert "bacs" not in faster["paymentType"]
        swift = build_disbursement({**shared, "payment_method": "SWIFT"})
        assert swift["paymentType"] == {"swift": 7}

    def test_an_adviser_fee_rides_the_disbursement_and_auto_disinvest_raises_cash(self) -> None:
        from app.integrations.sei.cash_receipts import build_disbursement

        shared = {
            "sei_account_number": "AIO00084",
            "sei_portfolio_id": "58532529",
            "description_code": "20317",
            "treatment_code": "2410",
            "amount": 50,
            "pay_to_profile_id": "59720304",
            "reference": "ins_abc",
        }
        plain = build_disbursement(shared)
        assert "feesAndChargesDetails" not in plain
        assert "processingDateOptions" not in plain
        charged = build_disbursement(
            {**shared, "adviser_fee": 12.5, "fee_description_id": "81970", "raise_cash": True}
        )
        assert charged["feesAndChargesDetails"] == [
            {"feeTypeId": "81970", "overrideFeeAmount": "12.50", "isOverridden": "1"}
        ]
        assert charged["processingDateOptions"]["raiseCashMethodOptions"]["requiredRaiseCashYes"][
            "waitForRaiseCashToCompleteDetails"
        ] == {"releaseCashImmediate": True}
        # A fee with no resolved fee type is refused upstream by _attach_adviser_fee, not dropped
        # here — but build_disbursement must not emit an incomplete fee if it ever sees one.
        assert "feesAndChargesDetails" not in build_disbursement({**shared, "adviser_fee": 12.5})


class TestAdviserFeeAttach:
    """SEI charges a fee against a fee TYPE: a chosen type is used as-is; without one we fall back
    to the firm's single initial advice fee, and failing that we ask the adviser to pick a type."""

    def test_a_chosen_fee_type_is_used_without_reading_the_catalogue(self) -> None:
        from app.integrations.sei.actions import _attach_adviser_fee

        context: dict[str, object] = {"firm_id": "35210"}
        _attach_adviser_fee(context, 12.5, movement="withdrawal", fee_id="81970")
        assert context["adviser_fee"] == 12.5
        assert context["fee_description_id"] == "81970"

    def test_a_zero_fee_attaches_nothing(self) -> None:
        from app.integrations.sei.actions import _attach_adviser_fee

        context: dict[str, object] = {"firm_id": "35210"}
        _attach_adviser_fee(context, 0, fee_id="81970")
        assert "adviser_fee" not in context

    def test_no_chosen_type_and_no_default_asks_for_one(self) -> None:
        from app.actions.attempt import OperationError
        from app.integrations.sei.actions import _attach_adviser_fee

        # No firm id ⇒ the catalogue default cannot resolve, so the fee must name a type itself.
        with pytest.raises(OperationError, match="Choose the fee type"):
            _attach_adviser_fee({}, 12.5, movement="withdrawal")


class TestExplain:
    """SEI answers a rejected payload with `errors[]`; the codes are the actionable part."""

    def test_lifts_the_error_code_and_detail(self) -> None:
        pytest.importorskip("sei")
        from sei import SeiApiError

        from app.integrations.sei.cash_receipts import _explain

        exc = SeiApiError(
            "boom",
            status=400,
            errors=[{"code": "1006", "detail": "Treatment code invalid - 4402"}],
        )
        assert _explain(exc) == (
            "SEI rejected the payment expectation — 1006: Treatment code invalid - 4402"
        )

    def test_falls_back_to_the_documented_meaning_when_there_is_no_detail(self) -> None:
        pytest.importorskip("sei")
        from sei import SeiApiError

        from app.integrations.sei.cash_receipts import _explain

        exc = SeiApiError("boom", status=400, errors=[{"code": "1001"}])
        assert "The firm is invalid or missing." in _explain(exc)

    def test_a_transport_failure_reads_as_unreachable(self) -> None:
        pytest.importorskip("sei")
        from sei import SeiApiError

        from app.integrations.sei.cash_receipts import _explain

        assert _explain(SeiApiError("connection refused")).startswith("SEI could not be reached")
