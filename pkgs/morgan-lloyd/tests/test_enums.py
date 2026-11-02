"""Pin the transcribed Dynamics 365 option-set values against the documented integers."""

from __future__ import annotations

import inspect
from enum import IntEnum

import pytest

from morgan_lloyd import enums
from morgan_lloyd.enums import (
    INVESTMENT_PATHWAY_DESCRIPTIONS,
    RELATIONSHIP_TYPE_IDS,
    RELATIONSHIP_TYPE_RECIPROCALS,
    TAX_RELIEF_DESCRIPTIONS,
    VULNERABILITY_DESCRIPTIONS,
    AssetStatus,
    AssetType,
    BenefitEventStatus,
    BenefitPaymentFrequency,
    ContactVulnerability,
    ContributionType,
    DrawdownOption,
    FamilyStatus,
    Gender,
    InvestmentType,
    MessageFlag,
    PensionType,
    ReceivedContributionType,
    ResponseKey,
    RiskLevel,
    SippType,
    SourceOfWealth,
    Title,
    TransferStatus,
    WithdrawalFrequency,
    YesNoString,
    multiselect,
)


class TestDocumentedValues:
    """Spot-check exact integers across the API sections."""

    def test_contacts_section(self) -> None:
        assert Title.MR == 809130000
        assert Title.MAJOR == 809130013
        assert Gender.FEMALE == 2
        assert FamilyStatus.CIVIL_PARTNER == 5
        assert SourceOfWealth.INHERITANCE == 809130007
        assert ContactVulnerability.RESILIENCE == 809130003

    def test_factfinds_section(self) -> None:
        assert RiskLevel.VERY_CAUTIOUS == 809130000
        assert RiskLevel.VERY_ADVENTUROUS == 809130009
        assert AssetStatus.NOT_TRANSFERRING == 5
        assert AssetType.CARE_PLANNING == 809130000
        assert InvestmentType.ISA_LIFETIME == 809130030
        assert PensionType.SMALL_SELF_ADMINISTERED_SCHEME == 19

    def test_schemes_section(self) -> None:
        assert SippType.VALUE_809130003 == 809130003
        assert 809130004 not in {int(v) for v in SippType}

    def test_transfers_section(self) -> None:
        assert TransferStatus.FUNDS_SENT == 4
        assert TransferStatus.NO_LONGER_TRANSFERRING == 5

    def test_pension_contributions_section(self) -> None:
        # The same field name carries two different option sets on different entities.
        assert ContributionType.PERSONAL == 809130000
        assert ReceivedContributionType.PERSONAL == 0
        assert ReceivedContributionType.OTHER == 809130000

    def test_benefits_section(self) -> None:
        assert DrawdownOption.FULL_TFC == 2
        assert DrawdownOption.PARTIAL_SINGLE_PAYMENT_CUSTOM_REGULAR_INCOME == 39
        assert WithdrawalFrequency.FORTNIGHTLY == 26
        assert BenefitEventStatus.APPLICATION == 809130000
        assert BenefitPaymentFrequency.AD_HOC == 100000000

    def test_shared_section(self) -> None:
        assert ResponseKey.SUCCESS == 0
        assert ResponseKey.FAILED == 1
        assert MessageFlag.VIEWED == 3
        assert YesNoString.YES == "1"


class TestMultiselect:
    def test_multiselect_joins_with_comma_space(self) -> None:
        result = multiselect(ContactVulnerability.HEALTH, ContactVulnerability.DISABILITY)
        assert result == "809130000, 809130001"

    def test_multiselect_single_option(self) -> None:
        assert multiselect(ContactVulnerability.AGE) == "809130005"


class TestLookupTables:
    def test_relationship_type_guids(self) -> None:
        assert RELATIONSHIP_TYPE_IDS["Spouse"] == "ee375944-5415-437d-9336-7698cf665b26"
        assert RELATIONSHIP_TYPE_RECIPROCALS["Child"] == "Parent"
        assert set(RELATIONSHIP_TYPE_IDS) == set(RELATIONSHIP_TYPE_RECIPROCALS)

    def test_description_mappings_cover_every_member(self) -> None:
        assert set(TAX_RELIEF_DESCRIPTIONS) == set(enums.TaxRelief)
        assert set(VULNERABILITY_DESCRIPTIONS) == set(ContactVulnerability)
        assert set(INVESTMENT_PATHWAY_DESCRIPTIONS) == set(enums.InvestmentPathway)
        assert set(enums.SUITABILITY_CONFIRMATION_DESCRIPTIONS) == set(
            enums.SuitabilityConfirmation
        )


def _int_enums() -> list[type[IntEnum]]:
    return [
        member
        for _, member in inspect.getmembers(enums, inspect.isclass)
        if issubclass(member, IntEnum) and member.__module__ == enums.__name__
    ]


@pytest.mark.parametrize("enum_cls", _int_enums(), ids=lambda cls: cls.__name__)
def test_int_enum_values_are_unique(enum_cls: type[IntEnum]) -> None:
    """No IntEnum in the module carries a duplicate (aliased) value."""
    assert len(set(enum_cls)) == len(list(enum_cls))
    # list(E) hides aliases, so also check the full member table against distinct values.
    assert len(enum_cls.__members__) == len({m.value for m in enum_cls.__members__.values()})


def test_module_defines_int_enums() -> None:
    # Guard the inspection sweep itself: the module holds a substantial number of option sets.
    assert len(_int_enums()) >= 40
