"""Outbound pending transactions — create paths, idempotency, status, matching.

Real Postgres: the interesting parts are the unique key, the graphene status CHECK, matching
not treating a pending deposit as an unclassified credit, and unlink not detaching the
Graphene sibling. Seccl/SEI are stubbed; an unconfigured deployment must still book.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import contributions, instructions, matching, outbound, transfers
from app.instruction_kinds import KIND_LABEL, display_kind
from app.integrations.seccl import money as seccl_money
from tests.conftest import SECCL_PLATFORM_SQL

pytestmark = pytest.mark.anyio


async def _seed(session: AsyncSession, *, product: str = "SIPP") -> dict[str, Any]:
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
                    "(tenant_id, uid, type, first_name, surname, dob) "
                    "VALUES (:t, :uid, 'individual', 'Sophie', 'Halloway', '1980-03-04') "
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
                    "INSERT INTO platform.products (name, product_type) "
                    "VALUES (:n, CAST(:p AS platform.product_type)) RETURNING id"
                ),
                {"n": f"{product} {suffix}", "p": product},
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
    return {"tenant_id": tenant_id, "contact_id": contact_id, "account_id": account_id}


async def _link(
    session: AsyncSession, seed: dict[str, Any], *, provider: str, external_id: str
) -> None:
    await session.execute(
        text(
            "INSERT INTO platform.links "
            "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
            "VALUES (:t, 'account', :a, CAST(:p AS platform.provider_code), 'primary', :ext)"
        ),
        {
            "t": seed["tenant_id"],
            "a": seed["account_id"],
            "p": provider,
            "ext": external_id,
        },
    )


async def _credit(session: AsyncSession, seed: dict[str, Any], *, amount: str = "800.00") -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO platform.transactions "
                    "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                    "   amount, currency, transaction_date) "
                    "VALUES (:t, :a, 'sei', :sid, 'deposit', :amt, 'GBP', :on) RETURNING id"
                ),
                {
                    "t": seed["tenant_id"],
                    "a": seed["account_id"],
                    "sid": f"txn_{uuid.uuid4().hex[:12]}",
                    "amt": amount,
                    "on": date.today(),
                },
            )
        ).scalar_one()
    )


class TestMapping:
    def test_source_key_is_the_public_id(self) -> None:
        assert outbound.source_key("instruction", "abc123XYZ789") == "abc123XYZ789"
        assert outbound.source_key("contribution", "uiduiduiduid") == "uiduiduiduid"

    def test_unknown_origin_is_refused(self) -> None:
        with pytest.raises(ValueError, match="Unknown outbound origin"):
            outbound.source_key("drawdown", "abc")

    @pytest.mark.parametrize(
        ("origin", "kind", "expected"),
        [
            ("instruction", "expected_in", "deposit"),
            ("instruction", "withdrawal", "withdrawal"),
            ("instruction", "adhoc_fee", "fee"),
            ("instruction", "model_topup", "deposit"),
            ("instruction", "model_withdrawal", "withdrawal"),
            ("contribution", "personal_contribution", "deposit"),
            ("contribution", "employer_contribution", "deposit"),
            ("transfer", "transfer_in", "transfer_in"),
        ],
    )
    def test_type_for_each_create_path(self, origin: str, kind: str, expected: str) -> None:
        assert outbound.transaction_type(origin=origin, kind=kind) == expected

    def test_seccl_leg_payment_in_and_withdrawal(self) -> None:
        pay_in = seccl_money.seccl_leg(
            origin="instruction", kind="expected_in", amount=100, currency="GBP"
        )
        assert pay_in is not None
        assert pay_in["transactionType"] == "Payment"
        assert pay_in["transactionSubType"] == "Deposit"
        assert pay_in["movementType"] == "In"
        out = seccl_money.seccl_leg(
            origin="instruction", kind="withdrawal", amount=50, currency="GBP"
        )
        assert out is not None
        assert out["transactionSubType"] == "Withdrawal"
        assert out["movementType"] == "Out"
        assert (
            seccl_money.seccl_leg(origin="transfer", kind="transfer_in", amount=1, currency="GBP")
            is None
        )

    def test_seccl_withdrawal_carries_the_chosen_rail_and_refuses_one_seccl_cannot_name(
        self,
    ) -> None:
        """The leg used to hard-code BACS Credit whatever the adviser chose. BACS is still the
        default and the only rail the vendored collection names; a Faster Payment or SWIFT
        withdrawal is refused rather than sent as BACS behind a quoted fee."""
        default = seccl_money.seccl_leg(
            origin="instruction", kind="withdrawal", amount=50, currency="GBP"
        )
        assert default is not None and default["method"] == "BACS Credit"
        bacs = seccl_money.seccl_leg(
            origin="instruction",
            kind="withdrawal",
            amount=50,
            currency="GBP",
            payment_method="bacs",
        )
        assert bacs is not None and bacs["method"] == "BACS Credit"
        for rail in ("faster_payment", "swift"):
            with pytest.raises(seccl_money.UnsupportedRail, match=rail):
                seccl_money.seccl_leg(
                    origin="instruction",
                    kind="withdrawal",
                    amount=50,
                    currency="GBP",
                    payment_method=rail,
                )
        pay_in = seccl_money.seccl_leg(
            origin="instruction",
            kind="expected_in",
            amount=100,
            currency="GBP",
            payment_method="faster_payment",
        )
        assert pay_in is not None and pay_in["method"] == "Bank Transfer"

    def test_auto_disinvest_rides_a_withdrawal_only_when_asked(self) -> None:
        """The leg used to hard-code autoDisinvest Proportional. It now follows the instruction's
        switch (default on), is dropped when the adviser turns it off, and never rides a pay-in."""
        on = seccl_money.seccl_leg(
            origin="instruction", kind="withdrawal", amount=50, currency="GBP"
        )
        assert on is not None and on["autoDisinvest"] == "Proportional"
        off = seccl_money.seccl_leg(
            origin="instruction",
            kind="withdrawal",
            amount=50,
            currency="GBP",
            auto_disinvest=False,
        )
        assert off is not None and "autoDisinvest" not in off
        pay_in = seccl_money.seccl_leg(
            origin="instruction", kind="expected_in", amount=100, currency="GBP"
        )
        assert pay_in is not None and "autoDisinvest" not in pay_in
        legs = seccl_money.seccl_legs(
            origin="instruction",
            kind="withdrawal",
            amount=50,
            currency="GBP",
            auto_disinvest=False,
        )
        assert legs and "autoDisinvest" not in legs[0]

    def test_display_kind_is_a_word_not_a_key(self) -> None:
        assert display_kind("expected_in") == "Payment"
        assert display_kind("adhoc_fee") == "Adviser fee"
        assert display_kind("model_topup") == "Model top-up"
        assert "_" not in display_kind("model_withdrawal")
        assert set(instructions.KINDS) <= set(KIND_LABEL)
        with pytest.raises(ValueError, match="payment or withdrawal only"):
            instructions._adviser_fee("personal_contribution", 500, 50)
        assert instructions._adviser_fee("expected_in", 500, 50) == 50
        # A withdrawal may carry one now; its fee is an extra debit, not bounded by the amount.
        assert instructions._adviser_fee("withdrawal", 500, 50) == 50

    def test_a_payment_with_a_fee_is_two_legs_in_one_group(self) -> None:
        legs = seccl_money.seccl_legs(
            origin="instruction",
            kind="expected_in",
            amount=500,
            currency="GBP",
            adviser_fee=50,
        )
        assert len(legs) == 2
        assert legs[0]["transactionType"] == "Payment"
        assert legs[0]["transactionSubType"] == "Deposit"
        assert legs[0]["amount"] == 500
        assert legs[1]["transactionType"] == "Cash"
        assert legs[1]["transactionSubType"] == "Adviser Adhoc Fee"
        assert legs[1]["amount"] == 50
        assert (
            len(
                seccl_money.seccl_legs(
                    origin="instruction", kind="expected_in", amount=500, currency="GBP"
                )
            )
            == 1
        )
        # A contribution cannot store a fee. Passing one must not invent a second leg.
        assert (
            len(
                seccl_money.seccl_legs(
                    origin="contribution",
                    kind="personal_contribution",
                    amount=500,
                    currency="GBP",
                    adviser_fee=50,
                )
            )
            == 1
        )


class TestEnsurePending:
    async def test_create_is_idempotent_on_the_graphene_key(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        first = await outbound.ensure_pending(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="contribution",
            origin_uid="aaaaaaaaaaaa",
            kind="personal_contribution",
            account_id=seed["account_id"],
            contact_id=seed["contact_id"],
            amount=800,
        )
        second = await outbound.ensure_pending(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="contribution",
            origin_uid="aaaaaaaaaaaa",
            kind="personal_contribution",
            account_id=seed["account_id"],
            contact_id=seed["contact_id"],
            amount=800,
        )
        assert first["id"] == second["id"]
        assert first["status"] == "pending"
        count = (
            await service_session.execute(
                text(
                    "SELECT count(*) FROM platform.transactions "
                    "WHERE tenant_id = :t AND source_system = 'graphene'"
                ),
                {"t": seed["tenant_id"]},
            )
        ).scalar_one()
        assert int(count) == 1

    async def test_submitted_is_not_restated_to_pending(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        await outbound.ensure_pending(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="contribution",
            origin_uid="bbbbbbbbbbbb",
            kind="personal_contribution",
            account_id=seed["account_id"],
            contact_id=seed["contact_id"],
            amount=800,
        )
        await outbound.mark_outcome(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="contribution",
            origin_uid="bbbbbbbbbbbb",
            status="submitted",
            provider="sei",
            external_id="857458",
        )
        again = await outbound.ensure_pending(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="contribution",
            origin_uid="bbbbbbbbbbbb",
            kind="personal_contribution",
            account_id=seed["account_id"],
            contact_id=seed["contact_id"],
            amount=999,
        )
        assert again["status"] == "submitted"
        assert again["amount"] == 800.0
        link = (
            await service_session.execute(
                text(
                    "SELECT external_id FROM platform.links "
                    "WHERE tenant_id = :t AND entity_type = 'transaction' "
                    "  AND provider = 'sei'"
                ),
                {"t": seed["tenant_id"]},
            )
        ).scalar_one()
        assert link == "857458"


class TestCreatePaths:
    async def test_booking_a_contribution_writes_a_pending_transaction(
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
        tx = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="contribution",
            origin_uid=str(booked["uid"]),
        )
        assert tx is not None
        assert tx["status"] == "pending"
        assert tx["type"] == "deposit"
        assert tx["amount"] == 800.0
        assert tx["source_transaction_id"] == outbound.source_key(
            "contribution", str(booked["uid"])
        )
        assert tx["expectation_id"] == booked["expectation_id"]

    async def test_classifying_a_booked_contribution_as_not_one_cancels_the_deposit(
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
        await contributions.categorise(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=booked["uid"],
            category="not_a_contribution",
        )
        tx = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="contribution",
            origin_uid=str(booked["uid"]),
        )
        assert tx is not None
        assert tx["status"] == "cancelled"

        record = await contributions.categorise(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=booked["uid"],
            category="personal_contribution",
        )
        assert record["status"] == "confirmed"
        restored = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="contribution",
            origin_uid=str(booked["uid"]),
        )
        assert restored is not None
        assert restored["status"] == "pending"

    async def test_a_pending_contribution_is_not_an_unclassified_credit(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
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
            await matching.unclassified_credits(service_session, tenant_id=seed["tenant_id"]) == []
        )

    async def test_lodging_at_sei_submits_and_links(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.integrations import sei as sei_integration

        seed = await _seed(service_session)
        await _link(service_session, seed, provider="sei", external_id="USB0042817")
        booked = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=False,
        )

        async def fake_create(context: dict[str, Any], **_: Any) -> dict[str, Any]:
            return {"activity_id": "857458", "warnings": [], "request": {"sent": True}}

        monkeypatch.setattr(sei_integration, "create_anticipated_receipt", fake_create)

        async def fake_note(
            session: AsyncSession, *, activity_id: str, event_id: str | None = None
        ):
            from app.integrations.sei.receipt_status import store_activity

            snapshot = {
                "activity_id": activity_id,
                "event_id": event_id,
                "activity_status": "7",
                "activity_status_description": "In Process",
                "activity_state": "307",
                "activity_state_description": "Waiting for Matching",
            }
            await store_activity(session, activity_id, snapshot)
            return snapshot

        monkeypatch.setattr("app.integrations.sei.receipt_status.note_lodged", fake_note)

        async def fake_context(*_a: Any, **_k: Any) -> dict[str, Any]:
            return {
                "sei_account_number": "ACC",
                "sei_portfolio_id": "PF",
                "description_type_code": "21121",
                "treatment_type_code": "4958",
                "firm_id": "1",
            }

        monkeypatch.setattr(contributions, "sei_context", fake_context)

        report = await contributions.lodge_expectation(
            service_session, tenant_id=seed["tenant_id"], contribution=booked
        )
        assert report["status"] == "instructed"
        tx = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="contribution",
            origin_uid=str(booked["uid"]),
        )
        assert tx is not None
        assert tx["status"] == "submitted"
        refs = (tx["data"] or {}).get("provider_references") or {}
        assert refs.get("sei") == "857458"
        assert (tx["data"] or {})["sei"]["activity_state_description"] == "Waiting for Matching"

    async def test_raising_a_payment_instruction_writes_pending(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        row = await instructions.create_instruction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by_workos=None,
            kind="expected_in",
            account_id=seed["account_id"],
            amount=250.0,
        )
        tx = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid=str(row["uid"]),
        )
        assert tx is not None
        assert tx["status"] == "pending"
        assert tx["type"] == "deposit"
        subtype = (
            await service_session.execute(
                text(
                    "SELECT source_subtype FROM platform.transactions "
                    "WHERE id = :i AND tenant_id = :t"
                ),
                {"i": tx["id"], "t": seed["tenant_id"]},
            )
        ).scalar_one()
        assert subtype == "Payment"
        assert (
            await matching.unclassified_credits(service_session, tenant_id=seed["tenant_id"]) == []
        )

    async def test_cancelling_an_instruction_cancels_the_pending_transaction(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        row = await instructions.create_instruction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by_workos=None,
            kind="expected_in",
            account_id=seed["account_id"],
            amount=100.0,
        )
        assert await instructions.cancel_instruction(
            service_session, tenant_id=seed["tenant_id"], uid=str(row["uid"])
        )
        tx = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid=str(row["uid"]),
        )
        assert tx is not None
        assert tx["status"] == "cancelled"

    async def test_a_submitted_instruction_cannot_be_cancelled(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        row = await instructions.create_instruction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by_workos=None,
            kind="expected_in",
            account_id=seed["account_id"],
            amount=100.0,
        )
        await outbound.mark_outcome(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid=str(row["uid"]),
            status="submitted",
            provider="seccl",
            external_id="0002P1FQM",
        )
        with pytest.raises(ValueError, match="already been sent"):
            await instructions.cancel_instruction(
                service_session, tenant_id=seed["tenant_id"], uid=str(row["uid"])
            )

    async def test_raising_a_transfer_writes_pending(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        booked = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Old Scheme Ltd",
            basis="cash",
            extent="full",
            total_value=10_000,
        )
        tx = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="transfer",
            origin_uid=str(booked["uid"]),
        )
        assert tx is not None
        assert tx["status"] == "pending"
        assert tx["type"] == "transfer_in"
        assert tx["amount"] == 10000.0

    async def test_recording_to_morgan_lloyd_submits_and_links(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        booked = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Old Scheme Ltd",
            basis="cash",
            extent="full",
            total_value=10_000,
        )
        ml_id = str(uuid.uuid4())
        await transfers.mark_ml_recorded(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=str(booked["uid"]),
            ml_transfer_id=ml_id,
        )
        tx = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="transfer",
            origin_uid=str(booked["uid"]),
        )
        assert tx is not None
        assert tx["status"] == "submitted"
        refs = (tx["data"] or {}).get("provider_references") or {}
        assert refs.get("morgan-lloyd") == ml_id


class TestMatching:
    async def test_matching_the_credit_posts_the_outbound_sibling(
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
        inbound_id = await _credit(service_session, seed)
        await matching.link(
            service_session,
            tenant_id=seed["tenant_id"],
            expectation_id=int(booked["expectation_id"]),
            transaction_id=inbound_id,
        )
        tx = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="contribution",
            origin_uid=str(booked["uid"]),
        )
        assert tx is not None
        assert tx["status"] == "posted"
        record = await contributions.get_contribution(
            service_session, tenant_id=seed["tenant_id"], uid=booked["uid"]
        )
        assert record is not None
        assert record["transaction_id"] == inbound_id
        assert record["transaction_id"] != tx["id"]

    async def test_unlinking_does_not_put_the_pending_row_in_the_queue(
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
        inbound_id = await _credit(service_session, seed)
        await matching.link(
            service_session,
            tenant_id=seed["tenant_id"],
            expectation_id=int(booked["expectation_id"]),
            transaction_id=inbound_id,
        )
        await matching.unlink(
            service_session,
            tenant_id=seed["tenant_id"],
            expectation_id=int(booked["expectation_id"]),
        )
        tx = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="contribution",
            origin_uid=str(booked["uid"]),
        )
        assert tx is not None
        assert tx["status"] == "pending"
        assert tx["expectation_id"] == booked["expectation_id"]
        queued = await matching.unclassified_credits(service_session, tenant_id=seed["tenant_id"])
        assert [row["id"] for row in queued] == [inbound_id]


class TestSecclDispatch:
    async def test_a_successful_group_submits_and_is_not_sent_twice(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        async def fake_group(
            account_id: str, transactions: list[dict[str, Any]], *, external_reference: str
        ) -> dict[str, Any]:
            calls.append(external_reference)
            return {"id": "0002P1FQM"}

        monkeypatch.setattr(seccl_money, "_seccl_group", fake_group)
        seed = await _seed(service_session)
        await _link(service_session, seed, provider="seccl", external_id="SECCL_ACC")
        row = await instructions.create_instruction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by_workos=None,
            kind="expected_in",
            account_id=seed["account_id"],
            amount=100.0,
        )
        tx = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid=str(row["uid"]),
        )
        assert tx is not None
        assert tx["status"] == "pending"
        assert calls == []

        sent = await outbound.dispatch(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid=str(row["uid"]),
        )
        assert sent["status"] == "submitted"
        tx = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid=str(row["uid"]),
        )
        assert tx is not None
        assert tx["status"] == "submitted"
        assert calls == [str(row["uid"])]
        again = await outbound.dispatch(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid=str(row["uid"]),
        )
        assert again["status"] == "skipped"
        assert calls == [str(row["uid"])]

    async def test_a_withdrawal_is_sent_on_the_rail_it_was_raised_on(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The chosen rail and the fee waiver ride on the pending row (``record_instruction``),
        and ``dispatch`` reads them back: BACS goes as BACS Credit; a rail Seccl cannot name is
        skipped with the reason and the row stays open — not sent as BACS behind a quoted fee."""
        legs: list[dict[str, Any]] = []

        async def fake_group(
            account_id: str, transactions: list[dict[str, Any]], *, external_reference: str
        ) -> dict[str, Any]:
            legs.extend(transactions)
            return {"id": "0002P1FQM"}

        monkeypatch.setattr(seccl_money, "_seccl_group", fake_group)
        seed = await _seed(service_session)
        await _link(service_session, seed, provider="seccl", external_id="SECCL_ACC")

        async def raised(uid: str, method: str, pass_on: bool) -> None:
            await outbound.record_instruction(
                service_session,
                tenant_id=seed["tenant_id"],
                instruction={
                    "uid": uid,
                    "kind": "withdrawal",
                    "amount": 500.0,
                    "rail_fee": 0.5,
                    "payment_method": method,
                    "pass_rail_fee": pass_on,
                    "due_date": None,
                },
                account_id=seed["account_id"],
                contact_id=seed["contact_id"],
                currency="GBP",
            )

        await raised("ins_bacs", "bacs", True)
        row = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid="ins_bacs",
        )
        assert row is not None
        assert row["data"]["payment_method"] == "bacs"
        assert row["data"]["pass_rail_fee"] is True
        sent = await outbound.dispatch(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid="ins_bacs",
        )
        assert sent["status"] == "submitted"
        assert [leg["method"] for leg in legs] == ["BACS Credit"]

        await raised("ins_fp", "faster_payment", False)
        row = await outbound.get_by_origin(
            service_session, tenant_id=seed["tenant_id"], origin="instruction", origin_uid="ins_fp"
        )
        assert row is not None
        assert row["data"]["pass_rail_fee"] is False
        held = await outbound.dispatch(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid="ins_fp",
        )
        assert held["status"] == "skipped"
        assert "faster_payment" in held["detail"]
        assert len(legs) == 1
        row = await outbound.get_by_origin(
            service_session, tenant_id=seed["tenant_id"], origin="instruction", origin_uid="ins_fp"
        )
        assert row is not None and row["status"] == "pending"

    async def test_dispatch_follows_the_account_custodian(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        async def fake_group(
            account_id: str, transactions: list[dict[str, Any]], *, external_reference: str
        ) -> dict[str, Any]:
            calls.append(external_reference)
            return {"id": "should-not-fire"}

        monkeypatch.setattr(seccl_money, "_seccl_group", fake_group)
        seed = await _seed(service_session)
        await _link(service_session, seed, provider="sei", external_id="USB0042817")
        row = await instructions.create_instruction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by_workos=None,
            kind="expected_in",
            account_id=seed["account_id"],
            amount=100.0,
        )
        result = await outbound.dispatch(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid=str(row["uid"]),
        )
        assert result["status"] == "skipped"
        assert "anticipated receipt" in result["detail"] or "contribution" in result["detail"]
        assert calls == []

    async def test_lodging_a_seccl_contribution_does_not_call_sei(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.integrations import sei as sei_integration

        async def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
            raise AssertionError("SEI must not be called for a Seccl account")

        monkeypatch.setattr(sei_integration, "create_anticipated_receipt", boom)

        async def fake_group(
            account_id: str, transactions: list[dict[str, Any]], *, external_reference: str
        ) -> dict[str, Any]:
            return {"id": "0002P1FQM"}

        monkeypatch.setattr(seccl_money, "_seccl_group", fake_group)
        seed = await _seed(service_session)
        await _link(service_session, seed, provider="seccl", external_id="SECCL_ACC")
        booked = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=False,
        )
        report = await contributions.lodge_expectation(
            service_session, tenant_id=seed["tenant_id"], contribution=booked
        )
        assert report["status"] == "instructed"
        tx = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="contribution",
            origin_uid=str(booked["uid"]),
        )
        assert tx is not None
        assert tx["status"] == "submitted"
        refs = (tx["data"] or {}).get("provider_references") or {}
        assert refs.get("seccl") == "0002P1FQM"

    async def test_a_payment_with_a_fee_is_two_graphene_rows_and_one_seccl_group(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        groups: list[list[dict[str, Any]]] = []

        async def fake_group(
            account_id: str, transactions: list[dict[str, Any]], *, external_reference: str
        ) -> dict[str, Any]:
            groups.append(transactions)
            return {"id": "0002P1FEE"}

        monkeypatch.setattr(seccl_money, "_seccl_group", fake_group)
        seed = await _seed(service_session)
        await _link(service_session, seed, provider="seccl", external_id="SECCL_ACC")
        row = await instructions.create_instruction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by_workos=None,
            kind="expected_in",
            account_id=seed["account_id"],
            amount=500.0,
            adviser_fee=50.0,
        )
        assert row["adviser_fee"] == 50.0
        ledger = (
            (
                await service_session.execute(
                    text(
                        "SELECT type::text AS type, source_subtype, amount::float8 AS amount, "
                        "       report_group, status "
                        "FROM platform.transactions "
                        "WHERE tenant_id = :t AND source_system = 'graphene' "
                        "ORDER BY type"
                    ),
                    {"t": seed["tenant_id"]},
                )
            )
            .mappings()
            .all()
        )
        assert [(r["type"], r["source_subtype"], r["amount"]) for r in ledger] == [
            ("deposit", "Payment", 500.0),
            ("fee", "Adviser fee", 50.0),
        ]
        assert ledger[0]["report_group"] == ledger[1]["report_group"] == str(row["uid"])
        assert {r["status"] for r in ledger} == {"pending"}

        sent = await outbound.dispatch(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid=str(row["uid"]),
        )
        assert sent["status"] == "submitted"
        assert len(groups) == 1
        assert len(groups[0]) == 2
        assert groups[0][0]["transactionSubType"] == "Deposit"
        assert groups[0][1]["transactionSubType"] == "Adviser Adhoc Fee"
        assert groups[0][0]["amount"] == 500
        assert groups[0][1]["amount"] == 50
        after = (
            (
                await service_session.execute(
                    text(
                        "SELECT status FROM platform.transactions "
                        "WHERE tenant_id = :t AND source_system = 'graphene'"
                    ),
                    {"t": seed["tenant_id"]},
                )
            )
            .scalars()
            .all()
        )
        assert set(after) == {"submitted"}

    async def test_a_transaction_link_skips_a_second_send_without_the_json_copy(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        async def fake_group(
            account_id: str, transactions: list[dict[str, Any]], *, external_reference: str
        ) -> dict[str, Any]:
            calls.append(external_reference)
            return {"id": "0002P1LNK"}

        monkeypatch.setattr(seccl_money, "_seccl_group", fake_group)
        seed = await _seed(service_session)
        await _link(service_session, seed, provider="seccl", external_id="SECCL_ACC")
        row = await instructions.create_instruction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by_workos=None,
            kind="expected_in",
            account_id=seed["account_id"],
            amount=100.0,
        )
        tx = await outbound.get_by_origin(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid=str(row["uid"]),
        )
        assert tx is not None
        await outbound.record_link(
            service_session,
            tenant_id=seed["tenant_id"],
            transaction_id=int(tx["id"]),
            provider="seccl",
            external_id="0002P1LNK",
        )
        await service_session.execute(
            text(
                "UPDATE platform.transactions SET data = data - 'provider_references' "
                "WHERE id = :i AND tenant_id = :t"
            ),
            {"i": tx["id"], "t": seed["tenant_id"]},
        )
        sent = await outbound.dispatch(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid=str(row["uid"]),
        )
        assert sent["status"] == "skipped"
        assert sent["id"] == "0002P1LNK"
        assert calls == []

    async def test_send_booked_sends_the_named_row_and_refuses_an_unnamed_pair(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.actions.attempt import OperationError
        from app.integrations.seccl.instruct import send_booked

        calls: list[str] = []

        async def fake_group(
            account_id: str, transactions: list[dict[str, Any]], *, external_reference: str
        ) -> dict[str, Any]:
            calls.append(external_reference)
            return {"id": "0002P1NAM"}

        monkeypatch.setattr(seccl_money, "_seccl_group", fake_group)
        seed = await _seed(service_session)
        await _link(service_session, seed, provider="seccl", external_id="SECCL_ACC")
        first = await instructions.create_instruction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by_workos=None,
            kind="expected_in",
            account_id=seed["account_id"],
            amount=100.0,
        )
        await instructions.create_instruction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by_workos=None,
            kind="expected_in",
            account_id=seed["account_id"],
            amount=200.0,
        )
        with pytest.raises(OperationError, match="More than one"):
            await send_booked(
                service_session,
                tenant_id=seed["tenant_id"],
                account_id=seed["account_id"],
                kinds={"expected_in"},
                missing="missing",
            )
        sent = await send_booked(
            service_session,
            tenant_id=seed["tenant_id"],
            account_id=seed["account_id"],
            kinds={"expected_in"},
            missing="missing",
            instruction_uid=str(first["uid"]),
        )
        assert sent["status"] == "submitted"
        assert calls == [str(first["uid"])]

    async def test_the_payment_attempt_is_the_seccl_call(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.integrations.seccl.actions import seccl_payment

        async def fake_group(
            account_id: str, transactions: list[dict[str, Any]], *, external_reference: str
        ) -> dict[str, Any]:
            return {
                "request": {
                    "firmId": "FIRM",
                    "accountId": account_id,
                    "externalReference": external_reference,
                    "transactions": transactions,
                },
                "response": {"id": "0002QNLQ8"},
            }

        monkeypatch.setattr(seccl_money, "_seccl_group", fake_group)
        seed = await _seed(service_session)
        await _link(service_session, seed, provider="seccl", external_id="SECCL_ACC")
        row = await instructions.create_instruction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by_workos=None,
            kind="expected_in",
            account_id=seed["account_id"],
            amount=100.0,
        )
        attempt = await seccl_payment(
            service_session,
            payload={
                "tenant_id": seed["tenant_id"],
                "account_id": seed["account_id"],
                "instruction_uid": str(row["uid"]),
            },
            subjects=[],
        )
        assert attempt.ok is True
        assert attempt.method == "POST"
        assert attempt.url == "/portfoliotransactiongroup"
        assert attempt.response == {"id": "0002QNLQ8"}
        assert attempt.request["externalReference"] == str(row["uid"])
        assert attempt.request["accountId"] == "SECCL_ACC"
        assert "rail" not in attempt.response
        assert all("nodeId" not in member for member in attempt.request["transactions"])

    async def test_a_cancelled_row_is_not_already_instructed(
        self, service_session: AsyncSession
    ) -> None:
        from app.actions.attempt import OperationError
        from app.integrations.seccl.instruct import send_booked

        seed = await _seed(service_session)
        row = await instructions.create_instruction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by_workos=None,
            kind="expected_in",
            account_id=seed["account_id"],
            amount=100.0,
        )
        await outbound.mark_outcome(
            service_session,
            tenant_id=seed["tenant_id"],
            origin="instruction",
            origin_uid=str(row["uid"]),
            status="cancelled",
        )
        with pytest.raises(OperationError, match="missing"):
            await send_booked(
                service_session,
                tenant_id=seed["tenant_id"],
                account_id=seed["account_id"],
                kinds={"expected_in"},
                missing="missing",
                instruction_uid=str(row["uid"]),
            )

    async def test_an_ad_hoc_fee_action_does_not_send_the_fee_on_a_payment(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.actions.attempt import OperationError
        from app.integrations.seccl.instruct import send_booked

        async def fake_group(
            account_id: str, transactions: list[dict[str, Any]], *, external_reference: str
        ) -> dict[str, Any]:
            return {"id": "should-not-fire"}

        monkeypatch.setattr(seccl_money, "_seccl_group", fake_group)
        seed = await _seed(service_session)
        await _link(service_session, seed, provider="seccl", external_id="SECCL_ACC")
        row = await instructions.create_instruction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by_workos=None,
            kind="expected_in",
            account_id=seed["account_id"],
            amount=500.0,
            adviser_fee=50.0,
        )
        sibling = (
            await service_session.execute(
                text(
                    "SELECT source_transaction_id FROM platform.transactions "
                    "WHERE tenant_id = :t AND data ->> 'companion_of' = :uid"
                ),
                {"t": seed["tenant_id"], "uid": str(row["uid"])},
            )
        ).scalar_one()
        with pytest.raises(OperationError, match="missing"):
            await send_booked(
                service_session,
                tenant_id=seed["tenant_id"],
                account_id=seed["account_id"],
                kinds={"adhoc_fee"},
                missing="missing",
            )
        with pytest.raises(OperationError, match="missing"):
            await send_booked(
                service_session,
                tenant_id=seed["tenant_id"],
                account_id=seed["account_id"],
                kinds={"adhoc_fee"},
                missing="missing",
                instruction_uid=str(sibling),
            )

    async def test_a_switched_off_payment_is_not_sent(self, service_session: AsyncSession) -> None:
        from app.actions.money import lodge_instruction
        from app.actions.runs import ActionError

        seed = await _seed(service_session)
        await _link(service_session, seed, provider="seccl", external_id="SECCL_ACC")
        row = await instructions.create_instruction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by_workos=None,
            kind="expected_in",
            account_id=seed["account_id"],
            amount=100.0,
        )
        await service_session.execute(
            text(
                "INSERT INTO actions.policies (tenant_id, action_key, enabled) "
                "VALUES (:t, 'transaction.payment', false)"
            ),
            {"t": seed["tenant_id"]},
        )
        with pytest.raises(ActionError, match="switched off"):
            await lodge_instruction(
                service_session,
                tenant_id=seed["tenant_id"],
                kind="expected_in",
                instruction_uid=str(row["uid"]),
                account_id=seed["account_id"],
            )


async def _approver(session: AsyncSession, tenant_id: int) -> tuple[int, str]:
    role_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
                    "VALUES (:t, 'approver', 'Approver', 'organisation', 'platform') "
                    "RETURNING id"
                ),
                {"t": tenant_id},
            )
        ).scalar_one()
    )
    nonce = uuid.uuid4().hex[:8]
    checker = str(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, workos_user_id, email, first_name) "
                    "VALUES (:uid, :t, :w, :e, 'Bea') RETURNING id"
                ),
                {
                    "uid": f"usr_{nonce}",
                    "t": tenant_id,
                    "w": f"user_{nonce}",
                    "e": f"bea.{nonce}@example.test",
                },
            )
        ).scalar_one()
    )
    return role_id, checker


async def _money_rows(session: AsyncSession, tenant_id: int, uid: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in (
            await session.execute(
                text(
                    "SELECT source_transaction_id, status, review_status "
                    "FROM platform.transactions "
                    "WHERE tenant_id = :t AND source_system = 'graphene' "
                    "  AND report_group = :uid"
                ),
                {"t": tenant_id, "uid": uid},
            )
        ).mappings()
    ]
