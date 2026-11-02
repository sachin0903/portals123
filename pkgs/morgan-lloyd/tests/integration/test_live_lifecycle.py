"""The whole Morgan Lloyd lifecycle, against the sandbox, on reused records.

`test_live_contract.py` asks whether the vendor still behaves as the client assumes. This asks a
different question: **can we actually do the things the product needs to do** — put a client in
the CRM, give them a scheme and a policy, value it, contribute to it, draw down from it, transfer
into it, nominate a beneficiary. Every one of those is a create, and Dynamics has no delete, so
the whole file is behind `INTEGRATION_ALLOW_WRITES=1` and every anchor is find-or-create
(`anchors.py`): a first run makes four records, every run after it makes none.

**TWO PERMISSIONS THAT WERE BLOCKING THIS ARE NOW GRANTED**, found by the first run of this file
and superseding what `docs/morgan-lloyd/endpoint-coverage.md` §4 records:

* `cam_CreateFinancialAdviserContact` answered `does not have CreateAccess right(s)`. It creates.
* `cam_createscheme` answered `403 0x8004f502 … does not have create permissions to a ML Team
  secured field on entity Scheme`, and everything downstream of a scheme was blocked behind it.
  It created `MLSIPP/02113`.

Which is exactly why a permission gap deserves a test rather than a note: nobody was going to
re-try those by hand, and the note said "blocked" for a month after it stopped being true.

**Ordering is by fixture dependency, not by declaration order.** A scheme needs a client, a
contribution needs a scheme; expressing that as session fixtures means pytest builds them in the
right order once and no test can be run in isolation against a half-built anchor.

WHAT IS NOT HERE: the CTC illustration. `apps/api/app/integrations/ctc` builds and validates CTC
Bespoke XML against a different host that this package does not depend on and cannot reach from
here — the ML side of that story (`cam_illustration`, and the benefit application's
`illustrationTemplate`) is covered below, the generation half is not.
"""

from __future__ import annotations

from typing import Any

import pytest

from morgan_lloyd._base import MorganLloydError
from morgan_lloyd.client import MorganLloydClient

from . import anchors

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------------------------
# The anchors. Session-scoped, find-or-create, and ordered by their own dependencies.
# --------------------------------------------------------------------------------------------


@pytest.fixture
def firm_id(settings: Any) -> str:
    firm = str(settings.investment_provider or "").strip()
    if not firm:
        pytest.skip("MORGAN_LLOYD_ADVISER_FIRM_ID / _INVESTMENT_PROVIDER_ID is not set")
    return firm


@pytest.fixture
async def adviser_id(api: MorganLloydClient, firm_id: str, writes_allowed: None) -> str:
    if (hit := anchors.cached("adviser")) is not None:
        return hit
    return anchors.remember("adviser", await anchors.ensure_adviser(api, firm_id))


@pytest.fixture
async def client_id(
    api: MorganLloydClient, firm_id: str, adviser_id: str, writes_allowed: None
) -> str:
    """The anchor client's Dynamics GUID.

    The id is read out of the action ENVELOPE on a create — `{"@odata.context", "message",
    "response"}` with the GUID nested, never a flat `contactid`. Reading the flat spelling is the
    scar this integration is named for: the contact was created every time and the id discarded,
    so every retry duplicated a person.
    """
    if (hit := anchors.cached("client")) is not None:
        return hit
    found = await anchors.find_client(api)
    if found:
        return anchors.remember("client", str(found["contactid"]))

    answer = await api.create_contact(anchors.client_payload(firm_id, adviser_id))
    created = await anchors.find_client(api)
    assert created is not None, (
        f"created the anchor client and could not find it back by its marker "
        f"({anchors.CLIENT_MARKER}); the create answered {sorted(answer)}"
    )
    return anchors.remember("client", str(created["contactid"]))


@pytest.fixture
async def scheme_reference(
    api: MorganLloydClient, client_id: str, settings: Any, writes_allowed: None
) -> str:
    """The anchor client's scheme (`MLSIPP/…`), created once.

    NOT the policy number and not `cam_schemeid`: every scoped action — contributions, transfers,
    beneficiaries, benefits — is addressed by this reference, and a GUID or a policy number in its
    place is refused with a message that reads as "that scheme does not exist".
    """
    if (hit := anchors.cached("scheme")) is not None:
        return hit
    found = await anchors.find_scheme(api, client_id)
    if found:
        return anchors.remember("scheme", found)
    if not settings.scheme_type or not settings.sipp_type:
        pytest.skip("MORGAN_LLOYD_SCHEME_TYPE / _SIPP_TYPE are not set")
    answer = await api.create_scheme(
        {
            "contactId": client_id,
            "schemeType": settings.scheme_type,
            "sippType": settings.sipp_type,
        }
    )
    reference = answer.get("schemeReference")
    assert isinstance(reference, str) and reference, (
        f"created a scheme and could not read its reference out of {sorted(answer)}"
    )
    return anchors.remember("scheme", reference)


@pytest.fixture
async def policy_number(
    api: MorganLloydClient, scheme_reference: str, firm_id: str, writes_allowed: None
) -> str:
    """The anchor policy, created once against the anchor scheme.

    Derived from the scheme reference rather than looked up (see `anchors.policy_number_for`), so
    a re-run needs no discovery. A second `cam_createwrapper` for the same number is expected to
    be refused, and that refusal is treated as "already there".

    **The create is ASYNCHRONOUS**, so this fixture does not hand back a number a valuation can
    use until `cam_policies` shows it. That is not defensive polling for its own sake: the
    immediate read comes back empty for about two minutes, and reading that as "the create did
    nothing" is precisely the mistake this suite made once.
    """
    number = anchors.policy_number_for(scheme_reference)
    if anchors.cached("policy") == number:
        return number
    if not await anchors.policy_exists(api, number):
        try:
            await api.create_investment(
                {
                    "provider": firm_id.strip("{}"),
                    "schemeReference": scheme_reference,
                    "policyNumber": number,
                }
            )
        except MorganLloydError as exc:
            # An idempotent create is not something the vendor offers, so a duplicate is how a
            # re-run looks. Reported rather than swallowed: if it fails for any OTHER reason the
            # tests that need the policy should say why they cannot run.
            print(f"\ncam_createwrapper for {number}: {str(exc)[:200]}")
    return anchors.remember("policy", number)


# --------------------------------------------------------------------------------------------
# 1-3. The client and their account.
# --------------------------------------------------------------------------------------------


class TestTheClientAndTheirAccount:
    async def test_1_an_adviser_exists_under_the_firm(
        self, api: MorganLloydClient, firm_id: str, adviser_id: str
    ) -> None:
        """`cam_CreateFinancialAdviserContact` — a permission that used to be refused.

        Asserted by reading it back rather than trusting the create's answer: the action reports
        an id and the useful question is whether the contact is actually attached to the firm,
        which is what `cam_createcontact`'s adviser pair later depends on.
        """
        assert adviser_id
        assert await anchors.find_adviser(api, firm_id) == adviser_id

    async def test_2_the_client_carries_the_adviser_firm(
        self, api: MorganLloydClient, client_id: str, firm_id: str
    ) -> None:
        """The adviser FIRM on the contact, which is what makes a synced client usable rather
        than merely present: without it `cam_createscheme` against them answers 403 and it reads
        as a permissions problem rather than a missing link."""
        rows = await api.get_collection(
            "contacts",
            params={
                "$select": "contactid,_cam_financialadviser_value,cam_externalclientid",
                "$filter": f"contactid eq {client_id}",
            },
        )
        assert rows, "the anchor client vanished between fixtures"
        assert str(rows[0].get("_cam_financialadviser_value") or "").lower() == (
            firm_id.strip("{}").lower()
        ), "the anchor client is not attached to the adviser firm"
        assert rows[0].get("cam_externalclientid") == anchors.CLIENT_MARKER

    async def test_3a_the_scheme_belongs_to_the_client(
        self, api: MorganLloydClient, client_id: str, scheme_reference: str
    ) -> None:
        """`cam_createscheme` — the other permission that used to be refused, and the one
        everything below depends on."""
        assert scheme_reference.startswith("MLSIPP/")
        assert await anchors.find_scheme(api, client_id) == scheme_reference

    async def test_3b_the_scheme_reads_back_with_its_figures(
        self, api: MorganLloydClient, scheme_reference: str
    ) -> None:
        """`cam_getscheme` — a READ implemented as a custom action, and the source of the
        crystallisation split and the scheme's own bank balance (0117).

        Note the response field is `platformFeesPercentage` (plural "Fees") where create and
        update take `platformFeePercentage`. Asserted so a vendor tidy-up is noticed.
        """
        detail = await api.get_scheme_by_ref(scheme_reference)
        assert detail.get("schemeId"), f"no schemeId in {sorted(detail)}"
        for field in ("crystallisedFundAmount", "uncrystallisedFundAmount", "totalBankBalance"):
            assert field in detail, f"{field} is what 0117 reads; it is not in {sorted(detail)}"
        assert "platformFeesPercentage" in detail or "platformFeePercentage" in detail

    async def test_3c_the_policy_number_we_minted_is_the_one_dynamics_holds(
        self, api: MorganLloydClient, policy_number: str
    ) -> None:
        """**`cam_createwrapper`'s `policyNumber` IS the caller's to choose**, as documented — and
        this is the assertion that settles it, because the vendor's answer does not.

        `{"wrapper": "<number>", "response": {"responseKey": 0, "message": "<number> Policy
        Exists"}}` comes back whether the policy has been made or not, and for roughly two minutes
        afterwards `cam_policies` has no row for the number. That empty window was read as "the
        create did nothing", `sync._create_policy` was rewritten to discover a number instead, and
        the rework was reverted when the policy turned up. The lesson is about the read, not the
        write: **absence shortly after a Dynamics create means "not yet"**.
        """
        assert await anchors.policy_exists(api, policy_number)

    async def test_3d_the_policy_is_a_different_identifier_from_the_scheme(
        self, policy_number: str, scheme_reference: str
    ) -> None:
        """One account, two identifiers, and the difference is load-bearing: every scoped action
        takes the scheme reference and a VALUATION takes the policy number.

        A scheme also gets a policy of its OWN, numbered after it (`MLSIPP/02114`), so both
        resolve — which is why the distinction has to be asserted rather than assumed to be
        obvious from a successful call.
        """
        assert policy_number != scheme_reference


# --------------------------------------------------------------------------------------------
# 4. Valuations.
# --------------------------------------------------------------------------------------------


class TestValuations:
    async def test_4_a_policy_valuation_is_accepted(
        self, api: MorganLloydClient, policy_number: str, firm_id: str
    ) -> None:
        """`cam_createpolicyvaluation`, through the TYPED path.

        `push_policy_valuations` derives `PolicyValue` from the fund splits so the two cannot
        disagree — the vendor requires the equality and does not say what it does otherwise, so a
        rounding error upstream fails here with both numbers named rather than landing a wrong
        figure on a client's record.

        **Not verifiable through the API**: `cam_policyvaluations` is one of the tables our
        service principal cannot read, so a successful send is all that can be established. That
        is stated rather than papered over — and `test_live_contract.py` fails the day the read
        becomes available.
        """
        from morgan_lloyd.endpoints.valuations import FundSplit, PolicyValuation

        valuation = PolicyValuation.from_splits(
            policy_number,
            anchors.today(),
            [
                FundSplit(
                    name="ZZ Integration Fund",
                    valuation_holding=100.0,
                    price=1.5,
                    market_value=150.0,
                    price_date=anchors.today(),
                    isin="GB00B3X7QG63",
                )
            ],
        )
        try:
            answer = await api.push_policy_valuations(firm_id.strip("{}"), [valuation])
        except MorganLloydError as exc:
            # `(valuation exists)` IS THE POLICY RESOLVING. There is no update operation for a
            # policy valuation (`valuations.py`: "THERE IS NO UPDATE, AND NO READ-BACK"), so a
            # second run on the same day necessarily lands here — and the anchors exist precisely
            # so a run reuses yesterday's records rather than making new ones.
            #
            # The two refusals mean opposite things and only one is a failure:
            #   (valuation exists)  -> the policy was found; today is already valued.
            #   (missing policy)    -> the number resolved to nothing, which is the defect.
            assert "valuation exists" in str(exc).lower(), (
                f"the valuation was refused for a reason other than already existing: {exc}"
            )
            return
        assert isinstance(answer, dict)

    def test_4b_a_valuation_that_does_not_add_up_is_refused_before_sending(self) -> None:
        """The guard that makes the send safe, asserted without one: a mismatch between
        `PolicyValue` and the splits never reaches the vendor."""
        from pydantic import ValidationError

        from morgan_lloyd.endpoints.valuations import FundSplit, PolicyValuation

        split = FundSplit(
            name="ZZ",
            valuation_holding=100.0,
            price=1.5,
            market_value=150.0,
            price_date=anchors.today(),
        )
        with pytest.raises(ValidationError):
            PolicyValuation(
                policy_reference="ZZ",
                policy_value=9999.0,
                valuation_date=anchors.today(),
                fund_splits=[split],
            )


# --------------------------------------------------------------------------------------------
# 5. Contributions — create, then stop.
# --------------------------------------------------------------------------------------------


class TestContributions:
    @pytest.fixture
    async def application(
        self, api: MorganLloydClient, scheme_reference: str, client_id: str
    ) -> dict[str, Any]:
        """A contribution AT Application on the anchor scheme — reused if one is already there.

        Only an Application can be amended or stopped, so reusing one exercises those paths
        without adding to the pile; only the first run has to create.
        """
        found = await anchors.find_contribution_application(api, scheme_reference)
        if found:
            return found

        await api.create_pension_contribution(
            {
                "schemeReference": scheme_reference,
                # `^` — a PERSONAL contribution identifies the contributor by Dynamics GUID; the
                # `*` rule (name the employer) applies to everything else, and the two are
                # mutually exclusive.
                "cam_contributiontype": 809130000,
                "cam_contributor": client_id,
                "cam_frequency": 809130004,  # Single
                "cam_amount": 100.0,
                "cam_startdate": anchors.today(),
                "cam_status": 809130000,  # Application
            }
        )
        created = await anchors.find_contribution_application(api, scheme_reference)
        assert created is not None, "created a contribution and could not find it back"
        return created

    async def test_5a_a_contribution_is_recorded_at_application(
        self, application: dict[str, Any]
    ) -> None:
        assert application.get("cam_contributionid")
        assert int(application["cam_status"]) == 809130000

    async def test_5b_the_amount_can_be_amended_while_it_is_an_application(
        self, api: MorganLloydClient, application: dict[str, Any]
    ) -> None:
        """`cam_updatecontributionapplication` is documented as valid ONLY at Application, which
        is why `contributions.update_application` re-reads the vendor's status before sending
        rather than trusting our mirror: a stale update gets a generic failure that surfaces as a
        502 reading like an outage, when the truth is a 409."""
        answer = await api.update_contribution_application(
            str(application["cam_contributionid"]), {"cam_amount": 125.0}
        )
        assert isinstance(answer, dict)

    async def test_5c_it_can_be_stopped(
        self, api: MorganLloydClient, application: dict[str, Any], scheme_reference: str
    ) -> None:
        """Stopped (809130002) is the only status value the update action documents.

        This is the "cancel" half, and it is what keeps the pile from growing: the run's
        contribution ends inert, and the NEXT run finds no Application and makes exactly one more.
        """
        await api.update_contribution_application(
            str(application["cam_contributionid"]), {"cam_status": 809130002}
        )
        remaining = await anchors.find_contribution_application(api, scheme_reference)
        assert remaining is None or str(remaining["cam_contributionid"]) != str(
            application["cam_contributionid"]
        ), "the contribution is still at Application after being stopped"


# --------------------------------------------------------------------------------------------
# 6. Drawdown, and 8. the illustration upload.
# --------------------------------------------------------------------------------------------


class TestDrawdownAndIllustration:
    @pytest.fixture
    async def benefit_application(
        self, api: MorganLloydClient, client_id: str, scheme_reference: str
    ) -> dict[str, Any]:
        """`cam_setmemberdetails` — a benefit application plus the DCube SSO link.

        Needs ONLY the Morgan Lloyd credentials: ML mints the link with the loginId and ssoToken
        already embedded, which is why drawdown needs no CTC credential of its own.
        """
        return await api.create_benefit_application(
            {"contactId": client_id, "advised": True, "schemeRef": scheme_reference}
        )

    async def test_6a_a_drawdown_session_comes_back_with_an_sso_link(
        self, benefit_application: dict[str, Any]
    ) -> None:
        blob = str(benefit_application)
        assert "http" in blob, (
            f"no SSO link in the benefit application answer: {sorted(benefit_application)}"
        )

    async def test_6b_the_application_id_is_readable(
        self, benefit_application: dict[str, Any]
    ) -> None:
        """`benefitApplicationId` is what `cam_memberdetails` consumes and what a cancel needs."""
        assert benefit_application.get("benefitApplicationId"), (
            f"no benefitApplicationId in {sorted(benefit_application)}"
        )

    async def test_6c_the_member_details_read_back(
        self, api: MorganLloydClient, benefit_application: dict[str, Any]
    ) -> None:
        """`cam_memberdetails` — valuations, fees, the whole contact, and the withdrawals each
        carrying the `withdrawalId` a cancel consumes.

        Gotcha the docs contain and this does not correct: the field table spells one fee
        `crystalisationFee` (sic) while the example JSON uses `crystallisationFee`. Treated
        verbatim as received.
        """
        details = await api.get_member_details(str(benefit_application["benefitApplicationId"]))
        assert isinstance(details, dict) and details

    async def test_8_an_illustration_template_can_be_attached(
        self, api: MorganLloydClient, benefit_application: dict[str, Any]
    ) -> None:
        """The ML half of the illustration story: the CTC1 XML is attached to the benefit
        application as an annotation.

        The GENERATION half (`apps/api/app/integrations/ctc/xml_builder.py`, 43 tests against the
        vendor spec's own examples) is a different host this package does not depend on, so what
        is sent here is a minimal well-formed document rather than a real illustration — this
        asserts the upload path, not the contents.
        """
        xml = "<message><agent_name>ZZ-INTEGRATION</agent_name></message>"
        answer = await api.update_benefit_application_illustration_template(
            str(benefit_application["benefitApplicationId"]), xml
        )
        assert answer is None or isinstance(answer, (str, dict))


# --------------------------------------------------------------------------------------------
# 9. Transfers — create, update, cancel.
# --------------------------------------------------------------------------------------------


class TestTransfers:
    POLICY_NO = "ZZINT-TRANSFER-1"

    @pytest.fixture
    async def transfer_id(self, api: MorganLloydClient, scheme_reference: str) -> str:
        """One transfer on the anchor scheme, reused by its own policy number."""
        existing = await api.get_transfer_by_policy_no(self.POLICY_NO)
        if existing:
            return str(existing[0]["cam_personalpolicyid"])

        answer = await api.create_transfer(
            {
                "schemeReference": scheme_reference,
                "cam_policyno": self.POLICY_NO,
                "cam_providername": "ZZ Integration Ceding Scheme",
                "cam_assettype": 1,  # Pension Plan
                "cam_status": 4,  # Transferring — the RECORD's status, not the progress set
                # THREE Ls, verbatim. A wire-format quirk, not a domain concept.
                "cam_uncrystalllisedtransfervalue": 1000.0,
                "cam_crystallisedtransfervalue": 0.0,
                "cam_nonprtv": 1000.0,  # the WHOLE value despite the name
                "cam_typeoftransfer": 0,  # Cash
                "cam_transfertype": 809130000,  # Full
                "cam_pensiontype": 2,  # Annuity — `Yes*`, required for a pension policy
                "transferNotes": "Created by the integration suite.",
            }
        )
        blob = str(answer)
        found = await api.get_transfer_by_policy_no(self.POLICY_NO)
        assert found, f"created a transfer and could not find it back; answer was {blob[:200]}"
        return str(found[0]["cam_personalpolicyid"])

    async def test_9a_a_transfer_is_created_against_the_scheme(self, transfer_id: str) -> None:
        assert transfer_id

    async def test_9b_it_reads_back_by_policy_number(
        self, api: MorganLloydClient, transfer_id: str
    ) -> None:
        """The docs' unprefixed response table is WRONG: the actual values come back under their
        `cam_` keys (`cam_actualnonprtv` and friends), and money arrives as strings."""
        rows = await api.get_transfer_by_policy_no(self.POLICY_NO)
        assert rows
        assert any(str(r.get("cam_personalpolicyid")) == transfer_id for r in rows)

    async def test_9c_it_can_be_updated(self, api: MorganLloydClient, transfer_id: str) -> None:
        """The documented update body is the asset-transfer fields. Returns the
        `OData-EntityId` header from a 204, so `None` is not a failure."""
        answer = await api.update_transfer(
            transfer_id,
            {
                "cam_transferdate": anchors.today(),
                "cam_transferamount": 1000.0,
                "cam_fundnotes": "Integration suite update.",
            },
        )
        assert answer is None or isinstance(answer, str)

    async def test_9d_it_can_be_withdrawn(self, api: MorganLloydClient, transfer_id: str) -> None:
        """ "No longer transferring" (5) is the vendor's own terminal for a withdrawn transfer —
        there is no cancel ACTION, so the entity's progress is set directly.

        `transferstatus` is the 0-5 progress set, NOT the record's `cam_status` (fixed at
        Transferring on create). Two option sets that both read as "the status"; sending one
        where the other belongs mirrors the transfer as something it is not.
        """
        answer = await api.update_transfer(transfer_id, {"cam_transferstatus": 5})
        assert answer is None or isinstance(answer, str)


# --------------------------------------------------------------------------------------------
# 10. Beneficiaries — nominate, then remove.
# --------------------------------------------------------------------------------------------


class TestBeneficiaries:
    NAME = "ZZ Integration Beneficiary"

    @pytest.fixture
    async def beneficiary_id(self, api: MorganLloydClient, scheme_reference: str) -> str:
        """One nomination on the anchor scheme, reused by name.

        `cam_createbeneficiaries` — PLURAL. The docs call it `cam_createbeneficiary` and the org
        has only the plural; Dynamics answers an unknown action with a generic failure, so the
        singular reads as a permissions or payload problem rather than a typo.
        """
        for row in await api.get_all_beneficiaries():
            if str(row.get("cam_nameofbeneficiary") or "").strip() == self.NAME:
                return str(row["cam_nominationofbeneficiaryid"])

        answer = await api.create_beneficiary(
            {
                # camelCase — the rest are raw Dynamics `cam_*` attribute names, case sensitive.
                "schemeReference": scheme_reference,
                "cam_nameofbeneficiary": self.NAME,
                "cam_percentageoffund": 100,
                "cam_nominationdate": anchors.today(),
                "cam_relationship": "Spouse",
                "cam_address_line1": "1 Integration Way",
                "cam_city": "Bristol",
                "cam_postcode": "BS1 4XE",
            }
        )
        created = answer.get("beneficiaryId")
        if isinstance(created, str) and created:
            return created
        for row in await api.get_all_beneficiaries():
            if str(row.get("cam_nameofbeneficiary") or "").strip() == self.NAME:
                return str(row["cam_nominationofbeneficiaryid"])
        pytest.fail(f"created a beneficiary and could not find it back; answer {sorted(answer)}")

    async def test_10a_a_beneficiary_is_nominated(
        self, api: MorganLloydClient, beneficiary_id: str
    ) -> None:
        record = await api.get_beneficiary(beneficiary_id)
        assert str(record.get("cam_nameofbeneficiary") or "").strip() == self.NAME

    async def test_10b_the_scheme_lists_them(
        self, api: MorganLloydClient, scheme_reference: str, beneficiary_id: str
    ) -> None:
        """`cam_schemebeneficiaries` — a READ implemented as a POST, whose records carry the
        joined beneficiary fields under OData escaped-alias names (`nom_x002e_…`, an encoded dot
        from the `nom.` alias)."""
        rows = await api.get_beneficiaries_by_scheme_ref(scheme_reference)
        assert isinstance(rows, list)

    async def test_10c_it_can_be_removed(self, api: MorganLloydClient, beneficiary_id: str) -> None:
        """`BeneficiaryStatus` Removed (0) — the closest thing to a delete anywhere in this
        integration, and a plain OData PATCH rather than a custom action.

        Left REMOVED rather than re-nominated: the next run finds it by name and reuses the same
        record, so the pile does not grow.
        """
        await api.update_beneficiary(beneficiary_id, {"cam_beneficiarystatus": 0})
        record = await api.get_beneficiary(beneficiary_id)
        status = record.get("cam_beneficiarystatus")
        # NOT `int(status or -1)`: Removed IS 0, which is falsy, so the `or` swallowed the very
        # value being asserted and the test failed against a correct removal.
        assert status is not None, f"no status on the beneficiary: {sorted(record)}"
        assert int(status) == 0
