"""Beneficiary nominations (0127) — the share arithmetic and the pension gate.

Against real Postgres, because the two rules worth testing are both enforced there or against it:
the composite (tenant_id, link) foreign keys that keep a nomination inside its own firm, and the
running total that a set is checked against.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import beneficiaries
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
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, :uid, 'individual', 'Sophie', 'Halloway') RETURNING id"
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


async def _add(session: AsyncSession, seed: dict[str, Any], **over: Any) -> dict[str, Any]:
    return await beneficiaries.add(
        session,
        tenant_id=seed["tenant_id"],
        account_id=seed["account_id"],
        created_by=None,
        **{"name": "Jamie Halloway", "proportion": 50, **over},
    )


class TestTheShareArithmetic:
    async def test_a_set_may_be_built_a_row_at_a_time(self, service_session: AsyncSession) -> None:
        """UNDER 100% is not refused, and that asymmetry is the whole rule.

        Demanding the set total 100 would make the FIRST insert fail — a 50% beneficiary added to
        an empty account, refused for totalling 50. Over 100 is unambiguously wrong at every point
        in the edit; under 100 is only wrong once the firm says it has finished, which is a
        judgement the write path cannot make.
        """
        seed = await _seed(service_session)
        first = await _add(service_session, seed, proportion=50)
        assert first["proportion"] == 50.0
        await _add(service_session, seed, name="Alex Halloway", proportion=25)
        rows = await beneficiaries.list_for_account(service_session, account_id=seed["account_id"])
        assert [r["proportion"] for r in rows] == [50.0, 25.0]

    async def test_over_a_hundred_is_refused_and_names_the_headroom(
        self, service_session: AsyncSession
    ) -> None:
        # "That is too much" without the number is a message an adviser has to do arithmetic to
        # act on.
        seed = await _seed(service_session)
        await _add(service_session, seed, proportion=70)
        with pytest.raises(beneficiaries.BeneficiaryConflict, match="30"):
            await _add(service_session, seed, name="Alex", proportion=40)

    async def test_exactly_a_hundred_is_allowed(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        await _add(service_session, seed, proportion=60)
        await _add(service_session, seed, name="Alex", proportion=40)
        rows = await beneficiaries.list_for_account(service_session, account_id=seed["account_id"])
        assert sum(r["proportion"] for r in rows) == 100.0

    async def test_a_three_way_split_keeps_its_thirds(self, service_session: AsyncSession) -> None:
        # numeric(6,3), so 33.333 records rather than forcing whole numbers that cannot total 100.
        seed = await _seed(service_session)
        for name in ("A", "B", "C"):
            await _add(service_session, seed, name=name, proportion=33.333)
        rows = await beneficiaries.list_for_account(service_session, account_id=seed["account_id"])
        assert [r["proportion"] for r in rows] == [33.333, 33.333, 33.333]

    @pytest.mark.parametrize("share", [0, -5, 101])
    async def test_a_share_outside_the_range_is_refused(
        self, service_session: AsyncSession, share: float
    ) -> None:
        seed = await _seed(service_session)
        with pytest.raises(ValueError):
            await _add(service_session, seed, proportion=share)

    async def test_a_nameless_nomination_is_refused(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        with pytest.raises(ValueError, match="name"):
            await _add(service_session, seed, name="   ")


class TestThePensionGate:
    async def test_a_nomination_needs_a_pension(self, service_session: AsyncSession) -> None:
        # An ISA or a GIA passes under the will and through probate, so offering a nomination there
        # invites a firm to record a wish that has no effect.
        seed = await _seed(service_session, product="ISA")
        with pytest.raises(ValueError, match="pension"):
            await _add(service_session, seed)

    async def test_an_unmapped_product_is_allowed_through(
        self, service_session: AsyncSession
    ) -> None:
        """`product_id` is NULL wherever the pipeline could not resolve a product (SEI's House
        Account, an unmodelled wrapper). Refusing there would mean a real SIPP with a missing
        product mapping cannot record a nomination — a data-quality problem punishing the member.
        A wrapper we KNOW is not a pension is refused; one we cannot name is not claimed either
        way."""
        seed = await _seed(service_session)
        await service_session.execute(
            text("UPDATE platform.accounts SET product_id = NULL WHERE id = :a"),
            {"a": seed["account_id"]},
        )
        assert await _add(service_session, seed)


class TestTenantContainment:
    async def test_another_firms_account_is_not_found(self, service_session: AsyncSession) -> None:
        mine, theirs = await _seed(service_session), await _seed(service_session)
        with pytest.raises(LookupError):
            await beneficiaries.add(
                service_session,
                tenant_id=mine["tenant_id"],
                account_id=theirs["account_id"],
                created_by=None,
                name="Jamie",
                proportion=50,
            )

    async def test_removing_another_firms_nomination_is_not_found(
        self, service_session: AsyncSession
    ) -> None:
        mine, theirs = await _seed(service_session), await _seed(service_session)
        row = await _add(service_session, theirs)
        with pytest.raises(LookupError):
            await beneficiaries.remove(
                service_session,
                tenant_id=mine["tenant_id"],
                account_id=mine["account_id"],
                uid=row["uid"],
            )
        # And it is still there for the firm that owns it.
        assert await beneficiaries.get(
            service_session, tenant_id=theirs["tenant_id"], uid=row["uid"]
        )


class TestWithdrawing:
    async def test_a_removal_frees_the_share(self, service_session: AsyncSession) -> None:
        seed = await _seed(service_session)
        row = await _add(service_session, seed, proportion=100)
        with pytest.raises(beneficiaries.BeneficiaryConflict):
            await _add(service_session, seed, name="Alex", proportion=1)
        await beneficiaries.remove(
            service_session,
            tenant_id=seed["tenant_id"],
            account_id=seed["account_id"],
            uid=row["uid"],
        )
        assert await _add(service_session, seed, name="Alex", proportion=100)

    async def test_removing_one_that_is_gone_is_a_lookup_error(
        self, service_session: AsyncSession
    ) -> None:
        seed = await _seed(service_session)
        with pytest.raises(LookupError):
            await beneficiaries.remove(
                service_session,
                tenant_id=seed["tenant_id"],
                account_id=seed["account_id"],
                uid="does-not-exist",
            )


class TestTheAccountScopesTheDelete:
    """A beneficiary uid alone must not reach across accounts.

    `firm_book_write_session` is `service_role`, so RLS refuses nothing here — the account in the
    URL is the whole of the control, and it only controls anything if the DELETE applies it. It
    did not: the statement was `WHERE tenant_id = :t AND uid = :u` under a route docstring saying
    in as many words that `account_id` is what stops a uid reaching across accounts. The delete is
    hard by design, so a wrong one leaves nothing to restore, and the nomination is the only
    instruction the scheme administrator holds about who a pension pays out to.
    """

    async def test_another_accounts_nomination_is_not_found(
        self, service_session: AsyncSession
    ) -> None:
        """SAME FIRM, two accounts — which is the case RLS and the tenant predicate both pass."""
        seed = await _seed(service_session)
        other = await _seed(service_session)
        row = await _add(service_session, other)

        with pytest.raises(LookupError):
            await beneficiaries.remove(
                service_session,
                tenant_id=other["tenant_id"],
                account_id=seed["account_id"],
                uid=row["uid"],
            )
        assert await beneficiaries.get(
            service_session, tenant_id=other["tenant_id"], uid=row["uid"]
        )
