"""The statement view of the transactions listing (``app/statement.py`` + ``/crm/transactions``).

Two halves. The vocabulary is pure and unit-tested here; the query is executed against real
Postgres through the same RLS-bound harness ``test_crm_api.py`` uses, because the parts that were
wrong before are exactly the parts a mock cannot see — the sign the SQL applies to a provider's
amount, and whether the window that produces the running total is computed over the account's
whole history or over the page.

**The bug this pins.** ``platform.transactions.amount`` is the provider's own gross figure and the
providers disagree about its sign: SEI signs it from its debit/credit flags, Seccl publishes a
magnitude and states direction as ``movement_type`` In/Out, which the conform step folds into the
canonical ``type``. A listing rendering ``amount`` as signed money therefore showed every Seccl
withdrawal as a credit — and a running total built on it walks the wrong way. The fixtures below
carry both conventions on one account for that reason: a firm's book spans custodians, and the
column has to be right for the mixture, not for either one alone.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import statement
from app.routers import crm as crm_router

# App-only portal (0107): resolve_claims matches an application id against the caller's tenant
# environment. Reusing the strings across tenants is safe — the match is per-tenant.
_APP_CLIENT = "app_stm_client"
_APP_PLATFORM = "app_stm_platform"

# ── the vocabulary ───────────────────────────────────────────────────────────────────────────


def test_a_family_and_a_type_that_disagree_match_nothing() -> None:
    """The intersection is the filter. Dropping the type and keeping the family shows the
    wrong rows — a fee asked for inside money-in is no rows, not every payment in."""
    assert statement.types_for(["money_in"], ["fee"]) == []
    assert statement.types_for(["money_in"], ["deposit", "fee"]) == ["deposit"]
    assert statement.types_for(["charge"], None) == ["fee", "tax"]
    assert statement.types_for(None, ["fee"]) == ["fee"]


def test_every_canonical_type_has_a_family() -> None:
    """A type with no family lands in `other`, which is a real bucket — but the twelve members of
    ``platform.transaction_type`` should each be classified deliberately, so an enum member added
    without touching this map fails here rather than quietly appearing under "Other"."""
    canonical = {
        "buy",
        "sell",
        "dividend",
        "interest",
        "fee",
        "tax",
        "transfer_in",
        "transfer_out",
        "deposit",
        "withdrawal",
        "corporate_action",
        "adjustment",
    }
    assert set(statement.FAMILIES) == canonical
    assert set(statement.FAMILIES.values()) <= set(statement.FAMILY_ORDER)


def test_direction_covers_every_type_exactly_once() -> None:
    assert not set(statement.CREDIT_TYPES) & set(statement.DEBIT_TYPES)
    classified = {*statement.CREDIT_TYPES, *statement.DEBIT_TYPES, *statement.DIRECTIONLESS}
    assert classified == set(statement.FAMILIES)


def test_the_sign_comes_from_the_type_not_from_the_providers_amount() -> None:
    """Both sign conventions land on the same answer, which is the whole point."""
    # Seccl: a positive magnitude, direction carried by the canonical type.
    assert statement.signed_amount("withdrawal", 500.0) == -500.0
    assert statement.signed_amount("deposit", 500.0) == 500.0
    # SEI: already signed. Applying the direction to the MAGNITUDE keeps it right rather than
    # flipping it back (which is what multiplying the published figure would have done).
    assert statement.signed_amount("withdrawal", -500.0) == -500.0
    assert statement.signed_amount("deposit", -500.0) == 500.0


def test_a_trade_is_a_cash_movement() -> None:
    # The ledger tracks cash: buying takes cash out of the account, selling settles cash in.
    assert statement.signed_amount("buy", 680.0) == -680.0
    assert statement.signed_amount("sell", 680.0) == 680.0


def test_a_directionless_type_keeps_the_published_sign() -> None:
    """A corporate action or a correcting entry can go either way and the conformed row does not
    say which, so it is passed through rather than guessed at."""
    assert statement.signed_amount("adjustment", -12.5) == -12.5
    assert statement.signed_amount("corporate_action", 12.5) == 12.5
    assert statement.direction("adjustment") is None
    assert statement.signed_amount("deposit", None) is None


def test_a_voided_movement_moves_nothing() -> None:
    assert statement.counts_towards_balance("settled") is True
    assert statement.counts_towards_balance("Pending") is True  # pending money is still expected
    assert statement.counts_towards_balance("CANCELLED") is False
    assert statement.counts_towards_balance("deleted") is False  # Seccl's word for a reversal
    # A status nobody has seen before COUNTS: dropping a movement because a custodian invented a
    # word would understate the ledger silently.
    assert statement.counts_towards_balance("part-filled") is True


def test_totals_break_the_flows_down_without_double_counting() -> None:
    rows = [
        {"type": "deposit", "signed_amount": 1000.0, "status": "settled", "family": "money_in"},
        {"type": "fee", "signed_amount": -12.0, "status": "settled", "family": "charge"},
        {"type": "dividend", "signed_amount": 30.0, "status": "settled", "family": "income"},
        # Cancelled: it moved no money, so it appears in none of the figures.
        {
            "type": "withdrawal",
            "signed_amount": -900.0,
            "status": "cancelled",
            "family": "money_out",
        },
    ]
    out = statement.totals(rows)
    assert out["in"] == 1030.0  # deposit + dividend
    assert out["out"] == 12.0  # the fee only
    assert out["net"] == 1018.0
    assert out["fees"] == 12.0
    assert out["income"] == 30.0
    # `count` is every row in the match set, including the ones that moved no money — the strip
    # summarises what is listed, and the list still shows the cancellation.
    assert out["count"] == 4


# ── the query ────────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def _authed(app_url: str, workos_user: str, perms: list[str]) -> AsyncIterator[AsyncSession]:
    """A request principal, exactly as db.py::tenant_session builds one (see test_crm_api)."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        resolved = (
            await session.execute(
                text("SELECT auth.resolve_claims(:w, NULL, :app)"),
                {"w": workos_user, "app": _APP_PLATFORM},
            )
        ).scalar_one()
        claims = {**resolved, "sub": workos_user, "role": "adviser", "permissions": perms}
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)}
        )
        yield session
    await engine.dispose()


@pytest.fixture
def ledger(super_engine: Engine) -> dict[str, object]:
    """One firm, one client, one account — and a statement whose rows deliberately mix the two
    provider sign conventions, so the assertions below are about the canonical type rather than
    about whichever custodian happened to publish the row."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"stmten_{s}", "a": f"stmfirm-{s}", "n": f"StmFirm {s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  client_portal_client_id, platform_client_id) "
                "VALUES (:t, 'dev', :env, :capp, :papp)"
            ),
            {
                "t": tenant,
                "env": f"env_stm_{s}",
                "capp": _APP_CLIENT,
                "papp": _APP_PLATFORM,
            },
        )
        user = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, "
                    "  first_name, last_name, enabled) "
                    "VALUES (:uid, :t, :e, :w, 'Ada', 'Keeper', true) RETURNING id"
                ),
                {
                    "uid": f"stmusr_{s}",
                    "t": tenant,
                    "e": f"ada_{s}@stmfirm.test",
                    "w": f"stmwos_{s}",
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": user, "t": tenant},
        )
        contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, "
                    "  service_type) VALUES (:t, :uid, 'individual', 'Peter', 'Vance', 'advised') "
                    "RETURNING id"
                ),
                {"t": tenant, "uid": f"stmcon_{s}"},
            ).scalar_one()
        )
        account = int(
            c.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "  (tenant_id, contact_id, uid, status, value, custodian, name) "
                    "VALUES (:t, :con, :uid, 'active', 50000, 'seccl', 'Peter''s Primary') "
                    "RETURNING id"
                ),
                {"t": tenant, "con": contact, "uid": f"stmacc_{s}"},
            ).scalar_one()
        )
        other = int(
            c.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, contact_id, uid, status, value) "
                    "VALUES (:t, :con, :uid, 'active', 10000) RETURNING id"
                ),
                {"t": tenant, "con": contact, "uid": f"stmacc2_{s}"},
            ).scalar_one()
        )

        #  date        type         amount    provider convention
        rows = [
            ("2026-01-10", "deposit", "1000.0000", "seccl"),  # +1000  -> 1000
            ("2026-02-10", "buy", "600.0000", "seccl"),  #  -600  ->  400
            ("2026-03-10", "dividend", "25.0000", "seccl"),  #   +25  ->  425
            ("2026-04-10", "fee", "-12.0000", "sei"),  #   -12  ->  413   (SEI: pre-signed)
            ("2026-05-10", "withdrawal", "500.0000", "seccl"),  #  -500  ->  -87   (Seccl: unsigned)
        ]
        for i, (day, kind, amount, source) in enumerate(rows):
            c.execute(
                text(
                    "INSERT INTO platform.transactions "
                    "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                    "   source_subtype, amount, currency, transaction_date, status, report_group) "
                    "VALUES (:t, :a, :src, :sid, CAST(:ty AS platform.transaction_type), :sub, "
                    "        :amt, 'GBP', CAST(:d AS date), :st, :grp)"
                ),
                {
                    "t": tenant,
                    "a": account,
                    "src": source,
                    "sid": f"STM_{s}_{i}",
                    "ty": kind,
                    "sub": f"{kind.title()} line",
                    "amt": amount,
                    "d": day,
                    "st": "settled",
                    "grp": f"BATCH_{s}",
                },
            )
        # A cancelled withdrawal on the same account: listed, but it moves no money.
        c.execute(
            text(
                "INSERT INTO platform.transactions "
                "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                "   amount, currency, transaction_date, status) "
                "VALUES (:t, :a, 'seccl', :sid, 'withdrawal', 9000, 'GBP', "
                "        CAST('2026-06-10' AS date), 'cancelled')"
            ),
            {"t": tenant, "a": account, "sid": f"STM_{s}_void"},
        )
        # …and one on the OTHER account, so a firm-wide read has two accounts in it.
        c.execute(
            text(
                "INSERT INTO platform.transactions "
                "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                "   amount, currency, transaction_date) "
                "VALUES (:t, :a, 'seccl', :sid, 'deposit', 250, 'GBP', "
                "        CAST('2026-03-01' AS date))"
            ),
            {"t": tenant, "a": other, "sid": f"STM_{s}_other"},
        )
        c.commit()
    return {
        "tenant": tenant,
        "wos": f"stmwos_{s}",
        "contact": contact,
        "account": account,
        "other": other,
        "group": f"BATCH_{s}",
    }


@pytest.mark.asyncio
async def test_the_sign_is_right_for_both_provider_conventions(
    db: dict[str, str], ledger: dict[str, object]
) -> None:
    """The regression that made this necessary: rendering `amount` showed a Seccl withdrawal as a
    credit, because Seccl publishes a magnitude and SEI publishes a signed figure."""
    async with _authed(db["app"], str(ledger["wos"]), ["contacts:read_all"]) as session:
        rows = await crm_router.list_transactions(
            account_id=int(ledger["account"]), session=session
        )
    assert isinstance(rows, list)
    by_type = {r["type"]: r for r in rows}
    assert by_type["withdrawal"]["amount"] == 500.0  # what Seccl published…
    assert by_type["withdrawal"]["signed_amount"] == -500.0  # …and what it means
    assert by_type["fee"]["amount"] == -12.0  # SEI's own sign, untouched
    assert by_type["fee"]["signed_amount"] == -12.0
    assert by_type["buy"]["signed_amount"] == -600.0
    assert by_type["dividend"]["signed_amount"] == 25.0


@pytest.mark.asyncio
async def test_the_running_total_accumulates_oldest_first_and_skips_the_void(
    db: dict[str, str], ledger: dict[str, object]
) -> None:
    """Newest-first on screen, accumulated oldest-first — the shape of a bank statement. The
    cancelled withdrawal is listed and contributes nothing, so the total does not fall by £9,000
    for money that never left."""
    async with _authed(db["app"], str(ledger["wos"]), ["contacts:read_all"]) as session:
        rows = await crm_router.list_transactions(
            account_id=int(ledger["account"]), session=session
        )
    assert isinstance(rows, list)
    assert [r["date"] for r in rows] == [
        "2026-06-10",
        "2026-05-10",
        "2026-04-10",
        "2026-03-10",
        "2026-02-10",
        "2026-01-10",
    ]
    assert [r["running_total"] for r in rows] == [-87.0, -87.0, 413.0, 425.0, 400.0, 1000.0]


@pytest.mark.asyncio
async def test_the_running_total_survives_a_filter(
    db: dict[str, str], ledger: dict[str, object]
) -> None:
    """Narrowing a statement to the charges must not renumber it: the total on a row is what that
    row actually had, which is why it is attached before the search and filters run. A total
    recomputed over the filtered rows would read as a ledger of charges alone — a different, and
    false, account."""
    async with _authed(db["app"], str(ledger["wos"]), ["contacts:read_all"]) as session:
        rows = await crm_router.list_transactions(
            account_id=int(ledger["account"]), family="charge", session=session
        )
    assert isinstance(rows, list)
    assert [r["type"] for r in rows] == ["fee"]
    assert rows[0]["running_total"] == 413.0


@pytest.mark.asyncio
async def test_a_family_and_a_type_that_disagree_list_nothing(
    db: dict[str, str], ledger: dict[str, object]
) -> None:
    """A fee asked for inside money-in is no rows. The old intersection dropped the type and
    showed the whole family, so the click that said "fees" returned the deposit."""
    async with _authed(db["app"], str(ledger["wos"]), ["contacts:read_all"]) as session:
        money_in = await crm_router.list_transactions(
            account_id=int(ledger["account"]), family="money_in", session=session
        )
        disagreed = await crm_router.list_transactions(
            account_id=int(ledger["account"]),
            family="money_in",
            type="fee",
            session=session,
        )
    assert isinstance(money_in, list) and [r["type"] for r in money_in] == ["deposit"]
    assert disagreed == []


@pytest.mark.asyncio
async def test_there_is_no_running_total_across_the_firm(
    db: dict[str, str], ledger: dict[str, object]
) -> None:
    """A running total over a listing spanning accounts is not a quantity — it would interleave
    two accounts' movements into one meaningless series. Unscoped, the column is absent."""
    async with _authed(db["app"], str(ledger["wos"]), ["contacts:read_all"]) as session:
        rows = await crm_router.list_transactions(session=session)
    assert isinstance(rows, list)
    assert len(rows) == 7  # both accounts
    assert {r["running_total"] for r in rows} == {None}


@pytest.mark.asyncio
async def test_the_row_carries_what_the_detail_panel_shows(
    db: dict[str, str], ledger: dict[str, object]
) -> None:
    """The custodian, the batch reference and the family — what the drawer and the filter bar
    actually print. Main names the custodian as the provider code on the account, not a joined
    display name."""
    async with _authed(db["app"], str(ledger["wos"]), ["contacts:read_all"]) as session:
        rows = await crm_router.list_transactions(
            account_id=int(ledger["account"]), session=session
        )
    assert isinstance(rows, list)
    row = next(r for r in rows if r["type"] == "dividend")
    assert row["custodian"] == "seccl"
    assert row["report_group"] == ledger["group"]
    assert row["family"] == "income"
    assert row["account_name"] == "Peter's Primary"
    assert row["client_name"] == "Peter Vance"


@pytest.mark.asyncio
async def test_the_batch_reference_is_searchable(
    db: dict[str, str], ledger: dict[str, object]
) -> None:
    """The legs of one instruction share a group id at the custodian; finding them is how an
    adviser answers "what did that rebalance actually do?"."""
    async with _authed(db["app"], str(ledger["wos"]), ["contacts:read_all"]) as session:
        rows = await crm_router.list_transactions(q=str(ledger["group"]), session=session)
    assert isinstance(rows, list)
    assert len(rows) == 5  # the five grouped rows, not the void one and not the other account


@pytest.mark.asyncio
async def test_the_paged_envelope_carries_the_totals_and_the_family_facet(
    db: dict[str, str], ledger: dict[str, object]
) -> None:
    """The strip above the table summarises the MATCH set, so it is computed server-side beside
    `total` rather than from the fifty rows the page happens to show."""
    async with _authed(db["app"], str(ledger["wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.list_transactions(
            account_id=int(ledger["account"]), paged=True, limit=2, session=session
        )
    assert isinstance(out, dict)
    assert len(out["items"]) == 2 and out["total"] == 6  # paged, but totalled over all six
    totals = out["totals"]
    assert totals["in"] == 1025.0  # deposit + dividend
    assert totals["out"] == 1112.0  # buy + fee + withdrawal; the cancellation is not money out
    assert totals["fees"] == 12.0
    assert totals["income"] == 25.0
    assert totals["net"] == -87.0  # agrees with the newest running total
    assert sorted(out["facets"]["family"]) == ["charge", "income", "money_in", "money_out", "trade"]
    assert out["capped"] is False


# ── the statement layer ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_row_carries_the_statement_layer_the_drawer_reads(
    db: dict[str, str], ledger: dict[str, object], super_engine: Engine
) -> None:
    """Nine columns the provider derives and the book used to drop at the staging boundary, so a
    question with an answer upstream could not be asked here.

    The one that changes what can be BUILT is `report_group_kind` — the custodian's own statement
    section, which is what makes the headline breakdown a GROUP BY over the ledger rather than a
    second read of a vendor report. The rest are what a ledger row is opened to answer: what it cost
    (the DEALING charge specifically — it used to be folded into one `fee_amount` alongside the
    platform's monthly fee), what a disposal realised, when the entitlement was struck, and what it
    did to the wrapper's allowance.

    Executed against real Postgres rather than asserted on the SELECT string: a column named in the
    projection but absent from the table is a 500 on the listing, which is the failure this catches.
    """
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO platform.transactions "
                "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                "   source_subtype, amount, currency, transaction_date, status, "
                "   fee_amount, dealing_charges, other_charges, fee_type, book_cost, "
                "   realised_gain, trade_date, equalisation_payment, subscription_amount, "
                "   report_group_kind) "
                "VALUES (:t, :a, 'seccl', :sid, 'sell', 'At Best', 700, 'GBP', "
                "        CAST('2026-07-02' AS date), 'settled', "
                "        6.50, 4.50, 2.00, 'Dealing + Other Charge', -500, "
                "        200, CAST('2026-07-01' AS date), 0.52, -700, 'Payments Out')"
            ),
            {
                "t": ledger["tenant"],
                "a": ledger["account"],
                "sid": f"STM_layer_{uuid.uuid4().hex[:8]}",
            },
        )
        c.commit()

    async with _authed(db["app"], str(ledger["wos"]), ["contacts:read_all"]) as session:
        rows = await crm_router.list_transactions(
            account_id=int(ledger["account"]), session=session
        )
    assert isinstance(rows, list)
    row = next(r for r in rows if r["type"] == "sell")

    # The charge, and what it was MADE OF. The total is unchanged (nothing that read it moves), and
    # the split is the new answer: £4.50 of it is what the trade itself cost.
    assert float(row["fee_amount"]) == 6.50
    assert float(row["dealing_charges"]) == 4.50
    assert float(row["other_charges"]) == 2.00
    assert row["fee_type"] == "Dealing + Other Charge"

    # The pooled cost basis and the gain it realised. Both come from the provider's recurrence
    # (share-matched on a GIA), never from arithmetic on this row.
    assert float(row["book_cost"]) == -500
    assert float(row["realised_gain"]) == 200

    # The ex/trade date, distinct from the transaction date — which is the whole reason it exists.
    assert str(row["trade_date"]) == "2026-07-01"
    assert str(row["transaction_date"]) == "2026-07-02"

    # The capital half of a distribution, and what the movement did to the allowance.
    assert float(row["equalisation_payment"]) == 0.52
    assert float(row["subscription_amount"]) == -700

    # The custodian's own statement section, beside the canonical type rather than instead of it.
    assert row["report_group_kind"] == "Payments Out"
    assert row["type"] == "sell", "the canonical type is unaffected by the custodian's own grouping"


@pytest.mark.asyncio
async def test_a_row_the_provider_says_nothing_about_carries_nulls(
    db: dict[str, str], ledger: dict[str, object]
) -> None:
    """SEI and Morgan Lloyd publish none of the statement layer, and a firm mid-resync has rows that
    predate it. NULL has to reach the surface as "not recorded" rather than as a zero: a £0.00
    dealing charge is a claim about the trade, and a nil realised gain is a claim about the
    disposal.

    The `ledger` fixture's own rows were inserted without any of these columns, which is exactly
    the unresynced case."""
    async with _authed(db["app"], str(ledger["wos"]), ["contacts:read_all"]) as session:
        rows = await crm_router.list_transactions(
            account_id=int(ledger["account"]), session=session
        )
    assert isinstance(rows, list)
    row = next(r for r in rows if r["type"] == "dividend")
    for column in (
        "dealing_charges",
        "other_charges",
        "fee_type",
        "book_cost",
        "realised_gain",
        "trade_date",
        "equalisation_payment",
        "subscription_amount",
        "report_group_kind",
    ):
        assert row[column] is None, f"{column} must be absent, not zero"


# ── the headline breakdown ──────────────────────────────────────────────────────────────────────
#
# The ten figures a client's own statement from the custodian shows. Derived from the ledger with
# the vendor's own bucketing rather than read back from its report — which is what makes each one
# CLICKABLE, and which is also how the discrepancy below was found.


#: A real client's statement figures, transcribed from a live account report. Used on BOTH sides of
#: the boundary (`pkgs/ui`'s `breakdown.test.ts` carries the same numbers) because the identity
#: `closing = opening + everything` is the reason these are derived, and it has to hold in the
#: surface as well as in the query.
_REAL_STATEMENT = (
    ("Payments In", 15000.00),
    ("Payments Out", -13927.25),
    ("Income", 75906.35),
    ("Charges", -3.36),
    ("Internal Transfers In", 19839.24),
    ("Internal Transfers Out", -19839.24),
)


def _rows(pairs: tuple[tuple[str, float], ...]) -> list[dict[str, object]]:
    return [
        {"status": "settled", "signed_amount": amount, "report_group_kind": group}
        for group, amount in pairs
    ]


def test_the_breakdown_reproduces_a_real_statement() -> None:
    """£77,156.59 — the closing balance the custodian's own report states for this account.

    This is the whole argument for deriving the figures. The identity holds exactly, AND the
    vendor's
    own STATED totals miss their own closing value by precisely their equalisation double-count (an
    accumulation distribution is listed under both `payments.transactionsIn` and
    `income.transactions`, each with its own share, so summing the gross in either bucket counts the
    whole distribution twice). Reading the report back would have inherited that.
    """
    figures = statement.breakdown(_rows(_REAL_STATEMENT), retained=180.85)
    assert figures["payments_in"] == 15000.00
    assert figures["payments_out"] == -13927.25
    assert figures["income"] == 75906.35
    assert figures["charges"] == -3.36
    assert figures["internal_in"] == 19839.24
    assert figures["internal_out"] == -19839.24
    assert figures["retained"] == 180.85
    assert figures["opening"] == 0.00
    assert figures["closing"] == 77156.59


def test_a_product_transfer_reads_as_a_transfer() -> None:
    """Several of the vendor's group names collapse: a product transfer and a cash transfer are both
    "money from another scheme" as far as a reader is concerned, and the distinction is on the ROW
    rather than in the headline."""
    figures = statement.breakdown(
        _rows((("Product Transfers In", 5000.0), ("Transfers In", 1000.0)))
    )
    assert figures["transfers_in"] == 6000.0


def test_a_trade_contributes_only_its_GAIN_to_the_balance() -> None:
    """A TRADE moves value between an account's cash and its investments, and the account is worth
    the same either side of it. So a disposal's PROCEEDS are not a statement movement — only the
    gain it realised.

    Counted as a movement too, a £700 sale of units that cost £500 would add £900 to the closing
    balance instead of £200. Keyed on the canonical FAMILY rather than the vendor's group, so it
    holds for a custodian that publishes no grouping at all — and so a BUY, which Seccl groups as
    `N/A` and the conform step makes NULL, is not an unclassified movement subtracting the whole
    purchase."""
    figures = statement.breakdown(
        [
            {
                "status": "settled",
                "signed_amount": 700.0,
                "type": "sell",
                "report_group_kind": "Transactions Realised",
                "realised_gain": 200.0,
            },
            # A purchase, with no group at all — the shape that made this wrong.
            {"status": "settled", "signed_amount": -1000.0, "type": "buy"},
        ]
    )
    assert figures["realised"] == 200.0
    assert figures["unclassified"] == 0.0
    assert figures["unclassified_count"] == 0
    assert figures["closing"] == 200.0, "only the gain changed what the account is worth"


def test_a_movement_the_custodian_cannot_bucket_is_counted_and_named() -> None:
    """SEI and Morgan Lloyd publish no grouping at all, and Seccl leaves it NULL where its own
    reference data is ambiguous (`('Payment','Deposit','In')` is both a client payment and a
    dividend tax reclaim). Those movements still moved money.

    Absorbed into a bucket they would be misfiled; DROPPED, the figures would add up to a closing
    balance the account does not have — which is the failure that reads as a complete picture."""
    figures = statement.breakdown(
        _rows((("Payments In", 100.0),)) + [{"status": "settled", "signed_amount": 40.0}]
    )
    assert figures["payments_in"] == 100.0
    assert figures["unclassified"] == 40.0
    assert figures["unclassified_count"] == 1
    assert figures["closing"] == 140.0, "the identity must still hold"


def test_the_breakdown_ignores_a_voided_movement() -> None:
    """The same rule the running total applies. A cancelled withdrawal is listed on the statement
    and contributes to no figure on it."""
    figures = statement.breakdown(
        [
            {"status": "settled", "signed_amount": 100.0, "report_group_kind": "Payments In"},
            {"status": "cancelled", "signed_amount": -50.0, "report_group_kind": "Payments Out"},
        ]
    )
    assert figures["payments_out"] == 0.0
    assert figures["closing"] == 100.0


def test_retained_is_absent_rather_than_nil_when_it_was_not_fetched() -> None:
    """Unrealised gain is a POSITIONS fact, and the transactions endpoint reads neither the lake nor
    the account. So it is omitted, and the strip omits the cell — a client with £180 of unrealised
    gain must not be told they have none."""
    figures = statement.breakdown(_rows(_REAL_STATEMENT))
    assert "retained" not in figures
    # ...and the closing balance is then the transactional one, which is a different (and honest)
    # figure rather than a wrong version of the same one.
    assert figures["closing"] == 76975.74


@pytest.mark.asyncio
async def test_clicking_a_figure_narrows_the_listing_to_its_own_rows(
    db: dict[str, str], ledger: dict[str, object], super_engine: Engine
) -> None:
    """The point of deriving the breakdown: the value that produced a figure is the value that
    filters to it, so the two cannot disagree about what a bucket contains.

    Executed end to end because the mapping is applied in TWO places — the figure (`breakdown`) and
    the filter (`_TRANSACTION_FILTERS['bucket']`) — and they read one function for that reason."""
    marker = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        for group, amount, kind in (
            ("Charges", -12.50, "fee"),
            ("Charges", -3.36, "fee"),
            ("Payments In", 500.0, "deposit"),
        ):
            c.execute(
                text(
                    "INSERT INTO platform.transactions "
                    "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                    "   amount, currency, transaction_date, status, report_group_kind) "
                    "VALUES (:t, :a, 'seccl', :sid, CAST(:ty AS platform.transaction_type), "
                    "        :amt, 'GBP', CAST('2026-07-05' AS date), 'settled', :grp)"
                ),
                {
                    "t": ledger["tenant"],
                    "a": ledger["account"],
                    "sid": f"BKT_{marker}_{group}_{amount}",
                    "ty": kind,
                    "amt": abs(amount),
                    "grp": group,
                },
            )
        c.commit()

    async with _authed(db["app"], str(ledger["wos"]), ["contacts:read_all"]) as session:
        page = await crm_router.list_transactions(
            account_id=int(ledger["account"]), paged=True, session=session
        )
        charges = await crm_router.list_transactions(
            account_id=int(ledger["account"]), bucket="charges", paged=True, session=session
        )
    assert isinstance(page, dict)
    assert isinstance(charges, dict)

    # The figure...
    assert page["breakdown"]["charges"] == -15.86
    # ...and the rows behind it, which sum to exactly that.
    rows = charges["items"]
    assert len(rows) == 2
    assert round(sum(float(r["signed_amount"]) for r in rows), 2) == -15.86

    # THE BREAKDOWN DOES NOT COLLAPSE when a bucket is selected: it is computed over the MATCH set
    # before the bucket filter, because the strip is what a reader clicks to get back out. Narrowed
    # with the table, the way back would be a figure that no longer exists.
    assert charges["breakdown"]["payments_in"] == page["breakdown"]["payments_in"]
    assert charges["breakdown"]["payments_in"] != 0
