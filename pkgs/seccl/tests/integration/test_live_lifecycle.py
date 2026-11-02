"""One account's life at Seccl, against the real staging API, in order.

    node -> client -> account (with a MODEL attached) -> fee arrangement
         -> regular payment in -> regular withdrawal
         -> one-off payment in -> one-off withdrawal -> cancel it

The Morgan Lloyd suite is the sibling of this one and its subject is a SIPP at a scheme
administrator: contributions, relief, drawdown. Seccl is a CUSTODIAN, so the same lifecycle is
spelled in the vocabulary that actually applies here — **payments and withdrawals, regular and
one-off, plus the two standing arrangements a custodian holds on an account: its MODEL and its
FEE structure.**

**One endpoint instructs all the money.** `POST /portfoliotransactiongroup` is a payment in, a
withdrawal, a transfer and a trade; what distinguishes them is the
`transactionType`/`transactionSubType`/`movementType` triple. That is the single most useful thing
to know about this API and the single easiest thing to get subtly wrong, which is why every
combination this repo depends on is exercised below rather than described.

**REUSE IS THE DESIGN.** Every record is an anchor (`anchors.py`): discovered first, created only
when the discovery comes back empty. A first run against a clean staging firm creates three
things; every run after it creates none of them. The money instructions are necessarily new each
time — an instruction is an event, not a record — so they are small, and the withdrawal is
cancelled at the end.

WHAT IS NOT HERE, and why:

* **Trades.** A buy or a sell is the same endpoint with an `Order` type, and asserting that a
  market order fills is asserting something about staging's market simulation rather than about
  the API. `autoInvest`/`autoDisinvest` cover the part that matters to us — money arriving and
  leaving an INVESTED account, which is what a withdrawal from a real client actually is.
* **Pensions** (`crystallise`, `crystallise_regularly`). Seccl's pension surface needs a client
  PRODUCT, which is a different onboarding path; the anchor account is a GIA on purpose.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

import pytest

from seccl._base import SecclApiError
from seccl.client import SecclClient

from . import anchors

pytestmark = pytest.mark.integration


def _reference(what: str) -> str:
    """An `externalReference` for a money instruction.

    **Nothing about a transaction group is idempotent** — a retried group is a second instruction,
    and Seccl offers idempotency as a separate mechanism this client does not use. So every
    instruction carries a reference that says what it was and when, which is the difference
    between a duplicate somebody can recognise afterwards and one they cannot.
    """
    return f"ZZINT-{what}-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}"


# --------------------------------------------------------------------------------------------
# Anchors. Function-scoped and memoised through `anchors._CACHE` — a session-scoped async
# fixture would bind an event loop that pytest-asyncio does not reuse between tests.
# --------------------------------------------------------------------------------------------


@pytest.fixture
async def node_id(api: SecclClient) -> str:
    """The node the anchor client and account belong to.

    **Read, never assumed to be `"0"`.** Every example in the vendored Postman collection uses
    `nodeId: "0"` and no such node exists on this staging firm — its nodes are `21`, `49`, `40`,
    `9`… A missing one answers `404 DataNotFoundError "Node static data not found"`.

    And it PREFERS A SANDBOX NODE, which is not fussiness. Seccl staging is shared and its node
    tree is a list of real advice firms ("Aventur Wealth Ltd", "Epworth Investment Management
    Limited"); taking the first node would hang an integration client off one of them, where it
    becomes their unexplained record rather than ours. `SECCL_INTEGRATION_NODE_ID` overrides.
    """
    override = (os.environ.get("SECCL_INTEGRATION_NODE_ID") or "").strip()
    if override:
        return override

    nodes = await api.firm_nodes()
    if not nodes:
        pytest.skip("this staging firm has no nodes")

    def identifier(node: dict[str, Any]) -> str:
        return anchors.record_id(node) or str(node.get("nodeId") or "")

    named = [(str(node.get("name") or "").lower(), identifier(node)) for node in nodes]
    for word in ("sandbox", "demo", "without advisers"):
        for name, found in named:
            if word in name and found:
                return found
    pytest.skip(
        "no node on this firm looks like a sandbox, and the rest belong to real advice firms. "
        "Set SECCL_INTEGRATION_NODE_ID to one it is safe to create test records under."
    )


@pytest.fixture
async def client_id(api: SecclClient, node_id: str, writes_allowed: None) -> str:
    """The anchor client, WITH ITS TERMS ACCEPTED.

    The acceptance is part of the anchor rather than a test of its own, because everything below
    depends on it: a Pending client's accounts are Pending, and a Pending account books nothing.
    `ensure_terms_accepted` is idempotent, so this is one extra read on a warm run.
    """
    resolved = await anchors.ensure_client(api, node_id)
    await anchors.ensure_terms_accepted(api, resolved)
    return resolved


@pytest.fixture
async def model_id(api: SecclClient, node_id: str, writes_allowed: None) -> str:
    return await anchors.ensure_model(api, node_id)


@pytest.fixture
async def account_id(
    api: SecclClient, client_id: str, node_id: str, model_id: str, writes_allowed: None
) -> str:
    return await anchors.ensure_account(
        api, client_id=client_id, node_id=node_id, model_id=model_id
    )


# --------------------------------------------------------------------------------------------
# 1-2. The firm's tree, and the client on it.
# --------------------------------------------------------------------------------------------


class TestTheClientAndTheirAccount:
    async def test_1_the_firm_has_a_node_tree(self, api: SecclClient, node_id: str) -> None:
        """Seccl has no ADVISER entity — the Morgan Lloyd suite's step 1 has no counterpart here.
        The node is what plays that structural role: it is the unit a client belongs to and the
        unit that scopes models and fee structures, and it is `org_key`'s source in the conformed
        layer (`docs/platform-model.md` §3).
        """
        assert node_id

    async def test_2_the_client_exists_and_is_on_the_node(
        self, api: SecclClient, client_id: str, node_id: str
    ) -> None:
        found = await anchors.find_client(api)
        assert found is not None, "the anchor client was created and cannot be found back by name"
        assert anchors.record_id(found) == client_id

    async def test_3_the_account_is_ACTIVE_because_the_client_accepted_terms(
        self, api: SecclClient, account_id: str, client_id: str
    ) -> None:
        """**`status` on a create is a REQUEST, not a fact — and the way to make it true is on the
        CLIENT, not the account.**

        The anchor account is created with `"status": "Active"` and comes back `Pending`, because
        its client is Pending with `termsAccepted: None`. The obvious repair is the collection's
        own `PUT /account` with
        `{"updateAction": "AmendStatus", "updateData": {"status": "Active"}}` — which is ACCEPTED
        (200, empty body) and changes nothing. An answer with no effect and no error is the worst
        shape a vendor can offer, and it sent this suite down a path of documenting Pending as
        terminal.

        It is not terminal. `PUT /client` with `AmendTermsAccepted` turns the client Active and
        **the account follows on its own** — the status is a consequence, not a field. The client
        PRODUCT's `acceptedProviderTerms` is not needed for it (and is refused `Record updated by
        another user` straight afterwards, because accepting the client's terms bumps its
        `updateId`).

        This is what makes the money rail reachable: a Pending account books no transaction,
        refuses a withdrawal for insufficient funds, and shows an empty feed. `anchors
        .ensure_terms_accepted` carries the detail.
        """
        account = await api.account(account_id)
        assert str(account.get("clientId") or "") == client_id
        assert str(account.get("status") or "") == "Active", (
            f"the account is {account.get('status')!r}. Accepting the client's terms is what "
            "makes it Active (anchors.ensure_terms_accepted); if that stopped working, every "
            "money test below degrades to asserting that an empty account is empty."
        )


# --------------------------------------------------------------------------------------------
# 4. The MODEL — assigned at create time, and only at create time.
# --------------------------------------------------------------------------------------------


class TestTheModelAssignment:
    async def test_4a_the_model_is_attached_to_the_account(
        self, api: SecclClient, account_id: str, model_id: str
    ) -> None:
        """`wrapperDetail.assetAllocationId` is the assignment, and it is read back off the
        account rather than trusted from the create's answer."""
        account = await api.account(account_id)
        wrapper = account.get("wrapperDetail")
        assert isinstance(wrapper, dict), f"no wrapperDetail on the account: {sorted(account)}"
        assert str(wrapper.get("assetAllocationId") or "") == model_id, (
            f"the account's model is {wrapper.get('assetAllocationId')!r}, not the anchor model "
            f"{model_id!r} — the create-time attachment did not take."
        )

    def test_4b_a_model_cannot_be_reassigned_by_a_field_edit(self) -> None:
        """**The constraint that makes "change this account's model" a hard problem**, asserted
        against the generated models rather than the prose that describes it.

        `AccountUpdate` carries no `wrapperDetail`, so there is no field to PATCH. Reassignment is
        `recurringOrders` for the money that has not arrived plus a rebalance for the holdings
        that have — which means anything in our own book that writes `platform.accounts.model_id`
        and calls it done has changed our record and instructed nobody.

        A static check, deliberately: the day Seccl adds the field, this fails and the reassign
        path becomes buildable. That is a change nothing else would notice.
        """
        from seccl.models.account import AccountCreate, AccountUpdate

        assert "wrapper_detail" in AccountCreate.model_fields
        assert "wrapper_detail" not in AccountUpdate.model_fields, (
            "AccountUpdate now has wrapperDetail — Seccl may allow reassigning a model by PATCH, "
            "which would replace the recurringOrders-plus-rebalance dance."
        )

    async def test_4c_the_model_allocations_are_fractions(
        self, api: SecclClient, model_id: str
    ) -> None:
        """0.6 and 0.4, not 60 and 40. Sending percentages does not fail — it creates a model
        allocating 6000%, which is the `_allocation_lines` trap `docs/provider-pipeline.md`
        records and which the fee register hits the same way (`0.0035` = 0.35%).
        """
        model = await api.model(model_id)
        allocations = model.get("allocations")
        assert isinstance(allocations, list) and allocations, (
            f"the anchor model has no allocations: {sorted(model)}"
        )
        total = sum(float(line.get("percentage") or 0) for line in allocations)
        assert 0.9 < total <= 1.0, (
            f"the model's allocations total {total}, which is not a fraction of one — if this is "
            "near 100 the percentages were sent as percentages."
        )


# --------------------------------------------------------------------------------------------
# 5. FEES — the register, and assigning a structure to the account.
# --------------------------------------------------------------------------------------------


class TestTheFeeArrangement:
    async def test_5a_the_firms_fee_register_is_readable(self, api: SecclClient) -> None:
        """**An empty register is a real answer and this firm gives it.** Verified live on
        04 Aug 2026 (`meta.count` 0) and re-asserted here as a shape rather than a count, because
        it is the reason `services/seccl`'s `raw.fee_structures` declares its whole read shape as
        a spine: with no rows to infer from, `tiers` and `type` would not exist as columns and the
        conformed model would fail to bind on precisely the firm that has no fee structures.
        """
        register = await api.fee_structures()
        assert isinstance(register, list)
        if register:
            # If staging ever grows one, the conventions matter more than the count.
            first = register[0]
            assert anchors.record_id(first), f"a fee structure with no id: {sorted(first)}"

    async def test_5b_a_fee_structure_is_assigned_by_patching_the_account(
        self, api: SecclClient, account_id: str
    ) -> None:
        """`feeStructures.adviserFeeId` — a PATCH on the ACCOUNT, not a fees endpoint.

        That asymmetry is Seccl's own and is worth exercising rather than describing: a caller
        looking for `assign_fee_structure` in `seccl.endpoints.fees` finds nothing and could
        reasonably conclude it is unsupported.

        Skipped rather than faked when the register is empty. Inventing an id would assert that
        Seccl rejects nonsense, which is not what this is for, and a REAL id is the only thing
        that tests the path.
        """
        register = await api.fee_structures()
        adviser_fees = [
            anchors.record_id(row)
            for row in register
            if str(row.get("type") or "").lower().startswith("adviser")
        ]
        if not adviser_fees:
            pytest.skip(
                "this staging firm's fee register is empty, so there is no real adviserFeeId to "
                "assign. Recorded as a live finding rather than worked around — see 5a."
            )

        update_id = await anchors.update_id_of(api, account_id)
        await api.update_account(
            account_id, update_id, {"feeStructures": {"adviserFeeId": adviser_fees[0]}}
        )
        account = await api.account(account_id)
        structures = account.get("feeStructures") or {}
        assert str(structures.get("adviserFeeId") or "") == adviser_fees[0]

    async def test_5c_a_client_fee_group_can_be_created(
        self, api: SecclClient, client_id: str, account_id: str, node_id: str
    ) -> None:
        """The other half of the fee story, and the half that IS exercisable here: a group whose
        accounts are valued together before a tiered rate is applied — materially cheaper for a
        household, and the reason a firm asks for one.

        A `portfoliogroup` with `groupIntent: "Client Fee Group"`, not a fee structure. The two
        are different levels and conflating them is easy: the structure is the firm's priced
        product, the group is a set of accounts that share one.
        """
        created = await api.create_fee_group(
            {
                "groupIntent": "Client Fee Group",
                "name": f"ZZIntegration Fee Group {dt.date.today():%Y%m}",
                "clientId": client_id,
                # An ARRAY, like a client's. "Node Ids must be provided in an array" is the
                # refusal, and it is the same asymmetry `anchors.client_payload` records: a node
                # is a list wherever it belongs to a client-ish thing and a scalar on an account.
                "nodeId": [node_id],
                "accounts": [account_id],
                "intentDetails": {"adviserFee": True, "platformFee": False},
            }
        )
        identifier = anchors.record_id(created)
        assert identifier, f"created a fee group and could not read its id out of {sorted(created)}"


# --------------------------------------------------------------------------------------------
# 6-7. REGULAR money — the standing arrangements, in and out.
# --------------------------------------------------------------------------------------------


class TestRegularPaymentsAndWithdrawals:
    """`recurringPayments` on the ACCOUNT, one array holding both directions.

    That is the shape and it surprises people: a regular payment in and a regular withdrawal are
    not two features, they are `movementType: "In"` and `movementType: "Out"` in the same list. A
    PATCH therefore REPLACES the whole standing arrangement — sending only the withdrawal cancels
    the payment in — which is why each test below reads the array back and asserts on what is
    actually standing rather than on what it just sent.
    """

    async def test_6_a_regular_payment_in_is_set_up(
        self, api: SecclClient, account_id: str
    ) -> None:
        update_id = await anchors.update_id_of(api, account_id)
        await api.update_account(
            account_id,
            update_id,
            {
                "recurringPayments": [
                    {
                        "paymentDay": 14,
                        "amount": 70,
                        "movementType": "In",
                        "method": "Bank Transfer",
                        "autoInvest": True,
                    }
                ]
            },
        )
        account = await api.account(account_id)
        standing = account.get("recurringPayments") or []
        incoming = [p for p in standing if str(p.get("movementType") or "") == "In"]
        assert incoming, f"no incoming regular payment after the PATCH: {standing}"
        assert float(incoming[0].get("amount") or 0) == 70

    async def test_7_a_regular_withdrawal_replaces_it_and_needs_auto_disinvest(
        self, api: SecclClient, account_id: str
    ) -> None:
        """**Two things at once, because they are one fact.**

        A regular withdrawal is the same array with `movementType: "Out"` — so PATCHing it alone
        REPLACES the standing payment in, which is asserted here rather than left to be
        discovered by a firm whose client stopped contributing.

        And a withdrawal from an invested account needs `autoDisinvest` (`Largest` /
        `Proportional`), which is an ACCOUNT-level field, not part of the payment. Without it the
        standing order can only be met from uninvested cash, and a month it cannot is a failed
        payment to a client who was expecting money.
        """
        update_id = await anchors.update_id_of(api, account_id)
        await api.update_account(
            account_id,
            update_id,
            {
                "autoDisinvest": "Largest",
                "recurringPayments": [
                    {
                        "paymentDay": 28,
                        "amount": 150,
                        "movementType": "Out",
                        "method": "BACS Credit",
                    }
                ],
            },
        )
        account = await api.account(account_id)
        standing = account.get("recurringPayments") or []
        outgoing = [p for p in standing if str(p.get("movementType") or "") == "Out"]
        assert outgoing, f"no outgoing regular payment after the PATCH: {standing}"
        assert str(account.get("autoDisinvest") or "") == "Largest"
        assert not [p for p in standing if str(p.get("movementType") or "") == "In"], (
            "the incoming regular payment survived a PATCH that did not name it — "
            "`recurringPayments` may no longer be replace-the-whole-array, which would change "
            "how every standing-arrangement edit has to be written."
        )

    async def test_7b_the_standing_arrangement_can_be_cleared(
        self, api: SecclClient, account_id: str
    ) -> None:
        """An empty array is how a mandate is cancelled — there is no cancel endpoint. Left in a
        cleared state on purpose, so a re-run starts from the same place rather than from
        whatever the last run happened to leave standing."""
        update_id = await anchors.update_id_of(api, account_id)
        await api.update_account(account_id, update_id, {"recurringPayments": []})
        account = await api.account(account_id)
        assert not (account.get("recurringPayments") or [])


# --------------------------------------------------------------------------------------------
# 8-9. ONE-OFF money — a payment in, then a withdrawal, then cancel it.
# --------------------------------------------------------------------------------------------


class TestOneOffPaymentsAndWithdrawals:
    async def test_8_a_one_off_payment_in_is_instructed(
        self, api: SecclClient, account_id: str
    ) -> None:
        """`Payment` / `Deposit` / `In` — the triple that means "money is coming".

        This is an EXPECTATION, not a credit: Seccl records that a payment is due and reconciles
        the money when it lands. `autoInvest` makes it buy the account's model on arrival, which
        is the other half of what attaching a model at create time was for.
        """
        answer = await api.create_transaction_group(
            account_id,
            [
                {
                    "transactionType": "Payment",
                    "transactionSubType": "Deposit",
                    "movementType": "In",
                    "currency": "GBP",
                    "amount": 1000,
                    "method": "Bank Transfer",
                    "autoInvest": True,
                }
            ],
            externalReference=_reference("payin"),
        )
        # The group answers with its own id and nothing else — `{"id": "0002P1FQM"}`. That id is
        # the `linkId` the resulting transactions carry, which is the only thing tying a
        # "sell and withdraw" back together, so a caller that discards it cannot follow what it
        # instructed.
        assert anchors.record_id(answer), (
            f"the payment-in group answered without an id: {sorted(answer)}"
        )

    async def test_9a_a_one_off_withdrawal_is_instructed(
        self, api: SecclClient, account_id: str
    ) -> None:
        """`Payment` / `Withdrawal` / `Out`, with `autoDisinvest` so it can sell to fund itself.

        **`Insufficient funds for this transaction` is a PASS here, and that is not a fudge.**
        The anchor account is Pending, so it holds nothing; a withdrawal from it must be refused,
        and the refusal is proof the instruction was understood — it reached the money engine,
        was matched to this account, and was declined on the balance. The failure this test
        exists to catch is a refusal about the SHAPE (an unknown transactionSubType, a movement
        type that does not pair with it), which reads completely differently.

        Two outcomes, both accepted, nothing else:
          accepted  -> the account has funds; the triple is right.
          insufficient funds -> the account has none; the triple is still right.
        """
        try:
            answer = await api.create_transaction_group(
                account_id,
                [
                    {
                        "transactionType": "Payment",
                        "transactionSubType": "Withdrawal",
                        "movementType": "Out",
                        "currency": "GBP",
                        "amount": 100,
                        "method": "BACS Credit",
                        "autoDisinvest": "Proportional",
                    }
                ],
                externalReference=_reference("withdrawal"),
            )
        except SecclApiError as exc:
            assert "insufficient funds" in str(exc).lower(), (
                f"the withdrawal was refused for a reason other than the balance: {exc}"
            )
            return
        assert anchors.record_id(answer), (
            f"the withdrawal group answered without an id: {sorted(answer)}"
        )

    async def test_9b_the_instructions_read_back_on_the_account(
        self, api: SecclClient, account_id: str
    ) -> None:
        """Both directions, found by the account they were instructed against.

        Read through the transaction FEED rather than through the group's own answer, because the
        feed is what `services/seccl` syncs and what `platform.transactions` is built from — so
        this asserts the shape the pipeline actually consumes.
        """
        account = await api.account(account_id)
        movements = await api.transactions(accountId=account_id, transactionType="Payment")

        if str(account.get("status") or "") != "Active" and not movements:
            # A Pending account books nothing, so an empty feed there is consistent rather than a
            # lost instruction. Kept as a guard, not as the expected path: test_3 asserts the
            # account IS Active, so reaching this means the terms acceptance regressed and the
            # message should say so rather than letting the run go green on an empty book.
            pytest.skip(
                f"the anchor account is {account.get('status')!r}, so nothing is booked — see "
                "test_3 and anchors.ensure_terms_accepted."
            )

        assert movements, "no Payment transactions on an ACTIVE account after instructing two"
        subtypes = {str(row.get("transactionSubType") or "") for row in movements}
        assert {"Deposit", "Withdrawal"} & subtypes, (
            f"neither a Deposit nor a Withdrawal in {sorted(subtypes)} — the triple this repo "
            "depends on may have changed spelling."
        )

    async def test_9c_a_pending_instruction_can_be_cancelled(
        self, api: SecclClient, account_id: str
    ) -> None:
        """`request_deletion` — **a REQUEST, not a delete.**

        Seccl decides whether the instruction has gone too far, so a 200 is not proof the money
        will not move; the transaction has to be re-read. That distinction is the whole reason
        this test exists, and it is why the assertion is about the request being accepted and the
        status being re-readable, never about the transaction having vanished.

        **It cancels whatever this run instructed, preferring the withdrawal — and the deposit is
        not a consolation prize, it is the tidy-up.** Once the account is Active a payment in
        BOOKS, as a Pending expectation waiting for money that will never arrive on staging, so a
        suite that left it there would add one every run. That is the leak the anchors exist to
        avoid, in the one place anchoring cannot help: an instruction is an event, not a record,
        so it cannot be found-or-created — it can only be cleaned up.

        The withdrawal is usually absent, and that is consistent rather than a gap: the deposit is
        an EXPECTATION, so the cash never lands, so `autoDisinvest` has nothing to sell and the
        withdrawal is refused for insufficient funds (test_9a). Funding the account for real needs
        Seccl's settlement to run, which is their process and not an API call.
        """
        pending = [
            row
            for row in await api.transactions(accountId=account_id, transactionType="Payment")
            if str(row.get("status") or "").lower() in {"pending", "new", "instructed"}
        ]
        if not pending:
            pytest.skip("nothing pending on the anchor account — staging may have settled it")

        # The withdrawal first when there is one: it is the instruction that moves money OUT, so
        # it is the one worth proving cancellable. Otherwise the deposit, which is the one that
        # would otherwise pile up.
        pending.sort(key=lambda row: str(row.get("transactionSubType")) != "Withdrawal")

        for row in pending:
            transaction_id = anchors.record_id(row)
            if not transaction_id:
                continue
            try:
                await api.request_deletion(transaction_id)
            except SecclApiError as exc:
                # A refusal is legitimate — the instruction may have progressed. What would NOT
                # be is the endpoint being gone, which is a different error.
                assert "not found" not in str(exc).lower(), (
                    f"requestdeletion answered 'not found' for {transaction_id} — the action "
                    f"path may have moved: {exc}"
                )
                continue
            after = await api.transaction(transaction_id)
            assert isinstance(after, dict) and after, (
                "the transaction is unreadable after a deletion request — a 200 from a REQUEST "
                "is not proof of anything, so re-reading the status is the actual assertion."
            )
