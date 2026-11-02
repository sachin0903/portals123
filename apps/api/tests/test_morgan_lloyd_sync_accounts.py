"""Account-scoped Morgan Lloyd sync: one-account push, adviser-first, beneficiaries."""

import httpx
import pytest

from app.integrations.morgan_lloyd import sync
from tests.test_morgan_lloyd_sync import (
    _VENDOR_ANSWERS,
    ADVISER,
    BENEFICIARY,
    CLIENT,
    ISA,
    SIPP,
    _FakeApi,
    _install,
    _link,
    _session,
)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> _FakeApi:
    stub = _FakeApi(**_VENDOR_ANSWERS)
    _install(monkeypatch, stub)
    return stub


class TestPushingOneAccount:
    """`account_id` narrows the second half — the account record's own push.

    ONE FUNCTION WITH A FILTER, not a second entry point, and these cases are what that buys: the
    contact still comes first, each step still re-reads its link, and a per-account failure is
    still reported rather than raised. A second implementation would have to be given all of that
    again, and the half that gets forgotten is always the resume.
    """

    async def test_it_pushes_the_client_first_then_only_that_account(self, api: _FakeApi) -> None:
        """A scheme is raised AGAINST a contact and Morgan Lloyd refuses one that does not exist,
        so pushing an account necessarily pushes the client. The OTHER pension is left alone."""
        second = {**SIPP, "id": 902, "uid": "acc000000003", "name": "Achebe SIPP II"}
        session = _session(accounts=[SIPP, second])

        report = await sync.sync_client(session, contact_id=41, account_id=900)

        assert [name for name, _ in api.calls] == [
            "create_contact",
            "create_scheme",
            "create_investment",
        ]
        assert report["contact"]["status"] == "created"
        assert [step["account_id"] for step in report["accounts"]] == [900]

    async def test_a_second_press_creates_nothing(self, api: _FakeApi) -> None:
        """The links ARE the idempotence, and this is the button an adviser will press twice.
        `skipped` is the success: a retry must not put a second human being in the CRM."""
        already = {**SIPP, "ml_scheme_ref": "MLSIPP/02039", "ml_policy_ref": SIPP["uid"]}
        session = _session(client={**CLIENT, "ml_contact_id": "9f2c-existing"}, accounts=[already])

        report = await sync.sync_client(session, contact_id=41, account_id=900)

        assert api.calls == []
        assert report["contact"]["status"] == "skipped"
        assert report["accounts"][0]["status"] == "skipped"
        assert report["accounts"][0]["policy_status"] == "skipped"

    async def test_a_wrapper_with_no_scheme_is_refused_by_name(self, api: _FakeApi) -> None:
        """Asked FOR a GIA, silence is the wrong answer: the caller named this account, so the
        refusal says why. The whole-client push still SKIPS one, because there it is not an ask."""
        session = _session(accounts=[ISA])

        with pytest.raises(ValueError, match="administers pension schemes"):
            await sync.sync_client(session, contact_id=41, account_id=901)
        assert api.calls == []

    async def test_an_account_of_another_client_is_not_found(self, api: _FakeApi) -> None:
        """The account is filtered out of THIS client's list rather than trusted from the request,
        so a uid that resolves elsewhere cannot borrow this contact's GUID."""
        session = _session(accounts=[SIPP])

        with pytest.raises(LookupError, match="not one of this client's"):
            await sync.sync_client(session, contact_id=41, account_id=999)
        assert api.calls == []

    async def test_a_failed_policy_still_reports_the_scheme_it_created(self, api: _FakeApi) -> None:
        """A scheme with no policy is a usable record — contributions and transfers address the
        scheme — that simply cannot be valued yet. Collapsing the two into one failure would say
        the account never reached Morgan Lloyd, which is false and sends somebody to create it
        again."""
        session = _session()
        api.answers["create_investment"] = sync.MorganLloydUnavailable("cam_createwrapper refused")

        report = await sync.sync_client(session, contact_id=41, account_id=900)

        step = report["accounts"][0]
        assert step["status"] == "created"
        assert step["scheme_reference"] == "MLSIPP/02039"
        assert step["policy_status"] == "failed"
        assert _link(session, "account", "policy") is None


class TestTheAssignedAdviserIsSyncedFirst:
    """`cam_createcontact` requires the adviser CONTACT alongside the firm. The client's
    adviser is created once, recorded on `provider_refs`, and attached to the contact."""

    async def test_an_unlinked_adviser_is_created_before_the_contact(self, api: _FakeApi) -> None:
        api.answers["create_financial_adviser_contact"] = {
            "financialAdviserContactId": "bbbbbbbb-1111-2222-3333-444455556666"
        }
        session = _session(adviser=ADVISER)
        report = await sync.sync_client(session, contact_id=41)

        assert [name for name, _ in api.calls][:2] == [
            "get_financial_adviser_contacts",
            "create_financial_adviser_contact",
        ]
        assert report["adviser"] == {
            "status": "created",
            "external_id": "bbbbbbbb-1111-2222-3333-444455556666",
        }
        assert session.provider_refs == [
            {
                "u": ADVISER["user_id"],
                "ext": "bbbbbbbb-1111-2222-3333-444455556666",
                "tenant_id": 3,
            }
        ]
        attached = api.called("create_contact")[0]["cam_FinancialAdviserContact"]
        assert attached == "/contacts(bbbbbbbb-1111-2222-3333-444455556666)"

    async def test_an_already_linked_adviser_is_not_created_again(self, api: _FakeApi) -> None:
        session = _session(adviser={**ADVISER, "ml_adviser_id": "already-there"})
        report = await sync.sync_client(session, contact_id=41)

        assert api.called("create_financial_adviser_contact") == []
        assert api.called("get_financial_adviser_contacts") == []
        assert report["adviser"]["status"] == "skipped"
        assert report["adviser"]["external_id"] == "already-there"
        assert api.called("create_contact")[0]["cam_FinancialAdviserContact"] == (
            "/contacts(already-there)"
        )

    async def test_a_name_match_under_the_firm_is_reused_not_recreated(self, api: _FakeApi) -> None:
        """`cam_CreateFinancialAdviserContact` has no read of its own. If `provider_refs` was
        lost, matching first+last under the firm is what stops a retry minting a second person."""
        api.answers["get_financial_adviser_contacts"] = [
            {
                "firstname": "Rowan",
                "lastname": "Pryce",
                "contactid": "cccccccc-0000-0000-0000-000000000001",
            }
        ]
        session = _session(adviser=ADVISER)
        report = await sync.sync_client(session, contact_id=41)

        assert api.called("create_financial_adviser_contact") == []
        assert report["adviser"]["status"] == "skipped"
        assert report["adviser"]["external_id"] == "cccccccc-0000-0000-0000-000000000001"
        assert session.provider_refs[0]["ext"] == "cccccccc-0000-0000-0000-000000000001"

    async def test_no_assigned_adviser_falls_back_to_the_configured_contact(
        self, api: _FakeApi
    ) -> None:
        """The deployment's `adviser_contact_id` is the firm-level record, and it is what
        `cam_createcontact` already attached before this step existed."""
        session = _session()
        report = await sync.sync_client(session, contact_id=41)

        assert api.called("create_financial_adviser_contact") == []
        assert report["adviser"]["status"] == "skipped"
        assert report["adviser"]["external_id"] == "4dc533b3-0000-0000-0000-000000000000"
        assert api.called("create_contact")[0]["cam_FinancialAdviserContact"] == (
            "/contacts(4dc533b3-0000-0000-0000-000000000000)"
        )

    async def test_a_nameless_adviser_is_reported_not_raised(self, api: _FakeApi) -> None:
        session = _session(adviser={**ADVISER, "first_name": "", "last_name": ""})
        report = await sync.sync_client(session, contact_id=41)

        assert api.called("create_financial_adviser_contact") == []
        assert report["adviser"]["status"] == "failed"
        assert "first name" in report["adviser"]["error"]
        # The contact still goes out, attached to the configured firm-level adviser.
        assert report["contact"]["status"] == "created"


class TestBeneficiariesAreNominatedAgainstTheScheme:
    async def test_nominations_are_created_after_the_scheme(self, api: _FakeApi) -> None:
        api.answers["create_beneficiary"] = {
            "beneficiaryId": "dddddddd-0000-0000-0000-000000000001"
        }
        session = _session(beneficiaries=[BENEFICIARY])
        report = await sync.sync_client(session, contact_id=41)

        assert [name for name, _ in api.calls] == [
            "create_contact",
            "create_scheme",
            "create_investment",
            "create_beneficiary",
        ]
        sent = api.called("create_beneficiary")[0]
        assert sent["schemeReference"] == "MLSIPP/02039"
        assert sent["cam_nameofbeneficiary"] == "Mrs Ada Achebe"
        assert sent["cam_percentageoffund"] == 100
        assert sent["cam_relationship"] == "Spouse"
        assert sent["cam_nominationdate"] == "2020-05-26"
        assert sent["cam_address_line1"] == "4 Queen Square"
        step = report["accounts"][0]["beneficiaries"][0]
        assert step["status"] == "created"
        assert _link(session, "beneficiary", "primary") == {
            "tenant_id": 3,
            "kind": "beneficiary",
            "eid": 17,
            "ext": "dddddddd-0000-0000-0000-000000000001",
            "link_type": "primary",
        }

    async def test_an_already_linked_nomination_is_not_created_again(self, api: _FakeApi) -> None:
        session = _session(
            beneficiaries=[{**BENEFICIARY, "ml_beneficiary_id": "already-nominated"}]
        )
        report = await sync.sync_client(session, contact_id=41)

        assert api.called("create_beneficiary") == []
        assert report["accounts"][0]["beneficiaries"][0]["status"] == "skipped"

    async def test_the_members_address_is_the_fallback_when_the_nomination_has_none(
        self, api: _FakeApi
    ) -> None:
        """The account panel does not currently collect an address. Inventing one would put a
        false address on a death-benefit record; the member's is the one an expression-of-wish
        form typically already holds."""
        api.answers["create_beneficiary"] = {
            "beneficiaryId": "dddddddd-0000-0000-0000-000000000001"
        }
        session = _session(beneficiaries=[{**BENEFICIARY, "address": None}])
        await sync.sync_client(session, contact_id=41)

        sent = api.called("create_beneficiary")[0]
        assert sent["cam_address_line1"] == CLIENT["address_line1"]
        assert sent["cam_city"] == CLIENT["address_city"]
        assert sent["cam_postcode"] == CLIENT["address_postcode"]

    async def test_a_nomination_without_a_relationship_is_reported_not_raised(
        self, api: _FakeApi
    ) -> None:
        session = _session(beneficiaries=[{**BENEFICIARY, "relationship": None}])
        report = await sync.sync_client(session, contact_id=41)

        assert api.called("create_beneficiary") == []
        step = report["accounts"][0]
        assert step["status"] == "created"
        assert step["beneficiaries"][0]["status"] == "failed"
        assert "relationship" in step["beneficiaries"][0]["error"]

    async def test_a_failed_policy_still_nominates(self, api: _FakeApi) -> None:
        """Beneficiaries are addressed by the scheme reference, not the policy."""
        api.answers["create_investment"] = sync.MorganLloydUnavailable("cam_createwrapper refused")
        api.answers["create_beneficiary"] = {
            "beneficiaryId": "dddddddd-0000-0000-0000-000000000001"
        }
        session = _session(beneficiaries=[BENEFICIARY])
        report = await sync.sync_client(session, contact_id=41)

        assert report["accounts"][0]["policy_status"] == "failed"
        assert report["accounts"][0]["beneficiaries"][0]["status"] == "created"

    async def test_a_failed_scheme_does_not_attempt_nominations(self, api: _FakeApi) -> None:
        api.answers["create_scheme"] = httpx.ConnectError("boom")
        session = _session(beneficiaries=[BENEFICIARY])
        report = await sync.sync_client(session, contact_id=41)

        assert api.called("create_beneficiary") == []
        assert "beneficiaries" not in report["accounts"][0]

    async def test_only_this_accounts_nominations_are_pushed(self, api: _FakeApi) -> None:
        other = {**BENEFICIARY, "id": 18, "uid": "ben000000002", "account_id": 902}
        api.answers["create_beneficiary"] = {
            "beneficiaryId": "dddddddd-0000-0000-0000-000000000001"
        }
        session = _session(beneficiaries=[BENEFICIARY, other])
        await sync.sync_client(session, contact_id=41, account_id=900)

        assert len(api.called("create_beneficiary")) == 1
        assert api.called("create_beneficiary")[0]["cam_nameofbeneficiary"] == "Mrs Ada Achebe"
