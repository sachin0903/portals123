"""Every shipped catalogue entry, EXECUTED against real Postgres under the real envelope.

The two defects this exists to catch were both found by execution and neither is visible in the
SQL, in a type, or in any unit test:

* **Blank.** ``platform.valuations`` has no ``contact_id`` column, so its read policy's only
  client-side branch is ``account_id = ANY (auth.account_scope())``. Minted subject claims carried
  no accounts, so a per-client valuations query returned ZERO ROWS — in a client's statement,
  indistinguishable from a client who holds nothing.
* **Leak.** ``platform.contributions``' read policy is ``tenant_lineage OR account_scope`` with no
  ``auth.can_read_all()`` gate, so the same minted subject claims make the WHOLE FIRM's
  contributions visible. Another client's money, in this client's document.

The two are opposite, which is the point: RLS is not a usable scoping mechanism across these
tables, so each entry scopes itself and RLS is the backstop. That is only worth asserting against
a real database — a mock answers whatever it was told to.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.render import catalogue as cat
from app.render.resolve import _resolve_catalogue

pytestmark = pytest.mark.asyncio


@pytest.fixture
def book(super_engine: Engine) -> dict[str, Any]:
    """Two firms; firm A has two clients, each with an account carrying a valuation, a transaction
    and a contribution, plus a fee package on each firm. Seeded past RLS with a superuser.

    TWO clients in ONE firm is the shape that matters: a cross-TENANT check would pass on the
    tenant predicate alone and prove nothing about the per-client scoping, which is where both
    real defects were.
    """
    s = uuid.uuid4().hex[:8]
    out: dict[str, Any] = {}
    with super_engine.connect() as c:

        def tenant(tag: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:uid, :alias, :name) RETURNING id"
                    ),
                    {"uid": f"cten_{tag}_{s}", "alias": f"cfirm-{tag}-{s}", "name": f"CF {tag}"},
                ).scalar_one()
            )

        ta, tb = tenant("a"), tenant("b")

        def contact(tag: str, tid: int) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (uid, tenant_id, type, name, email) "
                        "VALUES (:u, :t, 'individual', :n, :e) RETURNING id"
                    ),
                    {
                        "u": f"cct_{tag}_{s}",
                        "t": tid,
                        "n": f"Client {tag}",
                        "e": f"{tag}_{s}@c.test",
                    },
                ).scalar_one()
            )

        def account(tag: str, tid: int, cid: int) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO platform.accounts "
                        "  (uid, tenant_id, contact_id, name, status, currency) "
                        "VALUES (:u, :t, :c, :n, 'active', 'GBP') RETURNING id"
                    ),
                    {"u": f"cacc_{tag}_{s}", "t": tid, "c": cid, "n": f"Account {tag}"},
                ).scalar_one()
            )

        mine, other = contact("mine", ta), contact("other", ta)
        theirs = contact("theirs", tb)
        acc_mine, acc_other = account("mine", ta, mine), account("other", ta, other)
        acc_theirs = account("theirs", tb, theirs)

        for tag, tid, cid, aid, amount in (
            ("mine", ta, mine, acc_mine, 1000),
            ("other", ta, other, acc_other, 2000),
            ("theirs", tb, theirs, acc_theirs, 3000),
        ):
            c.execute(
                text(
                    "INSERT INTO platform.valuations "
                    "  (tenant_id, account_id, as_of_date, source_system, market_value) "
                    "VALUES (:t, :a, :d, 'seccl', :v)"
                ),
                {"t": tid, "a": aid, "d": date(2026, 6, 30), "v": amount},
            )
            # A second, EARLIER valuation, so "latest per account" is a claim with something to be
            # wrong about rather than a single row that any query would return.
            c.execute(
                text(
                    "INSERT INTO platform.valuations "
                    "  (tenant_id, account_id, as_of_date, source_system, market_value) "
                    "VALUES (:t, :a, :d, 'seccl', :v)"
                ),
                {"t": tid, "a": aid, "d": date(2026, 1, 31), "v": amount / 2},
            )
            c.execute(
                text(
                    "INSERT INTO platform.transactions "
                    "  (tenant_id, account_id, contact_id, source_system, source_transaction_id, "
                    "   type, amount, transaction_date) "
                    "VALUES (:t, :a, :c, 'seccl', :sid, 'deposit', :v, :d)"
                ),
                {
                    "t": tid,
                    "a": aid,
                    "c": cid,
                    "sid": f"ctx_{tag}_{s}",
                    "v": amount,
                    "d": date(2026, 6, 1),
                },
            )
            c.execute(
                text(
                    "INSERT INTO platform.contributions "
                    "  (uid, tenant_id, account_id, contact_id, category, amount, "
                    "   contribution_date) "
                    "VALUES (:u, :t, :a, :c, 'personal_contribution', :v, :d)"
                ),
                {
                    "u": f"ccon_{tag}_{s}",
                    "t": tid,
                    "a": aid,
                    "c": cid,
                    "v": amount,
                    "d": date(2026, 6, 1),
                },
            )
            c.execute(
                text(
                    "INSERT INTO platform.fee_packages "
                    "  (external_id, tenant_id, name, category, frequency, fee_type, rule) "
                    "VALUES (:u, :t, :n, 'platform', 'ongoing', 'percentage', '{}'::jsonb)"
                ),
                {"u": f"cfee_{tag}_{s}", "t": tid, "n": f"Charge {tag}"},
            )
        c.commit()
    out.update(
        tenant_a=ta,
        tenant_b=tb,
        mine=mine,
        other=other,
        theirs=theirs,
        acc_mine=acc_mine,
        acc_other=acc_other,
    )
    return out


async def _run(session: Any, key: str, book: dict[str, Any], **params: Any) -> list[dict[str, Any]]:
    entry = cat.by_key(key)
    assert entry is not None, key
    run = await _resolve_catalogue(
        session,
        entry,
        params or None,
        book["mine"],
        book["tenant_a"],
        today=date(2026, 9, 4),
    )
    return run.rows


class TestEveryEntryReturnsRows:
    """The BLANK failure: an entry that resolves to nothing reads as "this client holds nothing"."""

    @pytest.mark.parametrize(
        "key", ["book_valuations", "book_transactions", "book_contributions", "book_fees"]
    )
    async def test_it_returns_rows_for_a_client_who_has_them(
        self, service_session: Any, book: dict[str, Any], key: str
    ) -> None:
        rows = await _run(service_session, key, book)
        assert rows, (
            f"{key} returned no rows for a client who has them — in a document that is "
            "indistinguishable from a client who holds nothing"
        )

    async def test_valuations_is_the_latest_per_account_not_the_whole_series(
        self, service_session: Any, book: dict[str, Any]
    ) -> None:
        rows = await _run(service_session, "book_valuations", book)
        assert len(rows) == 1, "one account should give one row, not its whole daily series"
        assert rows[0]["valued_on"] == date(2026, 6, 30)
        assert float(rows[0]["value"]) == 1000.0

    async def test_an_as_at_date_reaches_back_to_the_earlier_valuation(
        self, service_session: Any, book: dict[str, Any]
    ) -> None:
        # The date parameter is the feature; this proves it changes the answer rather than being a
        # control that does nothing.
        rows = await _run(service_session, "book_valuations", book, as_at="2026-03-31")
        assert len(rows) == 1
        assert rows[0]["valued_on"] == date(2026, 1, 31)
        assert float(rows[0]["value"]) == 500.0


class TestNoEntryLeaksAnotherClient:
    """The LEAK failure: `platform.contributions` shows the whole firm under minted subject claims,
    so the entry's own WHERE is the only boundary. Proved per entry, not argued once."""

    @pytest.mark.parametrize("key", ["book_valuations", "book_transactions", "book_contributions"])
    async def test_only_the_subjects_own_account_appears(
        self, service_session: Any, book: dict[str, Any], key: str
    ) -> None:
        rows = await _run(service_session, key, book)
        assert rows
        refs = {r["account_reference"] for r in rows}
        own = await service_session.execute(
            text("SELECT uid FROM platform.accounts WHERE id = :i"), {"i": book["acc_mine"]}
        )
        other = await service_session.execute(
            text("SELECT uid FROM platform.accounts WHERE id = :i"), {"i": book["acc_other"]}
        )
        assert refs == {own.scalar_one()}, f"{key} returned rows outside the subject's own account"
        assert other.scalar_one() not in refs

    async def test_the_amounts_are_the_subjects_own(
        self, service_session: Any, book: dict[str, Any]
    ) -> None:
        # The other client's figures are deliberately 2x, so a leak shows up as a WRONG NUMBER and
        # not merely as an extra row a count would have to notice.
        rows = await _run(service_session, "book_contributions", book)
        assert [float(r["amount"]) for r in rows] == [1000.0]


class TestNoEntryLeaksAnotherFirm:
    @pytest.mark.parametrize(
        "key", ["book_valuations", "book_transactions", "book_contributions", "book_fees"]
    )
    async def test_a_second_firms_rows_never_appear(
        self, service_session: Any, book: dict[str, Any], key: str
    ) -> None:
        rows = await _run(service_session, key, book)
        blob = repr(rows)
        assert "theirs" not in blob, f"{key} leaked a row belonging to another firm"


class TestTheEnvelopeStillApplies:
    async def test_the_query_runs_as_authenticated_not_service_role(
        self, service_session: Any, book: dict[str, Any]
    ) -> None:
        # The savepoint restores the ambient role afterwards, so the observable proof that the
        # downgrade happened is that the entry saw only its own client's rows above. Here we check
        # the session is handed back intact — a leaked `SET LOCAL ROLE` would break every later
        # write in the render job.
        await _run(service_session, "book_valuations", book)
        role = (await service_session.execute(text("SELECT current_user"))).scalar_one()
        assert role == "service_role"

    async def test_a_read_only_transaction_is_restored_too(
        self, service_session: Any, book: dict[str, Any]
    ) -> None:
        await _run(service_session, "book_valuations", book)
        ro = (await service_session.execute(text("SHOW transaction_read_only"))).scalar_one()
        assert ro == "off"


class TestAnEntryNeedsNoDatabaseRow:
    """The whole point of code-side entries: a firm can use one the moment it ships.

    A template names a dataset by CODE, and `_dataset_source` is the only database touch on the
    render path — so an entry resolves with nothing in `documents.datasets` at all. Nothing to
    seed, nothing to clone on tenant creation, and no firm left on an older copy of a query.
    """

    async def test_the_reporting_resolver_finds_a_shipped_entry_with_no_row(
        self, service_session: Any, book: dict[str, Any]
    ) -> None:
        from app.render.resolve import resolve_datasets_reporting

        assert (
            await service_session.execute(
                text("SELECT count(*) FROM documents.datasets WHERE tenant_id = :t"),
                {"t": book["tenant_a"]},
            )
        ).scalar_one() == 0

        res = await resolve_datasets_reporting(
            service_session,
            book["tenant_a"],
            ["book_valuations"],
            subject_id=book["mine"],
            today=date(2026, 9, 4),
        )
        assert not res.blocking, [p.detail for p in res.blocking]
        assert res.rows["book_valuations"], "a shipped entry resolved to nothing"

    async def test_an_unknown_code_is_still_reported_missing(
        self, service_session: Any, book: dict[str, Any]
    ) -> None:
        # The catalogue fallback must not turn every typo into a silent empty table.
        from app.render.resolve import resolve_datasets_reporting

        res = await resolve_datasets_reporting(
            service_session, book["tenant_a"], ["book.valuationz"], subject_id=book["mine"]
        )
        assert [p.reason for p in res.blocking] == ["missing"]

    async def test_a_per_client_entry_with_no_client_is_refused_not_run_firm_wide(
        self, service_session: Any, book: dict[str, Any]
    ) -> None:
        # Without a subject there is no client to scope to. Returning the firm's rows under a
        # per-client name is the leak; an empty table is the other failure. Refuse, and say so.
        from app.render.resolve import resolve_datasets_reporting

        res = await resolve_datasets_reporting(
            service_session, book["tenant_a"], ["book_valuations"], subject_id=None
        )
        assert [p.reason for p in res.blocking] == ["missing_subject"]
        assert res.rows["book_valuations"] == []
