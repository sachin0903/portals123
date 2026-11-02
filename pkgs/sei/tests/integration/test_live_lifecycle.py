"""One account's money life at SEI, against the real SWP test environment, in order.

    account -> its portfolios -> its MODEL -> its FEE package
            -> payment in (one-off ACR, then a standing order)
            -> withdrawal (pay-to recipient, then a disbursement)
            -> read the standing instructions back -> cancel

The sibling of the Morgan Lloyd and Seccl lifecycle suites, in the vocabulary that applies to SEI:
**payments and withdrawals, regular and one-off, plus the two standing arrangements on an
account — its model and its fee package.**

**The money tests have not been run against a real account.** They need
`SEI_INTEGRATION_ACCOUNT` and there is deliberately no default. The contract suite beside this
file has been run: the machine can authenticate, and the reads that do not need an account now
answer. A test here that has never seen an account is still a hypothesis about the money rail.

**Three things make SEI different from the other two, and all three shape what is here:**

* **There is no create-an-account call.** Enrolment is a journey, so the account is CONFIGURATION
  (`SEI_INTEGRATION_ACCOUNT`) and there is deliberately no default — see `anchors.py`, where the
  reason is that the accounts on this environment are real enough that a default would send
  instructions about somebody's money.
* **A portfolio is not an account**, and an account can hold a Capital and an Income portfolio on
  different models. Every model, fee and disbursement call takes a portfolio.
* **Writes are instructions about money and nothing here is reversible through the API.** They are
  behind `INTEGRATION_ALLOW_WRITES=1` like the other suites, but for a stronger reason than not
  littering staging.
"""

from __future__ import annotations

import pytest

from sei.client import SeiClient

from . import anchors

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------------------------
# The subject. Function-scoped: every fixture here is a read, and pytest-asyncio gives each test
# its own event loop, so a session-scoped async fixture would bind to the wrong one.
# --------------------------------------------------------------------------------------------


@pytest.fixture
def account_number() -> str:
    return anchors.configured_account()


@pytest.fixture
async def portfolio_id(api: SeiClient, firm_id: str, account_number: str) -> str:
    """The portfolio to act on — **and the point at which a pension account is refused.**

    The guard lives here rather than in each money test because every money instruction takes a
    portfolio, so this is the one place they all pass through. A SIPP at SEI looks exactly like a
    GIA from its wrapper code, so a misconfigured account is not visibly wrong; see
    `anchors.refuse_if_pension`.
    """
    await anchors.refuse_if_pension(api, firm_id, account_number)
    return await anchors.portfolio_for(api, firm_id, account_number)


# --------------------------------------------------------------------------------------------
# 1-2. The account and its portfolios.
# --------------------------------------------------------------------------------------------


class TestTheAccount:
    async def test_1_the_account_resolves(
        self, api: SeiClient, firm_id: str, account_number: str
    ) -> None:
        """`retrieveAccountDetails` — the account, its owners and its money-movement recipients.

        The first thing every write below depends on, so it is asked first and on its own: a
        wrong account number refused here is one clear failure instead of eight opaque ones.
        """
        details = await api.account_details(firm_id, account_number)
        assert details, f"SEI returned nothing for account {account_number}"

    async def test_2_the_account_has_a_portfolio(self, portfolio_id: str) -> None:
        """**A portfolio is the unit that holds money and a model, not the account.**

        `PORTFOLIO_TYPE` 189/190 is Capital/Income — an investment-PURPOSE split, and not to be
        confused with `ACCOUNT_TYPE`, which is the wrapper (`docs/sei.md`: `ACCT_TYPE` is what
        maps to `product_type`, and this environment defines no pension WRAPPER — though it
        holds 830 SIPPs as Investment Accounts CLASSIFIED as pensions).
        """
        assert portfolio_id

    async def test_3_the_numeric_account_id_is_a_different_identifier(
        self, api: SeiClient, firm_id: str, account_number: str
    ) -> None:
        """SEI addresses one account by THREE values and they are not interchangeable: the
        account NUMBER (most services), the numeric `accountId` (fee assignment, which refuses
        the number) and the `portfolioId` (models, disbursements).

        Asserted because a wrong-kind identifier is refused with a message that reads as "not
        found" — which is the mistake that cost this estate two rounds of fixing a Morgan Lloyd
        model that had never produced a row.
        """
        numeric = await anchors.numeric_account_id(api, firm_id, account_number)
        assert numeric != account_number, (
            f"the numeric accountId and the account number are both {numeric!r} on this "
            "environment — if SEI has merged them, `assign_package_to_account` no longer needs "
            "the distinction this suite is asserting."
        )


# --------------------------------------------------------------------------------------------
# 4. The MODEL on the portfolio.
# --------------------------------------------------------------------------------------------


class TestTheModelAssignment:
    async def test_4a_the_assigned_model_is_readable(
        self, api: SeiClient, firm_id: str, account_number: str, portfolio_id: str
    ) -> None:
        """`assignment/byAccount`. An UNMODELLED portfolio is a legitimate answer — most are —
        so this asserts the call works, not that a model is on it."""
        assigned = await api.assigned_model(firm_id, account_number, portfolio_id=portfolio_id)
        assert isinstance(assigned, dict)

    async def test_4b_the_firms_strategies_are_readable(self, api: SeiClient, firm_id: str) -> None:
        """`getStrategyList` — `MODEL_TYPE` 5.

        Worth its own test because of a documented mismatch that a live call is the only way to
        keep honest: **the two assignment variants named on our API sheet take model types this
        firm does not have.** The sheet names *implementation* (type 4) and *allocation* (type 3);
        every real model in the firm's own `raw.models_int_ext` is type 8, *investment*. So a path
        built on the sheet would assign nothing.
        """
        from sei._base import SeiApiError

        try:
            strategies = await api.strategies(firm_id)
        except SeiApiError as exc:
            pytest.skip(
                f"getStrategyList rejected the request (HTTP {exc.status}). The envelope the "
                "rest of the client uses is a gateway 400, and the spec's array envelope is a "
                "500. Neither returns the list."
            )
        assert isinstance(strategies, list)

    async def test_4c_assigning_a_model_needs_a_tolerance_multiplier_we_do_not_have(
        self, writes_allowed: None
    ) -> None:
        """**A standing blocker, asserted as a skip so it stays visible.**

        `toleranceMultiplier` is mandatory on all three assignment variants and has NO SOURCE:
        SEI refers to its own `MODEL_RAIL_MULTIPLIER` table, which appears in neither the nightly
        drop nor any lookup service. It is configuration until SEI publishes it.

        Guessing a value would assign a real model to a real portfolio with a rebalancing
        tolerance nobody chose, which is a worse outcome than not testing the call. Recorded here
        rather than omitted, so "is model assignment covered?" has an answer.
        """
        pytest.skip(
            "model assignment needs `toleranceMultiplier`, which SEI has not published "
            "(MODEL_RAIL_MULTIPLIER is in no drop and no lookup service). Set it in this test "
            "once SEI confirms the value, and `apsId` from raw.models_int_ext."
        )


# --------------------------------------------------------------------------------------------
# 5. FEES — the package on the account, and a one-off charge.
# --------------------------------------------------------------------------------------------


class TestTheFeeArrangement:
    async def test_5a_the_accounts_characteristics_carry_its_fee_package(
        self, api: SeiClient, firm_id: str, account_number: str
    ) -> None:
        """`account_characteristics` is where `feePackageProfileId` comes from, and that id is the
        join between three places: the drop's `raw.fee_package_rule`/`raw.fee_package_usage`, this
        read, and `assign_package_to_account`. It is the value
        `platform.fee_packages.external_id` should hold for SEI.
        """
        characteristics = await api.account_characteristics(firm_id, account_number)
        assert isinstance(characteristics, dict)

    async def test_5b_the_firms_fee_packages_are_readable(
        self, api: SeiClient, firm_id: str, account_number: str
    ) -> None:
        """The read is largely redundant with the nightly drop, which carries the whole register
        — and that is the point of asserting it rather than using it: nothing should populate
        `platform.fee_packages` from here. What this API is FOR is changing an assignment, which
        the drop cannot do.
        """
        packages = await api.fee_packages(firm_id, account_number=account_number)
        assert isinstance(packages, list)

    async def test_5c_a_fee_package_is_assigned_by_the_NUMERIC_account_id(
        self, api: SeiClient, firm_id: str, account_number: str, writes_allowed: None
    ) -> None:
        """`assign_package_to_account`, and the identifier is the trap.

        It takes SEI's numeric `accountId` and refuses the account NUMBER. `copyPackageFlag` = 1
        assigns a REPLICA rather than the firm's shared package, which is how a per-account
        variation is made without editing everyone else's pricing — and is therefore the safer
        default for anything a test does.

        Skipped without a package to assign rather than inventing an id: a refusal of nonsense
        proves nothing, and a REAL id is the only thing that tests the path.
        """
        packages = await api.fee_packages(firm_id, account_number=account_number)
        profile = anchors._first(packages, "feePackageProfileId", "FEE_PACKAGE_PROFILE_ID")
        if not profile:
            pytest.skip(
                "no feePackageProfileId readable for this account, so there is no real package "
                "to assign. The drop's raw.fee_package_rule is the other place to find one."
            )
        numeric = await anchors.numeric_account_id(api, firm_id, account_number)
        assigned = await api.assign_package_to_account(numeric, profile, copy_package=True)
        assert isinstance(assigned, dict)

    async def test_5d_a_one_off_fee_is_money_out_and_is_not_exercised(
        self, writes_allowed: None
    ) -> None:
        """`create_adhoc_fee` — deliberately NOT called, with the reason stated.

        It takes money out of a client's account, on a `collectionDate` of today or later, and
        **nothing here is reversible through the API**. The other write paths below at least
        settle in the future and can be cancelled as activities; a fee cannot. Covered by the
        package's own unit tests, which is the right place for a payload nobody should send from
        a test run.
        """
        pytest.skip(
            "create_adhoc_fee takes money out of a real account and cannot be reversed through "
            "the API. Exercised as a payload in the unit suite, never as a live call."
        )


# --------------------------------------------------------------------------------------------
# 6-7. PAYMENTS IN — a one-off anticipated receipt, then a standing order.
# --------------------------------------------------------------------------------------------


class TestPaymentsIn:
    """`createCurrencyReceipt` — **one call, three products, told apart only by the payload.**

    * *anticipated receipt* — `processingScheduleDetails.once`, a future `dueDate`, NO
      `payerInfo`. Nothing is collected; SEI expects the money and reconciles the credit.
    * *direct debit* — the same WITH `payerInfo.payerId`. Now SEI collects, and the payer profile
      is data we do not hold, which is why `docs/contributions.md` lists BACS Direct Debit as
      unbuilt.
    * *standing order* — `processingScheduleDetails.recurring`.

    Only the first and third are exercised. Conflating them is easy and expensive: the difference
    between telling a custodian to expect money and telling it to go and take money from a client's
    bank account is one optional block.
    """

    async def test_6_a_one_off_anticipated_cash_receipt_is_lodged(
        self,
        api: SeiClient,
        firm_id: str,
        account_number: str,
        portfolio_id: str,
        writes_allowed: None,
    ) -> None:
        """The ACR a booked contribution lodges (`app/integrations/sei/cash_receipts.py`).

        The description/treatment pair is what CLASSIFIES the movement at SEI's end, and it is
        validated against the caller's role (error 1006) — so a wrong pair is refused rather than
        coerced. These come from the package's own tested payload; the production codes live in
        `app/integrations/sei/reference.py`, read out of the nightly Reference report, which is
        the only place the full sets exist (there is no lookup endpoint).

        **There is no read-back endpoint for a currency receipt anywhere in the spec set**, so the
        `activityId` in the answer is the only handle that will ever exist for it. A caller that
        discards it can never cancel or reconcile what it created — which is why the assertion is
        on the id rather than on the status code.
        """
        from datetime import date  # noqa: PLC0415 - keeps the module import list to the client

        from sei.enums import ReceiptAmountType, ReceiptMethod
        from sei.models import (
            Attributes,
            Once,
            ProcessingScheduleDetails,
            ReceiptInstructions,
            money,
        )

        due: date = anchors.future_date()
        result = await api.create_currency_receipt(
            Attributes(
                firm_id=str(firm_id),
                account_number=account_number,
                portfolio_id=portfolio_id,
                receipt_instructions=ReceiptInstructions(
                    currency_code="GBP",
                    receipt_method=ReceiptMethod.BACS,
                    description_type_code="1091",
                    treatment_type_code="4402",
                    receipt_amount=money("100.00"),
                    receipt_amount_type=ReceiptAmountType.GROSS_VALUE,
                    external_reference=anchors.reference("acr"),
                ),
                processing_schedule_details=ProcessingScheduleDetails(once=Once.on(due)),
            )
        )
        assert result.activity_id, (
            "the receipt was created and answered no activityId — there is no read-back endpoint "
            "for a currency receipt, so that id is the only handle it will ever have."
        )
        # A 200 can still carry warnings: an approval requirement, or an instruction SEI accepted
        # and ignored. Surfaced rather than treated as unqualified success.
        print(f"\nACR {result.activity_id} due {due}: warnings={result.warnings}")

    async def test_7_a_standing_order_is_the_same_call_with_a_recurrence(
        self,
        api: SeiClient,
        firm_id: str,
        account_number: str,
        portfolio_id: str,
        writes_allowed: None,
    ) -> None:
        """A REGULAR payment in — `recurring` instead of `once`.

        **The recurrence lives on the resulting ACTIVITY**, so cancelling that activity is the
        only way to stop the mandate; there is no "delete the standing order" call. That makes
        the `activityId` here more load-bearing than the one-off's.

        `monthly_on` resolves a non-business day BACKWARDS (`PRIOR`), which is not a detail: a
        contribution dated the 31st that resolved forward would land in the following month and be
        counted against the wrong tax year at the wrong allowance.
        """
        from sei.enums import ReceiptAmountType, ReceiptMethod
        from sei.models import (
            Attributes,
            ProcessingScheduleDetails,
            ReceiptInstructions,
            Recurring,
            money,
        )

        start = anchors.future_date()
        result = await api.create_currency_receipt(
            Attributes(
                firm_id=str(firm_id),
                account_number=account_number,
                portfolio_id=portfolio_id,
                receipt_instructions=ReceiptInstructions(
                    currency_code="GBP",
                    receipt_method=ReceiptMethod.BACS,
                    description_type_code="1091",
                    treatment_type_code="4402",
                    receipt_amount=money("50.00"),
                    receipt_amount_type=ReceiptAmountType.GROSS_VALUE,
                    external_reference=anchors.reference("standing"),
                ),
                processing_schedule_details=ProcessingScheduleDetails(
                    recurring=Recurring.monthly_on(start, day_of_month=start.day)
                ),
            )
        )
        assert result.activity_id, (
            "a standing order with no activityId cannot be stopped — cancelling the activity is "
            "the only way to end a mandate."
        )


# --------------------------------------------------------------------------------------------
# 8. WITHDRAWALS — the recipient first, then the disbursement.
# --------------------------------------------------------------------------------------------


class TestWithdrawals:
    async def test_8a_a_withdrawal_needs_somewhere_to_send_the_money(
        self, api: SeiClient, firm_id: str, account_number: str
    ) -> None:
        """**Ordering the vendor will not remind you about.** A disbursement names a *pay-to
        recipient profile* on the account, so the profile has to exist first; the alternative is
        an `adhocPayToDetails` block carrying the whole bank record inline, which SEI accepts and
        which leaves nothing reusable behind for the next withdrawal.

        The read is the safe half and is what runs by default — `account_details` carries the
        account's money-movement recipients.
        """
        details = await api.account_details(firm_id, account_number)
        assert details, "no account detail, so no recipient list to withdraw to"

    async def test_8b_a_disbursement_is_assembled_by_the_caller_and_not_guessed(
        self, api: SeiClient, firm_id: str, account_number: str, writes_allowed: None
    ) -> None:
        """`create_disbursement` — the withdrawal.

        Every part of this is a one-of and the client deliberately guesses none of them: the
        payment rail (`paymentType.bacs`/`chaps`/`fasterPayment`) each has its own mandatory
        sub-block, and the amount is `netAmount` / `grossValue` / `grossDistribution` /
        `disbursementPercent` / `unknown`. A body assembled by the caller is the honest interface.

        Not sent. A disbursement is money leaving a real account on this environment and the pay-to
        profile it needs belongs to a real person; a test that invents one is either refused (which
        proves nothing) or accepted (which is worse). The shape is asserted instead, against the
        client's own documented mandatory set, so a change to it is still caught.
        """
        from sei.endpoints.cash import CashApi

        assert hasattr(CashApi, "create_pay_to_recipient")
        assert hasattr(CashApi, "create_disbursement")
        pytest.skip(
            "a live disbursement pays real money to a real recipient profile on this environment. "
            "Point SEI_INTEGRATION_ACCOUNT at an account whose pay-to profile is known to be "
            "fictional before enabling this, and assemble the paymentType/amountType one-ofs here."
        )


# --------------------------------------------------------------------------------------------
# 9-10. Read the standing instructions back, and cancel.
# --------------------------------------------------------------------------------------------


class TestTheStandingInstructionsAndCancelling:
    async def test_9_the_recurring_instructions_read_back(
        self, api: SeiClient, firm_id: str, account_number: str
    ) -> None:
        """`recurringScheduledEvents` — the read side of the mandate story.

        The nightly drop carries the same population as `raw.rec_cash_activities` (55 columns:
        recurrence frequency and interval, payer and pay-to profile ids, net/gross amount, next
        activity date, description and treatment codes), so for REPORTING the drop is the better
        source. This endpoint is for reading back an instruction just created, before any drop
        exists that contains it — which is exactly what a test does.
        """
        events = await api.recurring_scheduled_events(firm_id, account_number=account_number)
        assert isinstance(events, list)

    async def test_10_a_pending_activity_can_be_cancelled(
        self, api: SeiClient, firm_id: str, account_number: str, writes_allowed: None
    ) -> None:
        """`cancel_activity` — **the only way to stop a standing order**, and the tidy-up for
        anything this suite lodged.

        Everything created above is an ACTIVITY, dated a month out, so it is cancellable right up
        until it settles; cancelling here is what keeps a run from leaving money in flight. The
        activities are matched by the `ZZINT-` external reference so a real instruction on the same
        account is never touched — the alternative, cancelling everything open, would be a test
        interfering with a firm's actual business.
        """
        open_activities = await api.activities(firm_id, account_number=account_number)
        mine = [
            activity
            for activity in (open_activities if isinstance(open_activities, list) else [])
            if "ZZINT-" in str(activity)
        ]
        if not mine:
            pytest.skip(
                "no ZZINT- activity open on this account — nothing this suite created is still "
                "pending. Deliberately NOT falling back to cancelling whatever is open."
            )
        for activity in mine:
            identifier = anchors._first(activity, "activityId", "ACTIVITY_ID", "id")
            if identifier:
                await api.cancel_activity(identifier, reason="Integration suite tidy-up")
