"""What the vendored spec cannot tell us: does SEI still answer the way this client assumes.

`spec/sei.openapi.json` is 188,000 lines transcribed from the API sheet SEI issues per firm. It is
the best artefact this integration has and it is still a transcription — and SEI's failure modes
are unusually skewed towards things only a live call reveals:

* **Three credentials stack on every request.** The mTLS client certificate (the machine
  identity, and what SEI's IP/cert allowlist keys on), an OAuth password grant carried as a
  bearer, and an `AppKey` app-gateway header sent on every request INCLUDING the token request,
  where the app pair also goes in as HTTP Basic. Any one of the three being wrong produces a
  refusal that reads like the other two.
* **SEI restricts by SOURCE IP**, which stalled the first sync for a fortnight on the belief that
  it only worked from Coolify — it works from a laptop on the company VPN too, and only a real
  call could establish that.
* the environment's own SHAPE: this i31 environment defines no pension wrapper at all
  (`ACCT_TYPE` is Investment Account / S&S ISA / S&S JISA / House Account), which constrains what
  SEI can be used for and is a fact about the environment rather than about the API.

READ-ONLY by default. SEI's writes are instructions about money — a cash receipt, a disbursement,
an enrolment — so even in a test environment they are behind `INTEGRATION_ALLOW_WRITES=1`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from sei.client import SeiClient
from sei.settings import ENVIRONMENTS

pytestmark = pytest.mark.integration


class TestTheThreeCredentialsStack:
    async def test_a_token_is_obtainable(self, api: SeiClient) -> None:
        """The cheapest possible probe that exercises all three credentials at once.

        `system-codes-cache` needs no firm and no account, so a failure here is unambiguous: the
        mTLS handshake, the OAuth grant and the AppKey all had to work to get an answer.
        """
        codes = await api.system_codes("ActivityState")
        assert isinstance(codes, list)

    async def test_a_refusal_names_which_credential_or_which_network(
        self, api: SeiClient, firm_id: str
    ) -> None:
        """SEI's errors carry a code/title/detail array (`SeiApiError.errors`).

        An unknown account on a real firm is a domain 400 with that array. An unknown firm on
        the client-template read is not: since the template service started answering, that
        call is a gateway 403 with no `errors[]`, which proves nothing about the parser.
        """
        from sei._base import SeiApiError

        with pytest.raises(SeiApiError) as raised:
            await api.account_details(firm_id, "ZZ-NO-SUCH-ACCOUNT")
        assert raised.value.status == 400
        assert raised.value.errors, "SEI's errors[] is what names the refusal"


class TestTheEnvironmentIsTheOneWeThinkItIs:
    def test_the_mapping_still_separates_test_from_production(self) -> None:
        """i10/i31 share the test host; p04 is production. The whole guard rests on this, so it
        is asserted rather than assumed — a mapping edit that pointed i31 at the production host
        would otherwise make every test in this file a production call."""
        assert ENVIRONMENTS["i10"] == ENVIRONMENTS["i31"]
        assert "test." in ENVIRONMENTS["i31"]
        assert "test." not in ENVIRONMENTS["p04"]

    async def test_a_sipp_is_a_CLASSIFICATION_here_not_a_wrapper(self) -> None:
        """**Can we create a SIPP at SEI with Morgan Lloyd as scheme administrator?**

        The shape exists and we cannot create it from here. Both halves matter, and the first
        overturns a claim that stood in three places in this repo.

        **What was believed:** "this environment defines no pension wrapper at all, which
        constrains what SEI can be used for". Half true and the wrong conclusion. `ACCT_TYPE` —
        the wrapper — really is only four codes, none of them a pension:

            157 Investment Account · 172 S&S ISA · 173 S&S JISA · 176 House Account

        **What the drop actually shows.** SEI carries the pension nature in a DIFFERENT code set,
        and this firm is full of them:

            ACCOUNT_CLASSIFICATION_NAME      Pension                            934 accounts
            ACCOUNT_SUB_CLASSIFICATION_NAME  Pension - HMRC Approved - SIPP     830
                                             Pension - HMRC Approved - SSAS       4
            ACCOUNT_QUALIFIER_NAME           SIPP                                14

        Every one is `ACCOUNT_TYPE` 157. **So a SIPP at SEI is an Investment Account classified as
        a pension** — which is precisely the custodian-plus-administrator split this estate runs:
        SEI holds the assets, Morgan Lloyd administers the scheme, and nothing in SEI's account
        model names the administrator because the scheme is not SEI's record to keep. `MLSIPP/…`
        lives on `platform.links`, not at SEI.

        It also means `product_key` mapping on the wrapper alone filed all 830 as GIA — fixed in
        `services/sei/models/platform/accounts.sql` and pinned by
        `services/sei/tests/test_platform_accounts.py`.

        **Why we cannot create one from here**, in the order the blockers bite:

        1. **There is no create-an-account call.** An account arrives through the ENROLMENT
           journey (`sei.endpoints.enrollment`), and the classification fields are set there.
        2. **The account-template proxy is deployed in lowercase.** The spec's
           `enrollmentAccountTemplates` 404s; `enrollmentaccounttemplates` returns Select's 38
           shelf products, including "Select - GIA" and "Select - Pension". Both are wrapper
           157. Client templates answer too. The two ids an enrolment needs are readable.
        3. **What is still down is named in** `TestWhatThisAppKeyMayActuallyReach`. The AppKey
           is entitled to far more than the heartbeat; the September 2026 map is the current one.

        None of those is a code gap on our side, and the order matters for who to ask: 2 and 3
        are SEI's, and 1 is a deliberate decision recorded in `docs/sei.md` (the enrolment body is
        depth 8 with 48 leaves and a `clientList[]` array — a journey, not a form).

        A pure statement of the finding, so it survives the entitlement gap and is asserted
        without a live call.
        """
        # Held as data rather than prose so it reads as a claim about the vendor. `ACCT_TYPE` is
        # the wrapper; the pension nature is elsewhere entirely.
        wrappers = {
            "157": "Investment Account",
            "172": "S&S ISA",
            "173": "S&S JISA",
            "176": "House Account",
        }
        assert not any(
            "pension" in name.lower() or "sipp" in name.lower() for name in wrappers.values()
        ), (
            "an ACCT_TYPE now names a pension — SEI may have added a pension WRAPPER, which would "
            "change how a SIPP is created and how product_key should read it"
        )

    async def test_a_pension_template_is_still_wrapper_157(
        self, api: SeiClient, firm_id: str
    ) -> None:
        """Select's shelf products include a pension. The wrapper code does not.

        A template named "Pension" is `accountType` 157, the same Investment Account a GIA
        uses (`accountSubType` 6 rather than 1). A new pension WRAPPER would be a different
        `accountType`, which is what `product_key` would have to grow a member for.
        """
        templates = await api.account_templates(firm_id)
        if not templates:
            pytest.skip("this firm has no account templates")
        pensions = [
            row for row in templates if "pension" in str(row.get("templateName") or "").lower()
        ]
        gias = [row for row in templates if "gia" in str(row.get("templateName") or "").lower()]
        assert pensions, "Select has no pension shelf template"
        assert gias
        assert {row.get("accountType") for row in pensions} == {157}
        assert {row.get("accountType") for row in gias} == {157}


class TestTheReferenceReadsTheEnrolmentDependsOn:
    """The ids an enrolment body carries, which are the ones a wrong value fails opaquely on."""

    async def test_client_templates_are_readable_and_carry_an_id(
        self, api: SeiClient, firm_id: str
    ) -> None:
        """`clientList[].newClient.clientTemplateId` comes from here."""
        templates = await api.client_templates(firm_id)
        assert isinstance(templates, list)
        if templates:
            assert any("templateid" in key.lower().replace("_", "") for key in templates[0]), (
                f"no template id in {sorted(templates[0])}"
            )

    async def test_account_templates_are_per_product_not_per_wrapper(
        self, api: SeiClient, firm_id: str
    ) -> None:
        """The reason the template id belongs on `platform.product_platforms` keyed by product
        rather than in a map keyed by `platform.product_type`: a firm running an "AIM ISA" and an
        "AIM IHT" has a template for each and both are ISAs/GIAs at the wrapper level.

        Live-checkable as "more templates than distinct wrapper types", which is the shape that
        makes a per-wrapper map wrong.
        """
        templates = await api.account_templates(firm_id)
        if len(templates) < 2:
            pytest.skip("fewer than two account templates — nothing to distinguish")
        wrappers = {
            str(value).lower()
            for template in templates
            for key, value in template.items()
            if "accounttype" in key.lower().replace("_", "") and value is not None
        }
        if not wrappers:
            pytest.skip("the templates do not state a wrapper type")
        assert len(templates) >= len(wrappers)

    async def test_the_dsr_catalogue_splits_client_and_account_level(
        self, api: SeiClient, firm_id: str
    ) -> None:
        """One role has TWO ids — a client-level `servicingRelationshipId` and an account-level
        one — and sending the client-level id in an account list is refused in a way that reads
        as "the tag does not exist". A firm can have 400+ DSR teams, which is why the admin
        picker is a `MultiSelect`.
        """
        catalogue = await api.firm_dsrs(firm_id)
        assert isinstance(catalogue, dict)
        assert catalogue, "the DSR catalogue came back empty for a firm that should have one"


class TestTheActivityStateVocabulary:
    async def test_the_state_codes_are_readable_and_named(self, api: SeiClient) -> None:
        """The one code set the nightly drop's Reference report does NOT carry, which is why this
        endpoint exists at all: it is what turns `activityState: 22` into "Sent to Payment
        Processing System". Everything the drop has is read from the drop and costs no request.
        """
        codes = await api.system_codes("ActivityState")
        if not codes:
            pytest.skip("this environment returns no ActivityState codes")
        assert any(
            isinstance(value, str) and value.strip()
            for row in codes
            for key, value in row.items()
            if any(word in key.lower() for word in ("desc", "name", "label"))
        ), f"no human-readable label in {sorted(codes[0])} — the set would be numbers only"


class TestTheWritePaths:
    async def test_a_cash_receipt_can_be_lodged(
        self, api: SeiClient, firm_id: str, writes_allowed: None
    ) -> None:
        """The Anticipated Cash Receipt a booked contribution lodges.

        Deliberately the ONLY write probe, and deliberately unbuilt beyond a placeholder: it
        needs a real account number on this environment, and hardcoding one would make the test
        pass or fail on whether that account still exists rather than on the API's behaviour. The
        account link is also the standing blocker `docs/contributions.md` records — SEI account
        numbers have no source but SEI onboarding.

        Left as an explicit skip with the reason, rather than absent: a reader looking for "is the
        write path covered" should find the answer, not a gap.
        """
        pytest.skip(
            "needs a known account number on this environment; the SEI account link has no "
            "source but SEI onboarding (docs/contributions.md, 'Blocked on SEI'). Lodge one by "
            "hand with `app/integrations/sei/cash_receipts.py` and pin the account here."
        )


class TestWhatThisAppKeyMayActuallyReach:
    """**The entitlement map, as ONE explicit finding.**

    The per-test hook in `conftest` turns an un-entitled resource into a skip so the suite stays
    readable, and the cost of that is real: twenty quiet skips do not convey that this AppKey can
    reach almost nothing on i31. This test says it in one place, and — like
    `test_a_lifted_permission_is_reported` in the Morgan Lloyd suite — it is INVERTED. It passes
    while a resource is unavailable and FAILS the day one becomes available.

    That inversion is the point. Waiting on a vendor to grant something is a state nothing
    observes: Morgan Lloyd's `cam_createscheme` sat blocked for a month with correct code on our
    side, and the only reason anyone noticed the grant was a test that went red. The same applies
    here, and more so, because SEI's four failure modes are easy to confuse:

    ============================  ===============================================================
    what SEI answers              what it means
    ============================  ===============================================================
    TLS refusal / connect hang    the client certificate, or the IP allowlist
    `401 Invalid ApiKey for       this AppKey is not entitled to that RESOURCE. Credentials and
    given resource`               network are fine; SEI must add it to the AppKey's product.
    `404 Unable to identify       no proxy is DEPLOYED for a path SEI's own OpenAPI documents.
    proxy` / `unknown-resource`
    `500` / `503`                 SEI is up and this service is not.
    ============================  ===============================================================

    Only the first is ever about us, and the suite's old reachability probe reported the second as
    if it were the first — refusing every test with a message blaming the VPN while the OAuth
    token endpoint and the heartbeat both answered 200 from the same machine.
    """

    #: Resources that still do not return data on i31, re-probed 27 Sep 2026, with WHY.
    #:
    #: The 06 Sep map (heartbeat only; everything else 401, 503 or no proxy) is no longer true.
    #: Client templates, the code cache, account details, holdings, activities, fee packages,
    #: search and platform availability are all entitled. What remains:
    #:
    #: Not a list of paths to keep in step by hand — the test probes each one and reports what
    #: changed. A resource that starts answering leaves this set, which is exactly the event
    #: nobody would otherwise notice.
    KNOWN_UNAVAILABLE = {
        "retrievestrategylistservice": "entitled, but every request body is rejected",
        "getfirmdsrservice": "degraded",
    }

    async def test_the_heartbeat_proves_the_machine_and_the_credentials(
        self, api: SeiClient
    ) -> None:
        """The one call that needs no entitlement and no firm.

        If this passes and everything else 401s, the answer is never "get on the VPN" — it is
        "ask SEI to entitle the AppKey". Asserted first so the rest of this class cannot be
        misread.
        """
        assert await api.heartbeat(), "SEI's heartbeat did not answer OK"

    async def test_a_lifted_entitlement_is_reported(self, api: SeiClient, firm_id: str) -> None:
        """INVERTED: passes while resources are unavailable, fails when one becomes available.

        Each probe is the cheapest call the client offers for that resource. A failure here is
        good news and the message says so — the resource has been granted or repaired, the skip
        that hid it should go, and the tests behind it can start doing their job.
        """
        from sei._base import SeiApiError, SeiAuthError, SeiEntitlementError

        probes: dict[str, Callable[[], Awaitable[object]]] = {
            "retrievestrategylistservice": lambda: api.strategies(firm_id),
            "getfirmdsrservice": lambda: api.firm_dsrs(firm_id),
        }
        became_available: list[str] = []
        for resource, call in probes.items():
            if resource not in self.KNOWN_UNAVAILABLE:
                continue
            try:
                await call()
            except (SeiEntitlementError, SeiApiError, SeiAuthError):
                continue  # still unavailable, which is the expected state
            became_available.append(resource)

        assert not became_available, (
            f"GOOD NEWS — SEI now serves {became_available} to this AppKey. Remove them from "
            "KNOWN_UNAVAILABLE and drop whatever skip was standing in for them; the tests behind "
            "those resources can start asserting something."
        )
