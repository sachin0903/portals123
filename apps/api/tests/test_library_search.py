"""Forms, templates and datasets are searched in SQL.

All three pages filtered `name` and `code` in the browser over a complete set — so the answers were
right, they were just carried to the browser to be narrowed. Moving the search into SQL is what
makes the page's question the SERVER's question, and it is what stops the search being bounded by
whatever the loader happened to fetch the day one of these lists grows.

Three things worth pinning, and only one of them is "the search works":

- the search is ANDed onto whatever slice the other parameters chose, so it can never widen past
  `include_archived` / `status` / `format`;
- it matches `code` as well as `name`, because that is what the pages matched and dropping half of
  it silently narrows what people can find;
- it is case-insensitive, since nobody types a template code in caps.

Every needle carries a per-run MARKER. These run on `service_session`, which is `service_role` and
therefore bypasses RLS — so the listing sees every tenant's rows, not just the one seeded here. A
short needle like "ff" or "hold" then matches whatever another test happened to leave behind, and
the failure only appears in the full suite: alone they pass, together they do not.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers import datasets as ds
from app.routers import forms as fm
from app.routers import templates as tp


class _P:
    """A principal that passes the read permission checks these three share."""

    def __init__(self) -> None:
        self.permissions = ["forms:read", "templates:read", "datasets:read"]
        self.is_system = True
        self.user_id = "u-test"
        self.tenant_id = 1


@pytest.fixture
def principal() -> _P:
    return _P()


async def _tenant(session: AsyncSession) -> tuple[int, str]:
    """A tenant and an author. `created_by` is a real FK on templates and datasets, so a seed needs
    a user that exists — a zero uuid is refused, correctly."""
    s = uuid.uuid4().hex[:8]
    tid = int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"lib_{s}", "a": f"lib-{s}", "n": f"Library {s}"},
            )
        ).scalar_one()
    )
    author = str(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.users (tenant_id, email, first_name, last_name, enabled) "
                    "VALUES (:t, :e, 'Lib', 'Author', true) RETURNING id"
                ),
                {"t": tid, "e": f"lib_{s}@x.test"},
            )
        ).scalar_one()
    )
    return tid, author


@pytest.mark.asyncio
async def test_forms_search_matches_name_and_code(
    service_session: AsyncSession, principal: _P
) -> None:
    tid, author = await _tenant(service_session)
    mark = uuid.uuid4().hex[:8].upper()
    for name, code in ((f"Suitability {mark}", f"SUIT{mark}"), (f"Fact find {mark}", f"FF{mark}")):
        await service_session.execute(
            text(
                "INSERT INTO documents.forms (tenant_id, uid, name, code, status) "
                "VALUES (:t, :u, :n, :c, 'draft')"
            ),
            {"t": tid, "u": uuid.uuid4().hex[:12], "n": name, "c": code},
        )

    by_name = await fm.list_forms(
        q=f"suitability {mark}", principal=principal, session=service_session
    )
    assert [f.code for f in by_name] == [f"SUIT{mark}"]

    # Lower-cased needle against an upper-cased code: nobody types a code in caps.
    by_code = await fm.list_forms(
        q=f"ff{mark}".lower(), principal=principal, session=service_session
    )
    assert [f.code for f in by_code] == [f"FF{mark}"]


@pytest.mark.asyncio
async def test_forms_search_cannot_widen_past_the_archive_slice(
    service_session: AsyncSession, principal: _P
) -> None:
    # The search is ANDed onto the slice, never ORed beside it — otherwise searching would surface
    # archived forms on a page whose whole contract is that it excludes them.
    tid, author = await _tenant(service_session)
    mark = uuid.uuid4().hex[:8].upper()
    await service_session.execute(
        text(
            "INSERT INTO documents.forms (tenant_id, uid, name, code, status) "
            "VALUES (:t, :u, :n, :c, 'archived')"
        ),
        {"t": tid, "u": uuid.uuid4().hex[:12], "n": f"Archived {mark}", "c": f"ARCH{mark}"},
    )
    live = await fm.list_forms(q=f"archived {mark}", principal=principal, session=service_session)
    assert live == []
    everything = await fm.list_forms(
        q=f"archived {mark}", include_archived=True, principal=principal, session=service_session
    )
    assert [f.code for f in everything] == [f"ARCH{mark}"]


@pytest.mark.asyncio
async def test_templates_search_respects_the_status_slice(
    service_session: AsyncSession, principal: _P
) -> None:
    tid, author = await _tenant(service_session)
    mark = uuid.uuid4().hex[:8].upper()
    for name, code, archived in (
        (f"Live letter {mark}", f"LIVE{mark}", False),
        (f"Old letter {mark}", f"OLD{mark}", True),
    ):
        await service_session.execute(
            text(
                "INSERT INTO documents.templates "
                "  (tenant_id, uid, name, code, document_type, created_by, archived_at) "
                "VALUES (:t, :u, :n, :c, 'letter', :by, "
                "        CASE WHEN :arch THEN now() ELSE NULL END)"
            ),
            {
                "t": tid,
                "u": uuid.uuid4().hex[:12],
                "n": name,
                "c": code,
                "by": author,
                "arch": archived,
            },
        )
    found = await tp.list_templates(
        q=f"letter {mark}", principal=principal, session=service_session
    )
    assert [t.code for t in found] == [f"LIVE{mark}"]

    archived_only = await tp.list_templates(
        q=f"letter {mark}", status="archived", principal=principal, session=service_session
    )
    assert [t.code for t in archived_only] == [f"OLD{mark}"]


@pytest.mark.asyncio
async def test_datasets_search_sits_beside_the_other_filters(
    service_session: AsyncSession, principal: _P
) -> None:
    tid, author = await _tenant(service_session)
    mark = uuid.uuid4().hex[:8].upper()
    # `format` is the filter that sits beside the search now. `liveness` was retired with the
    # free-text SQL authoring it described (`0152_dataset_catalogue`), and `sql`/`csv` went with
    # it — a dataset is a catalogue entry or an inline table. The point of the test is unchanged:
    # the search is ANDed onto whatever slice the filter beside it chose.
    for name, code, fmt in (
        (f"Holdings {mark}", f"HOLD{mark}", "catalogue"),
        (f"Static {mark}", f"STAT{mark}", "inline"),
    ):
        await service_session.execute(
            text(
                "INSERT INTO documents.datasets "
                "  (tenant_id, uid, name, code, scope, created_by, format) "
                "VALUES (:t, :u, :n, :c, CAST('global' AS documents.dataset_scope), :by, "
                "        CAST(:f AS documents.dataset_format))"
            ),
            {
                "t": tid,
                "u": uuid.uuid4().hex[:12],
                "n": name,
                "c": code,
                "by": author,
                "f": fmt,
            },
        )
    found = await ds.list_datasets(
        q=f"holdings {mark}", principal=principal, session=service_session
    )
    assert [d.code for d in found] == [f"HOLD{mark}"]

    # Both narrow together — a search does not replace the format filter beside it.
    none_match = await ds.list_datasets(
        q=f"holdings {mark}", format="inline", principal=principal, session=service_session
    )
    assert none_match == []


@pytest.mark.asyncio
async def test_no_search_returns_everything(service_session: AsyncSession, principal: _P) -> None:
    # The parameter is additive: every existing caller passes none and must be unaffected.
    tid, author = await _tenant(service_session)
    await service_session.execute(
        text(
            "INSERT INTO documents.forms (tenant_id, uid, name, code, status) "
            "VALUES (:t, :u, 'Anything', 'ANY', 'draft')"
        ),
        {"t": tid, "u": uuid.uuid4().hex[:12]},
    )
    assert len(await fm.list_forms(principal=principal, session=service_session)) >= 1
