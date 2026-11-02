"""The library's server-side filters and facets on GET /documents.

These moved SERVER-side, which is the point of the suite. Filtering client-side over a capped page
could only ever narrow what had already been fetched: with more documents than the cap, a filter
silently reported "no matches" for a client whose documents were simply on the wrong side of the
cut, and the row counter counted the page rather than the book.

Three things are pinned here that a hand-check would not catch:

  * the CLIENT is filtered by UID, never by display name (two clients share a name more often than
    anyone expects, and the estate addresses a client by uid everywhere else);
  * ``total`` counts MATCHES rather than the size of the library, or the pager offers pages that do
    not exist;
  * a facet is computed with its OWN filter lifted, or picking a value leaves that value as the
    only option and the filter becomes a one-way door (app/listing.py's rule 3).
"""

# ruff: noqa: F811 -- pytest fixtures are REQUESTED by parameter name, so importing `firm` /
# `session_factory` from the documents suite and then naming them in a signature reads to ruff
# as a redefinition. It is how fixture reuse works; the alternative is a third copy of a tenant
# fixture that already exists twice.

from datetime import datetime
from io import BytesIO
from typing import Any

import pytest
from fastapi import UploadFile
from sqlalchemy import text

from app import listing, storage
from app.auth import Principal
from app.routers import documents as docs

from .test_documents_api import (  # noqa: F401 - fixtures are used by injection
    PDF,
    firm,
    session_factory,
)

pytestmark = pytest.mark.anyio


def _principal() -> Principal:
    return Principal(
        user_id="x",
        organisation_id="x",
        role="adviser",
        permissions=["documents:write", "documents:send", "contacts:read_all"],
    )


@pytest.fixture
async def book(session_factory, firm, super_engine, monkeypatch):
    """Two clients who SHARE A NAME, and four documents across them.

    The shared name is deliberate: it is the case a name-keyed filter cannot represent, and the
    reason ``client`` is keyed on the uid.
    """

    async def _put(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        return None

    monkeypatch.setattr(storage, "put_document", _put)

    ids = {}
    with super_engine.connect() as c:
        for tag in ("one", "two"):
            ids[tag] = int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                        "VALUES (:t, :uid, 'individual', 'Margaret', 'Okonjo') RETURNING id"
                    ),
                    {"t": firm["tenant_id"], "uid": f"dcf_{tag}_{firm['org']}"},
                ).scalar_one()
            )
        c.commit()

    async def upload(name: str, document_type: str, contact_id: int | None) -> str:
        async with session_factory() as session:
            result = await docs.upload_document(
                file=[UploadFile(filename=name, file=BytesIO(PDF), size=len(PDF))],
                document_type=document_type,
                name=name,
                subject_contact_id=contact_id,
                principal=_principal(),
                session=session,
            )
        return result.documents[0].uid

    return {
        "one": ids["one"],
        "two": ids["two"],
        "uid_one": f"dcf_one_{firm['org']}",
        "uid_two": f"dcf_two_{firm['org']}",
        "statement_one": await upload("Statement one.pdf", "statement", ids["one"]),
        "report_one": await upload("Report one.pdf", "report", ids["one"]),
        "statement_two": await upload("Statement two.pdf", "statement", ids["two"]),
        "unfiled": await upload("Unfiled.pdf", "report", None),
    }


async def _list(session_factory, **kwargs: Any) -> dict[str, Any]:
    async with session_factory() as session:
        result = await docs.list_documents(
            principal=_principal(), session=session, limit=25, **kwargs
        )
    assert isinstance(result, dict)
    return result


def _uids(page: dict[str, Any]) -> set[str]:
    return {d.uid for d in page["items"]}


async def test_the_client_filter_keys_on_the_uid_not_the_name(session_factory, book) -> None:
    """Two clients called Margaret Okonjo; filtering to one must not return the other's documents.

    A name-keyed filter cannot express this at all — it would return all three and read as correct,
    which is precisely how it would ship.
    """
    page = await _list(session_factory, client=book["uid_one"])
    assert _uids(page) == {book["statement_one"], book["report_one"]}
    assert page["total"] == 2

    other = await _list(session_factory, client=book["uid_two"])
    assert _uids(other) == {book["statement_two"]}


async def test_search_matches_the_linked_clients_name_as_well_as_the_documents(
    session_factory, book
) -> None:
    """FB-20260923-04. A generated document is named for its kind ("Statement one"), so a search
    for the client it belongs to found nothing. The client's name is searched beside the name; a
    document filed under nobody can only be found by its own."""
    page = await _list(session_factory, q="okonjo")
    assert _uids(page) == {book["statement_one"], book["report_one"], book["statement_two"]}
    assert page["total"] == 3
    # The client facet's labels are built over the same search, so both namesakes are offered.
    assert set(page["facet_labels"]["client"]) == {book["uid_one"], book["uid_two"]}

    by_name = await _list(session_factory, q="unfiled")
    assert _uids(by_name) == {book["unfiled"]}


async def test_type_and_author_narrow_and_compose(session_factory, book) -> None:
    by_type = await _list(session_factory, type="statement")
    assert _uids(by_type) == {book["statement_one"], book["statement_two"]}

    # Both filters at once: the intersection, not the union.
    both = await _list(session_factory, type="statement", client=book["uid_one"])
    assert _uids(both) == {book["statement_one"]}

    # An author nobody matches keeps nothing — the half that proves the filter is applied at all.
    assert (await _list(session_factory, author="Nobody At All"))["total"] == 0


async def test_q_searches_the_name_only(session_factory, book) -> None:
    page = await _list(session_factory, q="statement")
    assert _uids(page) == {book["statement_one"], book["statement_two"]}
    # A TYPE that is not in the name does not match: the type has its own filter, so folding it into
    # the search would make a search for "report" also return everything merely typed as one.
    named = await _list(session_factory, q="Unfiled")
    assert _uids(named) == {book["unfiled"]}


async def test_total_counts_matches_not_the_library(session_factory, book) -> None:
    """``total`` drives the pager. Counted before filtering — which is what the SQL ``count(*)``
    this replaced did — it would offer pages of a book the filter had already excluded."""
    page = await _list(session_factory, type="statement")
    assert page["total"] == 2
    assert len(page["items"]) == 2
    assert (await _list(session_factory))["total"] == 4


async def test_a_facet_keeps_its_own_options_after_it_is_picked(session_factory, book) -> None:
    """app/listing.py rule 3, and what makes a filter reversible.

    With ``type=statement`` applied, the TYPE facet must still offer ``report`` — otherwise the only
    option left is the one already chosen and there is no way back to the full list except clearing
    the filter by hand. Every OTHER facet is narrowed as normal.
    """
    page = await _list(session_factory, type="statement")
    assert set(page["facets"]["type"]) == {"statement", "report"}
    # The client facet IS narrowed by the active type filter — only clients holding a statement.
    assert set(page["facets"]["client"]) == {book["uid_one"], book["uid_two"]}

    filed = await _list(session_factory, client=book["uid_one"])
    assert set(filed["facets"]["client"]) == {book["uid_one"], book["uid_two"]}
    assert set(filed["facets"]["type"]) == {"statement", "report"}


async def test_the_client_facet_carries_names_for_every_option_it_offers(
    session_factory, book
) -> None:
    """The client options are UIDS, and a dropdown of nanoids is unusable.

    THE BUG THIS EXISTS FOR: the names were resolved in the portal from the rows on screen, while
    the options came from this facet — which `facets_for` computes with the CLIENT FILTER LIFTED.
    So the moment a client was picked, the returned rows held only that client and every OTHER
    option in the dropdown rendered as a bare nanoid, with no way to switch to it by name. The
    labels therefore have to be built the same way the options are, which is here.
    """
    page = await _list(session_factory, client=book["uid_one"])
    labels = page["facet_labels"]["client"]
    # Both clients are still offered, so both must still be named — including the one that is NOT
    # in `items`, which is the whole point.
    assert set(page["facets"]["client"]) == {book["uid_one"], book["uid_two"]}
    assert set(labels) >= {book["uid_one"], book["uid_two"]}
    assert labels[book["uid_two"]] == "Margaret Okonjo"


async def test_the_labels_honour_the_search_and_the_other_filters(session_factory, book) -> None:
    """Narrowed the same way the options are — a label offered for a value the facet excluded would
    be noise, and one missing for a value it offers is the bug above."""
    page = await _list(session_factory, q="statement")
    assert set(page["facet_labels"]["client"]) == set(page["facets"]["client"])


async def test_an_unpaged_call_still_returns_the_bare_list(session_factory, book) -> None:
    """Every existing caller passes no ``limit`` and parses a plain array. Filters still apply."""
    async with session_factory() as session:
        rows = await docs.list_documents(principal=_principal(), session=session, type="statement")
    assert isinstance(rows, list)
    assert {d.uid for d in rows} == {book["statement_one"], book["statement_two"]}


# size / created are RANGES, answered in Python over `size_bytes` / `created_at`.


async def test_the_size_and_created_ranges_narrow_and_report_bounds(
    session_factory, book, super_engine
) -> None:
    sizes = {"statement_one": 1_000, "report_one": 5_000, "statement_two": 50_000, "unfiled": 100}
    with super_engine.connect() as c:
        for key, size in sizes.items():
            c.execute(
                text(
                    "UPDATE documents.files SET size_bytes = :sz FROM documents.documents d "
                    "WHERE d.uid = :uid "
                    "AND documents.files.id = COALESCE(d.original_file_id, d.pdf_file_id)"
                ),
                {"sz": size, "uid": book[key]},
            )
        c.commit()

    page = await _list(session_factory, size=["2000", "60000"])
    assert _uids(page) == {book["report_one"], book["statement_two"]}
    # Bounds describe the whole SEARCHED set, or the slider's track shrinks under its handles.
    assert page["bounds"]["size"] == [100.0, 50000.0]
    # An open lower bound is "up to 2,000", not "from".
    assert _uids(await _list(session_factory, size=["", "2000"])) == {
        book["statement_one"],
        book["unfiled"],
    }
    # A queued render has no file: `size_bytes` is NULL through the LEFT JOIN, not zero. And the
    # DAY, not the moment: filed at 23:59 on the last day is still inside the range.
    assert docs._DOCUMENT_FILTERS["size"]({"size_bytes": None}, ["0", "500"]) is False
    created = docs._DOCUMENT_FILTERS["created"]
    pair = listing.clean_range(["", "2026-01-31"])
    assert created({"created_at": datetime(2026, 1, 31, 23, 59)}, pair) is True
    assert created({"created_at": datetime(2026, 2, 1, 0, 0)}, pair) is False
