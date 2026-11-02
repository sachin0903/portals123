"""The consolidated transfer saga, and the Seccl and SEI calls that move it.

`completed` is a reconciliation. A vendor saying the movement is booked — Seccl `Completed`,
SEI code 7 — is `funds_sent`. The pin against `stages.ts` is the same discipline the
contributions stages get: one vocabulary, two languages.
"""

from typing import Any

import pytest

from app import matching, transfers
from app.integrations.seccl import transfers as seccl
from app.integrations.sei import transfers as sei


class TestCrosswalks:
    def test_seccl_completed_is_with_the_custodian(self) -> None:
        assert transfers.seccl_stage("Completed") == "funds_sent"
        assert transfers.seccl_stage("With Previous Provider") == "submitted"
        assert transfers.seccl_stage("Onboarding Assets") == "in_progress"
        assert transfers.seccl_stage("Awaiting documentation") == "in_progress"
        # A cancellation request is not the transfer moving on to the ceding scheme.
        assert transfers.seccl_stage("Pending Cancellation") == "submitted"
        assert transfers.seccl_stage("Rejected") == "cancelled"
        assert (
            transfers.seccl_stage("Awaiting Crystallisation Information") == "manual_intervention"
        )
        assert transfers.seccl_stage("something new") is None
        assert transfers.synced_stage("seccl", "With Previous Provider") == "submitted"
        # A stock migration has no transfer status. The transaction's own status decides:
        # completed assets are with the custodian; a still-open line was submitted.
        assert transfers.synced_stage("seccl", None, "completed") == "funds_sent"
        assert transfers.synced_stage("seccl", None, "pending") == "submitted"
        assert transfers.synced_stage("seccl", None, "deleted") == "cancelled"
        assert transfers.synced_stage("seccl", None) == "submitted"
        # A published transfer status wins over the transaction status.
        assert transfers.synced_stage("seccl", "With Previous Provider", "completed") == "submitted"
        assert transfers.synced_stage("seccl", "Completed", "pending") == "funds_sent"
        assert transfers.synced_stage("sei", "7") == "funds_sent"
        # A posted SEI ledger row has no transfer-status code and no lifecycle. The
        # transactions listing shows that blank as settled; the workbench was calling
        # it Submitted. An explicit pending status is still open, and a pending-transfer
        # code still wins over the ledger.
        assert transfers.synced_stage("sei", None) == "funds_sent"
        assert transfers.synced_stage("sei", None, "settled") == "funds_sent"
        assert transfers.synced_stage("sei", None, "pending") == "submitted"
        assert transfers.synced_stage("sei", "2", "settled") == "submitted"

    def test_sei_complete_is_with_the_custodian(self) -> None:
        assert transfers.sei_stage("7") == "funds_sent"
        assert transfers.sei_stage("COMPLETE") == "funds_sent"
        assert transfers.sei_stage("2") == "submitted"
        assert transfers.sei_stage("IN PROCESS - SUBMITTED TO COUNTERPARTY") == "submitted"
        assert transfers.sei_stage("3") == "in_progress"
        assert transfers.sei_stage("10") == "in_progress"
        assert transfers.sei_stage("1") == "manual_intervention"
        assert transfers.sei_stage("9") == "cancelled"
        assert transfers.sei_stage("18") == "cancelled"

    def test_a_vendor_cannot_write_completed(self) -> None:
        assert transfers.next_status("funds_sent", "completed") == "funds_sent"
        assert transfers.next_status("in_progress", "funds_sent") == "funds_sent"
        assert transfers.next_status("funds_sent", "submitted") == "funds_sent"
        assert transfers.next_status("in_progress", "cancelled") == "cancelled"
        assert transfers.next_status("completed", "manual_intervention") == "completed"
        assert transfers.next_status("completed", "cancelled") == "cancelled"

    def test_route_follows_the_receiving_account(self) -> None:
        assert (
            transfers.route_of(
                custodian="sei", scheme_administrator="morgan-lloyd", is_pension=True
            )
            == "ml_sei"
        )
        assert (
            transfers.route_of(
                custodian="sei", scheme_administrator="morgan-lloyd", is_pension=False
            )
            == "sei"
        )
        assert (
            transfers.route_of(
                custodian="seccl", scheme_administrator="morgan-lloyd", is_pension=True
            )
            == "seccl"
        )
        assert transfers.route_of(custodian="seccl", scheme_administrator=None) == "seccl"
        assert (
            transfers.route_of(custodian="sei", scheme_administrator=None, is_pension=True) == "sei"
        )

    def test_a_synced_sei_cancellation_does_not_cancel_a_morgan_lloyd_transfer(self) -> None:
        assert (
            transfers.synced_status(route="ml_sei", current="funds_sent", target="cancelled")
            == "funds_sent"
        )
        assert (
            transfers.synced_status(route="sei", current="submitted", target="cancelled")
            == "cancelled"
        )
        assert transfers.synced_status(route="seccl", current="requested", target="submitted") == (
            "submitted"
        )

    def test_an_ambiguous_ceding_name_is_not_sent_to_seccl(self) -> None:
        with pytest.raises(ValueError, match="More than one ceding scheme"):
            transfers.chosen_masterparty(["MP1", "MP2"], route="seccl")
        assert transfers.chosen_masterparty(["MP1", "MP2"], route="sei") is None
        assert transfers.chosen_masterparty(["MP1", "MP1"], route="seccl") == "MP1"

    def test_the_chain_names_seccl_as_its_own_scheme_administrator(self) -> None:
        assert (
            transfers.displayed_scheme_administrator(
                custodian="seccl", scheme_administrator="morgan-lloyd", is_pension=True
            )
            == "seccl"
        )
        assert (
            transfers.displayed_scheme_administrator(
                custodian="sei", scheme_administrator="morgan-lloyd", is_pension=True
            )
            == "morgan-lloyd"
        )
        assert (
            transfers.displayed_scheme_administrator(
                custodian="sei", scheme_administrator="morgan-lloyd", is_pension=False
            )
            is None
        )

    @pytest.mark.pkgs_ui_pin
    def test_the_stages_match_the_portal_exactly(self) -> None:
        import re
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[3]
            / "pkgs/ui/src/lib/components/book/transfers/stages.ts"
        ).read_text(encoding="utf-8")
        block = source[source.index("export const STAGES") : source.index("/** The six the KPI")]
        portal = {
            key: tuple(re.findall(r"'([a-z_]+)'", statuses))
            for key, statuses in re.findall(
                r"key: '([a-z_]+)',[\s\S]*?statuses: \[([^\]]*)\]", block
            )
        }
        assert portal == transfers.STAGES
        assert list(transfers.METRIC_STAGES) == [
            "requested",
            "submitted",
            "in_progress",
            "assets_received",
            "funds_sent",
            "manual_intervention",
        ]


def _seccl_row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "uid": "trf000000001",
        "seccl_account_id": "ACC1",
        "masterparty_id": "MP1",
        "total_value": 25000,
        "basis": "cash",
        "transfer_type": "gia",
        "policy_number": "AV-1",
        "data": {
            "provider_address_line1": "1 Scheme Street",
            "provider_postcode": "EC2A 1AA",
            "provider_country": "GB",
        },
    }
    row.update(over)
    return row


class TestInstruments:
    def test_seccl_stock_lines_keep_their_own_status(self) -> None:
        lines = matching.normalise_instruments(
            [
                {
                    "name": "Fidelity Index UK Fund P Acc",
                    "code": "GB00BJS8SF95",
                    "quantity": 6332.25,
                    "status": "completed",
                }
            ]
        )
        assert lines == [
            {
                "name": "Fidelity Index UK Fund P Acc",
                "code": "GB00BJS8SF95",
                "quantity": 6332.25,
                "status": "completed",
            }
        ]

    def test_sei_advised_lines_have_units_and_no_status(self) -> None:
        lines = matching.normalise_instruments(
            [],
            [{"identifier": "GB00B0000001", "identifier_type": "ISIN", "units": "10"}],
        )
        assert lines == [{"name": None, "code": "GB00B0000001", "quantity": 10.0, "status": None}]


class TestSecclAdapter:
    def test_a_gia_is_not_a_safeguarded_pension(self) -> None:
        body = seccl.build_product_transfer(_seccl_row())
        assert body["externalReference"] == "trf000000001"
        assert body["transactionSubType"] == "Cash"
        assert "isPensionTransfer" not in body
        assert body["transferAccountAddress"]["country"] == "GB"

    def test_safeguarded_rights_are_what_is_pension_transfer_means(self) -> None:
        body = seccl.build_product_transfer(
            _seccl_row(
                transfer_type="pension",
                crystallisation="uncrystallised",
                safeguarded_benefits=True,
                data={
                    "provider_address_line1": "1 Scheme Street",
                    "provider_postcode": "EC2A 1AA",
                    "provider_country": "GB",
                    "fca_reference": "123456",
                },
            )
        )
        assert body["isPensionTransfer"] is True
        assert body["productDrawdownStatus"] == "No Drawdown"
        assert body["fcaReference"] == "123456"

    def test_a_missing_address_is_named(self) -> None:
        with pytest.raises(ValueError, match="address"):
            seccl.build_product_transfer(_seccl_row(data={}))

    async def test_place_sends_the_transfer_uid_as_the_external_reference(
        self, monkeypatch
    ) -> None:
        class _Api:
            def __init__(self) -> None:
                self.body: list[dict[str, Any]] = []

            async def __aenter__(self) -> "_Api":
                return self

            async def __aexit__(self, *_exc: object) -> None:
                return None

            async def create_transaction_group(
                self, account_id: str, transactions: list[dict[str, Any]]
            ) -> dict[str, str]:
                assert account_id == "ACC1"
                self.body = transactions
                return {"linkId": "LINK1"}

            async def transaction_group(self, link_id: str) -> list[dict[str, str]]:
                assert link_id == "LINK1"
                return [
                    {
                        "id": "TX1",
                        "transactionType": "Product Transfer",
                        "transferStatus": "Pending",
                    }
                ]

        api = _Api()
        monkeypatch.setattr(seccl, "_client", lambda: api)
        identifier, observed = await seccl.place_transfer(_seccl_row())
        assert identifier == "TX1"
        assert observed == "Pending"
        assert api.body[0]["externalReference"] == "trf000000001"

    def test_a_blank_basis_is_not_in_specie(self) -> None:
        with pytest.raises(ValueError, match="cash or in-specie"):
            seccl.build_product_transfer(_seccl_row(basis=""))

    async def test_a_failed_follow_up_read_still_returns_the_link_id(self, monkeypatch) -> None:
        class _Api:
            async def __aenter__(self) -> "_Api":
                return self

            async def __aexit__(self, *_exc: object) -> None:
                return None

            async def create_transaction_group(self, _account_id: str, _transactions: list) -> dict:
                return {"linkId": "LINK1"}

            async def transaction_group(self, _link_id: str) -> list:
                raise RuntimeError("follow-up read failed")

        monkeypatch.setattr(seccl, "_client", lambda: _Api())
        identifier, observed = await seccl.place_transfer(_seccl_row())
        assert identifier == "LINK1"
        assert observed is None

    async def test_a_create_with_no_id_is_not_a_retryable_refusal(self, monkeypatch) -> None:
        class _Api:
            async def __aenter__(self) -> "_Api":
                return self

            async def __aexit__(self, *_exc: object) -> None:
                return None

            async def create_transaction_group(self, _account_id: str, _transactions: list) -> dict:
                return {}

        monkeypatch.setattr(seccl, "_client", lambda: _Api())
        with pytest.raises(seccl.SecclPlacedWithoutId):
            await seccl.place_transfer(_seccl_row())

    def test_a_feed_row_cancels_only_while_seccl_will_take_it(self) -> None:
        assert transfers.feed_cancel_allowed(
            {
                "source_system": "seccl",
                "source_type": "Product Transfer",
                "transfer_status": "With Previous Provider",
            }
        )
        assert not transfers.feed_cancel_allowed(
            {
                "source_system": "seccl",
                "source_type": "Stock Transfer",
                "transfer_status": None,
            }
        )
        assert not transfers.feed_cancel_allowed(
            {
                "source_system": "seccl",
                "source_type": "Product Transfer",
                "transfer_status": "Onboarding Assets",
            }
        )
        assert transfers.sortable_amount({"total_value": 10, "transaction_amount": None}) == 10
        assert transfers.sortable_amount({"amount": 99, "total_value": 10}) == 10
        assert transfers.sortable_amount({"transaction_amount": 4, "total_value": 10}) == 4
        assert transfers.sortable_amount({}) is None

    def test_cancel_refuses_once_assets_are_onboarding(self) -> None:
        with pytest.raises(seccl.TransferNotCancellable):
            # The status check runs before any client is opened.
            import asyncio

            asyncio.run(
                seccl.cancel_transfer(
                    {"seccl_transfer_id": "T1"}, current_status="Onboarding Assets"
                )
            )


def _sei_row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "uid": "trf000000002",
        "sei_account_number": "ACC9",
        "sei_portfolio_id": "PF9",
        "ceding_provider": "Aviva",
        "policy_number": "AV-9",
        "basis": "in_specie",
        "extent": "full",
        "transfer_type": "gia",
        "client_name": "Sophie Halloway",
        "sei_config": {
            "firm_id": "42",
            "platform_user": "graphene",
            "transfer_description_code": "TFR",
            "transfer_treatment_code": "IN",
        },
        "data": {
            "signing_authority_tax_id": "AB123456C",
            "assets": [
                {
                    "identifier": "GB00B0000001",
                    "identifier_type": "ISIN",
                    "issue_country": "GB",
                    "units": "10",
                }
            ],
            "documents": [{"document_reference_id": "DOC1"}],
            "external_account_type": "Individual",
        },
    }
    row.update(over)
    return row


class TestSeiAdapter:
    def test_free_receipt_uses_the_vendor_spelling(self) -> None:
        body = sei.build_free_receipt(
            _sei_row(),
            {"firm_id": "42", "platform_user": "graphene"},
        )
        assert body["platforUser"] == "graphene"
        assert "platformUser" not in body

    def test_a_uk_clients_national_insurance_number_is_the_signing_tax_id(self) -> None:
        row = _sei_row(
            national_identifier="QQ123456C",
            national_identifier_type="nino",
        )
        data = dict(row["data"])
        data.pop("signing_authority_tax_id")
        row["data"] = data
        body = sei.build_transfer_in(row, {"platform_user": "graphene"})
        assert body["primarySigningAuthority"]["externalPrimaryTaxInfo"] == "QQ123456C"
        assert body["externalAccount"]["externalAccountType"] == "Individual"
        assert body["externalAccount"]["externalAccountType"] != row["transfer_type"]

    def test_a_wrapper_is_not_an_sei_account_type(self) -> None:
        row = _sei_row()
        data = dict(row["data"])
        data.pop("external_account_type")
        row["data"] = data
        with pytest.raises(ValueError, match="external_account_type"):
            sei.build_transfer_in(row, {"platform_user": "graphene"})

    def test_a_transfer_in_without_a_national_insurance_number_names_it(self) -> None:
        row = _sei_row()
        data = dict(row["data"])
        data.pop("signing_authority_tax_id")
        row["data"] = data
        with pytest.raises(ValueError, match="National Insurance number"):
            sei.build_transfer_in(row, {"platform_user": "graphene"})

    def test_a_transfer_in_without_holdings_omits_asset_details(self) -> None:
        row = _sei_row()
        data = dict(row["data"])
        data.pop("assets")
        row["data"] = data
        body = sei.build_transfer_in(row, {"platform_user": "graphene"})
        assert "assetDetails" not in body

    def test_a_partial_transfer_in_omits_asset_details(self) -> None:
        row = _sei_row()
        row["extent"] = "partial"
        body = sei.build_transfer_in(row, {"platform_user": "graphene"})
        assert "assetDetails" not in body
        assert body["assetTransferInstruction"]["assetTransferInstructionId"] == 2

    def test_transfer_in_names_a_missing_form(self) -> None:
        row = _sei_row()
        row["data"] = {**row["data"], "documents": []}
        with pytest.raises(ValueError, match="document_reference_id"):
            sei.build_transfer_in(row, {"platform_user": "graphene"})

    async def test_a_poll_with_two_rows_and_no_activity_id_is_refused(self, monkeypatch) -> None:
        async def rows(*_args: Any, **_kwargs: Any) -> list[dict[str, str]]:
            return [
                {"transferActivityId": "A", "transferStatusCode": "2"},
                {"transferActivityId": "B", "transferStatusCode": "7"},
            ]

        monkeypatch.setattr(sei, "_call", rows)
        with pytest.raises(ValueError, match="activity id"):
            await sei.poll_pending(_sei_row(route="sei"))

    async def test_a_single_pending_row_is_not_adopted_without_an_activity_id(
        self, monkeypatch
    ) -> None:
        async def rows(*_args: Any, **_kwargs: Any) -> list[dict[str, str]]:
            return [{"transferActivityId": "A", "transferStatusCode": "2"}]

        monkeypatch.setattr(sei, "_call", rows)
        with pytest.raises(ValueError, match="activity id"):
            await sei.poll_pending(_sei_row(route="sei"))

    async def test_a_cash_receipt_is_not_read_as_a_pending_transfer(self, monkeypatch) -> None:
        async def called(*_args: Any, **_kwargs: Any) -> list[dict[str, str]]:
            raise AssertionError("pending-transfer status was called")

        monkeypatch.setattr(sei, "_call", called)
        with pytest.raises(ValueError, match="transfer in"):
            await sei.poll_pending(_sei_row(route="sei", basis="cash", sei_activity_id="ACR1"))

    async def test_a_free_receipt_is_not_read_as_a_pending_transfer(self, monkeypatch) -> None:
        async def called(*_args: Any, **_kwargs: Any) -> list[dict[str, str]]:
            raise AssertionError("pending-transfer status was called")

        monkeypatch.setattr(sei, "_call", called)
        with pytest.raises(ValueError, match="transfer in"):
            await sei.poll_pending(_sei_row(route="ml_sei", sei_activity_id="FR1"))

    async def test_code_7_is_reported_as_a_code_not_completed(self, monkeypatch) -> None:
        async def rows(*_args: Any, **_kwargs: Any) -> list[dict[str, str]]:
            return [
                {
                    "transferActivityId": "A",
                    "transferStatusCode": "7",
                    "transferStatus": "COMPLETE",
                }
            ]

        monkeypatch.setattr(sei, "_call", rows)
        observed = await sei.poll_pending(_sei_row(route="sei", sei_activity_id="A"))
        assert observed["status_code"] == "7"
        assert transfers.sei_stage(observed["status_code"]) == "funds_sent"
