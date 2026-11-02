"""The booking flow end-to-end against a real database, with the SEI seam stubbed.

`app.contributions` is mostly SQL, and SQL is the part unit tests can't reach — a mistyped
column in the workbench's `_SELECT` only shows at runtime. So these drive the real functions
against a `service_role` session (mirroring `db.py::firm_session`) and stub only the outbound
custodian call.

The behaviour that matters most: **a SEI failure must not lose the contribution.** The local
records are Graphene's truth; the custodian is a downstream notification. Rolling back because
SEI is unreachable — or because a firm's reference codes aren't configured yet — would throw
away an adviser's work over an integration problem.

No truncate (service_session is shared): unique uids throughout, so these are order-independent.
"""

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import contributions, transfers
from app.integrations import sei as sei_integration
from tests.conftest import SECCL_PLATFORM_SQL, assert_public_id


async def _seed(
    session: AsyncSession, *, product: str = "SIPP", is_pension: bool | None = None
) -> dict[str, Any]:
    """One firm, one client, one account. `is_pension` stamps the shelf flag; omitted leaves
    the column's default, which is how a wrapper the firm has not classified arrives."""
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
    # The registry row still has to exist — `sync_platforms` seeds it on startup and the
    # merge resolves `platform.platforms.provider` — but the product no longer hangs off it
    # (0130 dropped `products.platform_id`), so the id is not bound to anything.
    await session.execute(text(SECCL_PLATFORM_SQL), {"n": f"Custodian {suffix}"})
    if is_pension is None:
        product_sql = (
            "INSERT INTO platform.products (name, product_type) "
            "VALUES (:n, CAST(:p AS platform.product_type)) RETURNING id"
        )
        product_params: dict[str, Any] = {"n": f"{product} {suffix}", "p": product}
    else:
        product_sql = (
            "INSERT INTO platform.products (name, product_type, is_pension) "
            "VALUES (:n, CAST(:p AS platform.product_type), :ip) RETURNING id"
        )
        product_params = {"n": f"{product} {suffix}", "p": product, "ip": is_pension}
    product_id = int((await session.execute(text(product_sql), product_params)).scalar_one())
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
    return {"tenant_id": tenant_id, "contact_id": contact_id, "account_id": account_id}


async def _configure_sei(session: AsyncSession, tenant_id: int) -> None:
    """Give the firm the SEI ids + reference codes a lodge needs."""
    await session.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id, provider, enabled, config) "
            "VALUES (:t, 'sei', true, CAST(:c AS jsonb))"
        ),
        {
            "t": tenant_id,
            "c": (
                '{"firm_id": "3102", "contribution_description_code": "1091", '
                '"contribution_treatment_codes": {"personal": "4402", "employer": "4403"}}'
            ),
        },
    )


async def _link_sei_account(session: AsyncSession, tenant_id: int, account_id: int) -> None:
    for link_type, external in (("primary", "USB0042817"), ("portfolio", "1234567")):
        await session.execute(
            text(
                "INSERT INTO platform.links "
                "(tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                "VALUES (:t, 'account', :e, 'sei', :lt, :ext)"
            ),
            {"t": tenant_id, "e": account_id, "lt": link_type, "ext": external},
        )


class TestBooking:
    async def test_books_a_contribution_and_its_expectation(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        record = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            contribution_date=date(2026, 9, 1),
            lodge=False,
        )

        assert_public_id(record["uid"])
        assert record["status"] == "awaiting_confirmation"
        # The relief arithmetic is computed and stored, not left to the client.
        assert record["amount"] == 800.0
        assert record["gross_amount"] == 1000.0
        assert record["relief_amount"] == 200.0
        assert record["tax_year"] == "2026/27"
        # The joined context the workbench renders — this is what proves _SELECT is right.
        assert record["client_name"] == "Sophie Halloway"
        assert record["account_name"] == "Pension"
        assert record["product_type"] == "SIPP"
        # And its forecast.
        assert_public_id(record["expectation_uid"])
        assert record["expectation_status"] == "expected"
        assert record["expectation_due_date"] == date(2026, 9, 1)

    async def test_the_expectation_carries_the_amount_and_account(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        record = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=False,
        )
        row = (
            (
                await service_session.execute(
                    text(
                        "SELECT kind, direction, amount::float8 AS amount, currency, account_id "
                        "FROM platform.transaction_expectations WHERE id = :i"
                    ),
                    {"i": record["expectation_id"]},
                )
            )
            .mappings()
            .one()
        )
        assert row["kind"] == "contribution"
        assert row["direction"] == "in"
        # The forecast is what was PAID, not the grossed-up figure — that is what arrives.
        assert row["amount"] == 800.0
        assert row["currency"] == "GBP"
        assert row["account_id"] == seed["account_id"]

    async def test_a_non_pension_account_is_refused(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session, product="ISA")
        with pytest.raises(ValueError, match="pension accounts only"):
            await contributions.create_contribution(
                service_session,
                tenant_id=seed["tenant_id"],
                created_by=None,
                account_id=seed["account_id"],
                category="personal_contribution",
                amount=800,
                lodge=False,
            )

    async def test_a_shelf_pension_flag_beats_the_wrapper_set(
        self, service_session: AsyncSession
    ) -> None:
        """The SIPP Payment drawer follows `is_pension`; the write used to ignore it and 422."""
        seed = await _seed(service_session, product="ISA", is_pension=True)
        record = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=False,
        )
        assert record["product_type"] == "ISA"
        assert record["status"] == "awaiting_confirmation"

    async def test_another_firms_account_is_not_found(self, service_session: AsyncSession) -> None:
        """service_role bypasses RLS, so the tenant scoping has to be in the query itself."""
        mine, theirs = await _seed(service_session), await _seed(service_session)
        with pytest.raises(LookupError):
            await contributions.create_contribution(
                service_session,
                tenant_id=mine["tenant_id"],
                created_by=None,
                account_id=theirs["account_id"],
                category="personal_contribution",
                amount=800,
                lodge=False,
            )

    async def test_an_employer_contribution_needs_a_name_up_front(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        with pytest.raises(ValueError, match="employer's name"):
            await contributions.create_contribution(
                service_session,
                tenant_id=seed["tenant_id"],
                created_by=None,
                account_id=seed["account_id"],
                category="employer_contribution",
                amount=800,
                lodge=False,
            )

    async def test_the_tax_year_defaults_from_the_contribution_date(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        record = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            contribution_date=date(2026, 4, 5),  # the last day of 2025/26
            lodge=False,
        )
        assert record["tax_year"] == "2025/26"


class TestLodging:
    async def test_a_successful_lodge_instructs_the_expectation(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed = await _seed(service_session)
        await _configure_sei(service_session, seed["tenant_id"])
        await _link_sei_account(service_session, seed["tenant_id"], seed["account_id"])

        async def fake_create(context: dict[str, Any], **_: Any) -> dict[str, Any]:
            return {"activity_id": "857458", "warnings": [], "request": {"sent": True}}

        monkeypatch.setattr(sei_integration, "create_anticipated_receipt", fake_create)

        async def fake_note(
            _session: AsyncSession, *, activity_id: str, event_id: str | None = None
        ):
            return {
                "activity_id": activity_id,
                "event_id": event_id,
                "activity_state_description": "Waiting for Matching",
            }

        monkeypatch.setattr("app.integrations.sei.receipt_status.note_lodged", fake_note)

        record = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=True,
        )
        assert record["sei"]["status"] == "instructed"
        assert record["sei"]["activity_id"] == "857458"
        assert record["sei"]["sei"]["activity_state_description"] == "Waiting for Matching"
        assert record["expectation_status"] == "instructed"
        assert record["expectation_reference"] == "857458"
        assert record["expectation_error"] is None

    async def test_missing_reference_codes_block_without_losing_the_contribution(
        self, service_session: AsyncSession
    ) -> None:
        """The account is on SEI but the firm has no reference codes — the commonest state today."""
        seed = await _seed(service_session)
        await _link_sei_account(service_session, seed["tenant_id"], seed["account_id"])
        record = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=True,
        )
        assert record["sei"]["status"] == "blocked"
        assert record["sei"]["missing"]
        assert any("treatment code" in m for m in record["sei"]["missing"])
        # The contribution still exists, and the expectation says why it wasn't lodged.
        assert_public_id(record["uid"])
        assert record["expectation_status"] == "expected"
        assert "SEI requires these details" in record["expectation_error"]

    async def test_a_vendor_failure_marks_the_expectation_failed(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed = await _seed(service_session)
        await _configure_sei(service_session, seed["tenant_id"])
        await _link_sei_account(service_session, seed["tenant_id"], seed["account_id"])

        async def boom(context: dict[str, Any], **_: Any) -> dict[str, Any]:
            raise sei_integration.SeiUnavailable("SEI refused the request (HTTP 403).")

        monkeypatch.setattr(sei_integration, "create_anticipated_receipt", boom)

        record = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=True,
        )
        assert record["sei"]["status"] == "failed"
        assert record["expectation_status"] == "failed"
        assert "403" in record["expectation_error"]
        # Still booked — the whole point.
        assert (
            await contributions.get_contribution(
                service_session, tenant_id=seed["tenant_id"], uid=record["uid"]
            )
        ) is not None

    async def test_unconfigured_credentials_are_reported_as_unavailable(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed = await _seed(service_session)
        await _configure_sei(service_session, seed["tenant_id"])
        await _link_sei_account(service_session, seed["tenant_id"], seed["account_id"])

        async def unconfigured(context: dict[str, Any], **_: Any) -> dict[str, Any]:
            raise sei_integration.SeiNotConfigured("SEI credentials are not set.")

        monkeypatch.setattr(sei_integration, "create_anticipated_receipt", unconfigured)

        record = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=True,
        )
        # 503, not 502: a deployment state rather than a vendor refusal.
        assert record["sei"]["status"] == "unavailable"
        assert record["expectation_status"] == "expected"

    async def test_the_sei_context_resolves_links_and_codes(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        await _configure_sei(service_session, seed["tenant_id"])
        await _link_sei_account(service_session, seed["tenant_id"], seed["account_id"])
        context = await contributions.sei_context(
            service_session,
            tenant_id=seed["tenant_id"],
            account_id=seed["account_id"],
            category="employer_contribution",
        )
        assert context["sei_account_number"] == "USB0042817"
        assert context["sei_portfolio_id"] == "1234567"
        assert context["firm_id"] == "3102"
        assert context["description_code"] == "1091"
        # Per category — this is what actually classifies it at SEI.
        assert context["treatment_code"] == "4403"
        assert sei_integration.missing_fields(context) == []

    async def test_the_principal_portfolio_stands_in_when_there_is_no_portfolio_link(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        await _configure_sei(service_session, seed["tenant_id"])
        await service_session.execute(
            text(
                "INSERT INTO platform.links "
                "(tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                "VALUES (:t, 'account', :e, 'sei', 'primary', 'USB0042817')"
            ),
            {"t": seed["tenant_id"], "e": seed["account_id"]},
        )
        await service_session.execute(
            text(
                "INSERT INTO platform.portfolios "
                "  (tenant_id, account_id, provider, external_id, is_principal) "
                "VALUES (:t, :a, 'sei', '58066414', true)"
            ),
            {"t": seed["tenant_id"], "a": seed["account_id"]},
        )
        context = await contributions.sei_context(
            service_session,
            tenant_id=seed["tenant_id"],
            account_id=seed["account_id"],
            category="personal_contribution",
        )
        assert context["sei_portfolio_id"] == "58066414"

    async def test_an_enrolment_global_id_resolves_to_the_portfolio_entity(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        await _configure_sei(service_session, seed["tenant_id"])
        await service_session.execute(
            text(
                "INSERT INTO platform.links "
                "(tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                "VALUES (:t, 'account', :e, 'sei', 'primary', 'AIO00213'), "
                "       (:t, 'account', :e, 'sei', 'portfolio', '3108-SWP-I31-59103919')"
            ),
            {"t": seed["tenant_id"], "e": seed["account_id"]},
        )
        context = await contributions.sei_context(
            service_session,
            tenant_id=seed["tenant_id"],
            account_id=seed["account_id"],
            category="",
        )
        assert context["sei_portfolio_id"] == "59103919"

    async def test_an_isa_payment_does_not_take_a_contribution_category(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session, product="ISA")
        await _configure_sei(service_session, seed["tenant_id"])
        await _link_sei_account(service_session, seed["tenant_id"], seed["account_id"])
        context = await contributions.sei_context(
            service_session,
            tenant_id=seed["tenant_id"],
            account_id=seed["account_id"],
            category="",
        )
        assert context["product_type"] == "ISA"
        assert context["description_code"] == "20009"
        assert context["treatment_code"] == "1401"
        assert sei_integration.missing_fields(context) == []


class TestClassifying:
    async def test_recomputes_the_relief_for_the_new_category(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        booked = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=False,
        )
        assert booked["relief_amount"] == 200.0

        # Reclassified as employer: paid gross, so no relief is reclaimable.
        updated = await contributions.categorise(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=booked["uid"],
            category="employer_contribution",
            employer={"employer_name": "Halloway Joinery Ltd"},
        )
        assert updated["status"] == "confirmed"
        assert updated["gross_amount"] == 800.0
        assert updated["relief_amount"] == 0.0
        assert updated["employer_name"] == "Halloway Joinery Ltd"

    async def test_refuses_an_employer_category_with_no_name(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        booked = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=False,
        )
        with pytest.raises(ValueError, match="employer's name"):
            await contributions.categorise(
                service_session,
                tenant_id=seed["tenant_id"],
                uid=booked["uid"],
                category="employer_contribution",
            )

    async def test_refuses_to_reclassify_past_confirmed(
        self, service_session: AsyncSession
    ) -> None:
        """Downstream has acted on it; changing it here would desynchronise the back office."""
        seed = await _seed(service_session)
        booked = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=False,
        )
        await contributions.mark_ml_recorded(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=booked["uid"],
            ml_contribution_id="fc264062-fbf0-ea11-a815-000d3a86f6ce",
        )
        # ContributionConflict, not a bare ValueError: the router maps this to 409 by TYPE
        # now. It used to split on `"past classification" in str(exc)`, which was one reworded
        # message away from silently answering 400 for a state conflict.
        with pytest.raises(contributions.ContributionConflict, match="past classification"):
            await contributions.categorise(
                service_session,
                tenant_id=seed["tenant_id"],
                uid=booked["uid"],
                category="third_party_contribution",
            )

    async def test_another_firms_contribution_is_not_found(
        self, service_session: AsyncSession
    ) -> None:
        mine, theirs = await _seed(service_session), await _seed(service_session)
        booked = await contributions.create_contribution(
            service_session,
            tenant_id=theirs["tenant_id"],
            created_by=None,
            account_id=theirs["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=False,
        )
        with pytest.raises(LookupError):
            await contributions.categorise(
                service_session,
                tenant_id=mine["tenant_id"],
                uid=booked["uid"],
                category="transfer_in",
            )


class TestListing:
    async def test_lists_the_firms_contributions_newest_first(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        for amount in (100, 200, 300):
            await contributions.create_contribution(
                service_session,
                tenant_id=seed["tenant_id"],
                created_by=None,
                account_id=seed["account_id"],
                category="personal_contribution",
                amount=amount,
                lodge=False,
            )
        rows = await contributions.list_contributions(service_session, tenant_id=seed["tenant_id"])
        assert len(rows) == 3
        assert {r["amount"] for r in rows} == {100.0, 200.0, 300.0}

    async def test_narrows_to_one_account(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        other = await _seed(service_session)
        await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=False,
        )
        assert (
            len(
                await contributions.list_contributions(
                    service_session, tenant_id=seed["tenant_id"], account_id=other["account_id"]
                )
            )
            == 0
        )
        by_client = await contributions.list_contributions(
            service_session,
            tenant_id=seed["tenant_id"],
            contact_id=seed["contact_id"],
        )
        assert len(by_client) == 1
        assert by_client[0]["contact_id"] == seed["contact_id"]


class TestMlContext:
    async def test_resolves_the_scheme_and_contact_guids(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        await service_session.execute(
            text(
                "INSERT INTO platform.links "
                "(tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                "VALUES (:t, 'account', :a, 'morgan-lloyd', 'primary', 'SR-000123'), "
                "       (:t, 'contact', :c, 'morgan-lloyd', 'primary', 'guid-1')"
            ),
            {"t": seed["tenant_id"], "a": seed["account_id"], "c": seed["contact_id"]},
        )
        booked = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=False,
        )
        context = await contributions.ml_context(
            service_session, tenant_id=seed["tenant_id"], contribution=booked
        )
        assert context["ml_scheme_ref"] == "SR-000123"
        assert context["ml_contact_id"] == "guid-1"
        assert context["investor_ref"] == booked["account_uid"]

        from app.integrations.morgan_lloyd import contributions as ml

        assert ml.missing_fields(context) == []

    async def test_an_unlinked_account_reports_what_is_missing(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        booked = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=False,
        )
        context = await contributions.ml_context(
            service_session, tenant_id=seed["tenant_id"], contribution=booked
        )
        from app.integrations.morgan_lloyd import contributions as ml

        assert "scheme reference" in ml.missing_fields(context)[0]


class TestTransfers:
    async def test_raises_a_transfer_and_its_expectation(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            policy_number="AV-99123",
            basis="cash",
            extent="full",
            crystallised_value=10000,
            uncrystallised_value=15000,
            expected_date=date(2026, 10, 1),
        )
        assert_public_id(record["uid"])
        assert record["ceding_provider"] == "Aviva"
        assert record["status"] == "requested"
        assert record["basis"] == "cash"
        assert record["extent"] == "full"
        # Total derived from the split, so an adviser doesn't have to add it up by hand.
        assert record["total_value"] == 25000.0
        assert record["expectation_status"] == "expected"
        assert record["expectation_due_date"] == date(2026, 10, 1)
        assert record["client_name"] == "Sophie Halloway"

    async def test_a_transfer_with_no_value_raises_no_expectation(
        self, service_session: AsyncSession
    ) -> None:
        """A transfer can be started before the ceding scheme states a value; there is nothing
        to forecast yet."""
        seed = await _seed(service_session)
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="cash",
            extent="full",
        )
        assert record["total_value"] is None
        assert record["expectation_id"] is None

    async def test_completing_stamps_the_time(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
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
        done = await transfers.set_status(
            service_session, tenant_id=seed["tenant_id"], uid=record["uid"], status="completed"
        )
        assert done["status"] == "completed"
        assert done["completed_at"] is not None

    async def test_an_unnamed_ceding_provider_is_refused(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        with pytest.raises(ValueError, match="ceding provider"):
            await transfers.create_transfer(
                service_session,
                tenant_id=seed["tenant_id"],
                created_by=None,
                account_id=seed["account_id"],
                ceding_provider="  ",
                basis="cash",
                extent="full",
            )
