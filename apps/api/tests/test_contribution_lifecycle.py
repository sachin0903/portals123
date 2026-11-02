"""The lifecycle 0056 made reachable: classify-from-credit, matching, ML status, cancel.

Companion to ``test_contributions_flow.py``, which covers booking and lodging. What is tested
here is the rest of the journey the user's flow describes and the workbench could not drive:

    A. client sends money -> SEI transaction -> adviser categorises -> ML (Application)
       -> ML+HMRC (Active) -> ML (Received) -> SEI relief payment -> reconciled
    B. adviser books (SEI ACR) -> client sends money -> transaction MATCHED -> ...

Real Postgres, because the interesting parts are SQL: the "already accounted for" predicate
behind the unclassified queue, the matcher's window, and the CHECK constraints 0056 added.
No truncate (``service_session`` is shared), so every fixture mints unique uids.
"""

import json
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import contributions, matching, transfers
from tests.conftest import SECCL_PLATFORM_SQL

pytestmark = pytest.mark.anyio


async def _seed(session: AsyncSession, *, product: str = "SIPP") -> dict[str, Any]:
    """One firm, one client, one pension account."""
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
    # The registry row still has to exist — `sync_platforms` seeds it on startup and the
    # merge resolves `platform.platforms.provider` — but the product no longer hangs off it
    # (0130 dropped `products.platform_id`), so the id is not bound to anything.
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


async def _credit(
    session: AsyncSession,
    seed: dict[str, Any],
    *,
    amount: str = "800.00",
    on: date | None = None,
    kind: str = "deposit",
    description_code: str | None = None,
) -> int:
    """A realised incoming transaction, as the materialiser would have landed it."""
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO platform.transactions "
                    "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                    "   source_type, source_subtype, amount, currency, transaction_date) "
                    "VALUES (:t, :a, 'sei', :sid, CAST(:kind AS platform.transaction_type), "
                    "        'Cash Receipt', :code, :amt, 'GBP', :on) RETURNING id"
                ),
                {
                    "t": seed["tenant_id"],
                    "a": seed["account_id"],
                    "sid": f"txn_{uuid.uuid4().hex[:12]}",
                    "kind": kind,
                    "code": description_code,
                    "amt": amount,
                    "on": on or date.today(),
                },
            )
        ).scalar_one()
    )


class TestUnclassifiedQueue:
    """Path A's front: money that arrived with nothing said about it."""

    async def test_an_incoming_credit_appears(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await _credit(service_session, seed)
        rows = await matching.unclassified_credits(service_session, tenant_id=seed["tenant_id"])
        assert [r["amount"] for r in rows] == [800.0]
        assert rows[0]["client_name"] == "Sophie Halloway"

    async def test_money_going_out_does_not(self, service_session: AsyncSession) -> None:
        """The queue is for classifying contributions; a withdrawal is not one."""
        seed = await _seed(service_session)
        await _credit(service_session, seed, kind="withdrawal")
        assert (
            await matching.unclassified_credits(service_session, tenant_id=seed["tenant_id"]) == []
        )

    async def test_a_classified_credit_leaves_the_queue(
        self, service_session: AsyncSession
    ) -> None:
        """Otherwise an adviser classifies the same credit every time they open the page."""
        seed = await _seed(service_session)
        transaction_id = await _credit(service_session, seed)
        await contributions.classify_transaction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            transaction_id=transaction_id,
            category="personal_contribution",
        )
        assert (
            await matching.unclassified_credits(service_session, tenant_id=seed["tenant_id"]) == []
        )

    async def test_a_matched_credit_leaves_the_queue(self, service_session: AsyncSession) -> None:
        """Path B's credit is claimed via the expectation, not by `transaction_id` directly —
        so a queue that only checked for a contribution row would show it as unclassified."""
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
        transaction_id = await _credit(service_session, seed)
        await matching.link(
            service_session,
            tenant_id=seed["tenant_id"],
            expectation_id=int(booked["expectation_id"]),
            transaction_id=transaction_id,
        )
        assert (
            await matching.unclassified_credits(service_session, tenant_id=seed["tenant_id"]) == []
        )

    async def test_another_firms_credit_is_invisible(self, service_session: AsyncSession) -> None:
        mine = await _seed(service_session)
        theirs = await _seed(service_session)
        await _credit(service_session, theirs)
        assert (
            await matching.unclassified_credits(service_session, tenant_id=mine["tenant_id"]) == []
        )

    async def test_sei_relief_credits_are_flagged_not_offered_as_contributions(
        self, service_session: AsyncSession
    ) -> None:
        """21122 is the relief SEI paid in. Classifying it as a fresh contribution would
        double-count the member's annual allowance and reclaim relief on relief."""
        seed = await _seed(service_session)
        await _credit(service_session, seed, amount="200.00", description_code="21122")
        row = (await matching.unclassified_credits(service_session, tenant_id=seed["tenant_id"]))[0]
        assert row["is_relief_payment"] is True
        assert row["suggested_category"] is None

    async def test_a_member_net_credit_suggests_a_personal_contribution(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        await _credit(service_session, seed, description_code="21121")
        row = (await matching.unclassified_credits(service_session, tenant_id=seed["tenant_id"]))[0]
        assert row["suggested_category"] == "personal_contribution"
        assert row["provider_description"] == "Funding Transaction Member Net Contribution"


class TestClassifyFromTransaction:
    async def test_the_contribution_takes_the_credits_amount(
        self, service_session: AsyncSession
    ) -> None:
        """Not the caller's — a £450 credit must not become a £500 basic-rate reclaim."""
        seed = await _seed(service_session)
        transaction_id = await _credit(service_session, seed, amount="450.00")
        record = await contributions.classify_transaction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            transaction_id=transaction_id,
            category="personal_contribution",
        )
        assert record["amount"] == 450.0
        assert record["gross_amount"] == 562.5
        assert record["relief_amount"] == 112.5

    async def test_it_raises_no_expectation(self, service_session: AsyncSession) -> None:
        """A forecast for money that already arrived is a row the matcher can never resolve."""
        seed = await _seed(service_session)
        transaction_id = await _credit(service_session, seed)
        record = await contributions.classify_transaction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            transaction_id=transaction_id,
            category="personal_contribution",
        )
        assert record["expectation_id"] is None
        assert record["transaction_id"] == transaction_id
        assert record["status"] == "confirmed"

    async def test_a_second_classification_is_refused(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        transaction_id = await _credit(service_session, seed)
        await contributions.classify_transaction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            transaction_id=transaction_id,
            category="personal_contribution",
        )
        with pytest.raises(contributions.AlreadyClassified):
            await contributions.classify_transaction(
                service_session,
                tenant_id=seed["tenant_id"],
                created_by=None,
                transaction_id=transaction_id,
                category="personal_contribution",
            )

    async def test_not_a_contribution_is_recorded_and_cancelled(
        self, service_session: AsyncSession
    ) -> None:
        """The adviser's decision is kept — that is what stops the credit resurfacing — but it
        does not sit in the pipeline pretending to be work."""
        seed = await _seed(service_session)
        transaction_id = await _credit(service_session, seed)
        record = await contributions.classify_transaction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            transaction_id=transaction_id,
            category="not_a_contribution",
        )
        assert record["status"] == "cancelled"
        assert record["cancelled_at"] is not None
        assert (
            await matching.unclassified_credits(service_session, tenant_id=seed["tenant_id"]) == []
        )

    async def test_a_transfer_in_is_recorded_and_cancelled(
        self, service_session: AsyncSession
    ) -> None:
        """Same as `not_a_contribution`: a transfer is not a contribution, so it must not sit
        in Not lodged inflating the KPI and blocking a push that `UNPUSHABLE` already forbids.

        `categorise` already cancelled both disposals; `classify_transaction` only cancelled
        `not_a_contribution`, which is how a credit marked transfer-in stayed live.
        """
        seed = await _seed(service_session)
        transaction_id = await _credit(service_session, seed)
        record = await contributions.classify_transaction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            transaction_id=transaction_id,
            category="transfer_in",
        )
        assert record["status"] == "cancelled"
        assert record["cancelled_at"] is not None
        assert record["cancelled_reason"]
        assert contributions.stage_of(record["status"]) == "cancelled"
        assert contributions.stage_of(record["status"]) != "not_lodged"
        assert (
            await matching.unclassified_credits(service_session, tenant_id=seed["tenant_id"]) == []
        )

    async def test_another_firms_credit_is_not_found(self, service_session: AsyncSession) -> None:
        mine = await _seed(service_session)
        theirs = await _seed(service_session)
        transaction_id = await _credit(service_session, theirs)
        with pytest.raises(LookupError):
            await contributions.classify_transaction(
                service_session,
                tenant_id=mine["tenant_id"],
                created_by=None,
                transaction_id=transaction_id,
                category="personal_contribution",
            )


class TestReopeningADisposal:
    """A disposal is reversible; a recorded contribution is not.

    `not_a_contribution` is written AND cancelled in the same call — that is what stops the credit
    resurfacing — which lands it on a terminal status `CLASSIFIABLE` excludes. So an adviser's
    mis-click was permanent: the credit was gone from the queue and the only way back was SQL.

    Reopening one is safe for a stated reason rather than a convenient one: nothing downstream ever
    held it. No `cam_contribution` exists (`UNPUSHABLE`), no expectation was lodged, no relief was
    reclaimed — which is the whole argument the status gate is built on.
    """

    async def _disposed(self, session: AsyncSession, category: str) -> dict[str, Any]:
        seed = await _seed(session)
        transaction_id = await _credit(session, seed)
        record = await contributions.classify_transaction(
            session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            transaction_id=transaction_id,
            category=category,
        )
        return {**seed, "uid": record["uid"], "record": record}

    async def test_not_a_contribution_can_be_classified_back(
        self, service_session: AsyncSession
    ) -> None:
        d = await self._disposed(service_session, "not_a_contribution")
        assert d["record"]["status"] == "cancelled"
        record = await contributions.categorise(
            service_session,
            tenant_id=d["tenant_id"],
            uid=d["uid"],
            category="personal_contribution",
        )
        assert record["category"] == "personal_contribution"
        assert record["status"] == "confirmed"
        # AND the cancellation is undone. The panel renders `cancelled_reason`, so leaving it would
        # make the record say "Cancelled — classified as not a contribution" about a personal
        # contribution somebody had just corrected it to.
        assert record["cancelled_at"] is None
        assert record["cancelled_reason"] is None
        # The relief arithmetic is recomputed for the category it is NOW.
        assert record["relief_amount"] == 200.0

    async def test_a_transfer_in_can_be_classified_back(
        self, service_session: AsyncSession
    ) -> None:
        d = await self._disposed(service_session, "transfer_in")
        assert d["record"]["status"] == "cancelled"
        record = await contributions.categorise(
            service_session,
            tenant_id=d["tenant_id"],
            uid=d["uid"],
            category="personal_contribution",
        )
        assert record["category"] == "personal_contribution"
        assert record["status"] == "confirmed"

    async def test_classifying_TO_a_disposal_cancels_it(
        self, service_session: AsyncSession
    ) -> None:
        # `classify_transaction` did this on CREATE and `categorise` did not, so reclassifying an
        # existing row to `not_a_contribution` left it in the pipeline as live work — and the credit
        # kept resurfacing, which is the exact thing the disposal exists to stop.
        seed = await _seed(service_session)
        booked = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=500,
            lodge=False,
        )
        record = await contributions.categorise(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=booked["uid"],
            category="not_a_contribution",
        )
        assert record["status"] == "cancelled"
        assert record["cancelled_at"] is not None
        assert record["cancelled_reason"]

    async def test_a_recorded_contribution_is_still_refused(
        self, service_session: AsyncSession
    ) -> None:
        # The belt on the reopen rule: the argument is "nothing downstream held it", so a row that
        # somehow reached Morgan Lloyd is refused even though its category is a disposal.
        d = await self._disposed(service_session, "not_a_contribution")
        await service_session.execute(
            text("UPDATE platform.contributions SET ml_contribution_id = 'cam_x' WHERE uid = :u"),
            {"u": d["uid"]},
        )
        with pytest.raises(contributions.ContributionConflict):
            await contributions.categorise(
                service_session,
                tenant_id=d["tenant_id"],
                uid=d["uid"],
                category="personal_contribution",
            )


class TestClientAddressing:
    """A contribution names its client by a PUBLIC handle, not by the serial it joins on.

    `_SELECT` carried `account_uid` and no client counterpart, so the workbench could show a
    client's name and had no way to link to them. That is the third appearance of one shape:
    `docs/api.md` -> "Addressing" records the same omission on the transactions projection and on
    `GET /documents/requests`, both of which linked the SERIAL as a result — `/clients/42`.
    """

    async def test_a_contribution_carries_the_clients_uid(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        booked = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=500,
            lodge=False,
        )
        expected = (
            await service_session.execute(
                text("SELECT uid FROM crm.contacts WHERE id = :c"), {"c": seed["contact_id"]}
            )
        ).scalar_one()
        assert booked["contact_uid"] == expected
        # And the serial is still there — it is what the API's own filters and joins key on.
        assert booked["contact_id"] == seed["contact_id"]

    async def test_a_contribution_with_no_client_reports_no_uid(
        self, service_session: AsyncSession
    ) -> None:
        # `contact_id` is nullable (ON DELETE SET NULL), so the join can legitimately find nothing.
        # NULL is the honest answer: the portal renders the name as plain text rather than linking
        # to a record that is not there.
        seed = await _seed(service_session)
        booked = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=500,
            lodge=False,
        )
        await service_session.execute(
            text("UPDATE platform.contributions SET contact_id = NULL WHERE uid = :u"),
            {"u": booked["uid"]},
        )
        record = await contributions.get_contribution(
            service_session, tenant_id=seed["tenant_id"], uid=booked["uid"]
        )
        assert record is not None
        assert record["contact_uid"] is None


class TestEmployerFromTheClientRecord:
    """The contributor's name comes from the CLIENT (0126), not from a per-payment form.

    `platform.contributions` has carried a nine-column contributor block since 0051, on the
    reasoning that "the payer of an employer contribution is not the client, so crm.contacts cannot
    supply it". The first half is true; the second does not follow. The payer is the client's
    EMPLOYER, and who somebody works for is a fact about them that changes when they change jobs —
    not a fact about one £500 credit. The classify panel was asking for it (plus an address, a
    town, a postcode and an email that Morgan Lloyd marks optional) once per payment, of an adviser
    looking at a bank reference.

    So the client record holds it and every write path defaults from there. The contribution keeps
    its own copy on purpose: it is what was actually filed for that payment, and a client changing
    employer in March must not restate what was sent in February.
    """

    async def _employed(self, session: AsyncSession, employer: str | None) -> dict[str, Any]:
        seed = await _seed(session)
        await session.execute(
            text("UPDATE crm.contacts SET employer_name = :e WHERE id = :c"),
            {"e": employer, "c": seed["contact_id"]},
        )
        return seed

    async def test_a_classified_credit_takes_the_clients_employer(
        self, service_session: AsyncSession
    ) -> None:
        seed = await self._employed(service_session, "Wetherington-Smythe Group Holdings")
        transaction_id = await _credit(service_session, seed)
        record = await contributions.classify_transaction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            transaction_id=transaction_id,
            category="employer_contribution",
        )
        # Which is what satisfies `contributions_employer_named` at `confirmed` — the CHECK is
        # unchanged, it is just met from the place the name actually lives.
        assert record["employer_name"] == "Wetherington-Smythe Group Holdings"
        assert record["status"] == "confirmed"

    async def test_an_explicit_employer_still_wins(self, service_session: AsyncSession) -> None:
        # A one-off employer contribution from a company the client does not work for is a real
        # thing, so the client record is a DEFAULT and never an override.
        seed = await self._employed(service_session, "Wetherington-Smythe Group Holdings")
        transaction_id = await _credit(service_session, seed)
        record = await contributions.classify_transaction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            transaction_id=transaction_id,
            category="employer_contribution",
            employer={"employer_name": "A former employer"},
        )
        assert record["employer_name"] == "A former employer"

    async def test_no_employer_on_file_is_refused_by_naming_where_to_fix_it(
        self, service_session: AsyncSession
    ) -> None:
        # The panel cannot fix this, because an employer is not a property of one payment — so the
        # refusal has to say where it IS fixed, or an adviser is left looking for a field that no
        # longer exists on the form in front of them.
        seed = await self._employed(service_session, None)
        transaction_id = await _credit(service_session, seed)
        with pytest.raises(ValueError, match="client record"):
            await contributions.classify_transaction(
                service_session,
                tenant_id=seed["tenant_id"],
                created_by=None,
                transaction_id=transaction_id,
                category="employer_contribution",
            )

    async def test_booking_one_takes_it_too(self, service_session: AsyncSession) -> None:
        seed = await self._employed(service_session, "Wetherington-Smythe Group Holdings")
        record = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="employer_contribution",
            amount=500,
            lodge=False,
        )
        assert record["employer_name"] == "Wetherington-Smythe Group Holdings"

    async def test_categorising_falls_back_to_it(self, service_session: AsyncSession) -> None:
        # The panel sends no contributor block at all now, so `categorise` must be able to reach an
        # employer name or a personal contribution could never be reclassified as an employer one.
        seed = await self._employed(service_session, "Wetherington-Smythe Group Holdings")
        booked = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=500,
            lodge=False,
        )
        record = await contributions.categorise(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=booked["uid"],
            category="employer_contribution",
        )
        assert record["employer_name"] == "Wetherington-Smythe Group Holdings"

    async def test_the_queue_carries_it_so_the_panel_can_warn_first(
        self, service_session: AsyncSession
    ) -> None:
        # Read on the credit itself, so the panel says "this client has no employer on file" BEFORE
        # the adviser presses Classify rather than after the API refuses.
        seed = await self._employed(service_session, "Wetherington-Smythe Group Holdings")
        await _credit(service_session, seed)
        [credit] = await matching.unclassified_credits(service_session, tenant_id=seed["tenant_id"])
        assert credit["client_employer_name"] == "Wetherington-Smythe Group Holdings"


class TestMatcher:
    async def _booked(
        self,
        session: AsyncSession,
        seed: dict[str, Any],
        *,
        amount: int = 800,
        due: date | None = None,
    ) -> dict[str, Any]:
        return await contributions.create_contribution(
            session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=amount,
            due_date=due or date.today(),
            lodge=False,
        )

    async def test_an_exact_credit_in_the_window_is_matched(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        booked = await self._booked(service_session, seed)
        transaction_id = await _credit(service_session, seed, amount="800.00")

        report = await matching.run(service_session, tenant_id=seed["tenant_id"])
        assert report["matched"] == 1

        record = await contributions.get_contribution(
            service_session, tenant_id=seed["tenant_id"], uid=booked["uid"]
        )
        assert record is not None
        assert record["transaction_id"] == transaction_id
        assert record["expectation_status"] == "matched"
        # NULL matched_by is how an automatic match stays distinguishable from a person's.
        assert record["matched_by_hand"] is False

    async def test_a_different_amount_is_not_matched(self, service_session: AsyncSession) -> None:
        """No tolerance band: a £750 credit against an £800 forecast is a question, not a match."""
        seed = await _seed(service_session)
        await self._booked(service_session, seed)
        await _credit(service_session, seed, amount="750.00")
        report = await matching.run(service_session, tenant_id=seed["tenant_id"])
        assert report["matched"] == 0
        assert report["unmatched"] == 1

    async def test_a_credit_outside_the_window_is_not_matched(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        await self._booked(service_session, seed, due=date.today())
        await _credit(
            service_session,
            seed,
            amount="800.00",
            on=date.today() + timedelta(days=matching.MATCH_WINDOW_DAYS + 1),
        )
        report = await matching.run(service_session, tenant_id=seed["tenant_id"])
        assert report["matched"] == 0

    async def test_two_identical_credits_are_left_for_a_human(
        self, service_session: AsyncSession
    ) -> None:
        """The case a tolerance-based matcher gets wrong silently: a client who paid twice.
        Picking one is a coin flip against a pension."""
        seed = await _seed(service_session)
        await self._booked(service_session, seed)
        await _credit(service_session, seed, amount="800.00")
        await _credit(service_session, seed, amount="800.00")
        report = await matching.run(service_session, tenant_id=seed["tenant_id"])
        assert report["matched"] == 0
        assert report["ambiguous"] == 1
        assert len(report["needs_review"][0]["candidates"]) == 2

    async def test_a_credit_is_never_matched_twice(self, service_session: AsyncSession) -> None:
        """Two expectations of the same amount and one credit: the first claims it, and the
        second must not also claim it — that would report money we never received."""
        seed = await _seed(service_session)
        await self._booked(service_session, seed)
        await self._booked(service_session, seed)
        await _credit(service_session, seed, amount="800.00")

        report = await matching.run(service_session, tenant_id=seed["tenant_id"])
        assert report["matched"] == 1

        claimed = (
            await service_session.execute(
                text(
                    "SELECT count(*) FROM platform.contributions "
                    "WHERE tenant_id = :t AND transaction_id IS NOT NULL"
                ),
                {"t": seed["tenant_id"]},
            )
        ).scalar_one()
        assert claimed == 1

    async def test_running_twice_is_idempotent(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await self._booked(service_session, seed)
        await _credit(service_session, seed, amount="800.00")
        assert (await matching.run(service_session, tenant_id=seed["tenant_id"]))["matched"] == 1
        assert (await matching.run(service_session, tenant_id=seed["tenant_id"]))["matched"] == 0

    async def test_unlinking_returns_the_expectation_to_instructed(
        self, service_session: AsyncSession
    ) -> None:
        """It is still lodged at the custodian; only our belief about which credit paid it
        changed. Sending it back to `expected` would misreport the custodian's state."""
        seed = await _seed(service_session)
        booked = await self._booked(service_session, seed)
        expectation_id = int(booked["expectation_id"])
        await service_session.execute(
            text(
                "UPDATE platform.transaction_expectations "
                "SET status = 'instructed', instructed_at = now() WHERE id = :e"
            ),
            {"e": expectation_id},
        )
        await _credit(service_session, seed, amount="800.00")
        await matching.run(service_session, tenant_id=seed["tenant_id"])
        await matching.unlink(
            service_session, tenant_id=seed["tenant_id"], expectation_id=expectation_id
        )

        record = await contributions.get_contribution(
            service_session, tenant_id=seed["tenant_id"], uid=booked["uid"]
        )
        assert record is not None
        assert record["expectation_status"] == "instructed"
        assert record["transaction_id"] is None
        # ...and the credit is back in the queue rather than lost.
        assert (
            len(await matching.unclassified_credits(service_session, tenant_id=seed["tenant_id"]))
            == 1
        )

    async def test_a_manual_match_is_stamped_with_the_operator(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        booked = await self._booked(service_session, seed)
        transaction_id = await _credit(service_session, seed, amount="123.45")
        workos_id = f"user_{uuid.uuid4().hex[:10]}"
        # `name` is a generated column (first + last), so it is set by writing the parts.
        await service_session.execute(
            text(
                "INSERT INTO auth.users "
                "  (tenant_id, uid, workos_user_id, email, first_name, last_name) "
                "VALUES (:t, :uid, :w, :e, 'Ops', 'Person')"
            ),
            {
                "t": seed["tenant_id"],
                "uid": f"usr_{uuid.uuid4().hex[:10]}",
                "w": workos_id,
                "e": f"{workos_id}@example.com",
            },
        )
        await matching.link(
            service_session,
            tenant_id=seed["tenant_id"],
            expectation_id=int(booked["expectation_id"]),
            transaction_id=transaction_id,
            matched_by=workos_id,
        )
        record = await contributions.get_contribution(
            service_session, tenant_id=seed["tenant_id"], uid=booked["uid"]
        )
        assert record is not None
        assert record["matched_by_hand"] is True


class TestContributionDate:
    """The contribution date comes from the MONEY, not from whoever booked it.

    The booking drawer stopped asking for it (`contribution-form.svelte`): on a book-ahead the
    payment has not been made, so any date entered is a guess, and a guess written into a column
    the tax-year and over-75 checks read from is worse than a blank. So the column stays NULL
    until a credit is matched, and the matcher fills it.
    """

    async def _booked(
        self, session: AsyncSession, seed: dict[str, Any], **kw: Any
    ) -> dict[str, Any]:
        return await contributions.create_contribution(
            session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=False,
            **kw,
        )

    async def test_a_booking_with_no_date_stores_none(self, service_session: AsyncSession) -> None:
        """It used to store today's date, which reads as "the client paid today" — a fact nobody
        stated and one the reconciliation would then be measured against."""
        seed = await _seed(service_session)
        booked = await self._booked(service_session, seed)
        assert booked["contribution_date"] is None

    async def test_the_tax_year_is_still_derived(self, service_session: AsyncSession) -> None:
        """Leaving the date NULL must not leave the tax year NULL: the booking belongs to the year
        it is made in."""
        seed = await _seed(service_session)
        booked = await self._booked(service_session, seed)
        assert booked["tax_year"] == contributions.tax_year_of(date.today())

    async def test_the_matched_credit_supplies_the_date(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        booked = await self._booked(service_session, seed, due_date=date.today())
        paid_on = date.today() - timedelta(days=3)
        await _credit(service_session, seed, amount="800.00", on=paid_on)

        await matching.run(service_session, tenant_id=seed["tenant_id"])
        record = await contributions.get_contribution(
            service_session, tenant_id=seed["tenant_id"], uid=booked["uid"]
        )
        assert record is not None
        assert record["contribution_date"] == paid_on

    async def test_a_stated_date_survives_the_match(self, service_session: AsyncSession) -> None:
        """An adviser recording a payment they know the date of has stated a fact. The credit we
        matched to it settles on its own timetable, and must not overwrite one."""
        seed = await _seed(service_session)
        stated = date.today() - timedelta(days=10)
        booked = await self._booked(service_session, seed, contribution_date=stated)
        await _credit(service_session, seed, amount="800.00", on=date.today())

        await matching.run(service_session, tenant_id=seed["tenant_id"])
        record = await contributions.get_contribution(
            service_session, tenant_id=seed["tenant_id"], uid=booked["uid"]
        )
        assert record is not None
        assert record["contribution_date"] == stated

    async def test_unlinking_takes_back_the_date_it_stamped(
        self, service_session: AsyncSession
    ) -> None:
        """Otherwise a rejected match leaves behind a date sourced from a credit we no longer
        believe paid this contribution."""
        seed = await _seed(service_session)
        booked = await self._booked(service_session, seed, due_date=date.today())
        await _credit(service_session, seed, amount="800.00", on=date.today() - timedelta(days=2))
        await matching.run(service_session, tenant_id=seed["tenant_id"])
        await matching.unlink(
            service_session,
            tenant_id=seed["tenant_id"],
            expectation_id=int(booked["expectation_id"]),
        )
        record = await contributions.get_contribution(
            service_session, tenant_id=seed["tenant_id"], uid=booked["uid"]
        )
        assert record is not None
        assert record["contribution_date"] is None

    async def test_unlinking_leaves_a_stated_date_alone(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        stated = date.today() - timedelta(days=10)
        booked = await self._booked(service_session, seed, contribution_date=stated)
        await _credit(service_session, seed, amount="800.00", on=date.today())
        await matching.run(service_session, tenant_id=seed["tenant_id"])
        await matching.unlink(
            service_session,
            tenant_id=seed["tenant_id"],
            expectation_id=int(booked["expectation_id"]),
        )
        record = await contributions.get_contribution(
            service_session, tenant_id=seed["tenant_id"], uid=booked["uid"]
        )
        assert record is not None
        assert record["contribution_date"] == stated

    async def test_classifying_a_credit_dates_it_from_the_credit(
        self, service_session: AsyncSession
    ) -> None:
        """Path A already had the answer: the money is in front of you, so its date is known at
        the moment the contribution is created."""
        seed = await _seed(service_session)
        paid_on = date.today() - timedelta(days=5)
        transaction_id = await _credit(service_session, seed, amount="800.00", on=paid_on)
        created = await contributions.classify_transaction(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            transaction_id=transaction_id,
            category="personal_contribution",
        )
        assert created["contribution_date"] == paid_on


class TestMorganLloydStatus:
    """The mirror the Application-only update rule is gated on."""

    async def _recorded(self, session: AsyncSession, seed: dict[str, Any]) -> dict[str, Any]:
        booked = await contributions.create_contribution(
            session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=800,
            lodge=False,
        )
        return await contributions.mark_ml_recorded(
            session,
            tenant_id=seed["tenant_id"],
            uid=booked["uid"],
            ml_contribution_id=str(uuid.uuid4()),
        )

    async def test_recording_mirrors_application(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        record = await self._recorded(service_session, seed)
        assert record["ml_status"] == contributions.ML_STATUS_APPLICATION
        assert record["status"] == "ml_recorded"

    @pytest.mark.parametrize(
        ("ml_status", "stage"),
        [
            (contributions.ML_STATUS_APPLICATION, "ml_recorded"),
            (contributions.ML_STATUS_ACTIVE, "ras_applied"),
            (contributions.ML_STATUS_RECEIVED, "ras_received"),
            (contributions.ML_STATUS_STOPPED, "cancelled"),
        ],
    )
    async def test_each_ml_status_maps_to_its_stage(
        self, service_session: AsyncSession, ml_status: int, stage: str
    ) -> None:
        seed = await _seed(service_session)
        record = await self._recorded(service_session, seed)
        updated = await contributions.record_ml_status(
            service_session, tenant_id=seed["tenant_id"], uid=record["uid"], ml_status=ml_status
        )
        assert updated["ml_status"] == ml_status
        assert updated["status"] == stage

    async def test_a_stale_read_does_not_walk_the_pipeline_backwards(
        self, service_session: AsyncSession
    ) -> None:
        """A nightly refresh returning Application on a contribution already seen at Received
        must not undo real progress — but the mirror still records what ML actually said."""
        seed = await _seed(service_session)
        record = await self._recorded(service_session, seed)
        await contributions.record_ml_status(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=record["uid"],
            ml_status=contributions.ML_STATUS_RECEIVED,
        )
        stale = await contributions.record_ml_status(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=record["uid"],
            ml_status=contributions.ML_STATUS_APPLICATION,
        )
        assert stale["status"] == "ras_received"
        assert stale["ml_status"] == contributions.ML_STATUS_APPLICATION

    async def test_stopped_cancels_even_from_further_along(
        self, service_session: AsyncSession
    ) -> None:
        """The scheme administrator stopping a contribution is authoritative, not stale."""
        seed = await _seed(service_session)
        record = await self._recorded(service_session, seed)
        await contributions.record_ml_status(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=record["uid"],
            ml_status=contributions.ML_STATUS_RECEIVED,
        )
        stopped = await contributions.record_ml_status(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=record["uid"],
            ml_status=contributions.ML_STATUS_STOPPED,
        )
        assert stopped["status"] == "cancelled"
        assert stopped["cancelled_reason"] == "Stopped at Morgan Lloyd."

    async def test_an_unknown_status_is_refused(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        record = await self._recorded(service_session, seed)
        with pytest.raises(ValueError, match="Unknown Morgan Lloyd"):
            await contributions.record_ml_status(
                service_session, tenant_id=seed["tenant_id"], uid=record["uid"], ml_status=42
            )


class TestCancelling:
    async def test_cancelling_records_the_reason_and_kills_the_expectation(
        self, service_session: AsyncSession
    ) -> None:
        """An open expectation for money that is no longer coming would sit in the matcher's
        queue and in "awaiting payment" totals forever."""
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
        record = await contributions.cancel(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=booked["uid"],
            reason="Client changed their mind.",
        )
        assert record["status"] == "cancelled"
        assert record["cancelled_reason"] == "Client changed their mind."
        assert record["expectation_status"] == "cancelled"

    async def test_a_reason_is_required(self, service_session: AsyncSession) -> None:
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
        with pytest.raises(ValueError, match="why"):
            await contributions.cancel(
                service_session, tenant_id=seed["tenant_id"], uid=booked["uid"], reason="  "
            )

    async def test_cancelling_after_relief_is_claimed_is_refused(
        self, service_session: AsyncSession
    ) -> None:
        """Past ras_applied there is a reclaim in flight at HMRC; cancelling locally without
        reversing it leaves the two disagreeing."""
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
        await contributions.set_status(
            service_session, tenant_id=seed["tenant_id"], uid=booked["uid"], status="ras_applied"
        )
        with pytest.raises(contributions.ContributionConflict, match="relief reclaim"):
            await contributions.cancel(
                service_session,
                tenant_id=seed["tenant_id"],
                uid=booked["uid"],
                reason="Too late.",
            )

    async def test_set_status_will_not_cancel_without_a_reason(
        self, service_session: AsyncSession
    ) -> None:
        """The generic status endpoint has nowhere to carry a reason, and the DB CHECK
        requires one — so it refuses rather than producing a constraint violation."""
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
        with pytest.raises(ValueError, match="cancel action"):
            await contributions.set_status(
                service_session,
                tenant_id=seed["tenant_id"],
                uid=booked["uid"],
                status="cancelled",
            )


class TestReconciliation:
    """Derived on read — see matching.reconcile for why there is no breaks table."""

    def test_nothing_landed_yet_is_pending_not_a_break(self) -> None:
        position = matching.reconcile({"amount": 800, "expectation_amount": 800})
        assert position["status"] == "pending"

    def test_the_legs_agreeing_is_a_match(self) -> None:
        position = matching.reconcile(
            {"amount": 800, "expectation_amount": 800, "transaction_amount": 800}
        )
        assert position["status"] == "matched"
        assert position["variance"] == 0.0

    def test_a_short_credit_is_a_break_that_says_how_short(self) -> None:
        position = matching.reconcile(
            {"amount": 800, "expectation_amount": 800, "transaction_amount": 750}
        )
        assert position["status"] == "break"
        assert position["variance"] == -50.0
        assert "less than" in position["reason"]

    def test_relief_awaited_until_a_relief_credit_lands(self) -> None:
        assert matching.relief_position({"relief_amount": 200})["status"] == "awaited"

    def test_relief_received_in_full(self) -> None:
        position = matching.relief_position({"relief_amount": 200, "relief_received": 200})
        assert position["status"] == "received"
        assert position["outstanding"] == 0.0

    def test_an_employer_contribution_has_no_relief_position(self) -> None:
        """Employer contributions are paid gross — the employer takes the corporation-tax
        deduction — so "relief awaited" would be a permanently outstanding row."""
        assert matching.relief_position({"relief_amount": 0})["status"] == "not_applicable"

    async def test_a_relief_credit_is_summed_against_the_account(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        await _credit(service_session, seed, amount="200.00", description_code="21122")
        totals = await matching.relief_credits(
            service_session, tenant_id=seed["tenant_id"], account_ids=[seed["account_id"]]
        )
        assert totals[seed["account_id"]] == pytest.approx(200)

    async def test_the_contribution_credit_is_not_counted_as_relief(
        self, service_session: AsyncSession
    ) -> None:
        """Both arrive as SEI type 6 on the same account; only the description code separates
        them. Counting the contribution as relief would report the reclaim already received."""
        seed = await _seed(service_session)
        await _credit(service_session, seed, amount="800.00", description_code="21121")
        totals = await matching.relief_credits(
            service_session, tenant_id=seed["tenant_id"], account_ids=[seed["account_id"]]
        )
        assert totals == {}


class TestTransferStatusMirror:
    async def _transfer(self, session: AsyncSession, seed: dict[str, Any]) -> dict[str, Any]:
        return await transfers.create_transfer(
            session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="cash",
            extent="full",
            total_value=25000,
        )

    @pytest.mark.parametrize(
        ("ml_status", "stage"),
        [
            (transfers.ML_AWAITING_FORMS, "submitted"),
            (transfers.ML_TO_REQUEST_TRANSFER, "submitted"),
            (transfers.ML_AWAITING_CEDING_SCHEME, "in_progress"),
            (transfers.ML_FUNDS_RECEIVED, "assets_received"),
            (transfers.ML_FUNDS_SENT, "funds_sent"),
            (transfers.ML_NO_LONGER_TRANSFERRING, "cancelled"),
        ],
    )
    async def test_each_ml_transfer_status_maps_to_its_stage(
        self, service_session: AsyncSession, ml_status: int, stage: str
    ) -> None:
        seed = await _seed(service_session)
        record = await self._transfer(service_session, seed)
        updated = await transfers.record_ml_status(
            service_session, tenant_id=seed["tenant_id"], uid=record["uid"], ml_status=ml_status
        )
        assert updated["status"] == stage
        assert updated["ml_status_label"] == transfers.ML_STATUS_LABELS[ml_status]

    async def test_completed_is_never_set_by_morgan_lloyd(self) -> None:
        """ML's last forward state is "Funds sent". A transfer is complete only once the money
        is matched at the custodian, which is our reconciliation and not ML's fact."""
        assert "completed" not in transfers.ML_STATUS_TO_STAGE.values()

    async def test_the_ceding_status_is_stored_verbatim(
        self, service_session: AsyncSession
    ) -> None:
        """Seccl documents a transferStatus enum in its Postman collection (With Previous
        Provider / Onboarding Assets / Completed / Rejected / Deleted). We still store it
        verbatim — mapping it onto this saga would invent a crosswalk the rest of the lifecycle
        does not run on."""
        seed = await _seed(service_session)
        record = await self._transfer(service_session, seed)
        updated = await transfers.record_ml_status(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=record["uid"],
            ml_status=transfers.ML_AWAITING_CEDING_SCHEME,
            ceding_status="With previous provider",
        )
        assert updated["ceding_status"] == "With previous provider"


class TestDetectedTransfers:
    """Transfers the custodian reported, which `platform.transfers` never held.

    The bug these exist for: the query required `amount > 0`, and a Seccl transfer-in carries its
    worth in `transfer_value` while `amount` is 0.00 — so the filter excluded exactly the rows it
    was written to find, and the workbench looked empty against a feed full of transfers.
    """

    async def _transfer_credit(
        self,
        session: AsyncSession,
        seed: dict[str, Any],
        *,
        amount: str = "0.00",
        transfer_value: str | None = "25000.00",
        source_system: str = "seccl",
        source_type: str = "Product Transfer",
        source_subtype: str | None = None,
        policy_number: str | None = None,
        status: str | None = None,
        transfer_status: str | None = None,
        link_id: str | None = None,
        isin: str | None = None,
        asset_name: str | None = None,
        quantity: str | None = None,
        related_activity_id: str | None = None,
        book_value: str | None = None,
        originator: str | None = None,
    ) -> int:
        payload: dict[str, str] = {}
        if transfer_value is not None:
            payload["transfer_value"] = transfer_value
        if policy_number is not None:
            payload["previous_provider_ref"] = policy_number
        if transfer_status is not None:
            payload["transfer_status"] = transfer_status
        if link_id is not None:
            payload["link_id"] = link_id
        if related_activity_id is not None:
            payload["RELATED_ACTIVITY_ID"] = related_activity_id
        if book_value is not None:
            payload["local_book_value"] = book_value
        if originator is not None:
            payload["ORIGINATOR"] = originator
        data = json.dumps(payload)
        return int(
            (
                await session.execute(
                    text(
                        "INSERT INTO platform.transactions "
                        "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                        "   source_type, source_subtype, amount, currency, transaction_date, "
                        "   status, isin, asset_name, quantity, data) "
                        "VALUES (:t, :a, :src, :sid, 'transfer_in', :st, :sub, "
                        "        :amt, 'GBP', CURRENT_DATE, :status, :isin, :name, :qty, "
                        "        CAST(:data AS jsonb)) "
                        "RETURNING id"
                    ),
                    {
                        "t": seed["tenant_id"],
                        "a": seed["account_id"],
                        "src": source_system,
                        "st": source_type,
                        "sub": source_subtype,
                        "sid": f"txn_{uuid.uuid4().hex[:12]}",
                        "amt": amount,
                        "status": status,
                        "isin": isin,
                        "name": asset_name,
                        "qty": quantity,
                        "data": data,
                    },
                )
            ).scalar_one()
        )

    async def test_a_zero_cash_transfer_is_still_found(self, service_session: AsyncSession) -> None:
        """An in-specie transfer settles as assets, so its cash amount is legitimately zero."""
        seed = await _seed(service_session)
        await self._transfer_credit(service_session, seed)
        rows = await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"])
        assert len(rows) == 1
        assert rows[0]["total_value"] == pytest.approx(25000)

    async def test_the_transfer_value_is_the_worth_not_the_cash(
        self, service_session: AsyncSession
    ) -> None:
        """`total_value` is the transfer's worth. An open row has not been received, so that
        worth is not copied into `transaction_amount`."""
        seed = await _seed(service_session)
        await self._transfer_credit(service_session, seed, amount="0.00")
        row = (await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"]))[0]
        assert row["total_value"] == pytest.approx(25000)
        assert row["transaction_amount"] is None
        assert row["detected"] is True
        # Nothing to reconcile: there was never a forecast to compare the credit against.
        assert row["reconciliation"]["status"] == "pending"

    async def test_a_completed_transfer_reports_its_value_as_received(
        self, service_session: AsyncSession
    ) -> None:
        """Seccl has no second amount. Once the transaction has completed, the transfer value
        is what the custodian booked."""
        seed = await _seed(service_session)
        await self._transfer_credit(
            service_session,
            seed,
            status="completed",
            transfer_status="Completed",
        )
        row = (await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"]))[0]
        assert row["status"] == "funds_sent"
        assert row["transaction_amount"] == pytest.approx(25000)

    async def test_a_cancellation_request_is_visible_before_the_next_sync(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        transaction_id = await self._transfer_credit(
            service_session,
            seed,
            status="pending",
            transfer_status="With Previous Provider",
        )
        row = (await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"]))[0]
        assert row["status"] == "submitted"
        assert row["transaction_amount"] is None
        await transfers.stamp_feed_transfer_status(
            service_session,
            tenant_id=seed["tenant_id"],
            transaction_id=transaction_id,
            status="Pending Cancellation",
        )
        updated = (await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"]))[
            0
        ]
        assert updated["ceding_status"] == "Pending Cancellation"
        assert updated["status"] == "submitted"

    async def test_an_in_specie_transfer_lists_the_stock_lines_on_its_link(
        self, service_session: AsyncSession
    ) -> None:
        """Seccl's product transfer is the instruction. Stock lines on its link are
        holdings, not further transfers, and the queue names the ceding policy."""
        seed = await _seed(service_session)
        await self._transfer_credit(
            service_session,
            seed,
            source_subtype="In Specie",
            link_id="LINK1",
            policy_number="16121775",
            status="completed",
            transfer_status="Completed",
        )
        for isin, name in (
            ("GB00BJS8SF95", "Fidelity Index UK Fund P Acc"),
            ("GB00BJS8SH10", "Fidelity Index US Fund P Acc"),
        ):
            await self._transfer_credit(
                service_session,
                seed,
                source_type="Stock Transfer",
                source_subtype="Migration",
                link_id="LINK1",
                transfer_value=None,
                status="completed",
                isin=isin,
                asset_name=name,
                quantity="10",
            )
        rows = await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"])
        assert len(rows) == 1
        product = rows[0]
        assert product["policy_number"] == "16121775"
        assert product["total_value"] == pytest.approx(25000)
        assert [line["code"] for line in product["instruments"]] == [
            "GB00BJS8SF95",
            "GB00BJS8SH10",
        ]
        assert product["instruments"][0]["status"] == "completed"

    async def test_stock_lines_with_no_product_transfer_collapse_by_link(
        self, service_session: AsyncSession
    ) -> None:
        """A migration that never published a product transfer is still one transfer."""
        seed = await _seed(service_session)
        await self._transfer_credit(
            service_session,
            seed,
            source_type="Stock Transfer",
            source_subtype="Migration",
            link_id="ORPHAN",
            policy_number="POL-9",
            transfer_value=None,
            isin="GB00AAAA",
            asset_name="Fund A",
            quantity="1",
        )
        await self._transfer_credit(
            service_session,
            seed,
            source_type="Stock Transfer",
            source_subtype="Migration",
            link_id="ORPHAN",
            transfer_value=None,
            isin="GB00BBBB",
            asset_name="Fund B",
            quantity="2",
        )
        await self._transfer_credit(
            service_session,
            seed,
            source_type="Stock Transfer",
            source_subtype="Migration",
            transfer_value=None,
            isin="GB00CCCC",
            asset_name="Unlinked",
            quantity="3",
        )
        rows = await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"])
        assert len(rows) == 2
        grouped = next(row for row in rows if row["policy_number"] == "POL-9")
        assert [line["code"] for line in grouped["instruments"]] == ["GB00AAAA", "GB00BBBB"]
        alone = next(row for row in rows if row is not grouped)
        assert [line["code"] for line in alone["instruments"]] == ["GB00CCCC"]

    async def test_a_cash_transfer_is_not_grouped_with_stock_lines_on_its_link(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        await self._transfer_credit(
            service_session,
            seed,
            source_subtype="Cash",
            link_id="BOTH",
            amount="18000.00",
            transfer_value=None,
        )
        for isin in ("GB00AAAA", "GB00BBBB"):
            await self._transfer_credit(
                service_session,
                seed,
                source_type="Stock Transfer",
                source_subtype="Migration",
                link_id="BOTH",
                transfer_value=None,
                isin=isin,
                asset_name=isin,
                quantity="1",
            )
        rows = await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"])
        assert len(rows) == 2
        cash = next(row for row in rows if row["basis"] == "cash")
        assert cash["instruments"] == []
        assets = next(row for row in rows if row["basis"] == "in_specie")
        assert [line["code"] for line in assets["instruments"]] == ["GB00AAAA", "GB00BBBB"]

    async def test_sei_free_receipts_group_on_related_activity(
        self, service_session: AsyncSession
    ) -> None:
        """TransactionHeader carries RELATED_ACTIVITY_ID. The transaction API does not
        return these movements, so the file is the group."""
        seed = await _seed(service_session)
        for isin, activity in (
            ("GB00AAAA", "ACT-1"),
            ("GB00BBBB", "ACT-1"),
            ("GB00CCCC", "ACT-2"),
        ):
            await self._transfer_credit(
                service_session,
                seed,
                source_system="sei",
                source_type="Free Receipt",
                transfer_value=None,
                amount="0.00",
                isin=isin,
                asset_name=isin,
                quantity="4",
                related_activity_id=activity,
            )
        await self._transfer_credit(
            service_session,
            seed,
            source_system="sei",
            source_type="Free Receipt",
            transfer_value=None,
            isin="GB00DDDD",
            asset_name="Ungrouped",
            quantity="1",
        )
        rows = await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"])
        assert len(rows) == 3
        grouped = next(row for row in rows if len(row["instruments"]) == 2)
        assert [line["code"] for line in grouped["instruments"]] == ["GB00AAAA", "GB00BBBB"]
        assert {row["instruments"][0]["code"] for row in rows if len(row["instruments"]) == 1} == {
            "GB00CCCC",
            "GB00DDDD",
        }

    async def test_a_posted_sei_free_receipt_is_completed_at_its_book_value(
        self, service_session: AsyncSession
    ) -> None:
        """The ledger shows these as settled. The workbench was calling the same blank
        status Submitted, and the cash amount is 0 because the cost is the book value.
        Two receipts on one activity are one transfer, worth the sum."""
        seed = await _seed(service_session)
        await self._transfer_credit(
            service_session,
            seed,
            source_system="sei",
            source_type="Free Receipt",
            transfer_value=None,
            amount="0.00",
            book_value="100.00",
            originator="Multrees",
            related_activity_id="ACT-9",
            isin="GB00AAAA",
            asset_name="AAAA",
        )
        await self._transfer_credit(
            service_session,
            seed,
            source_system="sei",
            source_type="Free Receipt",
            transfer_value=None,
            amount="0.00",
            book_value="250.50",
            related_activity_id="ACT-9",
            isin="GB00BBBB",
            asset_name="BBBB",
        )
        rows = await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"])
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "funds_sent"
        assert row["total_value"] == pytest.approx(350.50)
        assert row["transaction_amount"] == pytest.approx(350.50)
        assert row["ceding_provider"] == "Multrees"
        assert row["basis"] == "in_specie"

    async def test_a_cash_transfer_uses_its_amount(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await self._transfer_credit(service_session, seed, amount="18000.00", transfer_value=None)
        row = (await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"]))[0]
        assert row["total_value"] == pytest.approx(18000)

    @pytest.mark.parametrize(
        ("source_system", "source_type", "source_subtype", "expected"),
        [
            # A Product Transfer with no subtype stays in-specie: that is the securities reading
            # of the type. The subtype is what splits the mixture Seccl actually sends.
            ("seccl", "Product Transfer", None, "in_specie"),
            ("seccl", "Product Transfer", "Cash", "cash"),
            ("seccl", "Product Transfer", "In Specie", "in_specie"),
            ("seccl", "Stock Transfer", "Migration", "in_specie"),
            ("seccl", "Cash Transfer", None, "cash"),
            ("sei", "Free Receipt", None, "in_specie"),
            ("sei", "Security Transfer -Transfer To", None, "in_specie"),
            ("sei", "Currency Transfer To", None, "cash"),
            ("sei", "Intra Account Cash Transfer To", None, "cash"),
            # SEI's own reference has no unambiguous cash/security equivalent for this one —
            # it must not be guessed, so it falls through to the 'cash' default.
            ("sei", "Complex Trade Transfer To", None, "cash"),
        ],
    )
    async def test_transfer_type_is_read_per_custodian_vocabulary(
        self,
        service_session: AsyncSession,
        source_system: str,
        source_type: str,
        source_subtype: str | None,
        expected: str,
    ) -> None:
        """Each custodian names a security/stock movement in its own words (see
        `_IN_SPECIE_SOURCE_TYPES`). Seccl's Product Transfer is Cash or In Specie on the
        subtype; reading only the type marked every one in-specie."""
        seed = await _seed(service_session)
        await self._transfer_credit(
            service_session,
            seed,
            source_system=source_system,
            source_type=source_type,
            source_subtype=source_subtype,
        )
        row = (await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"]))[0]
        assert row["transfer_type"] == expected
        assert row["basis"] == expected

    async def test_a_seccl_product_transfer_keeps_its_policy_reference(
        self, service_session: AsyncSession
    ) -> None:
        """Seccl stores the ceding scheme's reference as previousProviderRef. The feed lands
        it as previous_provider_ref; dropping it left every detected policy as a dash."""
        seed = await _seed(service_session)
        await self._transfer_credit(service_session, seed, policy_number="AV-88213771")
        row = (await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"]))[0]
        assert row["policy_number"] == "AV-88213771"

    async def test_a_booked_transfer_is_not_also_detected(
        self, service_session: AsyncSession
    ) -> None:
        """Otherwise raising a transfer and then matching its credit shows it twice."""
        seed = await _seed(service_session)
        transaction_id = await self._transfer_credit(service_session, seed)
        raised = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="cash",
            extent="full",
            total_value=25000,
        )
        await matching.link(
            service_session,
            tenant_id=seed["tenant_id"],
            expectation_id=int(raised["expectation_id"]),
            transaction_id=transaction_id,
        )
        assert await matching.detected_transfers(service_session, tenant_id=seed["tenant_id"]) == []


class TestADisposalClosesItsForecast:
    """`cancel()` closes the open expectation and says why: "an open expectation for money that is
    no longer coming keeps showing in the matcher's queue and in 'awaiting payment' totals
    forever." The disposal branch of `categorise` cancels the contribution INLINE rather than
    through `cancel()`, and it took the row and left the forecast — so classifying a credit as
    not-a-contribution retired it from the queue and kept it in the totals.
    """

    async def test_the_expectation_is_cancelled_with_the_contribution(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        booked = await contributions.create_contribution(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            category="personal_contribution",
            amount=500,
            due_date=date.today(),
            lodge=False,
        )
        assert booked["expectation_id"] is not None

        await contributions.categorise(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=str(booked["uid"]),
            category="not_a_contribution",
        )

        status = (
            await service_session.execute(
                text("SELECT status FROM platform.transaction_expectations WHERE id = :e"),
                {"e": booked["expectation_id"]},
            )
        ).scalar_one()
        assert status == "cancelled"


class TestRaisedTransferInstruction:
    async def _seccl_transfer(self, session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
        seed = await _seed(session)
        await session.execute(
            text(
                "INSERT INTO platform.links "
                "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                "VALUES (:t, 'account', :a, 'seccl', 'primary', :ext)"
            ),
            {
                "t": seed["tenant_id"],
                "a": seed["account_id"],
                "ext": f"seccl_{uuid.uuid4().hex[:8]}",
            },
        )
        record = await transfers.create_transfer(
            session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="cash",
            extent="full",
            total_value=25000,
            provider_address_line1="1 High Street",
            provider_postcode="SW1A 1AA",
            provider_country="GB",
        )
        return seed, record

    async def test_a_failed_provider_call_keeps_the_transfer_for_retry(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.integrations.seccl.transfers import SecclUnavailable

        seed, record = await self._seccl_transfer(service_session)
        assert record["route"] == "seccl"
        assert record["status"] == "requested"

        async def refused(_context: dict[str, Any]) -> tuple[str, str]:
            raise SecclUnavailable("Seccl could not be reached.")

        monkeypatch.setattr("app.integrations.seccl.transfers.place_transfer", refused)
        updated = await transfers.instruct_raised(
            service_session, tenant_id=seed["tenant_id"], record=record
        )
        assert updated["status"] == "requested"
        assert updated["seccl_transfer_id"] is None
        assert "could not be reached" in (updated["provider_error"] or "")

    async def test_the_seccl_action_places_the_named_transfer(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.integrations.seccl.actions import seccl_transfer

        seed, record = await self._seccl_transfer(service_session)

        async def placed(_context: dict[str, Any]) -> tuple[str, str]:
            return "00009BN1G", "With Previous Provider"

        monkeypatch.setattr("app.integrations.seccl.transfers.place_transfer", placed)
        attempt = await seccl_transfer(
            service_session,
            payload={
                "tenant_id": seed["tenant_id"],
                "account_id": seed["account_id"],
                "uid": record["uid"],
            },
            subjects=[],
        )
        assert attempt.ok is True
        assert attempt.skipped is False
        shown = await transfers.get_transfer(
            service_session, tenant_id=seed["tenant_id"], uid=record["uid"]
        )
        assert shown is not None
        assert shown["seccl_transfer_id"] == "00009BN1G"

    async def test_a_successful_place_submits_the_transfer(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed, record = await self._seccl_transfer(service_session)

        async def placed(_context: dict[str, Any]) -> tuple[str, str]:
            return "00009BN1G", "With Previous Provider"

        monkeypatch.setattr("app.integrations.seccl.transfers.place_transfer", placed)
        updated = await transfers.instruct_raised(
            service_session, tenant_id=seed["tenant_id"], record=record
        )
        assert updated["status"] == "submitted"
        assert updated["seccl_transfer_id"] == "00009BN1G"
        assert updated["provider_error"] is None

    async def test_a_successful_instruction_clears_the_earlier_refusal(
        self, service_session: AsyncSession
    ) -> None:
        seed, record = await self._seccl_transfer(service_session)
        await transfers.remember_provider_failure(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=record["uid"],
            message="Seccl could not accept this request.",
        )
        updated = await transfers.mark_submitted(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=record["uid"],
            seccl_transfer_id="00009BN1G",
        )
        assert updated["status"] == "submitted"
        assert updated["provider_error"] is None

    async def test_an_sei_accept_without_an_activity_id_is_held(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed = await _seed(service_session, product="GIA")
        await service_session.execute(
            text("UPDATE platform.accounts SET custodian = 'sei' WHERE id = :a"),
            {"a": seed["account_id"]},
        )
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="in_specie",
            extent="full",
            total_value=1000,
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

        async def accepted(_context: dict[str, Any]) -> dict[str, Any]:
            return {"activity_id": None, "body": {}}

        monkeypatch.setattr("app.integrations.sei.transfers.submit_transfer_in", accepted)
        held = await transfers.instruct_raised(
            service_session, tenant_id=seed["tenant_id"], record=record
        )
        assert held["status"] == "manual_intervention"
        assert held["sei_accepted"] is True
        assert held["sei_activity_id"] is None
        assert "activity id" in (held["provider_error"] or "")

    async def test_a_morgan_lloyd_refresh_keeps_the_sei_hold_reason(
        self, service_session: AsyncSession
    ) -> None:
        seed, record = await self._seccl_transfer(service_session)
        held = await transfers.hold_unreadable_sei(
            service_session, tenant_id=seed["tenant_id"], uid=record["uid"], freeze_status=True
        )
        refreshed = await transfers.record_ml_status(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=record["uid"],
            ml_status=transfers.ML_AWAITING_FORMS,
        )
        assert refreshed["sei_accepted"] is True
        assert refreshed["provider_error"] == held["provider_error"]

    async def test_a_synced_transaction_advances_the_raised_transfer(
        self, service_session: AsyncSession
    ) -> None:
        seed, record = await self._seccl_transfer(service_session)
        source_id = f"seccl_{uuid.uuid4().hex[:12]}"
        submitted = await transfers.mark_submitted(
            service_session,
            tenant_id=seed["tenant_id"],
            uid=record["uid"],
            seccl_transfer_id=source_id,
            ceding_status="With Previous Provider",
        )
        assert submitted["status"] == "submitted"
        await service_session.execute(
            text(
                "INSERT INTO platform.transactions "
                "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                "   source_type, amount, currency, transaction_date, status, data) "
                "VALUES (:t, :a, 'seccl', :sid, 'transfer_in', 'Product Transfer', "
                "        25000, 'GBP', :on, 'completed', "
                "        CAST(:data AS jsonb))"
            ),
            {
                "t": seed["tenant_id"],
                "a": seed["account_id"],
                "sid": source_id,
                "on": date.today(),
                "data": json.dumps({"transfer_status": "Completed"}),
            },
        )
        await transfers.apply_synced_progress(service_session, tenant_id=seed["tenant_id"])
        advanced = await transfers.get_transfer(
            service_session, tenant_id=seed["tenant_id"], uid=record["uid"]
        )
        assert advanced is not None
        assert advanced["status"] == "funds_sent"
        assert advanced["ceding_status"] == "Completed"

    async def test_a_second_instruction_does_not_place_again(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed, record = await self._seccl_transfer(service_session)
        calls = {"n": 0}

        async def placed(_context: dict[str, Any]) -> tuple[str, str]:
            calls["n"] += 1
            return "00009BN1G", "With Previous Provider"

        monkeypatch.setattr("app.integrations.seccl.transfers.place_transfer", placed)
        first = await transfers.instruct_raised(
            service_session, tenant_id=seed["tenant_id"], record=record
        )
        second = await transfers.instruct_raised(
            service_session, tenant_id=seed["tenant_id"], record=first
        )
        assert calls["n"] == 1
        assert second["status"] == "submitted"
        assert second["seccl_transfer_id"] == "00009BN1G"

    async def test_sei_transfer_action_submits_the_row_the_workbench_shows(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.integrations.sei.actions import sei_transfer

        seed = await _seed(service_session, product="GIA")
        await service_session.execute(
            text("UPDATE platform.accounts SET custodian = 'sei' WHERE id = :a"),
            {"a": seed["account_id"]},
        )
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="in_specie",
            extent="full",
            total_value=1000,
            external_account_type="Individual",
            documents=[{"document_reference_id": "DOC1"}],
        )

        async def accepted(_context: dict[str, Any]) -> dict[str, Any]:
            return {"activity_id": "ACT-9"}

        monkeypatch.setattr("app.integrations.sei.transfers.submit_transfer_in", accepted)
        attempt = await sei_transfer(
            service_session,
            payload={"tenant_id": seed["tenant_id"], "account_id": seed["account_id"]},
            subjects=[],
        )
        assert attempt.ok is True
        assert attempt.skipped is False
        shown = await transfers.get_transfer(
            service_session, tenant_id=seed["tenant_id"], uid=record["uid"]
        )
        assert shown is not None
        assert shown["status"] == "submitted"
        assert shown["sei_activity_id"] == "ACT-9"
        assert shown["provider_error"] is None

        again = await sei_transfer(
            service_session,
            payload={"tenant_id": seed["tenant_id"], "account_id": seed["account_id"]},
            subjects=[],
        )
        assert again.ok is True
        assert again.skipped is True

    async def test_a_sipp_transfer_action_waits_for_morgan_lloyd(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.integrations.sei.actions import sei_transfer

        seed = await _seed(service_session, product="SIPP")
        product_id = (
            await service_session.execute(
                text("SELECT product_id FROM platform.accounts WHERE id = :a"),
                {"a": seed["account_id"]},
            )
        ).scalar_one()
        await service_session.execute(
            text("UPDATE platform.products SET is_pension = true WHERE id = :p"),
            {"p": product_id},
        )
        await service_session.execute(
            text("UPDATE platform.accounts SET custodian = 'sei' WHERE id = :a"),
            {"a": seed["account_id"]},
        )
        await service_session.execute(
            text(
                "INSERT INTO platform.product_platforms "
                "  (tenant_id, product_id, provider, role) "
                "VALUES (:t, :p, 'morgan-lloyd', 'scheme_administrator')"
            ),
            {"t": seed["tenant_id"], "p": product_id},
        )
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="cash",
            extent="full",
            total_value=1000,
            crystallisation="uncrystallised",
            pension_type=18,
        )
        assert record["route"] == "ml_sei"

        async def refused(_context: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("SEI must not be instructed before Morgan Lloyd")

        monkeypatch.setattr("app.integrations.sei.transfers.submit_transfer_in", refused)
        monkeypatch.setattr("app.transfers.lodge_transfer_cash", refused)
        attempt = await sei_transfer(
            service_session,
            payload={"tenant_id": seed["tenant_id"], "account_id": seed["account_id"]},
            subjects=[],
        )
        assert attempt.ok is True
        assert attempt.skipped is True
        shown = await transfers.get_transfer(
            service_session, tenant_id=seed["tenant_id"], uid=record["uid"]
        )
        assert shown is not None
        assert shown["status"] == "requested"
        assert shown["sei_activity_id"] is None

    async def test_a_refused_sei_transfer_stays_raised_on_the_workbench(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.integrations.sei.actions import sei_transfer
        from app.integrations.sei.cash_receipts import SeiUnavailable

        seed = await _seed(service_session, product="GIA")
        await service_session.execute(
            text("UPDATE platform.accounts SET custodian = 'sei' WHERE id = :a"),
            {"a": seed["account_id"]},
        )
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="in_specie",
            extent="full",
            total_value=1000,
            external_account_type="Individual",
            documents=[{"document_reference_id": "DOC1"}],
        )

        async def refused(_context: dict[str, Any]) -> dict[str, Any]:
            raise SeiUnavailable("SEI could not be reached.")

        monkeypatch.setattr("app.integrations.sei.transfers.submit_transfer_in", refused)
        attempt = await sei_transfer(
            service_session,
            payload={"tenant_id": seed["tenant_id"], "account_id": seed["account_id"]},
            subjects=[],
        )
        assert attempt.ok is False
        assert "could not be reached" in (attempt.error or "")
        shown = await transfers.get_transfer(
            service_session, tenant_id=seed["tenant_id"], uid=record["uid"]
        )
        assert shown is not None
        assert shown["status"] == "requested"
        assert "could not be reached" in (shown["provider_error"] or "")

    async def test_sei_cash_is_an_anticipated_receipt(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.integrations.sei.actions import _TRANSFER_CASH_URL, sei_transfer

        seed = await _seed(service_session, product="GIA")
        await service_session.execute(
            text("UPDATE platform.accounts SET custodian = 'sei' WHERE id = :a"),
            {"a": seed["account_id"]},
        )
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="cash",
            extent="full",
            total_value=1000,
        )
        calls = {"receipt": 0}

        async def receipt(session: AsyncSession, *, tenant_id: int, transfer: dict[str, Any]):
            calls["receipt"] += 1
            return await transfers.mark_submitted(
                session, tenant_id=tenant_id, uid=str(transfer["uid"]), sei_activity_id="REC-1"
            )

        async def transfer_in(_context: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("cash is an anticipated receipt, not a transfer-in")

        monkeypatch.setattr("app.transfers.lodge_transfer_cash", receipt)
        monkeypatch.setattr("app.integrations.sei.transfers.submit_transfer_in", transfer_in)
        attempt = await sei_transfer(
            service_session,
            payload={"tenant_id": seed["tenant_id"], "account_id": seed["account_id"]},
            subjects=[],
        )
        assert calls["receipt"] == 1
        assert attempt.ok is True
        assert attempt.url == _TRANSFER_CASH_URL
        shown = await transfers.get_transfer(
            service_session, tenant_id=seed["tenant_id"], uid=record["uid"]
        )
        assert shown is not None
        assert shown["sei_activity_id"] == "REC-1"

    async def test_a_completed_sipp_in_specie_transfer_is_a_free_receipt(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.integrations.sei.actions import _FREE_RECEIPT_URL, sei_transfer

        seed = await _seed(service_session, product="SIPP")
        product_id = (
            await service_session.execute(
                text("SELECT product_id FROM platform.accounts WHERE id = :a"),
                {"a": seed["account_id"]},
            )
        ).scalar_one()
        await service_session.execute(
            text("UPDATE platform.products SET is_pension = true WHERE id = :p"),
            {"p": product_id},
        )
        await service_session.execute(
            text("UPDATE platform.accounts SET custodian = 'sei' WHERE id = :a"),
            {"a": seed["account_id"]},
        )
        await service_session.execute(
            text(
                "INSERT INTO platform.product_platforms "
                "  (tenant_id, product_id, provider, role) "
                "VALUES (:t, :p, 'morgan-lloyd', 'scheme_administrator')"
            ),
            {"t": seed["tenant_id"], "p": product_id},
        )
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="in_specie",
            extent="full",
            total_value=1000,
            crystallisation="uncrystallised",
            pension_type=18,
            external_account_type="Individual",
            assets=[
                {
                    "identifier": "GB00B0000001",
                    "identifier_type": "ISIN",
                    "issue_country": "GB",
                    "units": 10,
                }
            ],
        )
        assert record["route"] == "ml_sei"
        await service_session.execute(
            text("UPDATE platform.transfers SET status = 'funds_sent' WHERE uid = :u"),
            {"u": record["uid"]},
        )
        calls = {"free": 0}

        async def free(_context: dict[str, Any]) -> dict[str, Any]:
            calls["free"] += 1
            return {"activity_id": "FR-1"}

        async def transfer_in(_context: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("Morgan Lloyd already asked the ceding scheme")

        monkeypatch.setattr("app.integrations.sei.transfers.register_free_receipt", free)
        monkeypatch.setattr("app.integrations.sei.transfers.submit_transfer_in", transfer_in)
        attempt = await sei_transfer(
            service_session,
            payload={
                "tenant_id": seed["tenant_id"],
                "account_id": seed["account_id"],
                "uid": record["uid"],
            },
            subjects=[],
        )
        assert calls["free"] == 1
        assert attempt.ok is True
        assert attempt.skipped is False
        assert attempt.url == _FREE_RECEIPT_URL
        shown = await transfers.get_transfer(
            service_session, tenant_id=seed["tenant_id"], uid=record["uid"]
        )
        assert shown is not None
        assert shown["status"] == "funds_sent"
        assert shown["sei_activity_id"] == "FR-1"

    async def test_raising_a_transfer_runs_the_action(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.auth import Principal
        from app.db import FirmContext
        from app.permissions import TRANSFERS_WRITE
        from app.routers.contributions import CreateTransfer, create_transfer

        seed = await _seed(service_session, product="GIA")
        await service_session.execute(
            text("UPDATE platform.accounts SET custodian = 'sei' WHERE id = :a"),
            {"a": seed["account_id"]},
        )
        seen: dict[str, Any] = {}

        async def lodged(_session: AsyncSession, **kwargs: Any) -> None:
            seen.update(kwargs)
            return None

        async def outside(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("the door must not instruct the provider itself")

        monkeypatch.setattr("app.actions.money.lodge_transfer", lodged)
        monkeypatch.setattr("app.transfers.instruct_raised", outside)
        shown = await create_transfer(
            CreateTransfer(
                account_id=seed["account_id"],
                ceding_provider="Aviva",
                basis="cash",
                extent="full",
                total_value=1000,
            ),
            FirmContext(
                session=service_session,
                tenant_id=seed["tenant_id"],
                principal=Principal(user_id="adviser", permissions=[TRANSFERS_WRITE.slug]),
            ),
        )
        assert seen["leg"] == "raise"
        assert seen["record"]["uid"] == shown["uid"]
        assert shown["status"] == "requested"

    async def test_recording_a_sipp_to_sei_is_the_later_leg(
        self, service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.auth import Principal
        from app.db import FirmContext
        from app.permissions import TRANSFERS_WRITE
        from app.routers.contributions import record_transfer_to_sei

        seed = await _seed(service_session, product="SIPP")
        product_id = (
            await service_session.execute(
                text("SELECT product_id FROM platform.accounts WHERE id = :a"),
                {"a": seed["account_id"]},
            )
        ).scalar_one()
        await service_session.execute(
            text("UPDATE platform.products SET is_pension = true WHERE id = :p"),
            {"p": product_id},
        )
        await service_session.execute(
            text("UPDATE platform.accounts SET custodian = 'sei' WHERE id = :a"),
            {"a": seed["account_id"]},
        )
        await service_session.execute(
            text(
                "INSERT INTO platform.product_platforms "
                "  (tenant_id, product_id, provider, role) "
                "VALUES (:t, :p, 'morgan-lloyd', 'scheme_administrator')"
            ),
            {"t": seed["tenant_id"], "p": product_id},
        )
        record = await transfers.create_transfer(
            service_session,
            tenant_id=seed["tenant_id"],
            created_by=None,
            account_id=seed["account_id"],
            ceding_provider="Aviva",
            basis="cash",
            extent="full",
            total_value=1000,
            crystallisation="uncrystallised",
            pension_type=18,
        )
        await service_session.execute(
            text("UPDATE platform.transfers SET status = 'funds_sent' WHERE uid = :u"),
            {"u": record["uid"]},
        )
        seen: dict[str, Any] = {}

        async def lodged(_session: AsyncSession, **kwargs: Any) -> None:
            seen.update(kwargs)
            return None

        monkeypatch.setattr("app.actions.money.lodge_transfer", lodged)
        await record_transfer_to_sei(
            record["uid"],
            FirmContext(
                session=service_session,
                tenant_id=seed["tenant_id"],
                principal=Principal(user_id="adviser", permissions=[TRANSFERS_WRITE.slug]),
            ),
        )
        assert seen["leg"] == "sei"
        assert seen["record"]["uid"] == record["uid"]
