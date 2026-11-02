"""The catalogue's two judgement calls: a fee rule matching its type, and allocations adding up.

Everything else in `app/catalogue.py` is SQL, exercised against real Postgres by the migration.
These are the rules that would otherwise fail quietly — a tiered package that prices like a flat
one, or a model that silently holds 97% and leaves the rest ambiguous.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import catalogue
from app.catalogue import (
    PRODUCT_PLATFORM_SETTINGS,
    CatalogueError,
    _product_platform_data,
    validate_allocations,
    validate_fee_rule,
)
from app.integrations.morgan_lloyd import sync as ml_sync


class TestProductKickoffRules:
    @pytest.mark.asyncio
    async def test_custody_choices_and_party_limit_round_trip(
        self, service_session: AsyncSession
    ) -> None:
        from app import catalogue

        suffix = uuid.uuid4().hex[:8]
        tenant = int(
            (
                await service_session.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"rules_{suffix}", "a": f"rules-{suffix}", "n": f"Rules {suffix}"},
                )
            ).scalar_one()
        )
        await service_session.execute(
            text(
                "UPDATE auth.tenants "
                "SET allowed_currencies = ARRAY['GBP', 'EUR']::text[] WHERE id = :t"
            ),
            {"t": tenant},
        )
        await catalogue.upsert_product(
            service_session,
            tenant,
            {
                "product_type": "GIA",
                "name": "Offshore GIA",
                "allowed_custodies": ["offshore"],
                "allowed_client_types": ["organisation"],
                "allowed_currencies": ["GBP", "EUR"],
                "max_parties": 3,
                "bank_details_allowed": True,
            },
        )
        [product] = await catalogue.products(service_session, tenant)
        assert product["allowed_custodies"] == ["offshore"]
        assert product["allowed_client_types"] == ["organisation"]
        assert product["allowed_currencies"] == ["GBP", "EUR"]
        assert product["max_parties"] == 3
        assert product["min_age"] is None
        assert product["max_age"] is None
        assert product["bank_details_allowed"] is True

        sipp = await catalogue.upsert_product(
            service_session,
            tenant,
            {"product_type": "SIPP", "name": "SIPP"},
        )
        assert sipp["allowed_currencies"] == ["GBP", "EUR"]

        jisa = await catalogue.upsert_product(
            service_session,
            tenant,
            {"product_type": "JISA", "name": "Junior ISA"},
        )
        assert jisa["max_age"] == 17
        assert jisa["allowed_client_types"] == ["individual"]
        assert jisa["allowed_currencies"] == ["GBP"]
        assert jisa["bank_details_allowed"] is False

        # The merge INSERT omits allowed_currencies. The trigger must default ISA to GBP, not copy
        # the firm's EUR+GBP list — that is `ISA products must allow GBP only` on every Seccl run.
        await service_session.execute(
            text(
                "INSERT INTO platform.products (tenant_id, product_type, name) "
                "VALUES (:t, CAST('ISA' AS platform.product_type), 'Stocks & Shares ISA')"
            ),
            {"t": tenant},
        )
        isa_currencies = (
            await service_session.execute(
                text(
                    "SELECT allowed_currencies FROM platform.products "
                    "WHERE tenant_id = :t AND product_type = 'ISA'"
                ),
                {"t": tenant},
            )
        ).scalar_one()
        assert list(isa_currencies) == ["GBP"]

        # The ceiling may be LOWERED (a firm's own policy) and neither raised nor removed: it is
        # the law's, not the firm's. Without this a PATCH could put the stored value at odds with
        # what `effective_age_bounds` enforces.
        lowered = await catalogue.upsert_product(
            service_session, tenant, {"max_age": 16}, product_id=int(jisa["id"])
        )
        assert lowered["max_age"] == 16
        for widened in ({"max_age": 25}, {"max_age": None}):
            with pytest.raises(CatalogueError, match="no older than 17"):
                await catalogue.upsert_product(
                    service_session, tenant, widened, product_id=int(jisa["id"])
                )

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ({"allowed_custodies": []}, "allowed_custodies"),
            ({"allowed_custodies": ["moon"]}, "onshore or offshore"),
            ({"allowed_client_types": []}, "allowed_client_types"),
            ({"allowed_client_types": ["partnership"]}, "individual or organisation"),
            ({"allowed_currencies": []}, "allowed_currencies"),
            ({"max_parties": 0}, "at least 1"),
            ({"min_age": -1}, "cannot be negative"),
            ({"min_age": 20, "max_age": 10}, "min_age"),
        ],
    )
    @pytest.mark.asyncio
    async def test_invalid_product_rules_are_refused(
        self, service_session: AsyncSession, payload: dict[str, object], message: str
    ) -> None:
        from app import catalogue

        with pytest.raises(CatalogueError, match=message):
            await catalogue.upsert_product(service_session, 1, payload, product_id=1)

    @pytest.mark.asyncio
    async def test_a_person_only_wrapper_cannot_be_widened_to_organisations(
        self, service_session: AsyncSession
    ) -> None:
        from app import catalogue

        with pytest.raises(CatalogueError, match="only be owned by an individual"):
            await catalogue.upsert_product(
                service_session,
                1,
                {
                    "product_type": "ISA",
                    "name": "ISA",
                    "allowed_client_types": ["individual", "organisation"],
                },
            )

    @pytest.mark.asyncio
    async def test_an_isa_cannot_be_widened_beyond_gbp(self, service_session: AsyncSession) -> None:
        with pytest.raises(CatalogueError, match="must allow GBP only"):
            await catalogue.upsert_product(
                service_session,
                1,
                {
                    "product_type": "ISA",
                    "name": "ISA",
                    "allowed_currencies": ["GBP", "EUR"],
                },
            )


class TestStatutoryAgeBounds:
    """A JISA owner must be under 18 whether or not the product row says so.

    `0162_product_ages` added the columns and backfilled `max_age = 17` for the JISAs that existed
    when it ran — which is all a backfill can do. Every layer that enforces the rule (the kickoff
    wizard's inline error, its Continue gate, and the API's refusal) opens by returning early when
    both bounds are null, so a JISA created afterwards let an adult through in silence: no warning,
    no blocker, no refusal.
    """

    def test_a_jisa_carrying_no_bounds_still_has_a_ceiling(self) -> None:
        from app import catalogue

        assert catalogue.effective_age_bounds("JISA", None, None) == (None, 17)
        # Lower-cased or oddly-cased product types resolve the same way; the column is an enum but
        # it arrives here as text from several different projections.
        assert catalogue.effective_age_bounds("jisa", None, None) == (None, 17)

    def test_configuration_may_narrow_the_ceiling_but_not_widen_it(self) -> None:
        from app import catalogue

        assert catalogue.effective_age_bounds("JISA", None, 16) == (None, 16)
        assert catalogue.effective_age_bounds("JISA", None, 25) == (None, 17)
        # A floor is the firm's own business and passes through untouched.
        assert catalogue.effective_age_bounds("JISA", 0, None) == (0, 17)

    def test_products_without_a_statutory_bound_are_left_alone(self) -> None:
        from app import catalogue

        assert catalogue.effective_age_bounds("ISA", None, None) == (None, None)
        assert catalogue.effective_age_bounds("SIPP", 18, None) == (18, None)
        assert catalogue.effective_age_bounds(None, None, None) == (None, None)

    def test_an_adult_is_refused_by_a_jisa_with_no_stored_bounds(self) -> None:
        """The reported symptom, end to end through the two pure functions the API composes."""
        from datetime import date

        from app import catalogue

        min_age, max_age = catalogue.effective_age_bounds("JISA", None, None)
        error = catalogue.product_age_error(
            name="Junior ISA",
            min_age=min_age,
            max_age=max_age,
            dob=date(2000, 1, 1),
            on=date(2026, 9, 15),
        )
        assert error == "Junior ISA is only available up to age 17."

        child = catalogue.product_age_error(
            name="Junior ISA",
            min_age=min_age,
            max_age=max_age,
            dob=date(2015, 1, 1),
            on=date(2026, 9, 15),
        )
        assert child is None

    def test_the_day_before_the_eighteenth_birthday_is_still_allowed(self) -> None:
        """17 completed years, not "under 18 next year" — the boundary is the whole rule."""
        from datetime import date

        from app import catalogue

        min_age, max_age = catalogue.effective_age_bounds("JISA", None, None)
        eve = catalogue.product_age_error(
            name="Junior ISA",
            min_age=min_age,
            max_age=max_age,
            dob=date(2008, 9, 16),
            on=date(2026, 9, 15),
        )
        assert eve is None
        birthday = catalogue.product_age_error(
            name="Junior ISA",
            min_age=min_age,
            max_age=max_age,
            dob=date(2008, 9, 15),
            on=date(2026, 9, 15),
        )
        assert birthday == "Junior ISA is only available up to age 17."


class TestFeeRules:
    def test_a_fixed_rule_keeps_its_amount(self) -> None:
        assert validate_fee_rule("fixed", {"amount": 250}) == {"amount": 250.0}

    def test_a_percentage_rule_keeps_its_rate(self) -> None:
        assert validate_fee_rule("percentage", {"rate": 0.75}) == {"rate": 0.75}

    def test_a_rule_that_does_not_match_its_type_is_rejected(self) -> None:
        """The failure worth catching: this reads as valid until it prices something."""
        with pytest.raises(CatalogueError, match="rule.tiers|at least one tier"):
            validate_fee_rule("tiered", {"rate": 0.75})

    def test_an_unknown_fee_type_is_rejected(self) -> None:
        with pytest.raises(CatalogueError, match="fee_type must be one of"):
            validate_fee_rule("sliding", {"rate": 1})

    @pytest.mark.parametrize("rate", [-1, 101])
    def test_a_rate_outside_nought_to_a_hundred_is_rejected(self, rate: float) -> None:
        with pytest.raises(CatalogueError, match="between 0 and 100"):
            validate_fee_rule("percentage", {"rate": rate})

    def test_a_negative_fixed_amount_is_rejected(self) -> None:
        with pytest.raises(CatalogueError, match="cannot be negative"):
            validate_fee_rule("fixed", {"amount": -10})

    def test_a_contiguous_tier_ladder_is_accepted(self) -> None:
        rule = validate_fee_rule(
            "tiered",
            {
                "tiers": [
                    {"from": 0, "to": 250_000, "rate": 1.0},
                    {"from": 250_000, "to": 1_000_000, "rate": 0.75},
                    {"from": 1_000_000, "to": None, "rate": 0.5},
                ]
            },
        )
        assert [tier["rate"] for tier in rule["tiers"]] == [1.0, 0.75, 0.5]
        assert rule["tiers"][-1]["to"] is None

    def test_a_gap_between_bands_is_rejected(self) -> None:
        """A gap charges nothing inside it, which looks like data until an invoice is wrong."""
        with pytest.raises(CatalogueError, match="previous band ended at"):
            validate_fee_rule(
                "tiered",
                {
                    "tiers": [
                        {"from": 0, "to": 100_000, "rate": 1.0},
                        {"from": 250_000, "to": None, "rate": 0.75},
                    ]
                },
            )

    def test_an_overlap_between_bands_is_rejected(self) -> None:
        """And an overlap charges twice."""
        with pytest.raises(CatalogueError, match="previous band ended at"):
            validate_fee_rule(
                "tiered",
                {
                    "tiers": [
                        {"from": 0, "to": 250_000, "rate": 1.0},
                        {"from": 100_000, "to": None, "rate": 0.75},
                    ]
                },
            )

    def test_an_open_ended_band_must_be_last(self) -> None:
        with pytest.raises(CatalogueError, match="open-ended tier must be the last"):
            validate_fee_rule(
                "tiered",
                {
                    "tiers": [
                        {"from": 0, "to": None, "rate": 1.0},
                        {"from": 250_000, "to": None, "rate": 0.75},
                    ]
                },
            )

    def test_a_band_that_ends_before_it_starts_is_rejected(self) -> None:
        with pytest.raises(CatalogueError, match="ends at or before it starts"):
            validate_fee_rule("tiered", {"tiers": [{"from": 500, "to": 100, "rate": 1.0}]})

    def test_a_single_open_ended_band_is_a_valid_ladder(self) -> None:
        rule = validate_fee_rule("tiered", {"tiers": [{"from": 0, "to": None, "rate": 0.9}]})
        assert rule == {"tiers": [{"from": 0.0, "to": None, "rate": 0.9}]}

    def test_a_rule_that_is_not_an_object_is_rejected(self) -> None:
        with pytest.raises(CatalogueError, match="rule must be an object"):
            validate_fee_rule("fixed", [250])

    def test_a_missing_amount_says_which_field(self) -> None:
        with pytest.raises(CatalogueError, match="rule.amount is required"):
            validate_fee_rule("fixed", {})


class TestAllocations:
    def test_lines_plus_cash_reaching_a_hundred_are_accepted(self) -> None:
        lines, cash = validate_allocations(
            [
                {"isin": "GB00B3X7QG63", "name": "UK equity", "percentage": 60},
                {"isin": "IE00B3RBWM25", "name": "World equity", "percentage": 37.5},
            ],
            2.5,
        )
        assert [line["percentage"] for line in lines] == [60.0, 37.5]
        assert cash == Decimal("2.5")
        # Order is preserved as sort_order, so the editor's ordering survives a round trip.
        assert [line["sort_order"] for line in lines] == [0, 1]

    def test_a_model_that_does_not_add_up_is_rejected(self) -> None:
        """97% invested is either a 3% cash position or a missing line, and the difference
        matters to every account tracking the model."""
        with pytest.raises(CatalogueError, match="total 97"):
            validate_allocations([{"isin": "X", "percentage": 97}], 0)

    def test_rounding_within_tolerance_is_accepted(self) -> None:
        """33.33 three times is 99.99, which is a real allocation, not an error."""
        lines, _ = validate_allocations(
            [
                {"isin": "A", "percentage": 33.33},
                {"isin": "B", "percentage": 33.33},
                {"isin": "C", "percentage": 33.33},
            ],
            0,
        )
        assert len(lines) == 3

    def test_cash_counts_towards_the_total(self) -> None:
        lines, cash = validate_allocations([{"isin": "A", "percentage": 90}], 10)
        assert len(lines) == 1 and cash == Decimal("10")

    def test_an_empty_version_is_rejected(self) -> None:
        with pytest.raises(CatalogueError, match="at least one allocation line"):
            validate_allocations([], 100)

    def test_a_line_with_no_identity_is_rejected(self) -> None:
        """A line naming no holding cannot be reconciled to a position."""
        with pytest.raises(CatalogueError, match="needs an instrument_id"):
            validate_allocations([{"name": "Something", "percentage": 100}], 0)

    def test_an_instrument_id_alone_is_identity_enough(self) -> None:
        lines, _ = validate_allocations([{"instrument_id": 42, "percentage": 100}], 0)
        assert lines[0]["instrument_id"] == 42

    def test_a_repeated_holding_is_rejected(self) -> None:
        """Two lines for one holding would sum in some readers and not others."""
        with pytest.raises(CatalogueError, match="repeats"):
            validate_allocations(
                [{"isin": "A", "percentage": 50}, {"isin": "A", "percentage": 50}], 0
            )

    @pytest.mark.parametrize("percentage", [0, -5])
    def test_a_line_of_zero_or_less_is_rejected(self, percentage: float) -> None:
        with pytest.raises(CatalogueError, match="greater than zero"):
            validate_allocations(
                [{"isin": "A", "percentage": percentage}, {"isin": "B", "percentage": 100}], 0
            )

    def test_negative_cash_is_rejected(self) -> None:
        with pytest.raises(CatalogueError, match="cash allocation cannot be negative"):
            validate_allocations([{"isin": "A", "percentage": 110}], -10)

    def test_a_percentage_that_is_not_a_number_says_so(self) -> None:
        with pytest.raises(CatalogueError, match="must be a number"):
            validate_allocations([{"isin": "A", "percentage": "sixty"}], 0)


class TestModelProductsShape:
    """`platform.models.products` must reach the page as a LIST.

    It is `platform.product_type[]` — a custom enum array psycopg has no loader for — so a plain
    `SELECT m.products` comes back as the raw Postgres literal STRING `'{GIA,ISA,JISA,SIPP}'`. The
    admin models listing filters on it with `matchesAny`, which calls `.some()`: against a string
    that throws, and the whole listing rendered nothing. Every type in play said `string[]`, so
    only executing the query catches it — hence a DB test rather than a unit one.
    """

    @pytest.mark.asyncio
    async def test_the_enum_array_arrives_as_a_list_of_strings(
        self, service_session: AsyncSession
    ) -> None:
        from app import catalogue
        from tests.conftest import SECCL_PLATFORM_SQL

        suffix = uuid.uuid4().hex[:8]
        tenant = int(
            (
                await service_session.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"prod_{suffix}", "a": f"prod-{suffix}", "n": f"Products {suffix}"},
                )
            ).scalar_one()
        )
        platform = str((await service_session.execute(text(SECCL_PLATFORM_SQL))).scalar_one())
        await service_session.execute(
            text(
                "INSERT INTO platform.models (tenant_id, provider, name, products) "
                "VALUES (:t, :p, :n, cast(:prods AS platform.product_type[]))"
            ),
            {
                "t": tenant,
                "p": platform,
                "n": f"Balanced {suffix}",
                "prods": ["GIA", "ISA", "SIPP"],
            },
        )

        rows = await catalogue.models(service_session, tenant)
        assert len(rows) == 1
        products = rows[0]["products"]
        assert isinstance(products, list), f"expected a list, got {type(products).__name__}"
        assert products == ["GIA", "ISA", "SIPP"]
        # The shape the page actually relies on: `.some()` exists on a list, not on a string.
        assert all(isinstance(p, str) for p in products)

    @pytest.mark.asyncio
    async def test_a_model_with_no_wrappers_is_null_not_an_empty_string(
        self, service_session: AsyncSession
    ) -> None:
        """The listing renders `model.products?.length` — NULL must stay nullish rather than
        arriving as the literal '{}'."""
        from app import catalogue
        from tests.conftest import SECCL_PLATFORM_SQL

        suffix = uuid.uuid4().hex[:8]
        tenant = int(
            (
                await service_session.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"noprod_{suffix}", "a": f"noprod-{suffix}", "n": f"NoProd {suffix}"},
                )
            ).scalar_one()
        )
        platform = str((await service_session.execute(text(SECCL_PLATFORM_SQL))).scalar_one())
        await service_session.execute(
            text("INSERT INTO platform.models (tenant_id, provider, name) VALUES (:t, :p, :n)"),
            {"t": tenant, "p": platform, "n": f"Unassigned {suffix}"},
        )
        rows = await catalogue.models(service_session, tenant)
        assert rows[0]["products"] is None


class TestProductPlatformSettings:
    """`product_platforms.data` is what a provider write path reads to decide what to SEND a
    custodian, so it is an allowlist rather than a free jsonb door.

    The column existed from 0042 and `set_product_platform` never wrote it — the same "the table
    is there and nothing populates it" shape as `platform.platforms`, which cost every account
    its `product_id`. A setting nothing can write is not configuration.
    """

    def test_absent_data_leaves_the_stored_object_alone(self) -> None:
        """This endpoint is also how an external_id is renamed. A rename must not wipe the
        scheme-type pairing a provider write path reads."""
        assert _product_platform_data({"external_id": "TPONBOND"}) is None

    def test_an_empty_object_clears_it(self) -> None:
        assert _product_platform_data({"data": {}}) == "{}"

    def test_the_morgan_lloyd_pairing_round_trips_as_numbers(self) -> None:
        stored = _product_platform_data({"data": {"ml_scheme_type": 17, "ml_sipp_type": 809130002}})
        assert json.loads(str(stored)) == {"ml_scheme_type": 17, "ml_sipp_type": 809130002}

    def test_a_numeric_string_is_stored_as_a_number(self) -> None:
        """The admin form posts strings, and `cam_createscheme`'s schemeType is an Edm.Int32 —
        so "17" reaching the vendor is a 400 that names nothing useful."""
        stored = _product_platform_data({"data": {"ml_scheme_type": "17"}})
        assert json.loads(str(stored)) == {"ml_scheme_type": 17}

    @pytest.mark.asyncio
    async def test_the_statement_actually_runs_and_the_pairing_round_trips(
        self, service_session: AsyncSession
    ) -> None:
        """EXECUTED, not built — and this is the case every test above it could not see.

        The statement wrote `:data::jsonb`, and SQLAlchemy's `text()` deliberately does NOT treat
        a bind followed by `::` as a parameter, because that is how it avoids mis-reading a
        Postgres cast. So the placeholder was left in the SQL literally and every call raised a
        syntax error at execute time — `set_product_platform` had never once succeeded, which is
        to say the admin catalogue could not record which reference a custodian knows a product
        by, nor the scheme-type pairing `cam_createscheme` needs.

        Four unit tests proved the VALUE was built correctly and none of them proved the
        statement could run. `app/matching.py` already carries a comment about this exact trap;
        this is the second time, so the guard is a test rather than another comment.
        """
        from app import catalogue

        suffix = uuid.uuid4().hex[:8]
        tenant = int(
            (
                await service_session.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"pps_{suffix}", "a": f"pps-{suffix}", "n": f"Settings {suffix}"},
                )
            ).scalar_one()
        )
        product = await catalogue.upsert_product(
            service_session, tenant, {"name": "SIPP", "product_type": "SIPP", "is_pension": True}
        )

        row = await catalogue.set_product_platform(
            service_session,
            tenant,
            int(product["id"]),
            "morgan-lloyd",
            {"role": "scheme_administrator", "data": {"ml_scheme_type": 16}},
        )
        assert row["role"] == "scheme_administrator"
        assert row["data"] == {"ml_scheme_type": 16}

        # The upsert half, and the rule the COALESCE exists for: a rename must not wipe the
        # pairing, which is the thing that took a phone call to the vendor to establish.
        renamed = await catalogue.set_product_platform(
            service_session, tenant, int(product["id"]), "morgan-lloyd", {"external_id": "QSIPP/1"}
        )
        assert renamed["external_id"] == "QSIPP/1"
        assert renamed["data"] == {"ml_scheme_type": 16}
        assert renamed["role"] == "scheme_administrator"

    @pytest.mark.asyncio
    async def test_an_enrolment_template_round_trips_and_a_rename_does_not_wipe_it(
        self, service_session: AsyncSession
    ) -> None:
        """`account_open_reference` is the id a write SENDS to open an account (SEI's
        accountTemplateId). It must survive a rename of `external_id`, which is the id the
        pipeline READS — the two are different values with different lifetimes, and wiping
        the template on a rename is how an operator's judgement disappears overnight."""
        from app import catalogue

        suffix = uuid.uuid4().hex[:8]
        tenant = int(
            (
                await service_session.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"aor_{suffix}", "a": f"aor-{suffix}", "n": f"OpenRef {suffix}"},
                )
            ).scalar_one()
        )
        product = await catalogue.upsert_product(
            service_session, tenant, {"name": "ISA", "product_type": "ISA"}
        )

        attached = await catalogue.set_product_platform(
            service_session,
            tenant,
            int(product["id"]),
            "sei",
            {"account_open_reference": "2981141", "role": "custodian"},
        )
        assert attached["account_open_reference"] == "2981141"
        assert attached["provider"] == "sei"
        assert attached["enabled"] is True

        renamed = await catalogue.set_product_platform(
            service_session,
            tenant,
            int(product["id"]),
            "sei",
            {"external_id": "172"},
        )
        assert renamed["external_id"] == "172"
        assert renamed["account_open_reference"] == "2981141"

        listed = await catalogue.products(service_session, tenant)
        refs = listed[0]["platforms"]
        assert refs[0]["account_open_reference"] == "2981141"

        cleared = await catalogue.set_product_platform(
            service_session,
            tenant,
            int(product["id"]),
            "sei",
            {"account_open_reference": ""},
        )
        assert cleared["account_open_reference"] is None
        assert cleared["external_id"] == "172"

        # A product-settings save sends only the template. That must not turn a
        # disabled SEI mapping back on — the mapping form is what sets `enabled`.
        disabled = await catalogue.set_product_platform(
            service_session,
            tenant,
            int(product["id"]),
            "sei",
            {"enabled": False},
        )
        assert disabled["enabled"] is False
        assert disabled["external_id"] == "172"
        retouched = await catalogue.set_product_platform(
            service_session,
            tenant,
            int(product["id"]),
            "sei",
            {"account_open_reference": "2981141"},
        )
        assert retouched["account_open_reference"] == "2981141"
        assert retouched["enabled"] is False

    @pytest.mark.asyncio
    async def test_a_template_save_without_a_role_is_still_a_custodian(
        self, service_session: AsyncSession
    ) -> None:
        """The products form posts only the enrolment template. A null role is
        invisible to the journey, so the shelf looks unconnected and the account
        is opened with no custodian. The provider's declared role fills that
        gap, and a later save that still omits role does not replace one the
        firm has set."""
        from app import catalogue

        suffix = uuid.uuid4().hex[:8]
        tenant = int(
            (
                await service_session.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"rol_{suffix}", "a": f"rol-{suffix}", "n": f"Role {suffix}"},
                )
            ).scalar_one()
        )
        product = await catalogue.upsert_product(
            service_session, tenant, {"name": "GIA", "product_type": "GIA"}
        )
        attached = await catalogue.set_product_platform(
            service_session,
            tenant,
            int(product["id"]),
            "sei",
            {"account_open_reference": "2981399"},
        )
        assert attached["role"] == "custodian"
        assert attached["account_open_reference"] == "2981399"

        kept = await catalogue.set_product_platform(
            service_session,
            tenant,
            int(product["id"]),
            "sei",
            {"role": "wrapper_provider"},
        )
        assert kept["role"] == "wrapper_provider"
        again = await catalogue.set_product_platform(
            service_session,
            tenant,
            int(product["id"]),
            "sei",
            {"external_id": "157"},
        )
        assert again["role"] == "wrapper_provider"
        assert again["external_id"] == "157"
        assert again["account_open_reference"] == "2981399"

    def test_optional_text_treats_absence_as_not_editing(self) -> None:
        from app.catalogue import _optional_text

        assert _optional_text({"external_id": "172"}, "account_open_reference") == (None, 0)
        assert _optional_text(
            {"account_open_reference": " 2981141 "}, "account_open_reference"
        ) == (
            "2981141",
            1,
        )
        assert _optional_text({"account_open_reference": ""}, "account_open_reference") == (None, 1)
        assert _optional_text({"account_open_reference": None}, "account_open_reference") == (
            None,
            1,
        )

    def test_an_unknown_key_is_refused_not_stored(self) -> None:
        """A typo in a key is a setting that silently never applies, which reads as "this product
        is not configured" — the exact failure this area keeps producing."""
        with pytest.raises(CatalogueError) as exc:
            _product_platform_data({"data": {"ml_schema_type": 17}})
        assert "ml_schema_type" in str(exc.value)
        assert "ml_scheme_type" in str(exc.value)  # names what it should have been

    def test_a_non_numeric_value_is_refused(self) -> None:
        with pytest.raises(CatalogueError) as exc:
            _product_platform_data({"data": {"ml_sipp_type": "standard"}})
        assert "whole number" in str(exc.value)

    def test_data_must_be_an_object(self) -> None:
        with pytest.raises(CatalogueError):
            _product_platform_data({"data": [17, 809130002]})

    @pytest.mark.parametrize("blank", [None, ""])
    def test_a_blank_value_is_omitted_rather_than_stored_null(self, blank: object) -> None:
        """Absent is how "not set" is spelled — `sync.scheme_types` reads a missing key as "use
        the deployment's", and a stored null would have to mean the same thing in a second place.
        """
        stored = _product_platform_data({"data": {"ml_scheme_type": 17, "ml_sipp_type": blank}})
        assert json.loads(str(stored)) == {"ml_scheme_type": 17}

    def test_the_allowlist_covers_what_the_sync_reads(self) -> None:
        """The two ends of one contract: these keys exist so `sync.scheme_types` can read them,
        and a rename on either side is a setting that stops applying with nothing to notice it."""
        assert set(PRODUCT_PLATFORM_SETTINGS) == {"ml_scheme_type", "ml_sipp_type"}
        assert "ml_scheme_type" in ml_sync._ACCOUNTS_SQL
        assert "ml_sipp_type" in ml_sync._ACCOUNTS_SQL


def test_a_future_date_of_birth_is_refused() -> None:
    """A future date clears BOTH bounds: `completed_age` returns a negative number, which is under
    every ceiling and below no floor worth setting. A JISA has a ceiling of 17 and no floor, so an
    owner not yet born was accepted — and a slipped year (2027 for 2017) is the ordinary way to
    mistype one."""
    unborn = date.today().replace(year=date.today().year + 1)
    issue = catalogue.product_age_error(name="Junior ISA", min_age=None, max_age=17, dob=unborn)
    assert issue is not None
    assert "future" in issue


def test_a_child_born_today_is_not_refused() -> None:
    """The boundary on the other side: age 0 is a perfectly good JISA owner, so the test is
    `> today` and never `>= today`."""
    assert (
        catalogue.product_age_error(name="Junior ISA", min_age=None, max_age=17, dob=date.today())
        is None
    )
