"""Cash vs in-specie, and full vs partial, as first-class transfer columns (0147).

`form_spec_for` is the table both create-validation and the drawer read. An SEI SIPP is
custodied by SEI and administered by Morgan Lloyd, so that account asks the split and
`cam_pensiontype`. A Seccl account, including a Seccl SIPP, hides extent and stores `full`.
Missing basis is refused rather than defaulted — Cash instructs the ceding scheme to sell.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import listing, providers, transfers
from app.routers.contributions import _TRANSFER_SORTS
from tests.conftest import SECCL_PLATFORM_SQL

# An SEI in-specie raise stores the holdings on the row. These cases are about the route and
# the crystallisation split; the line is only here so create can get past that check.
_HOLDING = {
    "identifier": "GB00B0000001",
    "identifier_type": "ISIN",
    "issue_country": "GB",
    "units": 10,
}

# No `pytestmark`: `asyncio_mode = "auto"` already runs the coroutines, and marking the SYNC
# `form_spec_for` cases with it is a warning.


async def _seed(session: AsyncSession, *, product: str = "SIPP") -> dict[str, Any]:
    """One firm, one client, one account. Returns `product_id` so a test can map an admin."""
    suffix = uuid.uuid4().hex[:10]
    tenant_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :alias) RETURNING id"
                ),
                {"uid": f"ten_{suffix}", "alias": f"firm-{suffix}"},
            )
        ).scalar_one()
    )
    contact_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO crm.contacts "
                    "(tenant_id, uid, type, first_name, surname, dob, relevant_uk_earnings) "
                    "VALUES (:t, :uid, 'individual', 'Sophie', 'Halloway', '1980-03-04', 55000) "
                    "RETURNING id"
                ),
                {"t": tenant_id, "uid": f"con_{suffix}"},
            )
        ).scalar_one()
    )
    await session.execute(text(SECCL_PLATFORM_SQL), {"n": f"Custodian {suffix}"})
    product_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO platform.products (tenant_id, name, product_type) "
                    "VALUES (:t, :n, CAST(:p AS platform.product_type)) RETURNING id"
                ),
                {"t": tenant_id, "n": f"{product} {suffix}", "p": product},
            )
        ).scalar_one()
    )
    account_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "(tenant_id, contact_id, product_id, uid, name, status, currency) "
                    "VALUES (:t, :c, :p, :uid, 'Pension', 'active', 'GBP') RETURNING id"
                ),
                {"t": tenant_id, "c": contact_id, "p": product_id, "uid": f"acc_{suffix}"},
            )
        ).scalar_one()
    )
    return {
        "tenant_id": tenant_id,
        "contact_id": contact_id,
        "account_id": account_id,
        "product_id": product_id,
    }


async def _set_custodian(session: AsyncSession, account_id: int, custodian: str) -> None:
    await session.execute(
        text(
            "UPDATE platform.accounts "
            "   SET custodian = CAST(:c AS platform.provider_code) WHERE id = :a"
        ),
        {"a": account_id, "c": custodian},
    )


async def _as_ml_pension(session: AsyncSession, seed: dict[str, Any]) -> None:
    """An SEI SIPP administered by Morgan Lloyd — the only account that pair describes."""
    await _set_custodian(session, seed["account_id"], "sei")
    await session.execute(
        text("UPDATE platform.products SET is_pension = true WHERE id = :p"),
        {"p": seed["product_id"]},
    )
    await session.execute(
        text(
            "INSERT INTO platform.product_platforms "
            "  (tenant_id, product_id, provider, role) "
            "VALUES (:t, :p, 'morgan-lloyd', 'scheme_administrator')"
        ),
        {"t": seed["tenant_id"], "p": seed["product_id"]},
    )


class TestFormSpec:
    def test_administrators_is_morgan_lloyd_today(self) -> None:
        """The Svelte drawer compares `scheme_administrator === 'morgan-lloyd'`. This pin is
        why that literal is safe — a second administrator would silently drop the split."""
        assert providers.administrators() == ("morgan-lloyd",)

    def test_sei_plus_ml_sipp_asks_extent_and_the_split(self) -> None:
        assert transfers.form_spec_for(
            custodian="sei", scheme_administrator="morgan-lloyd", is_pension=True
        ) == {
            "ask_extent": True,
            "ask_crystallised_split": True,
            "ask_pension_facts": True,
            "ask_pension_type": True,
            "default_extent": None,
            "allow_cash": True,
            "ask_ceding_address": False,
            "ask_sei_instruction": False,
        }

    def test_seccl_gia_hides_extent_and_stores_full(self) -> None:
        assert transfers.form_spec_for(
            custodian="seccl", scheme_administrator=None, is_pension=False
        ) == {
            "ask_extent": False,
            "ask_crystallised_split": False,
            "ask_pension_facts": False,
            "ask_pension_type": False,
            "default_extent": "full",
            "allow_cash": True,
            "ask_ceding_address": True,
            "ask_sei_instruction": False,
        }

    def test_sei_gia_asks_extent_not_the_split(self) -> None:
        assert transfers.form_spec_for(
            custodian="sei", scheme_administrator=None, is_pension=False
        ) == {
            "ask_extent": True,
            "ask_crystallised_split": False,
            "ask_pension_facts": False,
            "ask_pension_type": False,
            "default_extent": None,
            "allow_cash": True,
            "ask_ceding_address": False,
            "ask_sei_instruction": True,
        }

    def test_a_seccl_sipp_is_administered_by_seccl(self) -> None:
        """A product mapping that still names Morgan Lloyd does not make a Seccl SIPP one."""
        assert transfers.form_spec_for(
            custodian="seccl", scheme_administrator="morgan-lloyd", is_pension=True
        ) == {
            "ask_extent": False,
            "ask_crystallised_split": False,
            "ask_pension_facts": True,
            "ask_pension_type": False,
            "default_extent": "full",
            "allow_cash": True,
            "ask_ceding_address": True,
            "ask_sei_instruction": False,
        }

    def test_an_sei_gia_tagged_morgan_lloyd_stays_an_sei_gia(self) -> None:
        spec = transfers.form_spec_for(
            custodian="sei", scheme_administrator="morgan-lloyd", is_pension=False
        )
        assert spec["ask_crystallised_split"] is False
        assert spec["ask_pension_type"] is False
        assert spec["allow_cash"] is True

    def test_a_pension_without_an_administrator_still_asks_pension_facts(self) -> None:
        spec = transfers.form_spec_for(custodian="sei", scheme_administrator=None, is_pension=True)
        assert spec["ask_pension_facts"] is True
        assert spec["ask_crystallised_split"] is False
        assert spec["allow_cash"] is False

    def test_an_sei_in_specie_raise_stores_the_lines_the_call_will_need(self) -> None:
        stored = transfers.instruction_lines(
            custodian="sei",
            scheme_administrator=None,
            is_pension=False,
            basis="in_specie",
            external_account_type="Individual",
            assets=[
                {
                    "identifier": "GB00B0000001",
                    "identifier_type": "ISIN",
                    "issue_country": "GB",
                    "units": 10,
                }
            ],
            documents=[{"document_reference_id": "DOC1"}],
        )
        assert stored["external_account_type"] == "Individual"
        assert "assets" not in stored
        assert stored["documents"] == [{"document_reference_id": "DOC1"}]

    def test_an_sei_in_specie_raise_does_not_need_holdings(self) -> None:
        stored = transfers.instruction_lines(
            custodian="sei",
            scheme_administrator=None,
            is_pension=False,
            basis="in_specie",
            external_account_type="Individual",
            assets=None,
            documents=[{"document_reference_id": "DOC1"}],
        )
        assert "assets" not in stored
        assert stored["external_account_type"] == "Individual"

    def test_a_partial_sei_raise_does_not_need_holdings(self) -> None:
        stored = transfers.instruction_lines(
            custodian="sei",
            scheme_administrator=None,
            is_pension=False,
            basis="in_specie",
            external_account_type="Individual",
            assets=None,
            documents=[{"document_reference_id": "DOC1"}],
        )
        assert "assets" not in stored

    def test_a_wrapper_does_not_stand_in_for_the_ceding_account_type(self) -> None:
        with pytest.raises(ValueError, match="external_account_type"):
            transfers.instruction_lines(
                custodian="sei",
                scheme_administrator=None,
                is_pension=False,
                basis="in_specie",
                external_account_type=None,
                assets=[
                    {
                        "identifier": "GB00B0000001",
                        "identifier_type": "ISIN",
                        "issue_country": "GB",
                        "units": 10,
                    }
                ],
                documents=[{"document_reference_id": "DOC1"}],
            )

    def test_a_morgan_lloyd_sipp_stores_neither_holdings_nor_a_transfer_in(self) -> None:
        stored = transfers.instruction_lines(
            custodian="sei",
            scheme_administrator="morgan-lloyd",
            is_pension=True,
            basis="in_specie",
            external_account_type=None,
            assets=[
                {
                    "identifier": "GB00B0000001",
                    "identifier_type": "ISIN",
                    "issue_country": "GB",
                    "units": 10,
                }
            ],
            documents=None,
        )
        assert "external_account_type" not in stored
        assert stored == {}
        assert (
            transfers.instruction_lines(
                custodian="sei",
                scheme_administrator="morgan-lloyd",
                is_pension=True,
                basis="in_specie",
                external_account_type=None,
                assets=None,
                documents=None,
            )
            == {}
        )
        assert (
            transfers.instruction_lines(
                custodian="seccl",
                scheme_administrator=None,
                is_pension=False,
                basis="in_specie",
                external_account_type=None,
                assets=None,
                documents=None,
            )
            == {}
        )


class TestRouteFollowsTheCustodianLink:
    """Older rows leave `accounts.custodian` null and record it on the primary link."""

    async def test_an_sei_link_is_a_morgan_lloyd_sipp(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await _as_ml_pension(service_session, seed)
        await service_session.execute(
            text("UPDATE platform.accounts SET custodian = NULL WHERE id = :a"),
            {"a": seed["account_id"]},
        )
        await service_session.execute(
            text(
                "INSERT INTO platform.links "
                "(tenant_id, entity_type, entity_id, provider, external_id) "
                "VALUES (:t, 'account', :a, 'sei', 'SEI-1')"
            ),
            {"t": seed["tenant_id"], "a": seed["account_id"]},
        )
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="in_specie",
            extent="full",
            crystallisation="uncrystallised",
            pension_type=18,
            total_value=25000,
            assets=[_HOLDING],
        )
        assert record["route"] == "ml_sei"
        assert record["scheme_administrator"] == "morgan-lloyd"

    async def test_a_seccl_link_stays_with_seccl(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await _as_ml_pension(service_session, seed)
        await service_session.execute(
            text("UPDATE platform.accounts SET custodian = NULL WHERE id = :a"),
            {"a": seed["account_id"]},
        )
        await service_session.execute(
            text(
                "INSERT INTO platform.links "
                "(tenant_id, entity_type, entity_id, provider, external_id) "
                "VALUES (:t, 'account', :a, 'seccl', 'SCCL-1')"
            ),
            {"t": seed["tenant_id"], "a": seed["account_id"]},
        )
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="in_specie",
            total_value=25000,
            crystallisation="uncrystallised",
            provider_address_line1="1 Scheme Street",
            provider_postcode="EC2A 1AA",
            provider_country="GB",
        )
        assert record["route"] == "seccl"
        assert record["scheme_administrator"] == "seccl"
        assert record["pension_type"] is None


class TestCreateStoresTheColumns:
    async def test_basis_and_extent_land_on_the_row(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await _set_custodian(service_session, seed["account_id"], "sei")
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="cash",
            extent="full",
            total_value=25000,
        )
        assert record["basis"] == "cash"
        assert record["extent"] == "full"
        assert record["transfer_type"] == "pension"

    async def test_in_specie_is_folded_from_a_hyphenated_label(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="In Specie",
            extent="full",
            total_value=1000,
        )
        assert record["basis"] == "in_specie"

    async def test_missing_basis_is_refused_and_names_liquidation(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        with pytest.raises(ValueError, match="sell"):
            await transfers.create_transfer(
                service_session,
                tenant_id=seed["tenant_id"],
                created_by=None,
                account_id=seed["account_id"],
                ceding_provider="Aviva",
                extent="full",
                total_value=25000,
            )

    async def test_seccl_only_partial_is_refused(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session, product="GIA")
        await _set_custodian(service_session, seed["account_id"], "seccl")
        with pytest.raises(ValueError, match="full only"):
            await transfers.create_transfer(
                service_session,
                tenant_id=seed["tenant_id"],
                created_by=None,
                account_id=seed["account_id"],
                ceding_provider="Aviva",
                basis="cash",
                extent="partial",
                total_value=25000,
            )

    async def test_seccl_only_stores_full_when_extent_is_omitted(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session, product="GIA")
        await _set_custodian(service_session, seed["account_id"], "seccl")
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="cash",
            total_value=25000,
            provider_address_line1="1 Scheme Street",
            provider_postcode="EC2A 1AA",
            provider_country="GB",
        )
        assert record["extent"] == "full"
        assert record["transfer_type"] == "gia"

    async def test_ml_pension_missing_split_is_refused(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await _as_ml_pension(service_session, seed)
        with pytest.raises(ValueError, match="crystallised"):
            await transfers.create_transfer(
                service_session,
                tenant_id=seed["tenant_id"],
                created_by=None,
                account_id=seed["account_id"],
                ceding_provider="Aviva",
                basis="in_specie",
                extent="full",
                pension_type=18,
                total_value=25000,
            )

    async def test_ml_pension_zero_is_an_answer(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await _as_ml_pension(service_session, seed)
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="in_specie",
            extent="full",
            pension_type=18,
            crystallised_value=0,
            uncrystallised_value=184320.55,
            assets=[_HOLDING],
        )
        assert record["crystallised_value"] == 0.0
        assert record["total_value"] == pytest.approx(184320.55)
        assert record["basis"] == "in_specie"
        assert record["crystallisation"] == "uncrystallised"
        assert record["pension_type"] == 18
        assert record["safeguarded_benefits"] is False


class TestPensionFacts:
    async def test_a_pension_without_a_type_is_refused(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await _as_ml_pension(service_session, seed)
        with pytest.raises(ValueError, match="type of pension"):
            await transfers.create_transfer(
                service_session,
                tenant_id=seed["tenant_id"],
                created_by=None,
                account_id=seed["account_id"],
                ceding_provider="Aviva",
                basis="in_specie",
                extent="full",
                crystallisation="uncrystallised",
                total_value=25000,
            )

    async def test_uncrystallised_derives_the_split(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await _as_ml_pension(service_session, seed)
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="in_specie",
            extent="full",
            crystallisation="uncrystallised",
            pension_type=14,
            safeguarded_benefits=True,
            total_value=25000,
            assets=[_HOLDING],
        )
        assert record["crystallisation"] == "uncrystallised"
        assert record["crystallised_value"] == 0.0
        assert record["uncrystallised_value"] == 25000.0
        assert record["pension_type"] == 14
        assert record["safeguarded_benefits"] is True

    async def test_partial_derives_the_remainder(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await _as_ml_pension(service_session, seed)
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="in_specie",
            extent="partial",
            crystallisation="partial",
            pension_type=18,
            crystallised_value=10000,
            total_value=25000,
            assets=[_HOLDING],
        )
        assert record["crystallisation"] == "partial"
        assert record["crystallised_value"] == 10000.0
        assert record["uncrystallised_value"] == 15000.0

    async def test_crystallised_uses_the_whole_value(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await _as_ml_pension(service_session, seed)
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="in_specie",
            extent="full",
            crystallisation="crystallised",
            pension_type=7,
            total_value=25000,
            assets=[_HOLDING],
        )
        assert record["crystallisation"] == "crystallised"
        assert record["crystallised_value"] == 25000.0
        assert record["uncrystallised_value"] == 0.0

    async def test_an_unknown_pension_type_is_refused(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await _as_ml_pension(service_session, seed)
        with pytest.raises(ValueError, match="Unknown pension type"):
            await transfers.create_transfer(
                service_session,
                tenant_id=seed["tenant_id"],
                created_by=None,
                account_id=seed["account_id"],
                ceding_provider="Aviva",
                basis="in_specie",
                extent="full",
                crystallisation="uncrystallised",
                pension_type=21,
                total_value=25000,
            )


class TestStatusLabel:
    def test_covers_every_status_the_column_can_hold(self) -> None:
        assert set(transfers.STATUS_LABEL) == transfers.ALL_STATUSES


class TestTransferSorts:
    def test_status_sorts_on_the_label_not_the_raw_key(self) -> None:
        """`GET /transfers?sort=status` — both live mounts (`/operate/transfers`,
        `apps/platform` `/transfers`) are server-driven, so this dict is the only sort that
        actually runs for them; a frontend-only fix does nothing there.

        `cancelled`/`in_progress` are chosen because the two orderings DISAGREE: the raw keys
        sort `cancelled` before `in_progress` ('c' < 'i'), but the labels sort the other way —
        'At ceding scheme' before 'Cancelled' ('A' < 'C'). A pair the two orderings agree on
        would pass whichever key this reads, and prove nothing.
        """
        rows = [{"status": "cancelled"}, {"status": "in_progress"}]
        ordered = listing.sort_rows(rows, _TRANSFER_SORTS, "status", "asc")
        assert [row["status"] for row in ordered] == ["in_progress", "cancelled"]


class TestSplitForCrystallisation:
    def test_uncrystallised_is_all_uncrystallised(self) -> None:
        from decimal import Decimal

        assert transfers.split_for_crystallisation("uncrystallised", Decimal("25000"), None) == (
            Decimal("0"),
            Decimal("25000"),
        )

    def test_crystallised_without_an_amount_is_the_total(self) -> None:
        from decimal import Decimal

        assert transfers.split_for_crystallisation("crystallised", Decimal("25000"), None) == (
            Decimal("25000"),
            Decimal("0"),
        )

    def test_partial_refuses_the_poles(self) -> None:
        from decimal import Decimal

        with pytest.raises(ValueError, match="some, but not all"):
            transfers.split_for_crystallisation("partial", Decimal("25000"), Decimal("0"))
        with pytest.raises(ValueError, match="some, but not all"):
            transfers.split_for_crystallisation("partial", Decimal("25000"), Decimal("25000"))
