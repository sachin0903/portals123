"""Scheme type, policy number, and a failure that must not abandon the other accounts.

Split out of ``test_morgan_lloyd_sync`` so that file stays under a thousand lines.
The orchestration cases stay there; these are the vendor-call details of one account.
"""

import logging
from typing import Any

import httpx
import pytest

from app.integrations.morgan_lloyd import sync
from tests.test_morgan_lloyd_sync import (
    _VENDOR_ANSWERS,
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


class TestSchemeTypesComeFromTheProduct:
    async def test_the_products_own_pairing_wins(self, api: _FakeApi) -> None:
        """ML's SIPP covers scheme types 16 AND 17, so a firm with two SIPP products cannot be
        served by one pair of environment variables."""
        session = _session(
            accounts=[{**SIPP, "product_scheme_type": "17", "product_sipp_type": "809130002"}]
        )
        await sync.sync_client(session, contact_id=41)

        sent = api.called("create_scheme")[0]
        assert sent["schemeType"] == 17
        assert sent["sippType"] == 809130002

    async def test_jsonb_strings_are_sent_as_ints(self, api: _FakeApi) -> None:
        """`product_platforms.data` is jsonb, and `Edm.Int32` does not take "17"."""
        session = _session(
            accounts=[{**SIPP, "product_scheme_type": "17", "product_sipp_type": "809130002"}]
        )
        await sync.sync_client(session, contact_id=41)
        sent = api.called("create_scheme")[0]
        assert isinstance(sent["schemeType"], int)
        assert isinstance(sent["sippType"], int)

    async def test_the_deployment_setting_is_the_fallback(self, api: _FakeApi) -> None:
        """A firm with one SIPP has nothing to disambiguate, and that is today's configuration."""
        session = _session()
        await sync.sync_client(session, contact_id=41)
        sent = api.called("create_scheme")[0]
        assert (sent["schemeType"], sent["sippType"]) == (16, 809130016)

    async def test_a_half_configured_product_is_refused_not_completed_from_settings(
        self, api: _FakeApi
    ) -> None:
        """Mixing the product's schemeType with the deployment's sippType would send a pairing
        nobody chose, and the 14 valid pairings are the vendor's."""
        session = _session(
            accounts=[{**SIPP, "product_scheme_type": "17", "product_sipp_type": None}]
        )
        report = await sync.sync_client(session, contact_id=41)

        assert api.called("create_scheme") == []
        assert report["accounts"][0]["status"] == "failed"
        assert "scheme type" in report["accounts"][0]["error"].lower()

    async def test_an_unreadable_product_value_is_refused_not_treated_as_absent(
        self, api: _FakeApi
    ) -> None:
        """A typo means somebody DID configure this and got it wrong. Falling back to the
        deployment pairing would create the scheme under a pairing they explicitly did not
        choose, and the vendor cannot object: 14 pairings are valid, so the wrong valid one
        just succeeds."""
        session = _session(
            accounts=[{**SIPP, "product_scheme_type": "sixteen", "product_sipp_type": None}]
        )
        report = await sync.sync_client(session, contact_id=41)

        assert api.called("create_scheme") == []
        assert report["accounts"][0]["status"] == "failed"
        assert "'sixteen'" in report["accounts"][0]["error"]

    async def test_an_absent_product_value_is_not_a_refusal(self, api: _FakeApi) -> None:
        """The other half of that distinction: nothing configured means use the deployment's."""
        session = _session(
            accounts=[{**SIPP, "product_scheme_type": None, "product_sipp_type": ""}]
        )
        report = await sync.sync_client(session, contact_id=41)
        assert report["accounts"][0]["status"] == "created"
        assert api.called("create_scheme")[0]["schemeType"] == 16

    async def test_neither_configured_names_the_product_and_logs_the_rest(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """There are two places this can be set and only one of them is the firm's.

        The report names the PRODUCT, which an adviser can go and fix; the deployment fallback is
        ours, so it is logged rather than put in front of somebody with no access to it."""
        stub = _FakeApi(
            create_contact={"response": "9f2c1c76-1111-2222-3333-444455556666"},
        )
        _install(monkeypatch, stub, scheme_type=None, sipp_type=None)
        session = _session()
        with caplog.at_level(logging.WARNING):
            report = await sync.sync_client(session, contact_id=41)

        error = report["accounts"][0]["error"]
        assert "product" in error.lower()
        assert "MORGAN_LLOYD" not in error
        assert "MORGAN_LLOYD_SCHEME_TYPE" in caplog.text


class TestThePolicyNumberIsOurs:
    async def test_the_policy_number_is_the_accounts_uid(self, api: _FakeApi) -> None:
        """Documented only as "unique per application", so it is ours to mint — and the account's
        own public id is the one identifier an operator can line up across both systems."""
        session = _session()
        await sync.sync_client(session, contact_id=41)
        assert api.called("create_investment")[0]["policyNumber"] == "acc000000001"

    async def test_the_recorded_reference_is_what_we_sent(self, api: _FakeApi) -> None:
        session = _session()
        await sync.sync_client(session, contact_id=41)
        assert _link(session, "account", "policy")["ext"] == "acc000000001"

    async def test_a_vendor_that_stops_echoing_still_leaves_us_linked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The echo corroborates; it is not the source of the id. Requiring it would lose the
        link to a policy the vendor did create."""
        stub = _FakeApi(**{**_VENDOR_ANSWERS, "create_investment": {"message": "Created"}})
        _install(monkeypatch, stub)
        session = _session()
        report = await sync.sync_client(session, contact_id=41)

        assert report["accounts"][0]["policy_status"] == "created"
        assert _link(session, "account", "policy")["ext"] == "acc000000001"

    async def test_the_link_is_written_although_the_policy_is_not_there_yet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**The vendor creates the policy asynchronously** — about two minutes on the dev org,
        during which `cam_policies` has no row for the number and a valuation against it is
        refused as `(missing policy)`.

        Nothing here reads it back, and that is the argument for minting the number rather than
        discovering it: the link is the value we SENT, so the sync neither waits nor races. An
        earlier attempt to discover the number instead was written against exactly that empty
        read and had to be reverted — the policy was there, two minutes later.

        Asserted through the vendor's own ambiguous answer, which is the same either way.
        """
        stub = _FakeApi(
            **{
                **_VENDOR_ANSWERS,
                "create_investment": {
                    "wrapper": "acc000000001",
                    "response": {"responseKey": 0, "message": "acc000000001 Policy Exists"},
                },
            }
        )
        _install(monkeypatch, stub)
        session = _session()
        report = await sync.sync_client(session, contact_id=41)

        assert report["accounts"][0]["policy_status"] == "created"
        assert _link(session, "account", "policy")["ext"] == "acc000000001"

    async def test_a_mismatched_echo_records_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the vendor answers with a different number, recording either one would assert a
        policy reference this account may not have — and valuations are filed against it."""
        stub = _FakeApi(**{**_VENDOR_ANSWERS, "create_investment": {"wrapper": "SOMETHING/ELSE"}})
        _install(monkeypatch, stub)
        session = _session()
        report = await sync.sync_client(session, contact_id=41)

        assert report["accounts"][0]["policy_status"] == "failed"
        assert _link(session, "account", "policy") is None
        assert "SOMETHING/ELSE" in report["accounts"][0]["policy_error"]

    async def test_the_echo_is_read_out_of_the_envelope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The custom actions nest their answer under `response`, the trap `_created_id`
        documents — so a correct echo there must not read as a mismatch."""
        stub = _FakeApi(
            **{**_VENDOR_ANSWERS, "create_investment": {"response": {"wrapper": "acc000000001"}}}
        )
        _install(monkeypatch, stub)
        session = _session()
        report = await sync.sync_client(session, contact_id=41)
        assert report["accounts"][0]["policy_status"] == "created"

    async def test_no_provider_configured_is_refused_before_the_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _FakeApi(**{k: v for k, v in _VENDOR_ANSWERS.items() if k != "create_investment"})
        _install(monkeypatch, stub, adviser_firm_id="", investment_provider_id="")
        session = _session()
        report = await sync.sync_client(session, contact_id=41)

        assert stub.called("create_investment") == []
        assert report["accounts"][0]["policy_status"] == "failed"
        # Reported as unavailable; the missing setting is in the log, not in a report an adviser
        # reads. The scheme still stands, which is what `policy_status` exists to say.
        assert "MORGAN_LLOYD" not in report["accounts"][0]["policy_error"]

    async def test_the_provider_guid_is_sent_unbraced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A braced GUID is refused (`Invalid Format`), the lesson `build_contact` records."""
        stub = _FakeApi(**{**_VENDOR_ANSWERS, "create_investment": {"wrapper": "acc000000001"}})
        _install(monkeypatch, stub, adviser_firm_id="{6ac39921-d8b7-f011-bbd3-7ced8d6b85ff}")
        session = _session()
        await sync.sync_client(session, contact_id=41)
        assert stub.called("create_investment")[0]["provider"] == (
            "6ac39921-d8b7-f011-bbd3-7ced8d6b85ff"
        )


class TestPartialFailureIsReportedNotRaised:
    async def test_one_accounts_scheme_failing_does_not_abandon_the_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The report is the point: an adviser needs to know which half worked."""
        calls: list[str] = []

        class _Flaky(_FakeApi):
            async def create_scheme(self, body: dict[str, Any]) -> Any:
                calls.append(body["contactId"])
                if len(calls) == 1:
                    raise httpx.ConnectError("boom")
                return {"schemeReference": "MLSIPP/02040"}

        stub = _Flaky(
            **{
                **{k: v for k, v in _VENDOR_ANSWERS.items() if k != "create_scheme"},
                "create_investment": {"wrapper": "acc000000002"},
            }
        )
        _install(monkeypatch, stub)
        session = _session(
            accounts=[SIPP, {**SIPP, "id": 902, "uid": "acc000000002", "name": "Second SIPP"}]
        )
        report = await sync.sync_client(session, contact_id=41)

        assert [step["status"] for step in report["accounts"]] == ["failed", "created"]
        assert _link(session, "account", "primary")["eid"] == 902

    async def test_a_failed_scheme_does_not_attempt_the_policy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """There is no scheme reference to file it against."""
        stub = _FakeApi(**{**_VENDOR_ANSWERS, "create_scheme": httpx.ConnectError("boom")})
        _install(monkeypatch, stub)
        session = _session()
        report = await sync.sync_client(session, contact_id=41)

        assert stub.called("create_investment") == []
        assert "policy_status" not in report["accounts"][0]

    async def test_a_failed_policy_leaves_the_schemes_own_status_standing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scheme with no policy is a usable record that cannot be valued yet. Reporting the
        account as simply "failed" would say it never reached Morgan Lloyd."""
        stub = _FakeApi(**{**_VENDOR_ANSWERS, "create_investment": httpx.ConnectError("boom")})
        _install(monkeypatch, stub)
        session = _session()
        report = await sync.sync_client(session, contact_id=41)

        step = report["accounts"][0]
        assert step["status"] == "created"
        assert step["scheme_reference"] == "MLSIPP/02039"
        assert step["policy_status"] == "failed"
        # The scheme link IS written — it exists at the vendor and a retry must not recreate it.
        assert _link(session, "account", "primary") is not None
        assert _link(session, "account", "policy") is None

    async def test_a_scheme_with_no_reference_in_the_answer_is_a_failure_not_a_link(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The `create_contact` scar in another place: a created record whose id we cannot read
        must not be recorded as linked, or the retry duplicates it."""
        stub = _FakeApi(
            create_contact={"response": "9f2c1c76-1111-2222-3333-444455556666"},
            create_scheme={"message": "Created"},
        )
        _install(monkeypatch, stub)
        session = _session()
        report = await sync.sync_client(session, contact_id=41)

        assert report["accounts"][0]["status"] == "failed"
        assert _link(session, "account", "primary") is None
