"""Endpoint-behaviour tests for the /datasets router.

Complements the RLS proof (test_datasets_rls.py) by driving the router coroutines directly against a
real ``authenticated`` session (role + claims set exactly like db.py::tenant_session), so the SQL,
the version aggregation, the inline-sample shaping, the format filter, the provided catalogue and
the authoring endpoints (create/update/delete/version + snapshot) are all exercised.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import Principal
from app.render import catalogue as cat
from app.render.resolve import resolve_datasets
from app.render.sample import sample_datasets
from app.routers import datasets as datasets_router
from app.routers.datasets import (
    CatalogueRef,
    DatasetCreate,
    DatasetSample,
    DatasetUpdate,
    DatasetVersionCreate,
)


@pytest.fixture
def staff(super_engine: Engine) -> dict[str, str]:
    """A tenant + one platform staff user, plus the three dataset shapes — seeded past RLS.

    An inline table, a configured provided dataset (per-client, drawing on ``book_valuations``) and
    one with no version at all, which is what "no data yet" looks like on the listing.
    """
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {
                    "uid": f"daten_{s}",
                    "alias": f"dafirm-{s}",
                    "name": f"DAFirm {s}",
                },
            ).scalar_one()
        )
        user = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, first_name, last_name, "
                    "  enabled) VALUES (:uid, :email, :wos, 'Ada', 'Staff', true) RETURNING id"
                ),
                {"uid": f"dausr_{s}", "email": f"staff_{s}@dafirm.test", "wos": f"dawos_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": user, "t": tenant},
        )

        # An inline (typed-in) dataset with a published version carrying columns/rows.
        inline_uid = f"dds_inline_{s}"
        did = c.execute(
            text(
                "INSERT INTO documents.datasets "
                "  (uid, tenant_id, code, name, description, format, scope, created_by) "
                "VALUES (:uid, :t, :code, 'ISA Allowances', 'HMRC limits.', 'inline', "
                "  'global', CAST(:by AS uuid)) RETURNING id"
            ),
            {"uid": inline_uid, "t": tenant, "code": f"isa_{s}", "by": user},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO documents.dataset_versions "
                "  (uid, tenant_id, dataset_id, version_number, status, format, inline_data, "
                "   created_by, published_at) "
                "VALUES (:uid, :t, :did, 1, 'published', 'inline', CAST(:data AS jsonb), "
                "  CAST(:by AS uuid), now())"
            ),
            {
                "uid": f"ddsv_inline_{s}",
                "t": tenant,
                "did": did,
                "data": json.dumps(
                    {"columns": ["Year", "Limit"], "rows": [["2025/26", "£20,000"]]}
                ),
                "by": user,
            },
        )

        # A dataset with no version yet — nothing published, so nothing resolves.
        unpublished_uid = f"dds_unpub_{s}"
        c.execute(
            text(
                "INSERT INTO documents.datasets "
                "  (uid, tenant_id, code, name, format, scope, created_by) "
                "VALUES (:uid, :t, :code, 'Fee Schedule', 'catalogue', 'global', "
                "  CAST(:by AS uuid))"
            ),
            {"uid": unpublished_uid, "t": tenant, "code": f"fees_{s}", "by": user},
        )

        # A configured provided dataset — the firm's own row naming a catalogue entry, whose
        # declared columns are what the field pickers read.
        catalogue_uid = f"dds_cat_{s}"
        cat_did = c.execute(
            text(
                "INSERT INTO documents.datasets "
                "  (uid, tenant_id, code, name, format, scope, created_by) "
                "VALUES (:uid, :t, :code, 'Client Valuations', 'catalogue', 'subject', "
                "  CAST(:by AS uuid)) RETURNING id"
            ),
            {"uid": catalogue_uid, "t": tenant, "code": f"holdings_{s}", "by": user},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO documents.dataset_versions "
                "  (uid, tenant_id, dataset_id, version_number, status, format, catalogue_ref, "
                "   created_by, published_at) "
                "VALUES (:uid, :t, :did, 1, 'published', 'catalogue', CAST(:ref AS jsonb), "
                "  CAST(:by AS uuid), now())"
            ),
            {
                "uid": f"ddsv_cat_{s}",
                "t": tenant,
                "did": cat_did,
                "ref": json.dumps({"key": "book_valuations", "params": {}}),
                "by": user,
            },
        )
        c.commit()

    return {
        "tenant_id": str(tenant),
        "user_uuid": user,
        "org": f"daorg_{s}",
        "wos": f"dawos_{s}",
        "suffix": s,
        "inline_uid": inline_uid,
        "unpublished_uid": unpublished_uid,
        "catalogue_uid": catalogue_uid,
    }


def _principal(perms: list[str]) -> Principal:
    return Principal(user_id="x", organisation_id="x", role="adviser", permissions=perms)


def _firm_wide_author() -> Principal:
    """An author who may also configure FIRM-WIDE (global) provided datasets.

    ``datasets:write`` alone is not enough for those: a global catalogue dataset is resolved
    firm-wide at render (``render/resolve.py::_minted_claims``), so configuring one takes
    ``contacts:read_all`` too — see ``_require_firm_wide_author`` and the tests at the foot of this
    module. Every test that configures or republishes a global catalogue dataset uses this
    principal; the per-client and inline paths deliberately do not."""
    return _principal(["datasets:read", "datasets:write", "contacts:read_all"])


def _entry(key: str) -> cat.Entry:
    entry = cat.by_key(key)
    assert entry is not None, key
    return entry


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], staff: dict[str, str]):
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    claims = {
        "sub": staff["wos"],
        "role": "adviser",
        "portal": "platform",
        "permissions": ["datasets:read", "datasets:write"],
        "org_id": staff["org"],
        "user_uuid": staff["user_uuid"],
        "tenant_id": int(staff["tenant_id"]),
        "tenant_ids": [int(staff["tenant_id"])],
        "contact_ids": [],
        "account_ids": [],
    }

    @asynccontextmanager
    async def _open() -> AsyncIterator:
        async with maker() as session, session.begin():
            # Claims first, then the role (as the `app` login role) — see the note on the same
            # ordering in test_dataset_catalogue_execution.py: identical today, and forward
            # compatible with revoking set_config from `authenticated`.
            await session.execute(
                text("SELECT set_config('request.jwt.claims', :c, true)"),
                {"c": json.dumps(claims)},
            )
            await session.execute(text("SET LOCAL ROLE authenticated"))
            yield session

    yield _open
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_datasets_shapes_rows(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(["datasets:read"])
    async with session_factory() as session:
        listed = await datasets_router.list_datasets(principal=principal, session=session)
    by_uid = {d.uid: d for d in listed}

    inline = by_uid[staff["inline_uid"]]
    assert inline.format == "inline" and inline.scope == "global"
    assert inline.created_by == "Ada Staff"
    assert inline.version_count == 1 and inline.published_version_number == 1

    unpublished = by_uid[staff["unpublished_uid"]]
    assert unpublished.version_count == 0
    assert unpublished.published_version_number is None

    configured = by_uid[staff["catalogue_uid"]]
    assert configured.format == "catalogue" and configured.scope == "subject"
    # A firm's OWN row, not one of the shipped entries — those are appended separately and can be
    # neither edited nor deleted.
    assert configured.provided is False


@pytest.mark.asyncio
async def test_list_filters_by_format(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(["datasets:read"])
    async with session_factory() as session:
        inline = await datasets_router.list_datasets(
            principal=principal, session=session, format="inline"
        )
        configured = await datasets_router.list_datasets(
            principal=principal, session=session, format="catalogue"
        )
    inline_uids = {d.uid for d in inline}
    assert staff["inline_uid"] in inline_uids and staff["catalogue_uid"] not in inline_uids
    assert all(d.format == "inline" for d in inline)
    assert staff["catalogue_uid"] in {d.uid for d in configured}
    assert all(d.format == "catalogue" for d in configured)


@pytest.mark.asyncio
async def test_get_inline_dataset_returns_sample(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(["datasets:read"])
    async with session_factory() as session:
        got = await datasets_router.get_dataset(
            uid=staff["inline_uid"], principal=principal, session=session
        )
    assert got.description == "HMRC limits."
    assert got.sample is not None
    assert got.sample.columns == ["Year", "Limit"]
    assert got.sample.rows == [["2025/26", "£20,000"]]


@pytest.mark.asyncio
async def test_get_unpublished_dataset_has_no_sample(
    session_factory, staff: dict[str, str]
) -> None:
    """A dataset with no version has no preview and no column schema — there is no source to read
    either out of, so the pickers get an empty list rather than a stale one."""
    principal = _principal(["datasets:read"])
    async with session_factory() as session:
        got = await datasets_router.get_dataset(
            uid=staff["unpublished_uid"], principal=principal, session=session
        )
    assert got.sample is None
    assert got.columns == []


@pytest.mark.asyncio
async def test_get_dataset_exposes_columns_for_inline_and_catalogue(
    session_factory, staff: dict[str, str]
) -> None:
    principal = _principal(["datasets:read"])
    async with session_factory() as session:
        inline = await datasets_router.get_dataset(
            uid=staff["inline_uid"], principal=principal, session=session
        )
        configured = await datasets_router.get_dataset(
            uid=staff["catalogue_uid"], principal=principal, session=session
        )
    assert inline.columns == ["Year", "Limit"]
    # DECLARED by the entry, so the field picker has the real column names and their order.
    assert configured.columns == [c.name for c in _entry("book_valuations").columns]


@pytest.mark.asyncio
async def test_get_dataset_data_snapshot_inline(session_factory, staff: dict[str, str]) -> None:
    """The snapshot endpoint resolves an inline dataset to its rows."""
    principal = _principal(["datasets:read"])
    async with session_factory() as session:
        data = await datasets_router.get_dataset_data(
            uid=staff["inline_uid"], principal=principal, session=session
        )
    assert data.columns == ["Year", "Limit"]
    assert data.rows == [["2025/26", "£20,000"]]


@pytest.mark.asyncio
async def test_create_inline_dataset(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(["datasets:read", "datasets:write"])
    body = DatasetCreate(
        code=f"newinline_{staff['suffix']}",
        name="New Inline",
        format="inline",
        scope="global",
        inline=DatasetSample(columns=["A", "B"], rows=[[1, 2], [3, 4]]),
    )
    async with session_factory() as session:
        created = await datasets_router.create_dataset(
            body=body, principal=principal, session=session
        )
    assert created.format == "inline"
    assert created.version_count == 1
    assert created.sample is not None and created.sample.columns == ["A", "B"]


@pytest.mark.asyncio
async def test_create_catalogue_dataset_takes_its_columns_from_the_entry(
    session_factory, staff: dict[str, str]
) -> None:
    """Configuring a provided dataset caches the ENTRY's declared columns, which is what the
    chart/table field pickers bind to."""
    principal = _firm_wide_author()
    body = DatasetCreate(
        code=f"newcat_{staff['suffix']}",
        name="Our charges",
        format="catalogue",
        scope="global",
        catalogue=CatalogueRef(key="book_fees"),
    )
    async with session_factory() as session:
        created = await datasets_router.create_dataset(
            body=body, principal=principal, session=session
        )
    assert created.format == "catalogue"
    assert created.columns == [c.name for c in _entry("book_fees").columns]


@pytest.mark.asyncio
async def test_create_catalogue_dataset_with_sample_persists_it(
    session_factory, staff: dict[str, str]
) -> None:
    """A configured dataset can carry an author preview sample — stored on the version, returned on
    GET, and orthogonal to the entry's declared columns."""
    principal = _firm_wide_author()
    body = DatasetCreate(
        code=f"catsample_{staff['suffix']}",
        name="Charges with sample",
        format="catalogue",
        scope="global",
        catalogue=CatalogueRef(key="book_fees"),
        sample=DatasetSample(columns=["name", "minimum"], rows=[["Ongoing advice", 500]]),
    )
    async with session_factory() as session:
        created = await datasets_router.create_dataset(
            body=body, principal=principal, session=session
        )
    assert created.format == "catalogue"
    assert created.columns == [c.name for c in _entry("book_fees").columns]
    assert created.sample is not None and created.sample.rows == [["Ongoing advice", 500]]


@pytest.mark.asyncio
async def test_sample_datasets_uses_stored_sample_over_synthesis(
    session_factory, staff: dict[str, str]
) -> None:
    """The editor preview prefers the author's stored sample rows to synthesised junk — a preview
    never runs the entry's query, so without this it shows invented values for real columns."""
    principal = _firm_wide_author()
    code = f"catprev_{staff['suffix']}"
    body = DatasetCreate(
        code=code,
        name="Charges preview",
        format="catalogue",
        scope="global",
        catalogue=CatalogueRef(key="book_fees"),
        sample=DatasetSample(columns=["fund", "value"], rows=[["Property", 5000], ["Cash", 2500]]),
    )
    async with session_factory() as session:
        created = await datasets_router.create_dataset(
            body=body, principal=principal, session=session
        )
        assert created.columns == [c.name for c in _entry("book_fees").columns]
    async with session_factory() as session:
        out = await sample_datasets(session, int(staff["tenant_id"]), [code])
    assert out[code] == [
        {"fund": "Property", "value": 5000},
        {"fund": "Cash", "value": 2500},
    ]


@pytest.mark.asyncio
async def test_create_requires_write_permission(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(["datasets:read"])  # no write
    body = DatasetCreate(
        code=f"nope_{staff['suffix']}",
        name="Nope",
        format="inline",
        inline=DatasetSample(columns=["A"], rows=[[1]]),
    )
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.create_dataset(body=body, principal=principal, session=session)
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_create_rejects_an_unknown_catalogue_key(
    session_factory, staff: dict[str, str]
) -> None:
    """A source the resolver cannot read must fail on the author, not in a client's document.

    So a key this platform does not ship is refused, and the refusal NAMES the keys that exist —
    a bare "unknown dataset" is answered by guessing, and every guess lands as a blank table."""
    principal = _principal(["datasets:read", "datasets:write"])

    def body(ref: CatalogueRef | None) -> DatasetCreate:
        return DatasetCreate(
            code=f"badkey_{uuid.uuid4().hex[:6]}",
            name="Bad key",
            format="catalogue",
            scope="subject",
            catalogue=ref,
        )

    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.create_dataset(
                body=body(CatalogueRef(key="book.holdings")), principal=principal, session=session
            )
    assert ei.value.status_code == 422
    detail = str(ei.value.detail)
    assert "book.holdings" in detail
    assert "book_valuations" in detail  # what IS available

    # …and a catalogue dataset with no reference at all names the missing piece rather than
    # failing later on the DB CHECK.
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.create_dataset(
                body=body(None), principal=principal, session=session
            )
    assert ei.value.status_code == 422
    assert "catalogue key" in str(ei.value.detail)


@pytest.mark.asyncio
async def test_create_rejects_incoherent_scope(session_factory, staff: dict[str, str]) -> None:
    """Scope is enforced against the SOURCE, not merely stored.

    A configured dataset's scope must be the one its ENTRY declares: the entry is what decides the
    claims the query runs under, so a per-client entry stored as ``global`` would be handed
    firm-wide ones. 'collection' has no implementation anywhere, so it is refused rather than
    offered as a knob that does nothing.

    Authored by the firm-wide principal on purpose, so each refusal is provably about the scope
    rather than about the ``contacts:read_all`` entitlement."""
    principal = _firm_wide_author()

    def body(scope: str, key: str) -> DatasetCreate:
        return DatasetCreate(
            code=f"scoped_{uuid.uuid4().hex[:6]}",
            name="Scoped",
            format="catalogue",
            scope=scope,
            catalogue=CatalogueRef(key=key),
        )

    # A per-client entry declared firm-wide — the escalation.
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.create_dataset(
                body=body("global", "book_valuations"), principal=principal, session=session
            )
    assert ei.value.status_code == 422
    assert "per-client" in str(ei.value.detail) and "'subject'" in str(ei.value.detail)

    # …and the mirror: a firm-wide entry declared per-client, which would resolve one client's
    # document from a table that is not about a client at all.
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.create_dataset(
                body=body("subject", "book_fees"), principal=principal, session=session
            )
    assert ei.value.status_code == 422
    assert "firm-wide" in str(ei.value.detail) and "'global'" in str(ei.value.detail)

    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.create_dataset(
                body=body("collection", "book_fees"), principal=principal, session=session
            )
    assert ei.value.status_code == 422
    assert "collection datasets aren't implemented" in str(ei.value.detail)


@pytest.mark.asyncio
async def test_subject_scope_is_refused_for_an_inline_table(
    session_factory, staff: dict[str, str]
) -> None:
    """Only a provided dataset can be client-scoped: an inline table is the same rows for
    everybody, so ``scope='subject'`` on one would resolve the whole table into a single client's
    document. Refused on create AND on a later scope change (the resolver refuses such a pair too,
    for rows written past this guard)."""
    principal = _principal(["datasets:read", "datasets:write"])
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.create_dataset(
                body=DatasetCreate(
                    code=f"subinline_{uuid.uuid4().hex[:6]}",
                    name="Client-scoped table",
                    format="inline",
                    scope="subject",
                    inline=DatasetSample(columns=["fee"], rows=[["1%"]]),
                ),
                principal=principal,
                session=session,
            )
    assert ei.value.status_code == 422
    assert "per-client" in str(ei.value.detail)

    # …and the same guard on the update path: a global inline dataset can't be flipped to subject.
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.update_dataset(
                uid=staff["inline_uid"],
                body=DatasetUpdate(scope="subject"),
                principal=principal,
                session=session,
            )
    assert ei.value.status_code == 422
    assert "per-client" in str(ei.value.detail)
    async with session_factory() as session:
        unchanged = await datasets_router.get_dataset(
            uid=staff["inline_uid"], principal=principal, session=session
        )
    assert unchanged.scope == "global"


@pytest.mark.asyncio
async def test_delete_refused_while_a_template_uses_the_dataset(
    session_factory, super_engine: Engine, staff: dict[str, str]
) -> None:
    """A dataset a live template binds by code can't be deleted (409, with the count): the template
    would keep rendering, resolving the missing code to no rows — the silent-empty document. The
    count is also exposed on the detail so the confirm dialog can say so up front."""
    s = staff["suffix"]
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO documents.templates "
                "  (uid, tenant_id, code, name, document_type, linked_datasets, created_by) "
                "VALUES (:u, :t, :code, 'Suitability', 'suitability', ARRAY[:ds], "
                "  CAST(:by AS uuid))"
            ),
            {
                "u": f"dtpl_{s}",
                "t": int(staff["tenant_id"]),
                "code": f"tpl_{s}",
                "ds": f"isa_{s}",  # the inline dataset from the fixture
                "by": staff["user_uuid"],
            },
        )
        c.commit()
    principal = _principal(["datasets:read", "datasets:write"])
    async with session_factory() as session:
        detail = await datasets_router.get_dataset(
            uid=staff["inline_uid"], principal=principal, session=session
        )
        assert detail.used_by_templates == 1
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.delete_dataset(
                uid=staff["inline_uid"], principal=principal, session=session
            )
    assert ei.value.status_code == 409
    assert "1 template" in str(ei.value.detail)


@pytest.mark.asyncio
async def test_dataset_authoring_is_audited(session_factory, staff: dict[str, str]) -> None:
    """Authoring a dataset (and the entry + settings it draws on) lands in the append-only DML
    audit spine, with the actor — the trail that makes a firm's data sources accountable. The
    trigger comes from `audit.attach` in 0012_templates.sql; this pins that it is still attached."""
    principal = _firm_wide_author()
    code = f"audited_{staff['suffix']}"
    async with session_factory() as session:
        await datasets_router.create_dataset(
            body=DatasetCreate(
                code=code,
                name="Audited",
                format="catalogue",
                scope="global",
                catalogue=CatalogueRef(key="book_fees", params={"as_at": "-3m"}),
            ),
            principal=principal,
            session=session,
        )
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT action, user_id, detail FROM audit.log "
                        "WHERE entity_type = 'documents.datasets' "
                        "  AND detail -> 'row' ->> 'code' = :code"
                    ),
                    {"code": code},
                )
            )
            .mappings()
            .all()
        )
    assert [r["action"] for r in rows] == ["documents.datasets.insert"]
    assert str(rows[0]["user_id"]) == staff["user_uuid"]
    async with session_factory() as session:
        versions = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit.log "
                    "WHERE entity_type = 'documents.dataset_versions' "
                    "  AND detail -> 'row' -> 'catalogue_ref' ->> 'key' = 'book_fees' "
                    "  AND detail -> 'row' -> 'catalogue_ref' -> 'params' ->> 'as_at' = '-3m'"
                )
            )
        ).scalar_one()
    # The chosen entry AND the settings chosen on it, not just the dataset it was configured under.
    assert versions >= 1


@pytest.mark.asyncio
async def test_update_and_delete_dataset(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(["datasets:read", "datasets:write"])
    async with session_factory() as session:
        updated = await datasets_router.update_dataset(
            uid=staff["inline_uid"],
            body=DatasetUpdate(name="Renamed"),
            principal=principal,
            session=session,
        )
        # Format is immutable, so patching the metadata cannot change where the rows come from.
        assert updated.name == "Renamed" and updated.format == "inline"
    async with session_factory() as session:
        await datasets_router.delete_dataset(
            uid=staff["unpublished_uid"], principal=principal, session=session
        )
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.get_dataset(
                uid=staff["unpublished_uid"], principal=principal, session=session
            )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_add_dataset_version_bumps_count(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(["datasets:read", "datasets:write"])
    async with session_factory() as session:
        after = await datasets_router.add_dataset_version(
            uid=staff["inline_uid"],
            body=DatasetVersionCreate(inline=DatasetSample(columns=["Year"], rows=[["2026/27"]])),
            principal=principal,
            session=session,
        )
    assert after.version_count == 2 and after.sample is not None
    assert after.sample.rows == [["2026/27"]]


@pytest.mark.asyncio
async def test_list_dataset_versions_history(session_factory, staff: dict[str, str]) -> None:
    principal = _principal(["datasets:read", "datasets:write"])
    async with session_factory() as session:
        await datasets_router.add_dataset_version(
            uid=staff["inline_uid"],
            body=DatasetVersionCreate(inline=DatasetSample(columns=["Year"], rows=[["2027/28"]])),
            principal=principal,
            session=session,
        )
    async with session_factory() as session:
        versions = await datasets_router.list_dataset_versions(
            uid=staff["inline_uid"], principal=principal, session=session
        )
    # Newest first; the current version carries its inline source so the edit form can seed from it.
    assert [v.version_number for v in versions] == [2, 1]
    assert versions[0].inline is not None and versions[0].inline.rows == [["2027/28"]]


@pytest.mark.asyncio
async def test_list_dataset_versions_carries_the_catalogue_reference(
    session_factory, staff: dict[str, str]
) -> None:
    """A configured dataset's history carries the entry key and the settings VERBATIM, which is
    what the edit form seeds from — a relative range must come back as `-12m`, not as the date it
    happened to mean on the day it was saved."""
    principal = _firm_wide_author()
    async with session_factory() as session:
        await datasets_router.add_dataset_version(
            uid=staff["catalogue_uid"],
            body=DatasetVersionCreate(
                catalogue=CatalogueRef(key="book_valuations", params={"as_at": "-12m"})
            ),
            principal=principal,
            session=session,
        )
    async with session_factory() as session:
        versions = await datasets_router.list_dataset_versions(
            uid=staff["catalogue_uid"], principal=principal, session=session
        )
    assert [v.version_number for v in versions] == [2, 1]
    assert versions[0].catalogue is not None
    assert versions[0].catalogue.key == "book_valuations"
    assert versions[0].catalogue.params == {"as_at": "-12m"}


@pytest.mark.asyncio
async def test_list_dataset_versions_requires_read(session_factory, staff: dict[str, str]) -> None:
    principal = _principal([])  # no datasets:read
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.list_dataset_versions(
                uid=staff["inline_uid"], principal=principal, session=session
            )
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_get_missing_dataset_404(session_factory) -> None:
    principal = _principal(["datasets:read"])
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.get_dataset(uid="ds_nope", principal=principal, session=session)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_list_datasets_requires_permission(session_factory) -> None:
    principal = _principal([])  # no datasets:read
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.list_datasets(principal=principal, session=session)
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_get_dataset_data_rejects_unseen_contact(
    session_factory, staff: dict[str, str]
) -> None:
    """IDOR guard: a caller-supplied contact_id the caller can't see is refused (422) before it is
    bound as :subject_id into the entry's SQL (mirrors files.py's ownership check)."""
    principal = _principal(["datasets:read"])
    async with session_factory() as session:
        with pytest.raises(HTTPException) as ei:
            await datasets_router.get_dataset_data(
                uid=staff["catalogue_uid"],
                principal=principal,
                session=session,
                contact_id=999_999_999,  # not a crm.contacts row the caller can see
            )
    assert ei.value.status_code == 422
    # The refusal is the ownership check, not a downstream resolution problem — without asserting
    # the reason, an entry that simply returned no rows for an unknown client would pass this.
    assert "you don't have access" in str(ei.value.detail)


@pytest.mark.asyncio
async def test_a_firm_wide_dataset_is_rls_scoped_under_service_role(
    monkeypatch: pytest.MonkeyPatch,
    service_session,
    super_engine: Engine,
    staff: dict[str, str],
) -> None:
    """A dataset resolved under service_role (BYPASSRLS — as in the render worker) is still
    RLS-scoped: RLS is re-imposed via `SET LOCAL ROLE authenticated` + claims minted for the
    entry's declared scope (see resolve._minted_claims), so a firm's dataset resolves across THAT
    FIRM and never reaches another tenant's rows.

    The entry registered here carries NO tenant predicate of its own, deliberately: every shipped
    entry filters on `:tenant_id`, so running one proves its WHERE clause rather than the envelope
    around it. Here RLS is the only boundary there is.

    The entry is `global`, so the firm-wide rows are the right answer — with or without a subject
    client. What must never appear is tenant B's contact, which is the isolation the whole
    arrangement exists for."""
    s = staff["suffix"]
    tenant_a = int(staff["tenant_id"])
    mine = f"mine_{s}@tenanta.test"
    secret = f"secret_{s}@tenantb.test"
    with super_engine.connect() as c:
        mine_id = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (uid, tenant_id, type, name, email) "
                    "VALUES (:u, :t, 'organisation', 'Mine A', :e) RETURNING id"
                ),
                {"u": f"exfa_{s}", "t": tenant_a, "e": mine},
            ).scalar_one()
        )
        tenant_b = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"exfb_{s}", "a": f"exfb-{s}", "n": f"ExfB {s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO crm.contacts (uid, tenant_id, type, name, email) "
                "VALUES (:u, :t, 'organisation', 'Secret B', :e)"
            ),
            {"u": f"exfc_{s}", "t": tenant_b, "e": secret},
        )
        c.commit()

    entry = cat.Entry(
        key="book_test_exfil",
        name="Every contact",
        description="A firm-wide read with no tenant predicate of its own.",
        scope="global",
        sql="SELECT id, email FROM crm.contacts",
        columns=(cat.Column("id", "Id", "number"), cat.Column("email", "Email", "text")),
    )
    monkeypatch.setitem(cat._ENTRIES, entry.key, entry)

    # A registered entry needs no row of its own, so the code resolves straight off the registry.
    scoped = await resolve_datasets(service_session, tenant_a, [entry.key], subject_id=mine_id)
    emails = {r.get("email") for r in scoped.get(entry.key, [])}
    assert mine in emails
    assert secret not in emails  # never another tenant's (RLS held under service_role)
    # …and with no subject client at all it is still the firm's rows, not nothing: a firm-wide
    # dataset does not depend on there being a recipient. Resolving to [] here is the silent-subset
    # failure — a template's firm-wide table coming out blank in a document.
    unscoped = await resolve_datasets(service_session, tenant_a, [entry.key])
    unscoped_emails = {r.get("email") for r in unscoped[entry.key]}
    assert mine in unscoped_emails
    assert secret not in unscoped_emails


# ── declaring FIRM-WIDE data takes firm-wide entitlement ──────────────────────
# `scope='global'` decides who the query runs as at render: a global catalogue dataset is resolved
# with `contacts:read_all` for its own tenant (resolve._minted_claims), because that is the only way
# "firm-wide" can be honest — narrowing it to the document's client returns a subset presented as
# the whole. The flip side is that a book-scoped author with `datasets:write` must not be able to
# configure their way to the firm's whole book: they cannot see those rows in the preview (which
# runs under their own claims), so the escalation would land invisibly, in documents. Hence the
# gate — and hence these tests, which include the ways round it that had to be closed.
@pytest.mark.asyncio
async def test_global_catalogue_dataset_needs_read_all(
    session_factory, staff: dict[str, str]
) -> None:
    """Create: `datasets:write` alone is refused (403) for a global catalogue dataset; with
    `contacts:read_all` the same body succeeds."""
    body = DatasetCreate(
        code=f"firmwide_{staff['suffix']}",
        name="Firm-wide charges",
        format="catalogue",
        scope="global",
        catalogue=CatalogueRef(key="book_fees"),
    )
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.create_dataset(
                body=body,
                principal=_principal(["datasets:read", "datasets:write"]),
                session=session,
            )
    assert ei.value.status_code == 403
    assert "contacts:read_all" in str(ei.value.detail)

    async with session_factory() as session:
        created = await datasets_router.create_dataset(
            body=body, principal=_firm_wide_author(), session=session
        )
    assert created.scope == "global" and created.format == "catalogue"


@pytest.mark.asyncio
async def test_read_all_gate_is_not_satisfied_by_a_neighbouring_permission(
    session_factory, staff: dict[str, str]
) -> None:
    """The ways round the gate that a reader would try first, all refused:

    * ``contacts:write`` — the write⇒read implication covers ``contacts:read`` only, NOT the bespoke
      ``contacts:read_all`` entitlement, so "I can edit clients" is not "I can see all clients".
    * ``contacts:read`` — plain read is the book-scoped one; that is the whole distinction.
    * ``datasets:write`` twice over / a made-up ``datasets:read_all`` — the permission that counts
      is about seeing clients, not about datasets.

    …and ``system`` DOES pass, because it bypasses every check by design (``auth.is_system``)."""

    def body() -> DatasetCreate:
        return DatasetCreate(
            code=f"nearmiss_{uuid.uuid4().hex[:6]}",
            name="Near miss",
            format="catalogue",
            scope="global",
            catalogue=CatalogueRef(key="book_fees"),
        )

    for perms in (
        ["datasets:write", "contacts:write"],
        ["datasets:write", "contacts:read"],
        ["datasets:write", "datasets:read_all"],
        ["datasets:write", "contacts:read_all_clients"],
        ["datasets:write", "CONTACTS:READ_ALL"],
    ):
        with pytest.raises(HTTPException) as ei:
            async with session_factory() as session:
                await datasets_router.create_dataset(
                    body=body(), principal=_principal(perms), session=session
                )
        assert ei.value.status_code == 403, perms

    async with session_factory() as session:
        created = await datasets_router.create_dataset(
            body=body(), principal=_principal(["system"]), session=session
        )
    assert created.scope == "global"


@pytest.mark.asyncio
async def test_cannot_reach_global_scope_through_the_side_doors(
    session_factory, staff: dict[str, str]
) -> None:
    """The two sibling endpoints that reach the same state, which a route-specific check would miss:

    1. PATCH a per-client dataset to ``scope='global'`` — refused 403 (and the dataset is
       unchanged), so "configure it as subject, then flip it" doesn't work.
    2. POST a new VERSION of an existing global dataset — refused 403, so a book-scoped author
       cannot re-point a colleague's firm-wide dataset and read the result out of the next
       generated document.
    """
    scoped = _principal(["datasets:read", "datasets:write"])

    # (1) the per-client dataset the fixture configured (scope='subject', a per-client entry).
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.update_dataset(
                uid=staff["catalogue_uid"],
                body=DatasetUpdate(scope="global"),
                principal=scoped,
                session=session,
            )
    assert ei.value.status_code == 403
    async with session_factory() as session:
        unchanged = await datasets_router.get_dataset(
            uid=staff["catalogue_uid"], principal=scoped, session=session
        )
    assert unchanged.scope == "subject"

    # (2) a global dataset by a full-book author, then re-versioned by the book-scoped one.
    async with session_factory() as session:
        firm_wide = await datasets_router.create_dataset(
            body=DatasetCreate(
                code=f"sidedoor_{staff['suffix']}",
                name="Firm-wide",
                format="catalogue",
                scope="global",
                catalogue=CatalogueRef(key="book_fees"),
            ),
            principal=_firm_wide_author(),
            session=session,
        )
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.add_dataset_version(
                uid=firm_wide.uid,
                body=DatasetVersionCreate(
                    catalogue=CatalogueRef(key="book_fees", params={"as_at": "-24m"})
                ),
                principal=scoped,
                session=session,
            )
    assert ei.value.status_code == 403
    async with session_factory() as session:
        after = await datasets_router.list_dataset_versions(
            uid=firm_wide.uid, principal=scoped, session=session
        )
    assert [v.version_number for v in after] == [1]  # nothing was published


@pytest.mark.asyncio
async def test_the_gate_does_not_block_what_it_has_no_business_blocking(
    session_factory, staff: dict[str, str]
) -> None:
    """A guard that over-blocks is its own kind of failure, so: a per-client provided dataset, a
    global INLINE table (no query, nothing executed on the firm's behalf) and a new version of one
    are all still authorable with `datasets:write` alone."""
    scoped = _principal(["datasets:read", "datasets:write"])
    s = staff["suffix"]
    async with session_factory() as session:
        per_client = await datasets_router.create_dataset(
            body=DatasetCreate(
                code=f"perclient_{s}",
                name="Per client",
                format="catalogue",
                scope="subject",
                catalogue=CatalogueRef(key="book_transactions"),
            ),
            principal=scoped,
            session=session,
        )
        table = await datasets_router.create_dataset(
            body=DatasetCreate(
                code=f"globaltable_{s}",
                name="Global table",
                format="inline",
                scope="global",
                inline=DatasetSample(columns=["Year"], rows=[["2026/27"]]),
            ),
            principal=scoped,
            session=session,
        )
    assert per_client.scope == "subject" and table.scope == "global"
    async with session_factory() as session:
        bumped = await datasets_router.add_dataset_version(
            uid=table.uid,
            body=DatasetVersionCreate(inline=DatasetSample(columns=["Year"], rows=[["2027/28"]])),
            principal=scoped,
            session=session,
        )
    assert bumped.version_count == 2


@pytest.mark.asyncio
async def test_list_filters_refuse_an_unknown_value(session_factory) -> None:
    """An unrecognised filter value is refused rather than ignored: silently dropping
    `?format=inlien` returns the WHOLE catalogue, which reads as "there are no table datasets
    either way" — the same class of quiet wrongness as a dataset resolving to nothing. The refusal
    names the values that work, so the caller does not have to guess."""
    principal = _principal(["datasets:read"])
    with pytest.raises(HTTPException) as ei:
        async with session_factory() as session:
            await datasets_router.list_datasets(
                principal=principal, session=session, format="inlien"
            )
    assert ei.value.status_code == 422
    detail = str(ei.value.detail)
    assert "catalogue" in detail and "inline" in detail


# ── the provided catalogue ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_listing_offers_what_graphene_provides(
    session_factory, staff: dict[str, str]
) -> None:
    """A firm sees the shipped entries without having configured anything.

    They have no row in ``documents.datasets``, so this is the only thing that makes them
    discoverable — and the merge picker reads this same endpoint.
    """
    principal = _principal(["datasets:read"])
    async with session_factory() as session:
        listed = await datasets_router.list_datasets(principal=principal, session=session)
    provided = {d.uid for d in listed if d.provided}
    assert provided == cat.keys()
    assert all(d.created_by == "Graphene" for d in listed if d.provided)
    assert all(d.format == "catalogue" for d in listed if d.provided)


@pytest.mark.asyncio
async def test_a_provided_entry_carries_its_declared_columns_to_the_picker(
    session_factory, staff: dict[str, str]
) -> None:
    # DECLARED columns, because there is no SQL for the picker to parse a SELECT list out of — an
    # empty field list with no error to say why is the failure this replaces.
    principal = _principal(["datasets:read"])
    async with session_factory() as session:
        listed = await datasets_router.list_datasets(
            principal=principal, session=session, include="sample"
        )
    by_uid = {d.uid: d for d in listed}
    item = by_uid["book_valuations"]
    entry = cat.by_key("book_valuations")
    assert entry is not None
    assert item.columns == [c.name for c in entry.columns]
    assert "value" in item.columns


@pytest.mark.asyncio
async def test_the_format_filter_reaches_the_provided_entries_too(
    session_factory, staff: dict[str, str]
) -> None:
    """Filtering the database and then appending the shipped entries unconditionally is how a
    filter comes to mean nothing, so the filter applies to both halves of the listing."""
    principal = _principal(["datasets:read"])
    async with session_factory() as session:
        inline_only = await datasets_router.list_datasets(
            principal=principal, session=session, format="inline"
        )
        cat_only = await datasets_router.list_datasets(
            principal=principal, session=session, format="catalogue"
        )
    assert not [d for d in inline_only if d.provided]
    assert {d.uid for d in cat_only if d.provided} == cat.keys()


@pytest.mark.asyncio
async def test_the_catalogue_endpoint_describes_what_a_firm_may_set() -> None:
    principal = _principal(["datasets:read"])
    entries = await datasets_router.list_dataset_catalogue(principal=principal)
    by_key = {e.key: e for e in entries}
    assert by_key.keys() == cat.keys()

    valuations = by_key["book_valuations"]
    assert valuations.scope == "subject"
    assert [p.name for p in valuations.params] == ["as_at"]
    # The default reaches the form VERBATIM. It is resolved per render, so sending a resolved date
    # would freeze the range at whatever "today" meant when the page was served.
    assert valuations.params[0].default == "today"

    transactions = by_key["book_transactions"]
    assert [p.name for p in transactions.params] == ["date_from", "date_to"]
    assert transactions.params[0].default == "-12m"
    assert all(c.kind for c in transactions.columns)


@pytest.mark.asyncio
async def test_the_catalogue_endpoint_needs_read_permission() -> None:
    with pytest.raises(HTTPException) as exc:
        await datasets_router.list_dataset_catalogue(principal=_principal([]))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_an_entitled_entry_is_hidden_from_an_author_without_the_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No shipped entry declares an entitlement today, so the gate is exercised against a
    # locally-registered one rather than left untested until the first entry needs it.
    gated = cat.Entry(
        key="book_test_gated",
        name="Gated",
        description="Needs a permission.",
        scope="global",
        sql="SELECT 1 AS n WHERE :tenant_id IS NOT NULL",
        columns=(cat.Column("n", "N", "number"),),
        entitlement="contacts:read_all",
    )
    monkeypatch.setitem(cat._ENTRIES, gated.key, gated)

    without = await datasets_router.list_dataset_catalogue(principal=_principal(["datasets:read"]))
    assert "book_test_gated" not in {e.key for e in without}

    with_perm = await datasets_router.list_dataset_catalogue(
        principal=_principal(["datasets:read", "contacts:read_all"])
    )
    assert "book_test_gated" in {e.key for e in with_perm}


@pytest.mark.asyncio
async def test_a_firm_cannot_claim_a_reserved_code(session_factory, staff: dict[str, str]) -> None:
    """A firm's code must not collide with a shipped entry's key.

    A template binds a dataset by CODE, so a row spelling `book_valuations` would shadow the
    provided entry of that name for every template in the firm — and the two are answered by
    different halves of `_dataset_source`, so which one won would depend on whether the row
    existed. The namespace used to make this impossible for free (a dotted key cannot match
    `_CODE_RE`); keys are bare identifiers now, so the rule is stated instead.
    """
    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            await datasets_router.create_dataset(
                body=DatasetCreate(
                    code="book_valuations",
                    name="Mine",
                    format="inline",
                    inline=DatasetSample(columns=["a"], rows=[["1"]]),
                ),
                principal=_principal(["datasets:read", "datasets:write"]),
                session=session,
            )
    assert exc.value.status_code == 422
    assert "reserved" in str(exc.value.detail).lower()
