"""The firm's member directory: search, role filter, linked client records, and a window.

The Team and Users pages fetched every identity the firm has and searched them in the browser.
Staff is a small number; the CLIENT profiles are not — one per portal-enabled client — and the two
pages are meant to behave alike, so both narrow in SQL now.

Two things here are less obvious than the paging:

- **The window is applied in Python, over the assembled people.** The query returns one row per
  (person, portal) and the assembly folds them together, so a SQL ``LIMIT`` would cut the row set
  mid-person: someone holding two portals could arrive with one missing, or be counted twice. The
  filtering that matters still happens in SQL, so what is windowed is a list already narrowed to
  the answer.
- **``contacts`` is a list, not a column.** One login legitimately acts for several parties
  (authorised person, power of attorney), which is exactly why ``crm.contact_users`` is a table —
  and why the Users page shows a client's name rather than only an email.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.provisioning import members_with_portals


async def _tenant(session: AsyncSession) -> int:
    suffix = uuid.uuid4().hex[:8]
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"mem_{suffix}", "a": f"mem-{suffix}", "n": f"Members {suffix}"},
            )
        ).scalar_one()
    )


async def _user(session: AsyncSession, tenant_id: int, email: str, name: str) -> str:
    """`auth.users.name` is GENERATED from first_name/last_name, so the parts are what a seed
    writes — inserting the whole name is refused outright."""
    first, _, last = name.partition(" ")
    return str(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.users (tenant_id, email, first_name, last_name, enabled, "
                    "type) VALUES (:t, :e, :f, :l, true, ARRAY['staff']::text[]) RETURNING id"
                ),
                {"t": tenant_id, "e": email, "f": first, "l": last or None},
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
class TestMemberListing:
    async def test_without_a_limit_the_shape_is_the_bare_list(
        self, service_session: AsyncSession
    ) -> None:
        # The contract every existing caller reads — the invite pickers, the adviser selector.
        tid = await _tenant(service_session)
        await _user(service_session, tid, "amy@x.test", "Amy Adviser")
        out = await members_with_portals(service_session, tid)
        assert isinstance(out, list)
        assert [m["email"] for m in out] == ["amy@x.test"]

    async def test_with_a_limit_the_shape_is_an_envelope(
        self, service_session: AsyncSession
    ) -> None:
        tid = await _tenant(service_session)
        for n in range(3):
            await _user(service_session, tid, f"p{n}@x.test", f"Person {n}")
        out = await members_with_portals(service_session, tid, limit=2)
        assert isinstance(out, dict)
        assert len(out["items"]) == 2
        assert out["total"] == 3

    async def test_the_total_counts_the_firm_not_the_window(
        self, service_session: AsyncSession
    ) -> None:
        # What lets the page say "20 of 240". Counting the window makes every listing exactly one
        # window long, which reads as "that is everyone".
        tid = await _tenant(service_session)
        for n in range(5):
            await _user(service_session, tid, f"q{n}@x.test", f"Person {n}")
        out = await members_with_portals(service_session, tid, limit=2, offset=2)
        assert len(out["items"]) == 2
        assert out["total"] == 5

    async def test_search_matches_name_and_email(self, service_session: AsyncSession) -> None:
        tid = await _tenant(service_session)
        await _user(service_session, tid, "sachin@x.test", "Sachin Sharma")
        await _user(service_session, tid, "craig@x.test", "Craig Currie")
        by_name = await members_with_portals(service_session, tid, q="sachin sh", limit=50)
        assert [m["email"] for m in by_name["items"]] == ["sachin@x.test"]
        by_email = await members_with_portals(service_session, tid, q="CRAIG@", limit=50)
        assert [m["email"] for m in by_email["items"]] == ["craig@x.test"]

    async def test_search_narrows_the_total_too(self, service_session: AsyncSession) -> None:
        # The count is what the pager reads. Left at the unsearched size it would offer pages that
        # do not exist.
        tid = await _tenant(service_session)
        await _user(service_session, tid, "one@x.test", "Findable One")
        await _user(service_session, tid, "two@x.test", "Someone Else")
        out = await members_with_portals(service_session, tid, q="findable", limit=50)
        assert out["total"] == 1

    async def test_a_person_with_no_portal_still_appears(
        self, service_session: AsyncSession
    ) -> None:
        # The WorkOS sync imports people and deliberately invents no portal row. Dropping them here
        # would hide the very people the import exists to let an operator grant access to.
        tid = await _tenant(service_session)
        await _user(service_session, tid, "imported@x.test", "Imported Person")
        out = await members_with_portals(service_session, tid, limit=50)
        assert [m["email"] for m in out["items"]] == ["imported@x.test"]
        assert out["items"][0]["portals"] == []

    async def test_linked_client_records_come_back_as_a_list(
        self, service_session: AsyncSession
    ) -> None:
        # One login can act for several parties, so this is a list of names — not one column, and
        # not an email standing in for a person.
        tid = await _tenant(service_session)
        user_id = await _user(service_session, tid, "poa@x.test", "Pat Attorney")
        for first, surname in (("Max", "Oakden"), ("Rita", "Oakden")):
            contact_id = (
                await service_session.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                        "VALUES (:t, :u, 'individual', :f, :s) RETURNING id"
                    ),
                    {"t": tid, "u": uuid.uuid4().hex[:12], "f": first, "s": surname},
                )
            ).scalar_one()
            await service_session.execute(
                text(
                    "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                    "VALUES (:t, :c, :u, 'self')"
                ),
                {"t": tid, "c": contact_id, "u": user_id},
            )
        out = await members_with_portals(service_session, tid, limit=50)
        names = sorted(c["name"] for c in out["items"][0]["contacts"])
        assert names == ["Max Oakden", "Rita Oakden"]

    async def test_a_login_linked_to_nobody_carries_an_empty_list(
        self, service_session: AsyncSession
    ) -> None:
        # Never null: the column feeds a `{#each}`, and a null there renders nothing while throwing
        # on any caller that maps it.
        tid = await _tenant(service_session)
        await _user(service_session, tid, "solo@x.test", "Solo Staffer")
        out = await members_with_portals(service_session, tid, limit=50)
        assert out["items"][0]["contacts"] == []

    async def test_the_composed_name_is_used_not_the_bare_one(
        self, service_session: AsyncSession
    ) -> None:
        # `crm.contacts.name` is nullable and an individual's name lives in first_name/surname —
        # the same trap that made the documents Client column render "—" for every individual.
        tid = await _tenant(service_session)
        user_id = await _user(service_session, tid, "ind@x.test", "Ind Staffer")
        contact_id = (
            await service_session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, :u, 'individual', 'Simple', 'Accumulation') RETURNING id"
                ),
                {"t": tid, "u": uuid.uuid4().hex[:12]},
            )
        ).scalar_one()
        await service_session.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                "VALUES (:t, :c, :u, 'self')"
            ),
            {"t": tid, "c": contact_id, "u": user_id},
        )
        out = await members_with_portals(service_session, tid, limit=50)
        assert out["items"][0]["contacts"][0]["name"] == "Simple Accumulation"
