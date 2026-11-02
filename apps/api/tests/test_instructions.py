"""The money-movement write path (``app.instructions``) — the platform's "five actions".

These are the highest-risk buttons in the product, and until this file they had **no tests at all**.
What is pinned here is the control the CRM audit called its most severe defect: a cash withdrawal
used to be validated against ``platform.accounts.value`` — the account's TOTAL value — so a request
to pay away a whole £100,000 portfolio as cash passed validation when the account held £2,000 of
actual cash. The gate now stands on the provider's own position (cash leg for ``withdrawal``, the
invested leg for ``model_withdrawal``) and lives in the service module, because
``POST /firm/contacts/{id}/instructions`` reaches it with nothing but a ``contacts:write`` check —
a check that lived only in the SvelteKit action was one HTTP call away from being bypassed.

The gate is also **cumulative**. Nothing settles an instruction, so every pending row is still a
claim on the same money; checking one request at a time meant the limit was beaten by simple
repetition (three £2,000 withdrawals against a £2,000 cash leg were all accepted) or by swapping
kinds (a ``model_topup`` spends the same cash). Those two bypasses are pinned below, because a
control that only holds for the first request is not a control.

Also pinned: the refusals that were missing entirely (inactive client, non-open account, no provider
position to check against), the malformed ``due_date`` that used to reach the SQL cast and 500, a
past Expected On (the Payment drawer posts JSON and so bypasses the form-action check), and
the NaN amount that passed every comparison and was written into the amount column.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import instructions as instructions_mod
from app.lake import holdings as lake_holdings
from app.routers import crm as crm_router
from tests.conftest import SECCL_PLATFORM_SQL

# ---- the provider position the fixtures pretend the lake holds -------------------------------
# A £50,000 account: £2,000 of it is cash, £48,000 is invested. Note the money-market row's
# asset_type is the lake's coarse "Stock" — it is cash only because the NAME says so, which is
# exactly the classifier the withdrawal gate depends on.
CASH_LEG = 2000.0
INVESTED_LEG = 48000.0
_POSITION = [
    {
        "asset_name": "BlackRock ICS Sterling Liquidity Fund",
        "asset_type": "Stock",
        "value": CASH_LEG,
        "as_of": "2026-06-30",
    },
    {
        "asset_name": "Vanguard FTSE Global All Cap",
        "asset_type": "Stock",
        "value": INVESTED_LEG,
        "as_of": "2026-07-01",
    },
]


@pytest.fixture
def position(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Stand the lake read up as ``_POSITION`` (mutable, so a test can empty it)."""
    rows: list[dict[str, object]] = [dict(r) for r in _POSITION]

    async def fake_holdings(
        provider: str, tenant_uid: str, external_account_ids: list[str]
    ) -> list[dict[str, object]]:
        # The signature is the point: the read is now per FIRM (`tenant_{uid}.positions`, the
        # firm's own slice) rather than one committed `docs/seccl.duckdb` extract shared by every
        # deployment. A stub that ignored the uid would let that regress silently.
        assert provider == "seccl"
        assert tenant_uid, "the gate must name the firm whose positions it is reading"
        return [] if not external_account_ids else rows

    monkeypatch.setattr(lake_holdings, "firm_holdings", fake_holdings)
    return rows


@asynccontextmanager
async def _firm(app_url: str) -> AsyncIterator[AsyncSession]:
    """A service_role session — mirrors db.py::firm_session (the instruction write path)."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        yield session
    await engine.dispose()


@pytest.fixture
def book(super_engine: Engine) -> dict[str, int]:
    """One firm with an active client (an active + a closed account, both Seccl-linked and both
    worth £100,000 on the OLTP snapshot), a deactivated client, and a second firm's account."""
    s = uuid.uuid4().hex[:8]
    out: dict[str, int] = {}
    with super_engine.connect() as c:

        def mk_tenant(tag: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"insten_{tag}_{s}", "a": f"insfirm-{tag}-{s}", "n": f"Ins {tag} {s}"},
                ).scalar_one()
            )

        def mk_contact(tenant: int, tag: str, enabled: bool) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, surname, enabled) "
                        "VALUES (:t, :u, 'individual', :sn, :en) RETURNING id"
                    ),
                    {"t": tenant, "u": f"inscon_{tag}_{s}", "sn": tag.title(), "en": enabled},
                ).scalar_one()
            )

        def mk_account(tenant: int, contact: int, tag: str, status: str, linked: bool) -> int:
            aid = int(
                c.execute(
                    text(
                        "INSERT INTO platform.accounts "
                        "  (tenant_id, contact_id, uid, status, value) "
                        "VALUES (:t, :con, :u, CAST(:st AS platform.account_status), 100000.00) "
                        "RETURNING id"
                    ),
                    {"t": tenant, "con": contact, "u": f"insacc_{tag}_{s}", "st": status},
                ).scalar_one()
            )
            if linked:
                c.execute(
                    text(
                        "INSERT INTO platform.links "
                        "  (tenant_id, entity_type, entity_id, provider, external_id) "
                        "VALUES (:t, 'account', :eid, 'seccl', :ext)"
                    ),
                    {"t": tenant, "eid": aid, "ext": f"SECCL_{tag}_{s}"},
                )
            return aid

        tenant = mk_tenant("main")
        other = mk_tenant("other")
        live = mk_contact(tenant, "live", True)
        dormant = mk_contact(tenant, "dormant", False)
        other_contact = mk_contact(other, "outsider", True)

        provider = str(
            c.execute(
                text(SECCL_PLATFORM_SQL),
                {"n": f"Seccl {s}"},
            ).scalar_one()
        )
        model = int(
            c.execute(
                text(
                    "INSERT INTO platform.models (tenant_id, provider, name) "
                    "VALUES (:t, :p, 'Balanced') RETURNING id"
                ),
                {"t": tenant, "p": provider},
            ).scalar_one()
        )
        active = mk_account(tenant, live, "active", "active", True)
        c.execute(
            text("UPDATE platform.accounts SET model_id = :m WHERE id = :a"),
            {"m": model, "a": active},
        )
        out = {
            "tenant": tenant,
            "other_tenant": other,
            "contact": live,
            "model": model,
            "active": active,
            "closed": mk_account(tenant, live, "closed", "closed", True),
            "draft": mk_account(tenant, live, "draft", "draft", True),
            "unlinked": mk_account(tenant, live, "unlinked", "active", False),
            # A second linked, active account on the same client — the pending-claim aggregate is
            # per account, and only a second account can prove it isn't per client.
            "second": mk_account(tenant, live, "second", "active", True),
            "dormant": mk_account(tenant, dormant, "dormant", "active", True),
            "foreign": mk_account(other, other_contact, "foreign", "active", True),
        }
        c.commit()
    return out


# ---- the classifier the gate stands on (pure, no DB) -----------------------------------------


def test_cash_is_recognised_by_name_not_just_asset_type() -> None:
    """The lake's asset_type is coarse ("Stock"), so a money-market fund is cash only because its
    NAME says so. If this stops matching, the withdrawal gate silently drops to £0 available."""
    assert instructions_mod.is_cash_holding(_POSITION[0]) is True
    assert instructions_mod.is_cash_holding(_POSITION[1]) is False
    assert instructions_mod.is_cash_holding({"asset_type": "Cash", "asset_name": "GBP"}) is True
    assert instructions_mod.is_cash_holding({"asset_name": "Money Market Fund"}) is True
    assert instructions_mod.is_cash_holding({"asset_name": "Sterling money-market"}) is True
    assert instructions_mod.is_cash_holding({}) is False


def test_sei_names_a_currency_holding_by_the_currency() -> None:
    """SEI publishes cash as `asset_type = 'MRCXXXXXXX'` with a blank ISIN and the currency for a
    name, so NOTHING about it contains the word cash and the name test above returns False for
    every one of them — £10.2m of currency positions on the 2026-03-27 drop. It became reachable
    when SEI's `positions` joined the lake-only relations, and it costs money three ways: a cash
    withdrawal REFUSED for want of cash the client has, a model disinvestment over-permitted, and
    currency sent to Morgan Lloyd as a FundSplit that inflates the filed PolicyValue.

    Matched as a CODE, not folded into the word pattern: that pattern has word boundaries
    precisely because a substring test once counted a fund manager's naming choice as cash."""
    assert instructions_mod.is_cash_holding({"asset_type": "MRCXXXXXXX", "asset_name": "GBP"})
    assert instructions_mod.is_cash_holding({"asset_type": " mrcxxxxxxx ", "asset_name": "USD"})
    # …and not as a substring of somebody's fund name.
    assert (
        instructions_mod.is_cash_holding(
            {"asset_type": "EQUITY", "asset_name": "MRCXXXXXXX Growth Fund"}
        )
        is False
    )


def test_a_fund_whose_name_merely_contains_cash_is_not_cash() -> None:
    """The classifier was a bare substring test, so a provider's naming choice inflated the money
    a withdrawal could be paid from: an equity fund called "Cashel …" counted as cash. Word
    boundaries now decide, and the same rows still bucket the donut the same way (crm.py
    delegates here)."""
    for name in (
        "Cashel Capital Global Equity Fund",  # the probe that got £90,000 paid away
        "Encashment Growth Trust",
        "Liquidityplus Equity Income",
        "Cashflow Opportunities Fund",
    ):
        holding = {"asset_type": "Stock", "asset_name": name}
        assert instructions_mod.is_cash_holding(holding) is False, name
        assert crm_router._asset_class(holding) == "Equities", name


def test_funding_basis_splits_cash_from_invested_and_keeps_the_latest_as_at() -> None:
    basis = instructions_mod.funding_basis([dict(r) for r in _POSITION])
    assert basis == {"cash": CASH_LEG, "invested": INVESTED_LEG, "as_of": "2026-07-01"}
    assert instructions_mod.funding_basis([]) == {"cash": 0.0, "invested": 0.0, "as_of": None}


def test_a_non_finite_value_in_the_extract_does_not_poison_the_leg() -> None:
    """One NaN row would make the whole leg NaN, and every `amount > available` comparison against
    NaN is False — the entire gate defeated by one bad row in the provider extract."""
    basis = instructions_mod.funding_basis(
        [
            {"asset_name": "Cash", "value": float("nan")},
            {"asset_name": "Cash", "value": 100.0},
            {"asset_name": "Equity", "value": float("inf")},
        ]
    )
    assert basis["cash"] == 100.0
    assert basis["invested"] == 0.0


def test_the_donut_and_the_gate_share_one_definition_of_cash() -> None:
    """crm.py's allocation bucket delegates to the same test, so the "Cash" a screen shows and the
    cash a withdrawal is checked against cannot drift apart."""
    assert crm_router._asset_class(_POSITION[0]) == "Cash"
    assert crm_router._asset_class(_POSITION[1]) == "Equities"


# ---- the withdrawal control (DB-backed) ------------------------------------------------------


async def _instructions_for(app_url: str, account_id: int) -> list[dict[str, object]]:
    """What actually landed in platform.instructions for an account — a refusal has to mean no
    row, not just an error message."""
    async with _firm(app_url) as session:
        rows = await session.execute(
            text(
                "SELECT uid, kind, amount::float8 AS amount, "
                "       rail_fee::float8 AS rail_fee, status "
                "FROM platform.instructions WHERE account_id = :a"
            ),
            {"a": account_id},
        )
        return [dict(r) for r in rows.mappings().all()]


@pytest.mark.asyncio
async def test_cash_withdrawal_above_the_cash_leg_is_refused(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """The defect this file exists for: the account is worth £100,000 and holds £2,000 of cash.
    £10,000 used to be accepted (10,000 < 100,000). It must now be refused, and nothing recorded."""
    with pytest.raises(ValueError, match="cash"):
        async with _firm(db["app"]) as session:
            await instructions_mod.create_instruction(
                session,
                tenant_id=book["tenant"],
                created_by_workos=None,
                kind="withdrawal",
                account_id=book["active"],
                amount=10_000.0,
            )
    assert await _instructions_for(db["app"], book["active"]) == []


@pytest.mark.asyncio
async def test_the_refusal_states_the_cash_and_its_as_at(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """An adviser must be told what IS available and how old that position is — otherwise the only
    way to discover the limit is to guess amounts."""
    with pytest.raises(ValueError) as exc:
        async with _firm(db["app"]) as session:
            await instructions_mod.create_instruction(
                session,
                tenant_id=book["tenant"],
                created_by_workos=None,
                kind="withdrawal",
                account_id=book["active"],
                amount=10_000.0,
            )
    assert "£2,000.00" in str(exc.value)
    assert "2026-07-01" in str(exc.value)


@pytest.mark.asyncio
async def test_cash_withdrawal_within_the_cash_leg_is_recorded(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    async with _firm(db["app"]) as session:
        row = await instructions_mod.create_instruction(
            session,
            tenant_id=book["tenant"],
            created_by_workos=None,
            kind="withdrawal",
            account_id=book["active"],
            amount=CASH_LEG,
        )
    assert row["status"] == "pending"
    assert row["rail_fee"] == 0  # Seccl is not charged SEI's tariff.
    assert [r["kind"] for r in await _instructions_for(db["app"], book["active"])] == ["withdrawal"]


@pytest.mark.asyncio
async def test_sei_rail_fee_is_stored_gated_and_reserved(
    db: dict[str, str],
    super_engine: Engine,
    book: dict[str, int],
    position: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client receives £1,999.50 but SEI debits £2,000.00. The fee is part of both the first
    gate and every later pending-claim calculation, and is stable if bank details later change."""
    with super_engine.connect() as c:
        c.execute(
            text(
                "UPDATE platform.accounts "
                "SET custodian = 'sei', bank_accounts = "
                """'[{"name":"Holder","sort_code":"12-34-56","account_number":"12345678",
                       "method":"faster_payment"}]'::jsonb """
                "WHERE id = :a"
            ),
            {"a": book["active"]},
        )
        c.execute(
            text(
                "INSERT INTO platform.links "
                "  (tenant_id, entity_type, entity_id, provider, external_id) "
                "VALUES (:t, 'account', :a, 'sei', :ext)"
            ),
            {"t": book["tenant"], "a": book["active"], "ext": "SEI_fee_test"},
        )
        c.commit()

    async def sei_holdings(
        provider: str, tenant_uid: str, external_account_ids: list[str]
    ) -> list[dict[str, object]]:
        assert provider == "sei"
        assert tenant_uid.startswith("insten_main_")
        assert external_account_ids == ["SEI_fee_test"]
        return position

    monkeypatch.setattr(lake_holdings, "firm_holdings", sei_holdings)
    first = await _raise(db["app"], book, "withdrawal", CASH_LEG - 0.50)
    assert first["amount"] == CASH_LEG - 0.50
    assert first["rail_fee"] == 0.50
    assert first["payment_method"] == "faster_payment"
    assert first["pass_rail_fee"] is True

    with pytest.raises(ValueError, match="already committed"):
        await _raise(db["app"], book, "withdrawal", 0.01)

    stored = await _instructions_for(db["app"], book["active"])
    assert stored[0]["rail_fee"] == 0.50


async def _as_sei(
    super_engine: Engine, book: dict[str, int], *, method: str = "faster_payment"
) -> None:
    with super_engine.connect() as c:
        c.execute(
            text(
                "UPDATE platform.accounts "
                "SET custodian = 'sei', bank_accounts = "
                """CAST(:bank AS jsonb) """
                "WHERE id = :a"
            ),
            {
                "a": book["active"],
                "bank": json.dumps(
                    [
                        {
                            "name": "Holder",
                            "sort_code": "12-34-56",
                            "account_number": "12345678",
                            "method": method,
                        }
                    ]
                ),
            },
        )
        c.execute(
            text(
                "UPDATE platform.links SET provider = 'sei', external_id = 'SEI_choice' "
                "WHERE entity_type = 'account' AND entity_id = :a"
            ),
            {"a": book["active"]},
        )
        c.commit()


@pytest.mark.asyncio
async def test_a_withdrawal_uses_stated_cash_when_positions_omit_the_account(
    db: dict[str, str],
    super_engine: Engine,
    book: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The latest positions file can omit an account whose last valuation still states cash.
    The withdrawal is checked against that stated cash, not against an empty positions read,
    and an administrator balance is not cash that can be paid away from the custodian."""

    async def positions_must_not_be_read(
        provider: str, tenant_uid: str, external_account_ids: list[str]
    ) -> list[dict[str, object]]:
        del provider, tenant_uid, external_account_ids
        raise AssertionError("cash was stated, so the positions file is not the balance")

    monkeypatch.setattr(lake_holdings, "firm_holdings", positions_must_not_be_read)
    with super_engine.connect() as c:
        for day, source, cash in (
            ("2026-10-01", "seccl", 10),
            ("2026-10-13", "seccl", 1500),
            ("2026-10-13", "morgan-lloyd", 9999),
        ):
            c.execute(
                text(
                    "INSERT INTO platform.valuations "
                    "  (tenant_id, account_id, as_of_date, source_system, market_value, "
                    "   cash_value) "
                    "VALUES (:t, :a, CAST(:d AS date), :s, :cash, :cash)"
                ),
                {
                    "t": book["tenant"],
                    "a": book["active"],
                    "d": day,
                    "s": source,
                    "cash": cash,
                },
            )
        c.commit()

    with pytest.raises(ValueError, match=r"Only £1,500.00 is cash.*2026-10-13"):
        await _raise(db["app"], book, "withdrawal", 1500.01)
    row = await _raise(db["app"], book, "withdrawal", 1500)
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_a_withdrawal_can_use_bacs_even_when_the_account_defaults_to_faster_payment(
    db: dict[str, str],
    super_engine: Engine,
    book: dict[str, int],
    position: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The saved method is the default, not a lock. BACS is free, so the whole cash leg can go."""
    await _as_sei(super_engine, book)

    async def sei_holdings(
        provider: str, tenant_uid: str, external_account_ids: list[str]
    ) -> list[dict[str, object]]:
        assert provider == "sei"
        return position

    monkeypatch.setattr(lake_holdings, "firm_holdings", sei_holdings)
    row = await _raise(db["app"], book, "withdrawal", CASH_LEG, payment_method="bacs")
    assert row["payment_method"] == "bacs"
    assert row["rail_fee"] == 0

    with pytest.raises(ValueError, match="UK bank account"):
        await _raise(db["app"], book, "withdrawal", 0.01, payment_method="swift")


@pytest.mark.asyncio
async def test_the_firm_can_pay_the_faster_payment_fee(
    db: dict[str, str],
    super_engine: Engine,
    book: dict[str, int],
    position: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tariff is still recorded. It is not reserved from the client's cash, so the whole
    leg can be paid out and a later penny is what fails, not the 50p."""
    await _as_sei(super_engine, book, method="bacs")

    async def sei_holdings(
        provider: str, tenant_uid: str, external_account_ids: list[str]
    ) -> list[dict[str, object]]:
        assert provider == "sei"
        return position

    monkeypatch.setattr(lake_holdings, "firm_holdings", sei_holdings)
    row = await _raise(
        db["app"],
        book,
        "withdrawal",
        CASH_LEG,
        payment_method="faster_payment",
        pass_rail_fee=False,
    )
    assert row["payment_method"] == "faster_payment"
    assert row["rail_fee"] == 0.50
    assert row["pass_rail_fee"] is False

    with pytest.raises(ValueError, match="already committed"):
        await _raise(db["app"], book, "withdrawal", 0.01, payment_method="bacs")


# ---- the gate is cumulative, not per-request ------------------------------------------------


async def _raise(
    app_url: str,
    book: dict[str, int],
    kind: str,
    amount: float,
    account: str = "active",
    **extra: object,
) -> dict[str, object]:
    async with _firm(app_url) as session:
        return await instructions_mod.create_instruction(
            session,
            tenant_id=book["tenant"],
            created_by_workos=None,
            kind=kind,
            account_id=book[account],
            amount=amount,
            **extra,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_the_cash_leg_cannot_be_withdrawn_twice_over(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """The surviving exploit after the per-request fix: nothing aggregated the instructions already
    pending, so the £2,000 cash leg was withdrawn three times over (£6,000 of pending withdrawals on
    £2,000 of cash) simply by submitting the same request again."""
    first = await _raise(db["app"], book, "withdrawal", CASH_LEG)
    assert first["status"] == "pending"

    for attempt in (CASH_LEG, 0.01):  # the whole leg again, then a penny
        with pytest.raises(ValueError) as exc:
            await _raise(db["app"], book, "withdrawal", attempt)
        # The message has to explain the ceiling, or the only way to find it is to guess amounts.
        assert "already committed to pending instructions" in str(exc.value)
        assert "£2,000.00" in str(exc.value)

    rows = await _instructions_for(db["app"], book["active"])
    assert [r["amount"] for r in rows] == [CASH_LEG]  # exactly one row, not three


@pytest.mark.asyncio
async def test_a_pending_top_up_is_a_claim_on_the_same_cash(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """Same-kind aggregation alone is defeated by swapping kinds: a model top-up buys into the
    model out of the account's cash, so topping up the whole cash leg and then withdrawing it would
    promise the same £2,000 away twice. Both directions are checked."""
    await _raise(db["app"], book, "model_topup", CASH_LEG)
    with pytest.raises(ValueError, match="already committed"):
        await _raise(db["app"], book, "withdrawal", 1.0)

    # ...and a top-up is itself funded from cash, so it cannot outrun the leg either.
    with pytest.raises(ValueError, match="only invest cash the account already holds"):
        await _raise(db["app"], book, "model_topup", 1.0)
    assert len(await _instructions_for(db["app"], book["active"])) == 1


@pytest.mark.asyncio
async def test_a_pending_adhoc_fee_is_a_claim_on_the_same_cash(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """An ad hoc fee spends cash like a withdrawal: charging the whole cash leg and then
    withdrawing a penny would promise the same money away twice."""
    await _raise(db["app"], book, "adhoc_fee", CASH_LEG)
    with pytest.raises(ValueError, match="already committed"):
        await _raise(db["app"], book, "withdrawal", 1.0)
    with pytest.raises(ValueError, match="more cash on the account"):
        await _raise(db["app"], book, "adhoc_fee", 1.0)
    assert len(await _instructions_for(db["app"], book["active"])) == 1


@pytest.mark.asyncio
async def test_the_two_legs_keep_their_own_headroom(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """Cash and the model are different pots: committing all the cash must not block a
    disinvestment, and vice versa. (A gate that lumped them together would refuse both.)"""
    await _raise(db["app"], book, "withdrawal", CASH_LEG)
    invested = await _raise(db["app"], book, "model_withdrawal", INVESTED_LEG)
    assert invested["status"] == "pending"
    with pytest.raises(ValueError, match="already committed"):
        await _raise(db["app"], book, "model_withdrawal", 1.0)


@pytest.mark.asyncio
async def test_cancelling_gives_the_headroom_back(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """Only *pending* rows are claims. A cancelled instruction promises nothing, so the adviser who
    raised the wrong amount can cancel it and raise the right one."""
    first = await _raise(db["app"], book, "withdrawal", CASH_LEG)
    async with _firm(db["app"]) as session:
        assert await instructions_mod.cancel_instruction(
            session, tenant_id=book["tenant"], uid=str(first["uid"])
        )
    again = await _raise(db["app"], book, "withdrawal", CASH_LEG)
    assert again["status"] == "pending"


@pytest.mark.asyncio
async def test_a_pension_payment_keeps_the_contribution_category(
    db: dict[str, str], book: dict[str, int]
) -> None:
    row = await _raise(db["app"], book, "expected_in", 10, category="third_party_contribution")
    assert row["category"] == "third_party_contribution"
    with pytest.raises(ValueError, match="contribution category"):
        await _raise(db["app"], book, "withdrawal", 1, category="personal_contribution")


@pytest.mark.asyncio
async def test_expected_money_in_never_raises_what_can_be_paid_out(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """Unsettled money must not be spendable: £250,000 expected in does not fund a £2,001 cash-out.
    Nothing credits the leg — the only figure that counts is the provider's own position."""
    await _raise(db["app"], book, "expected_in", 250_000.0)
    with pytest.raises(ValueError, match="cash"):
        await _raise(db["app"], book, "withdrawal", CASH_LEG + 1)


@pytest.mark.asyncio
async def test_another_accounts_pending_instructions_do_not_count(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """The claim is per account. The fixture's second linked account has its own position, so a
    withdrawal against it must not be constrained by what the first account has promised away."""
    await _raise(db["app"], book, "withdrawal", CASH_LEG)
    other = await _raise(db["app"], book, "withdrawal", CASH_LEG, account="second")
    assert other["status"] == "pending"


@pytest.mark.asyncio
async def test_a_cash_named_equity_fund_does_not_fund_a_withdrawal(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """End-to-end for the classifier: with the account's only holding an equity fund whose NAME
    contains "cash", a withdrawal of its whole value used to be accepted (£90,000 paid away against
    £0 of real cash)."""
    position[:] = [
        {
            "asset_name": "Cashel Capital Global Equity Fund",
            "asset_type": "Stock",
            "value": 90_000.0,
            "as_of": "2026-07-01",
        }
    ]
    with pytest.raises(ValueError, match="Only £0.00 is cash"):
        await _raise(db["app"], book, "withdrawal", 90_000.0)
    assert await _instructions_for(db["app"], book["active"]) == []


@pytest.mark.asyncio
async def test_a_second_request_cannot_read_the_same_headroom_concurrently(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """Aggregating pending rows is only a control if it can't be raced: two simultaneous withdrawals
    would otherwise both read £2,000 uncommitted and both pass. ``create_instruction`` takes a row
    lock on the account, so the second one waits — proven here by holding the lock and giving the
    second request a short ``lock_timeout`` instead of letting it block the test run."""
    from sqlalchemy.exc import DBAPIError

    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as holder, holder.begin():
        await holder.execute(text("SET LOCAL ROLE service_role"))
        await holder.execute(
            text("SELECT 1 FROM platform.accounts WHERE id = :a FOR UPDATE"),
            {"a": book["active"]},
        )
        with pytest.raises(DBAPIError, match="lock timeout"):
            async with _firm(db["app"]) as session:
                await session.execute(text("SET LOCAL lock_timeout = '300ms'"))
                await instructions_mod.create_instruction(
                    session,
                    tenant_id=book["tenant"],
                    created_by_workos=None,
                    kind="withdrawal",
                    account_id=book["active"],
                    amount=1.0,
                )
    await engine.dispose()
    assert await _instructions_for(db["app"], book["active"]) == []


# ---- a non-number amount ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["expected_in", "withdrawal", "adhoc_fee"])
async def test_a_non_finite_amount_is_refused(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]], kind: str
) -> None:
    """Every comparison against NaN is False, so `amount <= 0` and `amount > available` both passed
    and NaN was written into platform.instructions.amount. Infinity reached the same INSERT for the
    inflow kinds, which the withdrawal gate happened to catch and the others didn't."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="greater than zero"):
            await _raise(db["app"], book, kind, bad)
    assert await _instructions_for(db["app"], book["active"]) == []


@pytest.mark.asyncio
async def test_a_model_from_another_firm_is_refused(
    db: dict[str, str],
    book: dict[str, int],
    position: list[dict[str, object]],
    super_engine: Engine,
) -> None:
    """`model_id` is client input and this session is service_role (RLS bypassed), so the foreign
    key alone would accept another firm's model — which the instruction list then joins and shows
    by name."""
    with super_engine.connect() as c:
        provider = str(c.execute(text(SECCL_PLATFORM_SQL)).scalar_one())
        foreign_model = int(
            c.execute(
                text(
                    "INSERT INTO platform.models (tenant_id, provider, name) "
                    "VALUES (:t, :p, 'Their Balanced') RETURNING id"
                ),
                {"t": book["other_tenant"], "p": provider},
            ).scalar_one()
        )
        c.commit()

    with pytest.raises(ValueError, match="isn't one of your firm's"):
        async with _firm(db["app"]) as session:
            await instructions_mod.create_instruction(
                session,
                tenant_id=book["tenant"],
                created_by_workos=None,
                kind="model_topup",
                account_id=book["active"],
                amount=100.0,
                model_id=foreign_model,
            )
    assert await _instructions_for(db["app"], book["active"]) == []


@pytest.mark.asyncio
async def test_model_withdrawal_is_checked_against_what_is_invested(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """A model disinvestment comes out of the invested leg (£48,000), not the account total
    (£100,000) and not the cash leg."""
    with pytest.raises(ValueError, match="invested in the model"):
        async with _firm(db["app"]) as session:
            await instructions_mod.create_instruction(
                session,
                tenant_id=book["tenant"],
                created_by_workos=None,
                kind="model_withdrawal",
                account_id=book["active"],
                amount=60_000.0,
            )
    async with _firm(db["app"]) as session:
        row = await instructions_mod.create_instruction(
            session,
            tenant_id=book["tenant"],
            created_by_workos=None,
            kind="model_withdrawal",
            account_id=book["active"],
            amount=INVESTED_LEG,
        )
    assert row["model_id"] == book["model"]  # falls back to the account's linked model


@pytest.mark.asyncio
async def test_an_inflow_is_not_cash_checked(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """The gate is a withdrawal control: money coming IN is unconstrained by what's held."""
    async with _firm(db["app"]) as session:
        row = await instructions_mod.create_instruction(
            session,
            tenant_id=book["tenant"],
            created_by_workos=None,
            kind="expected_in",
            account_id=book["active"],
            amount=250_000.0,
            due_date="2027-01-31",
        )
    assert row["kind"] == "expected_in"
    assert str(row["due_date"]) == "2027-01-31"


@pytest.mark.asyncio
async def test_withdrawal_refused_when_the_provider_holds_no_position(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """Fail-closed. An empty lake read used to be indistinguishable from "plenty available", because
    the check never looked at the lake — it looked at the account's total value."""
    position.clear()
    with pytest.raises(ValueError, match="can't be confirmed"):
        async with _firm(db["app"]) as session:
            await instructions_mod.create_instruction(
                session,
                tenant_id=book["tenant"],
                created_by_workos=None,
                kind="withdrawal",
                account_id=book["active"],
                amount=1.0,
            )
    assert await _instructions_for(db["app"], book["active"]) == []


@pytest.mark.asyncio
async def test_withdrawal_refused_when_the_account_has_no_provider_link(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    with pytest.raises(ValueError, match="isn't linked to a provider"):
        async with _firm(db["app"]) as session:
            await instructions_mod.create_instruction(
                session,
                tenant_id=book["tenant"],
                created_by_workos=None,
                kind="withdrawal",
                account_id=book["unlinked"],
                amount=1.0,
            )


# ---- the refusals that were missing entirely -------------------------------------------------


@pytest.mark.asyncio
async def test_no_instruction_against_an_inactive_client(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """`crm.contacts.enabled` was rendered in three places and enforced nowhere."""
    with pytest.raises(ValueError, match="inactive"):
        async with _firm(db["app"]) as session:
            await instructions_mod.create_instruction(
                session,
                tenant_id=book["tenant"],
                created_by_workos=None,
                kind="expected_in",
                account_id=book["dormant"],
                amount=100.0,
            )
    assert await _instructions_for(db["app"], book["dormant"]) == []


@pytest.mark.asyncio
async def test_no_instruction_against_an_archived_client(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """Archiving is a SEPARATE axis from `enabled` (`crm.contacts.deleted_at`), so the gate that
    only checked `enabled` left archive-then-instruct open: a client could be archived and then have
    money moved on their account. Archive itself refuses while instructions are pending, so this is
    the other half of that rule."""
    async with _firm(db["app"]) as session:
        await session.execute(
            text("UPDATE crm.contacts SET deleted_at = now() WHERE id = :c AND tenant_id = :t"),
            {"c": book["contact"], "t": book["tenant"]},
        )
        await session.commit()
    with pytest.raises(ValueError, match="archived"):
        async with _firm(db["app"]) as session:
            await instructions_mod.create_instruction(
                session,
                tenant_id=book["tenant"],
                created_by_workos=None,
                kind="withdrawal",
                account_id=book["active"],
                amount=100.0,
            )
    assert await _instructions_for(db["app"], book["active"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["closed", "draft"])
async def test_no_instruction_against_an_account_that_is_not_open(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]], which: str
) -> None:
    """A draft account isn't open yet and a closed one has been paid away. The portal's picker
    excludes both; the API must not rely on the portal for that."""
    with pytest.raises(ValueError, match=which):
        async with _firm(db["app"]) as session:
            await instructions_mod.create_instruction(
                session,
                tenant_id=book["tenant"],
                created_by_workos=None,
                kind="expected_in",
                account_id=book[which],
                amount=100.0,
            )


@pytest.mark.asyncio
async def test_malformed_due_date_is_a_bad_request_not_a_500(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """It used to be passed straight to the SQL date cast, so "tomorrow" raised a DB error and
    surfaced as a 500 (ValueError → 400 at the router)."""
    for bad in ("tomorrow", "31/01/2027", "2027-02-30"):
        with pytest.raises(ValueError, match="valid date"):
            async with _firm(db["app"]) as session:
                await instructions_mod.create_instruction(
                    session,
                    tenant_id=book["tenant"],
                    created_by_workos=None,
                    kind="expected_in",
                    account_id=book["active"],
                    amount=100.0,
                    due_date=bad,
                )


@pytest.mark.asyncio
async def test_payment_in_may_be_backdated(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """The client paid last week and the record is being made now. The Payment drawer posts JSON
    through a same-origin proxy that does not re-check, so the floor (a year) is held here, and a
    date just inside it is kept as given."""
    last_week = (date.today() - timedelta(days=7)).isoformat()
    async with _firm(db["app"]) as session:
        row = await instructions_mod.create_instruction(
            session,
            tenant_id=book["tenant"],
            created_by_workos=None,
            kind="expected_in",
            account_id=book["active"],
            amount=100.0,
            due_date=last_week,
        )
    assert str(row["due_date"]) == last_week

    edge = (date.today() - timedelta(days=instructions_mod.BACKDATE_LIMIT_DAYS)).isoformat()
    async with _firm(db["app"]) as session:
        row = await instructions_mod.create_instruction(
            session,
            tenant_id=book["tenant"],
            created_by_workos=None,
            kind="expected_in",
            account_id=book["active"],
            amount=100.0,
            due_date=edge,
        )
    assert str(row["due_date"]) == edge


@pytest.mark.asyncio
async def test_payment_in_more_than_a_year_ago_is_refused(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    too_old = (date.today() - timedelta(days=instructions_mod.BACKDATE_LIMIT_DAYS + 1)).isoformat()
    with pytest.raises(ValueError, match="more than a year ago"):
        async with _firm(db["app"]) as session:
            await instructions_mod.create_instruction(
                session,
                tenant_id=book["tenant"],
                created_by_workos=None,
                kind="expected_in",
                account_id=book["active"],
                amount=100.0,
                due_date=too_old,
            )


@pytest.mark.asyncio
async def test_withdrawal_due_date_in_the_past_is_still_refused(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """Backdating is for money that has already arrived. A withdrawal is money the custodian has
    yet to move, so yesterday on it is still a mistake."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="in the past"):
        async with _firm(db["app"]) as session:
            await instructions_mod.create_instruction(
                session,
                tenant_id=book["tenant"],
                created_by_workos=None,
                kind="withdrawal",
                account_id=book["active"],
                amount=100.0,
                due_date=yesterday,
            )


@pytest.mark.asyncio
async def test_due_date_today_is_accepted(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    today = date.today().isoformat()
    async with _firm(db["app"]) as session:
        row = await instructions_mod.create_instruction(
            session,
            tenant_id=book["tenant"],
            created_by_workos=None,
            kind="expected_in",
            account_id=book["active"],
            amount=100.0,
            due_date=today,
        )
    assert str(row["due_date"]) == today


@pytest.mark.asyncio
async def test_another_firms_account_is_not_found(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """The write path runs as service_role (RLS bypassed), so tenant scoping is this module's own
    job: a supplied account id from another firm must not resolve."""
    with pytest.raises(LookupError):
        async with _firm(db["app"]) as session:
            await instructions_mod.create_instruction(
                session,
                tenant_id=book["tenant"],
                created_by_workos=None,
                kind="expected_in",
                account_id=book["foreign"],
                amount=100.0,
            )
    assert await _instructions_for(db["app"], book["foreign"]) == []


@pytest.mark.asyncio
async def test_amount_must_be_positive(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    for bad in (0.0, -50.0):
        with pytest.raises(ValueError, match="greater than zero"):
            async with _firm(db["app"]) as session:
                await instructions_mod.create_instruction(
                    session,
                    tenant_id=book["tenant"],
                    created_by_workos=None,
                    kind="withdrawal",
                    account_id=book["active"],
                    amount=bad,
                )


@pytest.mark.asyncio
async def test_cancel_is_tenant_scoped_and_only_touches_pending(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    async with _firm(db["app"]) as session:
        row = await instructions_mod.create_instruction(
            session,
            tenant_id=book["tenant"],
            created_by_workos=None,
            kind="expected_in",
            account_id=book["active"],
            amount=100.0,
        )
    uid = str(row["uid"])
    async with _firm(db["app"]) as session:
        assert (
            await instructions_mod.cancel_instruction(
                session, tenant_id=book["other_tenant"], uid=uid
            )
            is False
        )
    async with _firm(db["app"]) as session:
        assert (
            await instructions_mod.cancel_instruction(session, tenant_id=book["tenant"], uid=uid)
            is True
        )
    async with _firm(db["app"]) as session:  # already cancelled → no second effect
        assert (
            await instructions_mod.cancel_instruction(session, tenant_id=book["tenant"], uid=uid)
            is False
        )


# ---- the account's mandate (DB-backed) -------------------------------------------------------
# `platform.accounts.model_id` was read in four places (discretionary, model_name, the model
# instruction default) and written by NOTHING, so the firm's models — and GET /catalog/models —
# were unreachable from the product. These pin the writer.


@pytest.mark.asyncio
async def test_associating_a_model_makes_the_account_discretionary(
    db: dict[str, str], book: dict[str, int]
) -> None:
    """The consequence that matters: a model_id is what "discretionary" means, and it is what makes
    a model top-up possible on the account at all."""
    async with _firm(db["app"]) as session:
        with pytest.raises(ValueError, match="isn't linked to a model"):
            await instructions_mod.create_instruction(
                session,
                tenant_id=book["tenant"],
                created_by_workos=None,
                kind="model_topup",
                account_id=book["unlinked"],
                amount=100.0,
            )
    async with _firm(db["app"]) as session:
        out = await instructions_mod.set_account_model(
            session,
            tenant_id=book["tenant"],
            account_id=book["unlinked"],
            model_id=book["model"],
        )
    assert out["model_id"] == book["model"]
    assert out["discretionary"] is True
    assert out["model_name"] == "Balanced"


@pytest.mark.asyncio
async def test_clearing_the_mandate_returns_the_account_to_advisory(
    db: dict[str, str], book: dict[str, int]
) -> None:
    async with _firm(db["app"]) as session:
        out = await instructions_mod.set_account_model(
            session, tenant_id=book["tenant"], account_id=book["active"], model_id=None
        )
    assert out["model_id"] is None
    assert out["discretionary"] is False
    assert out["model_name"] is None


@pytest.mark.asyncio
async def test_an_account_fee_must_come_from_the_firms_compatible_catalogue(
    db: dict[str, str], book: dict[str, int]
) -> None:
    async with _firm(db["app"]) as session:
        provider = (
            await session.execute(
                text("SELECT provider FROM platform.models WHERE id = :m"),
                {"m": book["model"]},
            )
        ).scalar_one()
        fee = int(
            (
                await session.execute(
                    text(
                        "INSERT INTO platform.fee_packages "
                        "  (tenant_id, provider, model_id, name, category, frequency, "
                        "   fee_type, rule) "
                        "VALUES (:t, :p, :m, 'Advised standard', 'adviser', 'ongoing', "
                        "        'percentage', '{\"rate\": 0.005}'::jsonb) RETURNING id"
                    ),
                    {"t": book["tenant"], "p": provider, "m": book["model"]},
                )
            ).scalar_one()
        )
        second_fee = int(
            (
                await session.execute(
                    text(
                        "INSERT INTO platform.fee_packages "
                        "  (tenant_id, provider, name, category, frequency, fee_type, rule) "
                        "VALUES (:t, :p, 'Platform charge', 'platform', 'ongoing', "
                        "        'fixed', '{\"amount\": 100}'::jsonb) RETURNING id"
                    ),
                    {"t": book["tenant"], "p": provider},
                )
            ).scalar_one()
        )
        foreign_fee = int(
            (
                await session.execute(
                    text(
                        "INSERT INTO platform.fee_packages "
                        "  (tenant_id, provider, name, category, frequency, fee_type, rule) "
                        "VALUES (:t, :p, 'Foreign fee', 'adviser', 'ongoing', "
                        "        'fixed', '{\"amount\": 100}'::jsonb) RETURNING id"
                    ),
                    {"t": book["other_tenant"], "p": provider},
                )
            ).scalar_one()
        )

    async with _firm(db["app"]) as session:
        out = await instructions_mod.set_account_fee_packages(
            session,
            tenant_id=book["tenant"],
            account_id=book["active"],
            fee_package_ids=[second_fee, fee],
        )
    assert out["fee_packages"] == [
        {"id": second_fee, "name": "Platform charge", "sort_order": 0},
        {"id": fee, "name": "Advised standard", "sort_order": 1},
    ]

    async with _firm(db["app"]) as session:
        with pytest.raises(ValueError, match="fee packages are tied"):
            await instructions_mod.set_account_model(
                session,
                tenant_id=book["tenant"],
                account_id=book["active"],
                model_id=None,
            )

    async with _firm(db["app"]) as session:
        with pytest.raises(ValueError, match="isn't available"):
            await instructions_mod.set_account_fee_packages(
                session,
                tenant_id=book["tenant"],
                account_id=book["active"],
                fee_package_ids=[fee, foreign_fee],
            )

        # Validation happens before replacement: the failed mixed-tenant request preserves both
        # existing assignments and their display order.
        assigned = (
            (
                await session.execute(
                    text(
                        "SELECT fee_package_id FROM platform.account_fee_packages "
                        "WHERE tenant_id = :t AND account_id = :a "
                        "ORDER BY sort_order, fee_package_id"
                    ),
                    {"t": book["tenant"], "a": book["active"]},
                )
            )
            .scalars()
            .all()
        )
    assert assigned == [second_fee, fee]


@pytest.mark.asyncio
async def test_a_mandate_cannot_be_set_to_another_firms_model(
    db: dict[str, str], book: dict[str, int]
) -> None:
    """The session is service_role, so the foreign key alone would accept it — and the account
    would then display another firm's model as its mandate."""
    async with _firm(db["app"]) as session:
        foreign_model = int(
            (
                await session.execute(
                    text(
                        "INSERT INTO platform.models (tenant_id, provider, name) "
                        "SELECT :t, provider, 'Outsider' FROM platform.models WHERE id = :m "
                        "RETURNING id"
                    ),
                    {"t": book["other_tenant"], "m": book["model"]},
                )
            ).scalar_one()
        )
    async with _firm(db["app"]) as session:
        with pytest.raises(ValueError, match="isn't one of your firm's"):
            await instructions_mod.set_account_model(
                session,
                tenant_id=book["tenant"],
                account_id=book["unlinked"],
                model_id=foreign_model,
            )


@pytest.mark.asyncio
async def test_a_mandate_write_cannot_reach_another_firms_account(
    db: dict[str, str], book: dict[str, int]
) -> None:
    async with _firm(db["app"]) as session:
        with pytest.raises(LookupError):
            await instructions_mod.set_account_model(
                session,
                tenant_id=book["tenant"],
                account_id=book["foreign"],
                model_id=book["model"],
            )


@pytest.mark.asyncio
async def test_the_mandate_cannot_be_changed_under_a_pending_model_claim(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """A pending model instruction is a claim against the model that was in force when it was
    raised. Re-pointing (or clearing) the mandate underneath it would leave a live claim describing
    a mandate the account no longer has."""
    async with _firm(db["app"]) as session:
        await instructions_mod.create_instruction(
            session,
            tenant_id=book["tenant"],
            created_by_workos=None,
            kind="model_topup",
            account_id=book["active"],
            amount=100.0,
        )
    async with _firm(db["app"]) as session:
        with pytest.raises(ValueError, match="pending model instruction"):
            await instructions_mod.set_account_model(
                session, tenant_id=book["tenant"], account_id=book["active"], model_id=None
            )
    # Re-affirming the SAME model is not a change, so it is not blocked.
    async with _firm(db["app"]) as session:
        out = await instructions_mod.set_account_model(
            session, tenant_id=book["tenant"], account_id=book["active"], model_id=book["model"]
        )
    assert out["model_id"] == book["model"]


@pytest.mark.asyncio
async def test_an_unreadable_warehouse_refuses_rather_than_permitting(
    db: dict[str, str], book: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAIL CLOSED, and say which failure it was.

    The gate used to read `docs/seccl.duckdb` through `app/lake/seccl.py`, which answers `[]` for
    a missing file — so "the warehouse is unreachable" and "this account holds nothing" arrived as
    the same value. Both refuse, which is right for a cash-out ("we can't tell" must never mean
    "go ahead"), but only one of them is the account's fault and only one is worth paging about.
    """
    from app.lake import holdings as lake_holdings_mod

    async def _down(_provider: str, _uid: str, _ids: list[str]) -> list[dict[str, object]]:
        raise lake_holdings_mod.HoldingsUnavailable("DATALAKE_CATALOG_DSN not configured")

    monkeypatch.setattr(lake_holdings_mod, "firm_holdings", _down)

    with pytest.raises(ValueError, match="could not be read"):
        await _raise(db["app"], book, "withdrawal", 1.0)
    assert await _instructions_for(db["app"], book["active"]) == []


@pytest.mark.asyncio
async def test_the_gate_reads_this_firms_slice_and_no_other(
    db: dict[str, str], book: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The uid is what selects the firm's own positions, so the gate has to resolve and pass it.

    Before this it passed only the account's provider id into a single committed extract shared by
    every deployment — which is the whole defect: the same file answered for every firm.
    """
    from app.lake import holdings as lake_holdings_mod

    seen: dict[str, object] = {}

    async def _capture(provider: str, tenant_uid: str, ids: list[str]) -> list[dict[str, object]]:
        seen.update({"provider": provider, "uid": tenant_uid, "ids": ids})
        return [
            {
                "asset_name": "Cash",
                "asset_type": "Cash",
                "value": 5_000.0,
                "as_of": "2026-07-01",
            }
        ]

    monkeypatch.setattr(lake_holdings_mod, "firm_holdings", _capture)
    await _raise(db["app"], book, "withdrawal", 100.0)

    assert seen["provider"] == "seccl"
    assert str(seen["uid"]).startswith("ten_") or seen["uid"], "the firm must be named"
    assert seen["ids"], "the account's provider id must still narrow the read"


@pytest.mark.asyncio
async def test_regular_movements_replace_the_jsonb_and_clear_on_empty(
    db: dict[str, str], book: dict[str, int]
) -> None:
    from app import regular_movements as movements

    async with _firm(db["app"]) as session:
        out = await movements.set_regular_movements(
            session,
            tenant_id=book["tenant"],
            account_id=book["active"],
            payments=[{"amount": 500, "frequency": "monthly", "method": "direct_debit"}],
            withdrawals=[{"amount": 100, "frequency": "quarterly", "date": "2026-10-06"}],
        )
    assert float(out["payments"][0]["amount"]) == 500
    assert out["payments"][0]["frequency"] == "monthly"
    assert out["payments"][0]["method"] == "direct_debit"
    assert out["withdrawals"][0]["next_date"] == "2026-10-06"

    async with _firm(db["app"]) as session:
        cleared = await movements.set_regular_movements(
            session,
            tenant_id=book["tenant"],
            account_id=book["active"],
            payments=[],
            withdrawals=[],
        )
    assert cleared["payments"] is None
    assert cleared["withdrawals"] is None


# ---- WHICH fee an ad hoc fee is ----------------------------------------------------------------
#
# `adhoc_fee` recorded an amount and nothing else, so a firm running an ongoing adviser charge, an
# investment-management charge and an initial charge had one undifferentiated row for all three.
# The vocabulary is the firm's own register, never an enum of ours — and the gate is drawn against
# exactly the list `GET /catalog/fee-packages` feeds the picker, or the API refuses a fee type the
# adviser was just offered.


@pytest.fixture
def fee_register(super_engine: Engine, book: dict[str, int]) -> dict[str, int]:
    """Two of the firm's fee packages, one of another firm's, and one scoped to a product this
    account does not hold."""
    out: dict[str, int] = {}
    with super_engine.connect() as c:

        def mk(tenant: int, name: str, category: str, product: int | None = None) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO platform.fee_packages "
                        "  (tenant_id, name, category, frequency, fee_type, rule, product_id) "
                        "VALUES (:t, :n, :cat, 'ongoing', 'percentage', '{\"rate\": 0.75}', :p) "
                        "RETURNING id"
                    ),
                    {"t": tenant, "n": name, "cat": category, "p": product},
                ).scalar_one()
            )

        def mk_product(product_type: str, name: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO platform.products (tenant_id, product_type, name) "
                        "VALUES (:t, CAST(:pt AS platform.product_type), :n) RETURNING id"
                    ),
                    {"t": book["tenant"], "pt": product_type, "n": name},
                ).scalar_one()
            )

        out["isa_product"] = mk_product("ISA", "Stocks & Shares ISA")
        out["gia_product"] = mk_product("GIA", "General Investment Account")
        out["ongoing"] = mk(book["tenant"], "Ongoing adviser charge", "adviser")
        out["initial"] = mk(book["tenant"], "Initial advice charge", "adviser")
        out["foreign"] = mk(book["other_tenant"], "Someone else's charge", "adviser")
        out["isa_only"] = mk(book["tenant"], "ISA-only charge", "platform", out["isa_product"])
        c.commit()
    return out


@pytest.mark.asyncio
async def test_an_ad_hoc_fee_names_which_fee_it_is(
    db: dict[str, str],
    book: dict[str, int],
    position: list[dict[str, object]],
    fee_register: dict[str, int],
) -> None:
    """The point of the field: the charge is recorded under one of the firm's fee packages, and the
    read path names it — a type nothing reads back is a field that only looks like a control."""
    row = await _raise(db["app"], book, "adhoc_fee", 250.0, fee_package_id=fee_register["ongoing"])
    assert row["fee_package_id"] == fee_register["ongoing"]

    async with _firm(db["app"]) as session:
        listed = await instructions_mod.list_for_contact(
            session, tenant_id=book["tenant"], contact_id=book["contact"]
        )
    assert [(r["kind"], r["fee_package_name"]) for r in listed] == [
        ("adhoc_fee", "Ongoing adviser charge")
    ]


@pytest.mark.asyncio
async def test_an_ad_hoc_fee_does_not_need_a_type(
    db: dict[str, str],
    book: dict[str, int],
    position: list[dict[str, object]],
    fee_register: dict[str, int],
) -> None:
    """The drawer records a fee amount and no type. A register on the account must not revive the
    old required picker — that would refuse every ad hoc fee the screen can actually raise."""
    assert fee_register["ongoing"]
    row = await _raise(db["app"], book, "adhoc_fee", 250.0)
    assert row["fee_package_id"] is None


@pytest.mark.asyncio
async def test_a_firm_with_no_fee_register_can_still_charge(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    """A firm with no fee register can still raise an ad hoc fee — the type is optional either
    way. The drawer records an amount and nothing else."""
    row = await _raise(db["app"], book, "adhoc_fee", 250.0)
    assert row["fee_package_id"] is None


@pytest.mark.asyncio
async def test_another_firms_fee_package_is_refused(
    db: dict[str, str],
    book: dict[str, int],
    position: list[dict[str, object]],
    fee_register: dict[str, int],
) -> None:
    """`fee_package_id` is caller input and this session is service_role (RLS bypassed), so the
    foreign key alone would take another firm's package — which the instruction history then names
    as a fee this firm charges under."""
    with pytest.raises(ValueError, match="isn't one your firm charges"):
        await _raise(db["app"], book, "adhoc_fee", 250.0, fee_package_id=fee_register["foreign"])
    assert await _instructions_for(db["app"], book["active"]) == []


@pytest.mark.asyncio
async def test_the_gate_offers_exactly_what_the_picker_does(
    db: dict[str, str], book: dict[str, int], fee_register: dict[str, int]
) -> None:
    """A narrowing here that `GET /catalog/fee-packages` does not do refuses a fee type the adviser
    was just offered — a refusal that reads as a bug in the form. So: an account naming no product
    sees the whole enabled register (including product-scoped packages, which is the endpoint's own
    `:prod IS NULL` arm), an account naming one loses the packages scoped elsewhere, and no account
    ever sees another firm's."""
    async with _firm(db["app"]) as session:
        wide = await instructions_mod.fee_types_for_account(
            session, tenant_id=book["tenant"], product_id=None, model_id=None
        )
        narrow = await instructions_mod.fee_types_for_account(
            session,
            tenant_id=book["tenant"],
            product_id=fee_register["gia_product"],
            model_id=None,
        )
    assert set(wide) == {
        fee_register["ongoing"],
        fee_register["initial"],
        fee_register["isa_only"],
    }
    assert set(narrow) == {fee_register["ongoing"], fee_register["initial"]}
    assert fee_register["foreign"] not in wide


@pytest.mark.asyncio
async def test_a_fee_type_on_a_withdrawal_is_refused(
    db: dict[str, str],
    book: dict[str, int],
    position: list[dict[str, object]],
    fee_register: dict[str, int],
) -> None:
    """A fee type belongs to a fee. Dropping it silently would accept a request whose whole point
    was the field the caller sent, and report success — and the row would then render as a charge
    in the history it appears in."""
    for kind in ("withdrawal", "expected_in", "model_topup"):
        with pytest.raises(ValueError, match="applies to an ad hoc fee only"):
            await _raise(db["app"], book, kind, 1.0, fee_package_id=fee_register["ongoing"])
    assert await _instructions_for(db["app"], book["active"]) == []


@pytest.mark.asyncio
async def test_an_adviser_fee_on_a_payment_is_stored(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    row = await _raise(db["app"], book, "expected_in", 500.0, adviser_fee=50.0)
    assert row["adviser_fee"] == 50.0
    assert row["kind"] == "expected_in"
    async with _firm(db["app"]) as session:
        listed = await instructions_mod.list_for_contact(
            session, tenant_id=book["tenant"], contact_id=book["contact"]
        )
    assert [(r["kind"], r["adviser_fee"]) for r in listed] == [("expected_in", 50.0)]


@pytest.mark.asyncio
async def test_an_adviser_fee_can_ride_a_withdrawal_but_not_other_kinds(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    # A withdrawal may now carry an adviser fee — an additional debit alongside the money out.
    row = await _raise(db["app"], book, "withdrawal", 1.0, adviser_fee=0.5)
    assert row["kind"] == "withdrawal"
    assert row["adviser_fee"] == 0.5
    # It still applies to a money movement only — refused on an ad hoc fee.
    with pytest.raises(ValueError, match="applies to a payment or withdrawal only"):
        await _raise(db["app"], book, "adhoc_fee", 1.0, adviser_fee=0.5)


@pytest.mark.asyncio
async def test_an_adviser_fee_must_be_less_than_the_payment(
    db: dict[str, str], book: dict[str, int], position: list[dict[str, object]]
) -> None:
    with pytest.raises(ValueError, match="less than the payment"):
        await _raise(db["app"], book, "expected_in", 50.0, adviser_fee=50.0)
    with pytest.raises(ValueError, match="less than the payment"):
        await _raise(db["app"], book, "expected_in", 50.0, adviser_fee=60.0)
    assert await _instructions_for(db["app"], book["active"]) == []


def _hold_on_this_database(monkeypatch: pytest.MonkeyPatch, app_url: str) -> None:
    """The money-out hold opens ``system_transaction``, which is the process database.
    Tests run on the container."""
    from app import db as db_mod

    @asynccontextmanager
    async def _sys() -> AsyncIterator[AsyncSession]:
        engine = create_async_engine(app_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            yield session
        await engine.dispose()

    monkeypatch.setattr(db_mod, "system_transaction", _sys)


@pytest.mark.asyncio
async def test_two_custodian_links_are_not_treated_as_sei(
    db: dict[str, str], book: dict[str, int]
) -> None:
    """Enum order lists SEI before Seccl. Two primary links are not a choice."""
    from app.actions.household import load_household
    from app.actions.runs import _load_account

    async with _firm(db["app"]) as session:
        await session.execute(
            text("UPDATE platform.accounts SET custodian = NULL WHERE id = :a"),
            {"a": book["active"]},
        )
        one = await _load_account(session, tenant_id=book["tenant"], account_id=book["active"])
        assert one is not None
        assert one["custodian"] == "seccl"
        household = await load_household(
            session, tenant_id=book["tenant"], account_id=book["active"]
        )
        assert household is not None
        assert household["accounts"][0]["custodian"] == "seccl"
        await session.execute(
            text(
                "INSERT INTO platform.links "
                "  (tenant_id, entity_type, entity_id, provider, external_id) "
                "VALUES (:t, 'account', :a, 'sei', 'SEI-1')"
            ),
            {"t": book["tenant"], "a": book["active"]},
        )
        both = await _load_account(session, tenant_id=book["tenant"], account_id=book["active"])
        assert both is not None
        assert both["custodian"] is None
        household = await load_household(
            session, tenant_id=book["tenant"], account_id=book["active"]
        )
        assert household is not None
        assert household["accounts"][0]["custodian"] is None


@pytest.mark.asyncio
async def test_a_new_withdrawal_is_not_already_with_sei(
    db: dict[str, str], book: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The booking writes the pending row in this transaction. The hold used to look for it
    on another connection, miss, and refuse every withdrawal as if SEI already had the money."""
    from app import outbound
    from app.actions.attempt import OperationError
    from app.integrations.sei.actions import _claim_money_out, _release_money_out
    from app.integrations.sei.cash_receipts import money_out_held

    _hold_on_this_database(monkeypatch, db["app"])
    uid = f"wd-{uuid.uuid4().hex[:12]}"
    async with _firm(db["app"]) as session:
        current = await outbound.ensure_pending(
            session,
            tenant_id=book["tenant"],
            origin="instruction",
            origin_uid=uid,
            kind="withdrawal",
            account_id=book["active"],
            contact_id=book["contact"],
            amount=10.0,
            currency="GBP",
        )
        claimed = await _claim_money_out(
            session,
            tenant_id=book["tenant"],
            current=current,
            origin_uid=uid,
            kind="withdrawal",
            account_id=book["active"],
            amount=10.0,
            currency="GBP",
        )
        assert money_out_held(claimed["data"]) is True
        with pytest.raises(OperationError, match="not sent again"):
            await _claim_money_out(
                session,
                tenant_id=book["tenant"],
                current=claimed,
                origin_uid=uid,
                kind="withdrawal",
                account_id=book["active"],
                amount=10.0,
                currency="GBP",
            )
        await _release_money_out(session, tenant_id=book["tenant"], current=claimed)
        again = await outbound.get_by_origin(
            session, tenant_id=book["tenant"], origin="instruction", origin_uid=uid
        )
        assert again is not None
        assert money_out_held(again.get("data")) is False


@pytest.mark.asyncio
async def test_a_committed_hold_still_refuses_a_second_send(
    db: dict[str, str], book: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the row is committed, the hold is taken on its own connection. A retry that
    still has the old copy, without the flag, does not send the payment again."""
    from app import outbound
    from app.actions.attempt import OperationError
    from app.integrations.sei.actions import _claim_money_out

    _hold_on_this_database(monkeypatch, db["app"])
    uid = f"wd-{uuid.uuid4().hex[:12]}"
    kwargs = {
        "tenant_id": book["tenant"],
        "origin_uid": uid,
        "kind": "withdrawal",
        "account_id": book["active"],
        "amount": 10.0,
        "currency": "GBP",
    }
    async with _firm(db["app"]) as session:
        await outbound.ensure_pending(
            session,
            origin="instruction",
            contact_id=book["contact"],
            **kwargs,
        )
    async with _firm(db["app"]) as session:
        stale = await outbound.get_by_origin(
            session, tenant_id=book["tenant"], origin="instruction", origin_uid=uid
        )
        assert stale is not None
        await _claim_money_out(session, current=stale, **kwargs)
    async with _firm(db["app"]) as session:
        stale = await outbound.get_by_origin(
            session, tenant_id=book["tenant"], origin="instruction", origin_uid=uid
        )
        assert stale is not None
        stale["data"] = {}
        with pytest.raises(OperationError, match="not sent again"):
            await _claim_money_out(session, current=stale, **kwargs)


@pytest.mark.asyncio
async def test_a_rolled_back_request_does_not_pay_sei_twice(
    db: dict[str, str], book: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hold is committed before the money call. The request then rolling back
    does not clear it, and a new instruction uid for the same account is refused."""
    from app import outbound
    from app.actions.attempt import OperationError
    from app.integrations.sei.actions import _claim_money_out

    _hold_on_this_database(monkeypatch, db["app"])
    first = f"wd-{uuid.uuid4().hex[:12]}"
    second = f"wd-{uuid.uuid4().hex[:12]}"
    kwargs = {
        "tenant_id": book["tenant"],
        "kind": "withdrawal",
        "account_id": book["active"],
        "amount": 10.0,
        "currency": "GBP",
    }
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        try:
            async with session.begin():
                await session.execute(text("SET LOCAL ROLE service_role"))
                current = await outbound.ensure_pending(
                    session,
                    origin="instruction",
                    origin_uid=first,
                    contact_id=book["contact"],
                    **kwargs,
                )
                await _claim_money_out(session, current=current, origin_uid=first, **kwargs)
                raise RuntimeError("the request failed after the hold")
        except RuntimeError:
            pass
    await engine.dispose()
    async with _firm(db["app"]) as session:
        other = await outbound.ensure_pending(
            session,
            origin="instruction",
            origin_uid=second,
            contact_id=book["contact"],
            **kwargs,
        )
        with pytest.raises(OperationError, match="not sent again"):
            await _claim_money_out(session, current=other, origin_uid=second, **kwargs)
    # Once the held instruction is on the book as submitted, the account can pay again.
    async with _firm(db["app"]) as session:
        booked = await outbound.ensure_pending(
            session,
            origin="instruction",
            origin_uid=first,
            contact_id=book["contact"],
            **kwargs,
        )
        await session.execute(
            text("UPDATE platform.transactions SET status = 'submitted' WHERE id = :i"),
            {"i": booked["id"]},
        )
    third = f"wd-{uuid.uuid4().hex[:12]}"
    async with _firm(db["app"]) as session:
        nxt = await outbound.ensure_pending(
            session,
            origin="instruction",
            origin_uid=third,
            contact_id=book["contact"],
            **kwargs,
        )
        claimed = await _claim_money_out(session, current=nxt, origin_uid=third, **kwargs)
        assert claimed["data"]["sei_inflight"] is True
