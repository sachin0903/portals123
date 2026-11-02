"""Reading the outbound-email ledger — scoping, filters, facets and the deny list.

Against real Postgres, because every claim here is a claim about SQL. The one that matters most is
the first: ``firm_session`` runs as ``service_role``, so RLS is bypassed and the ``tenant_id``
predicate in ``app/comms/stream.py`` is the ONLY thing keeping one firm out of another's ledger.
A unit test with a fake session would prove nothing about it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text

from app.comms import stream

DENY = ("aml.", "action.aml-")


async def _tenant(session: Any, tag: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"ten_{tag}", "a": f"firm-{tag}", "n": f"Firm {tag}"},
            )
        ).scalar_one()
    )


async def _message(
    session: Any,
    *,
    uid: str,
    tenant: int,
    status: str = "sent",
    event_type: str | None = None,
    template_code: str | None = None,
    category: str | None = None,
    subject: str = "A message",
    contact: int | None = None,
    address: str = "someone@example.test",
) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO comms.messages (uid, tenant_id, recipient_kind, "
                    "  recipient_contact_id, to_address, to_domain, subject, status, event_type, "
                    "  template_code, category, html) "
                    "VALUES (:uid, :t, :kind, :con, :addr, 'example.test', :subj, :st, :et, :tpl, "
                    "  :cat, '<p>body</p>') RETURNING id"
                ),
                {
                    "uid": uid,
                    "t": tenant,
                    "kind": "contact" if contact is not None else "external",
                    "con": contact,
                    "addr": address,
                    "subj": subject,
                    "st": status,
                    "et": event_type,
                    "tpl": template_code,
                    "cat": category,
                },
            )
        ).scalar_one()
    )


@pytest_asyncio.fixture
async def seed(service_session: Any) -> Any:
    """Two firms with their own mail, plus a client to attribute one message to.

    Cleans up in child-before-parent order: `crm.contacts` carries an `audit.record()` trigger that
    writes an audit row keyed by tenant_id, so a cascading tenant delete fails on a foreign key
    (the same trap `test_aml_rls.py` and `test_comms_rls.py` both record).
    """
    s = service_session
    import uuid

    n = uuid.uuid4().hex[:6]
    a = await _tenant(s, f"a{n}")
    b = await _tenant(s, f"b{n}")
    contact = int(
        (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, "
                    "  service_type) VALUES (:t, :u, 'individual', 'Ada', 'Okafor', 'advised') "
                    "RETURNING id"
                ),
                {"t": a, "u": f"con_{n}"},
            )
        ).scalar_one()
    )
    ids = {
        "invite": await _message(
            s,
            uid=f"m1{n}".ljust(12, "0"),
            tenant=a,
            status="sent",
            template_code="invite",
            subject="Welcome to Acme",
        ),
        "doc": await _message(
            s,
            uid=f"m2{n}".ljust(12, "0"),
            tenant=a,
            status="delivered",
            event_type="document.sent",
            template_code="document_sent",
            category="documents",
            subject="Your suitability report",
            contact=contact,
            address="ada@example.test",
        ),
        "bounced": await _message(
            s,
            uid=f"m3{n}".ljust(12, "0"),
            tenant=a,
            status="bounced",
            event_type="task.created",
            template_code="task_notice",
            category="tasks",
        ),
        "screening": await _message(
            s,
            uid=f"m4{n}".ljust(12, "0"),
            tenant=a,
            status="sent",
            event_type="aml.screen.completed",
            subject="Screening outcome",
        ),
        "other_firm": await _message(
            s, uid=f"m5{n}".ljust(12, "0"), tenant=b, status="sent", subject="Not yours"
        ),
    }
    uids = {k: f"m{i + 1}{n}".ljust(12, "0") for i, k in enumerate(ids)}
    yield {"a": a, "b": b, "contact": contact, "contact_uid": f"con_{n}", "ids": ids, "uids": uids}

    for stmt in (
        "DELETE FROM comms.messages WHERE tenant_id = ANY(:ids)",
        "DELETE FROM crm.contacts WHERE tenant_id = ANY(:ids)",
        "DELETE FROM auth.tenants WHERE id = ANY(:ids)",
    ):
        await s.execute(text(stmt), {"ids": [a, b]})


async def _uids(session: Any, tenant: int, **kw: Any) -> list[str]:
    page = await stream.fetch_messages(session, tenant_id=tenant, **kw)
    return [row["uid"] for row in page["items"]]


# ---------------------------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------------------------


async def test_each_firm_reads_only_its_own_mail(service_session: Any, seed: dict) -> None:
    """The claim the whole surface rests on, checked from BOTH sides — a predicate that leaked one
    way and not the other would pass a single-direction test.

    `firm_session` runs as service_role, so RLS is not doing this: the `tenant_id` predicate in
    `stream.py` is the only thing keeping one firm out of another's mail.
    """
    got_a = await _uids(service_session, seed["a"])
    assert seed["uids"]["invite"] in got_a
    assert seed["uids"]["other_firm"] not in got_a
    assert await _uids(service_session, seed["b"]) == [seed["uids"]["other_firm"]]


async def test_a_message_uid_from_another_firm_is_not_readable(
    service_session: Any, seed: dict
) -> None:
    """The detail pane's deep link must not become a way round the scope."""
    assert (
        await stream.fetch_message(service_session, tenant_id=seed["b"], uid=seed["uids"]["invite"])
        is None
    )


# ---------------------------------------------------------------------------------------------
# The AML rail
# ---------------------------------------------------------------------------------------------


async def test_the_aml_family_is_withheld_from_rows_and_facets(
    service_session: Any, seed: dict
) -> None:
    got = await _uids(service_session, seed["a"], exclude_event_prefixes=DENY)
    assert seed["uids"]["screening"] not in got

    facets = await stream.message_facets(
        service_session, tenant_id=seed["a"], exclude_event_prefixes=DENY
    )
    assert "aml.screen.completed" not in facets["event_types"], (
        "an OPTION is a disclosure too — offering the type in a dropdown tells a firm that "
        "screening happened even while the rows are withheld"
    )


async def test_a_withheld_message_is_not_reachable_by_uid(service_session: Any, seed: dict) -> None:
    assert (
        await stream.fetch_message(
            service_session,
            tenant_id=seed["a"],
            uid=seed["uids"]["screening"],
            exclude_event_prefixes=DENY,
        )
        is None
    )


async def test_the_deny_list_keeps_transactional_mail(service_session: Any, seed: dict) -> None:
    """The subtle one. A transactional send (an invite) has NO event_type, and `NOT LIKE` on NULL
    is NULL — so a deny clause written the obvious way drops every invite, password reset and test
    send from the feed while looking like it only hides screening."""
    got = await _uids(service_session, seed["a"], exclude_event_prefixes=DENY)
    assert seed["uids"]["invite"] in got


# ---------------------------------------------------------------------------------------------
# Filters — each narrows, none widens
# ---------------------------------------------------------------------------------------------


async def test_each_filter_narrows_to_what_it_names(service_session: Any, seed: dict) -> None:
    """One seeded firm, one assertion per filter. A wrong predicate shows the same way in all of
    them — the wrong rows — so separate tests bought separate names and nothing else.

    `event_type` matches by PREFIX, so a family and an exact type are the same filter (an exact
    type is its own prefix). Same wire format as the action stream, so one toolbar builder serves
    both.
    """
    doc = seed["uids"]["doc"]
    assert set(await _uids(service_session, seed["a"], status="delivered,bounced")) == {
        doc,
        seed["uids"]["bounced"],
    }
    assert await _uids(service_session, seed["a"], event_type="document.") == [doc]
    assert await _uids(service_session, seed["a"], event_type="document.sent") == [doc]
    assert await _uids(service_session, seed["a"], contact=seed["contact"]) == [doc]
    # Search spans the subject, the address and the client's name.
    for term in ("suitability", "ada@example", "Okafor"):
        assert await _uids(service_session, seed["a"], query=term) == [doc]
    # And they COMPOSE by narrowing rather than widening.
    assert await _uids(service_session, seed["a"], status="delivered", query="suitability") == [doc]
    assert await _uids(service_session, seed["a"], status="bounced", query="suitability") == []


async def test_a_search_wildcard_is_escaped_not_honoured(service_session: Any, seed: dict) -> None:
    """Its own test because it is the one with a security edge: a reader typing `%` means a literal
    percent sign, and an unescaped one turns any search into "match everything"."""
    assert await _uids(service_session, seed["a"], query="%") == []


# ---------------------------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------------------------


async def test_the_page_reports_more_without_returning_it(service_session: Any, seed: dict) -> None:
    page = await stream.fetch_messages(service_session, tenant_id=seed["a"], limit=2)
    assert len(page["items"]) == 2
    assert page["has_more"] is True


async def test_before_walks_backwards_and_after_asks_what_is_new(
    service_session: Any, seed: dict
) -> None:
    first = await stream.fetch_messages(service_session, tenant_id=seed["a"], limit=2)
    oldest = first["items"][-1]["id"]
    older = await stream.fetch_messages(
        service_session, tenant_id=seed["a"], limit=10, before=oldest
    )
    assert all(row["id"] < oldest for row in older["items"])

    newest = first["items"][0]["id"]
    newer = await stream.fetch_messages(
        service_session, tenant_id=seed["a"], limit=10, after=newest
    )
    assert newer["items"] == []


async def test_a_row_does_not_carry_the_body(service_session: Any, seed: dict) -> None:
    """A page of 25 rendered emails is megabytes. The body is read one at a time, by the detail."""
    page = await stream.fetch_messages(service_session, tenant_id=seed["a"], limit=5)
    assert "html" not in page["items"][0]
    assert "request" not in page["items"][0]


# ---------------------------------------------------------------------------------------------
# Facets and the detail
# ---------------------------------------------------------------------------------------------


async def test_facets_offer_what_this_firm_actually_sends(service_session: Any, seed: dict) -> None:
    """The filter vocabulary is scoped like the rows: a firm is offered its own templates and
    statuses, never another's — otherwise the dropdown itself leaks what a competitor sends."""
    facets = await stream.message_facets(service_session, tenant_id=seed["a"])
    assert set(facets["template_codes"]) == {"invite", "document_sent", "task_notice"}
    assert set(facets["categories"]) == {"documents", "tasks"}

    other = await stream.message_facets(service_session, tenant_id=seed["b"])
    assert other["template_codes"] == []
    assert other["statuses"] == ["sent"]


async def test_the_detail_carries_the_body_and_its_timeline(
    service_session: Any, seed: dict
) -> None:
    await service_session.execute(
        text(
            "INSERT INTO comms.message_events (message_id, tenant_id, event_type, "
            "  provider_event_id) VALUES (:m, :t, 'email.delivered', 'svix_1')"
        ),
        {"m": seed["ids"]["doc"], "t": seed["a"]},
    )
    message = await stream.fetch_message(
        service_session, tenant_id=seed["a"], uid=seed["uids"]["doc"]
    )
    assert message is not None
    assert message["html"] == "<p>body</p>"
    assert [e["event_type"] for e in message["events"]] == ["email.delivered"]
    assert message["recipient_name"] == "Ada Okafor"
    # A row that NAMES a client also has to link to them, and a client record is addressed by its
    # uid — the serial `recipient_contact_id` is internal and the routes that once accepted one are
    # gone, so a link built on it resolves to nothing. Without this column the detail pane has no
    # uid to build the href from and silently renders the name unlinked.
    assert message["recipient_contact_uid"] == seed["contact_uid"]


async def test_the_envelope_is_projected_for_the_feed_and_the_detail(
    service_session: Any, seed: dict
) -> None:
    """From and Reply-To come out of the STORED WIRE REQUEST, on both reads.

    Both, because the question they answer — "who did this appear to come from" — belongs on the row
    as well as in the pane: on this platform the sender is not a constant (`resolve_sender` sends as
    the firm, or as the adviser for a manual send), so a feed showing only the recipient cannot
    answer it without opening every message.

    `request` is the only source, and there are no `from_address`/`reply_to` COLUMNS — nor should
    there be: the request already holds the exact JSON that was posted, so a column would be a
    second copy of the same fact that could disagree with it. This test is what notices if those
    JSON keys ever move.
    """
    await service_session.execute(
        text("UPDATE comms.messages SET request = CAST(:r AS jsonb) WHERE uid = :u"),
        {
            "r": json.dumps(
                {
                    "method": "POST",
                    "body": {
                        "from": '"Ridley & Co" <no-reply@ridley.test>',
                        "reply_to": "sarah@ridley.test",
                        "to": ["ada@example.test"],
                    },
                }
            ),
            "u": seed["uids"]["doc"],
        },
    )

    detail = await stream.fetch_message(
        service_session, tenant_id=seed["a"], uid=seed["uids"]["doc"]
    )
    assert detail is not None
    assert detail["from_address"] == '"Ridley & Co" <no-reply@ridley.test>'
    assert detail["reply_to"] == "sarah@ridley.test"

    page = await stream.fetch_messages(service_session, tenant_id=seed["a"], limit=50)
    row = next(r for r in page["items"] if r["uid"] == seed["uids"]["doc"])
    assert row["from_address"] == '"Ridley & Co" <no-reply@ridley.test>'
    assert row["reply_to"] == "sarah@ridley.test"


async def test_a_row_with_no_request_reports_no_envelope_rather_than_failing(
    service_session: Any, seed: dict
) -> None:
    """A row written before `0118_comms_request` has none, and the reader must say so rather than
    raise or invent — the pane omits the field, the feed prints "Not recorded"."""
    await service_session.execute(
        text("UPDATE comms.messages SET request = NULL WHERE uid = :u"),
        {"u": seed["uids"]["bounced"]},
    )
    page = await stream.fetch_messages(service_session, tenant_id=seed["a"], limit=50)
    row = next(r for r in page["items"] if r["uid"] == seed["uids"]["bounced"])
    assert row["from_address"] is None
    assert row["reply_to"] is None


async def test_the_detail_carries_the_wire_request(service_session: Any, seed: dict) -> None:
    """Raw is the POST, not a second dump of the preview HTML. The listing omits it for the same
    reason it omits `html`."""
    await service_session.execute(
        text("UPDATE comms.messages SET request = CAST(:r AS jsonb) WHERE uid = :u"),
        {
            "r": json.dumps(
                {
                    "method": "POST",
                    "body": {
                        "from": "Firm <noreply@example.test>",
                        "to": ["ada@example.test"],
                        "subject": "Your suitability report",
                        "html": "<p>wire</p>",
                    },
                }
            ),
            "u": seed["uids"]["doc"],
        },
    )
    message = await stream.fetch_message(
        service_session, tenant_id=seed["a"], uid=seed["uids"]["doc"]
    )
    assert message is not None
    assert message["request"]["body"]["html"] == "<p>wire</p>"
    assert message["request"]["body"]["from"] == "Firm <noreply@example.test>"


async def test_the_detail_withholds_the_providers_raw_payload(
    service_session: Any, seed: dict
) -> None:
    """It repeats what the columns already carry and can hold recipient detail the feed has no
    reason to re-publish."""
    message = await stream.fetch_message(
        service_session, tenant_id=seed["a"], uid=seed["uids"]["doc"]
    )
    assert message is not None
    assert all("payload" not in event for event in message["events"])


@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-99"])
async def test_an_unparseable_date_is_refused_rather_than_ignored(
    service_session: Any, seed: dict, bad: str
) -> None:
    """Silently dropping a filter it could not read would show a reader MORE than they asked for
    and look like the filter had been applied."""
    # The specific refusal, not a blind Exception: `parse_instant` answers 422, and asserting on
    # `Exception` would pass just as happily on a typo in the call below.
    with pytest.raises(HTTPException) as caught:
        await stream.fetch_messages(service_session, tenant_id=seed["a"], since=bad)
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_a_chosen_order_sorts_the_whole_ledger_and_drops_the_keyset_cursor(
    service_session: Any, seed: dict
) -> None:
    """The headers were inert: the rows are ONE page, so sorting in the browser would order the
    page and present it as the book."""
    a = seed["a"]
    mine = {"Zebra", "Apple", "Mango"}
    for uid, subject in (("cs1zzzzzzzzz", "Zebra"), ("cs2aaaaaaaaa", "Apple"), ("cs3mmm", "Mango")):
        await _message(service_session, uid=uid, tenant=a, subject=subject)

    async def subjects(**kw: Any) -> list[str]:
        page = await stream.fetch_messages(service_session, tenant_id=a, limit=50, **kw)
        return [row["subject"] for row in page["items"] if row["subject"] in mine]

    assert await subjects(sort="subject", direction="asc") == ["Apple", "Mango", "Zebra"]

    # A cursor is a position in ONE order, so `before` is ignored once a sort is chosen: it would
    # otherwise drop rows that sort after the cursor row but were created before it.
    newest = (await stream.fetch_messages(service_session, tenant_id=a, limit=1))["items"][0]
    assert await subjects(sort="subject", direction="asc", before=int(newest["id"])) == [
        "Apple",
        "Mango",
        "Zebra",
    ]

    # `sort` is interpolated into an ORDER BY, so an undeclared key is ignored, not refused.
    default = await stream.fetch_messages(service_session, tenant_id=a, limit=50)
    junk = await stream.fetch_messages(service_session, tenant_id=a, limit=50, sort="(SELECT 1)")
    assert [row["uid"] for row in junk["items"]] == [row["uid"] for row in default["items"]]
