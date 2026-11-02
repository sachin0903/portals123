"""Mapping a Graphene contribution onto `cam_createcontribution`.

Three mappings do the real work and each loses information if it is wrong: five Graphene
categories onto Morgan Lloyd's three, a real frequency period, and the contributor identity
(GUID for personal, name+address for anything else). The option-set values are inlined in the
adapter so it stays importable without the vendor package — pinned here against
`morgan_lloyd.enums` so the two cannot drift.
"""

import pytest

from app.integrations.morgan_lloyd import contributions

PERSONAL = {
    "uid": "con_abc",
    "account_id": 1,
    "contact_id": 2,
    "category": "personal_contribution",
    "amount": 1250.0,
    "frequency": "single",
    "contribution_date": "2026-09-01",
    "ml_scheme_ref": "SR-000123",
    "ml_contact_id": "fc264062-fbf0-ea11-a815-000d3a86f6ce",
    "transaction_id": None,
}
EMPLOYER = {
    **PERSONAL,
    "category": "employer_contribution",
    "ml_contact_id": None,
    "employer_name": "Halloway Joinery Ltd",
    "address_line1": "1 Example Street",
    "city": "Bristol",
    "postcode": "BS1 4XE",
}


class TestOptionSetsMatchTheVendorPackage:
    """The adapter inlines the 8091300xx values; these must equal what pkgs/morgan-lloyd
    transcribed from the docs."""

    def test_contribution_types(self) -> None:
        enums = pytest.importorskip("morgan_lloyd.enums")
        assert contributions.CATEGORY_TO_ML == {
            "personal_contribution": enums.ContributionType.PERSONAL,
            "employer_contribution": enums.ContributionType.EMPLOYER,
            "third_party_contribution": enums.ContributionType.OTHER,
        }

    def test_frequencies(self) -> None:
        enums = pytest.importorskip("morgan_lloyd.enums")
        assert contributions.FREQUENCY_TO_ML == {
            "monthly": enums.PensionContributionFrequency.MONTHLY,
            "quarterly": enums.PensionContributionFrequency.QUARTERLY,
            "half_yearly": enums.PensionContributionFrequency.HALF_YEARLY,
            "annually": enums.PensionContributionFrequency.ANNUALLY,
            "single": enums.PensionContributionFrequency.SINGLE,
        }

    def test_statuses(self) -> None:
        enums = pytest.importorskip("morgan_lloyd.enums")
        assert contributions.STATUS_APPLICATION == enums.ContributionStatus.APPLICATION
        assert contributions.STATUS_ACTIVE == enums.ContributionStatus.ACTIVE
        assert contributions.STATUS_RECEIVED == enums.ContributionStatus.RECEIVED
        assert contributions.STATUS_STOPPED == enums.ContributionStatus.STOPPED

    def test_the_status_set_is_complete(self) -> None:
        """All four, not a subset. `record_ml_status` refuses a value it does not know, so a
        fifth member appearing at Morgan Lloyd must fail here rather than at a firm's refresh."""
        enums = pytest.importorskip("morgan_lloyd.enums")
        assert set(contributions.STATUS_LABELS) == {int(s) for s in enums.ContributionStatus}

    def test_the_service_layer_mirror_agrees_with_the_adapter(self) -> None:
        """`app.contributions` inlines the same four values to stay importable without the
        vendor package. Two copies of an option set is one copy too many to leave unpinned."""
        from app import contributions as service

        assert service.ML_STATUS_APPLICATION == contributions.STATUS_APPLICATION
        assert service.ML_STATUS_ACTIVE == contributions.STATUS_ACTIVE
        assert service.ML_STATUS_STOPPED == contributions.STATUS_STOPPED
        assert service.ML_STATUS_RECEIVED == contributions.STATUS_RECEIVED
        assert service.ML_STATUS_LABELS == contributions.STATUS_LABELS

    def test_every_ml_status_maps_somewhere_in_the_pipeline(self) -> None:
        """A status with no mapping would leave `record_ml_status` mirroring the vendor value
        while the contribution sat at whatever stage it was already on — silently stalled."""
        from app import contributions as service

        assert set(service.ML_STATUS_TO_PIPELINE) == set(service.ML_STATUS_LABELS)
        for stage in service.ML_STATUS_TO_PIPELINE.values():
            assert stage in service.ALL_STATUSES


class TestTransferStatusesMatchTheVendorPackage:
    """`app.transfers` inlines Morgan Lloyd's SIX-value transfer progress set.

    Worth pinning loudly: the transfers workbench prototype renders a NINE-stage saga that
    matches none of these. It was written against `0003_pensions_ledger.sql`, a schema
    superseded by 0051 that never shipped, so the rail was showing invented states.
    """

    def test_the_six_values(self) -> None:
        enums = pytest.importorskip("morgan_lloyd.enums")
        from app import transfers

        assert transfers.ML_AWAITING_FORMS == enums.TransferStatus.AWAITING_FORMS
        assert transfers.ML_TO_REQUEST_TRANSFER == enums.TransferStatus.ML_TO_REQUEST_TRANSFER
        assert (
            transfers.ML_AWAITING_CEDING_SCHEME
            == enums.TransferStatus.AWAITING_ACTION_FROM_CEDING_SCHEME
        )
        assert transfers.ML_FUNDS_RECEIVED == enums.TransferStatus.FUNDS_RECEIVED
        assert transfers.ML_FUNDS_SENT == enums.TransferStatus.FUNDS_SENT
        assert transfers.ML_NO_LONGER_TRANSFERRING == enums.TransferStatus.NO_LONGER_TRANSFERRING

    def test_the_set_is_complete(self) -> None:
        enums = pytest.importorskip("morgan_lloyd.enums")
        from app import transfers

        assert set(transfers.ML_STATUS_LABELS) == {int(s) for s in enums.TransferStatus}

    def test_every_status_maps_into_the_saga(self) -> None:
        from app import transfers

        assert set(transfers.ML_STATUS_TO_STAGE) == set(transfers.ML_STATUS_LABELS)
        for stage in transfers.ML_STATUS_TO_STAGE.values():
            assert stage in transfers.ALL_STATUSES


class TestUnpushableCategories:
    """A transfer in and a "not a contribution" have no Morgan Lloyd equivalent. Coercing them
    to OTHER would put a contribution on a client's pension record that never happened."""

    @pytest.mark.parametrize("category", ["transfer_in", "not_a_contribution"])
    def test_refuses_rather_than_coercing(self, category: str) -> None:
        with pytest.raises(ValueError) as exc:
            contributions.build_contribution({**PERSONAL, "category": category})
        message = str(exc.value).lower()
        assert "transfer" in message or "nothing to record" in message

    def test_unknown_category_is_refused(self) -> None:
        with pytest.raises(ValueError, match="Unknown contribution category"):
            contributions.build_contribution({**PERSONAL, "category": "wat"})


class TestMissingFields:
    def test_a_complete_personal_contribution_has_nothing_missing(self) -> None:
        assert contributions.missing_fields(PERSONAL) == []

    def test_a_complete_employer_contribution_has_nothing_missing(self) -> None:
        assert contributions.missing_fields(EMPLOYER) == []

    def test_personal_needs_the_dynamics_contact_guid(self) -> None:
        absent = contributions.missing_fields({**PERSONAL, "ml_contact_id": None})
        assert absent == ["a Morgan Lloyd contact for the client (push the client first)"]

    def test_employer_needs_a_contributor_name(self) -> None:
        absent = contributions.missing_fields({**EMPLOYER, "employer_name": None})
        assert absent == ["contributor name"]

    def test_an_unlinked_account_names_the_scheme_reference(self) -> None:
        absent = contributions.missing_fields({**PERSONAL, "ml_scheme_ref": None})
        assert "scheme reference" in absent[0]

    def test_blank_strings_count_as_missing(self) -> None:
        assert contributions.missing_fields({**EMPLOYER, "employer_name": ""}) == [
            "contributor name"
        ]


class TestBuildContribution:
    def test_personal_addresses_the_contributor_by_guid(self) -> None:
        payload = contributions.build_contribution(PERSONAL)
        assert payload["schemeReference"] == "SR-000123"
        assert payload["cam_contributiontype"] == 809130000
        assert payload["cam_contributor"] == PERSONAL["ml_contact_id"]
        assert payload["cam_amount"] == 1250.0
        assert payload["cam_startdate"] == "2026-09-01"
        # The `*` fields belong to non-personal contributions only.
        assert "cam_employerothername" not in payload

    def test_employer_names_the_payer_and_omits_the_guid(self) -> None:
        payload = contributions.build_contribution(EMPLOYER)
        assert payload["cam_contributiontype"] == 809130001
        assert payload["cam_employerothername"] == "Halloway Joinery Ltd"
        assert payload["cam_addressline1"] == "1 Example Street"
        assert payload["cam_city"] == "Bristol"
        assert payload["cam_postcode"] == "BS1 4XE"
        assert "cam_contributor" not in payload

    def test_blank_address_fields_are_omitted_not_sent_empty(self) -> None:
        """An empty string is a value to Dynamics and would overwrite what is already there."""
        payload = contributions.build_contribution({**EMPLOYER, "city": "", "postcode": None})
        assert "cam_city" not in payload
        assert "cam_postcode" not in payload

    def test_third_party_maps_to_other(self) -> None:
        payload = contributions.build_contribution(
            {**EMPLOYER, "category": "third_party_contribution"}
        )
        assert payload["cam_contributiontype"] == 809130002

    @pytest.mark.parametrize(
        ("frequency", "expected"),
        [
            ("single", 809130004),
            ("monthly", 809130000),
            ("quarterly", 809130001),
            ("half_yearly", 809130002),
            ("annually", 809130003),
        ],
    )
    def test_frequency_maps_to_the_period_not_a_boolean(
        self, frequency: str, expected: int
    ) -> None:
        payload = contributions.build_contribution({**PERSONAL, "frequency": frequency})
        assert payload["cam_frequency"] == expected

    def test_an_unmatched_contribution_is_an_application(self) -> None:
        assert contributions.build_contribution(PERSONAL)["cam_status"] == 809130000

    def test_a_matched_contribution_is_received(self) -> None:
        """ "For an Investment Contribution set status Received" — the money has landed."""
        payload = contributions.build_contribution({**PERSONAL, "transaction_id": 42})
        assert payload["cam_status"] == 809130003

    def test_amount_is_a_json_number_not_a_decimal(self) -> None:
        """Unlike SEI, Morgan Lloyd takes JSON numbers — and Decimal is not serialisable."""
        from decimal import Decimal

        payload = contributions.build_contribution({**PERSONAL, "amount": Decimal("1250.00")})
        assert isinstance(payload["cam_amount"], float)
        assert payload["cam_amount"] == 1250.0

    def test_start_date_is_truncated_to_a_date(self) -> None:
        payload = contributions.build_contribution(
            {**PERSONAL, "contribution_date": "2026-09-01T09:30:00+00:00"}
        )
        assert payload["cam_startdate"] == "2026-09-01"

    def test_optional_extras_are_only_sent_when_held(self) -> None:
        bare = contributions.build_contribution(PERSONAL)
        assert "cam_prefundedrasamount" not in bare
        assert "cam_investorref" not in bare
        rich = contributions.build_contribution(
            {**PERSONAL, "prefunded_ras_amount": 250, "investor_ref": "ACC-991"}
        )
        assert rich["cam_prefundedrasamount"] == 250.0
        assert rich["cam_investorref"] == "ACC-991"
