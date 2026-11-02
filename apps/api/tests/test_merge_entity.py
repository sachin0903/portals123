"""platform.merge_entity (0064): the merge policy, against a real Postgres.

The policy is one sentence — insert a row the first time the provider shows it, then only update
the columns the provider owns — and every test here is a way of getting it wrong:

* a second run must not resurrect a value a human changed (the whole reason the policy exists);
* it must still refresh `data`, or the provider's payload silently goes stale;
* the two declared exceptions (`accounts.value`, transactions in full) must actually apply;
* an unchanged row must not be written, because platform.links/orgs carry audit triggers and a
  nightly rewrite would bury the trail in changes that did not happen.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def _tenant(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(text("SELECT id FROM auth.tenants ORDER BY id LIMIT 1"))
        ).scalar_one()
    )


async def _stage_orgs(session: AsyncSession, tenant_id: int, rows: list[dict[str, object]]) -> None:
    """Stand in for what the pipeline lands in `inbound`."""
    await session.execute(text("DROP TABLE IF EXISTS t_orgs"))
    await session.execute(
        text(
            "CREATE TEMP TABLE t_orgs ("
            "  tenant_id int, provider text, key text, type platform.node_type,"
            "  name text, data jsonb)"
        )
    )
    for row in rows:
        await session.execute(
            text(
                "INSERT INTO t_orgs (tenant_id, provider, key, type, name, data) "
                "VALUES (:t, 'seccl', :k, 'node', :n, cast(:d AS jsonb))"
            ),
            {"t": tenant_id, "k": row["key"], "n": row["name"], "d": row["data"]},
        )


#: The function's own default. Passed explicitly so the SQL below is one fixed string rather than
#: one assembled per call — the assembling is what ruff (rightly) reads as query construction.
DEFAULT_UPDATABLE = ["data"]

_MERGE_SQL = text(
    "SELECT inserted, updated FROM platform.merge_entity("
    "  't_orgs'::regclass, 'platform.nodes'::regclass,"
    "  ARRAY['tenant_id','provider','key'], :upd)"
)


async def _merge_orgs(session: AsyncSession, updatable: list[str] | None = None) -> tuple[int, int]:
    row = (
        (await session.execute(_MERGE_SQL, {"upd": updatable or DEFAULT_UPDATABLE}))
        .mappings()
        .one()
    )
    return int(row["inserted"]), int(row["updated"])


@pytest.mark.asyncio
async def test_first_run_inserts_second_run_does_not(service_session: AsyncSession) -> None:
    tenant_id = await _tenant(service_session)
    await _stage_orgs(
        service_session, tenant_id, [{"key": "n1", "name": "Node One", "data": '{"v": 1}'}]
    )
    assert await _merge_orgs(service_session) == (1, 0)
    # Nothing changed upstream, so nothing is written — not even `updated_at`.
    assert await _merge_orgs(service_session) == (0, 0)


@pytest.mark.asyncio
async def test_a_human_edit_survives_the_next_run(service_session: AsyncSession) -> None:
    """The point of the policy. The provider named this org once; a person renamed it; the
    provider must not put its own name back tomorrow."""
    tenant_id = await _tenant(service_session)
    await _stage_orgs(
        service_session, tenant_id, [{"key": "n2", "name": "Provider Name", "data": '{"v": 1}'}]
    )
    await _merge_orgs(service_session)
    await service_session.execute(
        text(
            "UPDATE platform.nodes SET name = 'What The Firm Calls It' "
            "WHERE tenant_id = :t AND key = 'n2'"
        ),
        {"t": tenant_id},
    )
    # Same provider row, re-published with a changed payload.
    await _stage_orgs(
        service_session, tenant_id, [{"key": "n2", "name": "Provider Name", "data": '{"v": 2}'}]
    )
    assert await _merge_orgs(service_session) == (0, 1)

    row = (
        (
            await service_session.execute(
                text("SELECT name, data FROM platform.nodes WHERE tenant_id = :t AND key = 'n2'"),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    assert row["name"] == "What The Firm Calls It", "a provider must not overwrite a human edit"
    assert row["data"] == {"v": 2}, "but `data` must still be refreshed, or it goes stale"


@pytest.mark.asyncio
async def test_a_declared_exception_is_provider_owned(service_session: AsyncSession) -> None:
    """`platform.accounts.value` is the exception: the balance IS the provider's to state, so it
    must be overwritten even though the typed columns beside it are not."""
    tenant_id = await _tenant(service_session)
    await _stage_orgs(service_session, tenant_id, [{"key": "n3", "name": "First", "data": "{}"}])
    await _merge_orgs(service_session, updatable=["data", "name"])
    await service_session.execute(
        text("UPDATE platform.nodes SET name = 'Edited' WHERE tenant_id = :t AND key = 'n3'"),
        {"t": tenant_id},
    )
    await _stage_orgs(service_session, tenant_id, [{"key": "n3", "name": "Second", "data": "{}"}])
    assert await _merge_orgs(service_session, updatable=["data", "name"]) == (0, 1)
    name = (
        await service_session.execute(
            text("SELECT name FROM platform.nodes WHERE tenant_id = :t AND key = 'n3'"),
            {"t": tenant_id},
        )
    ).scalar_one()
    assert name == "Second", "a column named in p_updatable is the provider's"


@pytest.mark.asyncio
async def test_a_star_updatable_owns_every_shared_column(service_session: AsyncSession) -> None:
    """`{*}` is how platform.transactions is merged: Graphene never writes a transaction, so a
    provider status change is simply the truth and there is no local edit to lose."""
    tenant_id = await _tenant(service_session)
    await _stage_orgs(service_session, tenant_id, [{"key": "n4", "name": "A", "data": "{}"}])
    await _merge_orgs(service_session, updatable=["*"])
    await service_session.execute(
        text("UPDATE platform.nodes SET name = 'local' WHERE tenant_id = :t AND key = 'n4'"),
        {"t": tenant_id},
    )
    await _stage_orgs(service_session, tenant_id, [{"key": "n4", "name": "B", "data": "{}"}])
    assert await _merge_orgs(service_session, updatable=["*"]) == (0, 1)
    name = (
        await service_session.execute(
            text("SELECT name FROM platform.nodes WHERE tenant_id = :t AND key = 'n4'"),
            {"t": tenant_id},
        )
    ).scalar_one()
    assert name == "B"


@pytest.mark.asyncio
async def test_a_source_column_the_target_lacks_is_ignored(service_session: AsyncSession) -> None:
    """The conformed layer publishes more than any one target holds — `status_raw`,
    `decision_maker`, `lineage` on relations that have no such axis. Extra columns must be
    dropped, not error, or every target would have to grow a column per provider quirk."""
    tenant_id = await _tenant(service_session)
    await service_session.execute(text("DROP TABLE IF EXISTS t_orgs"))
    await service_session.execute(
        text(
            "CREATE TEMP TABLE t_orgs ("
            "  tenant_id int, provider text, key text, type platform.node_type,"
            "  name text, data jsonb, status_raw text, decision_maker text)"
        )
    )
    await service_session.execute(
        text(
            "INSERT INTO t_orgs VALUES "
            "(:t, 'seccl', 'n5', 'node', 'Five', '{}'::jsonb, 'Active', 'someone')"
        ),
        {"t": tenant_id},
    )
    assert await _merge_orgs(service_session) == (1, 0)


@pytest.mark.asyncio
async def test_a_text_landing_table_still_binds(service_session: AsyncSession) -> None:
    """The real `inbound.*` tables arrive over interlace's Arrow path, so a column the book
    declares as an enum, a numeric or a jsonb lands as TEXT — and Postgres will not
    assignment-cast text into platform.node_type ("column type is of type platform.node_type but
    expression is of type text"). Every temp table above declares the true types, which is exactly
    how 0064 shipped without noticing; 0065 casts the SELECT list to the target's own types."""
    tenant_id = await _tenant(service_session)
    await service_session.execute(text("DROP TABLE IF EXISTS t_orgs"))
    await service_session.execute(
        text(
            "CREATE TEMP TABLE t_orgs ("
            "  tenant_id int, provider text, key text, type text, name text, data text)"
        )
    )
    await service_session.execute(
        text("INSERT INTO t_orgs VALUES (:t, 'seccl', 'n6', 'node', 'Six', '{\"v\": 1}')"),
        {"t": tenant_id},
    )
    assert await _merge_orgs(service_session) == (1, 0)
    row = (
        (
            await service_session.execute(
                text("SELECT type, data FROM platform.nodes WHERE tenant_id = :t AND key = 'n6'"),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    assert str(row["type"]) == "node"
    assert row["data"] == {"v": 1}


@pytest.mark.asyncio
async def test_no_shared_columns_is_an_error_not_a_silent_no_op(
    service_session: AsyncSession,
) -> None:
    """A staging table that shares nothing with its target means the model is pointed at the wrong
    place. Silently merging zero rows would look exactly like "this firm has no data"."""
    await service_session.execute(text("DROP TABLE IF EXISTS t_wrong"))
    await service_session.execute(text("CREATE TEMP TABLE t_wrong (nothing_in_common text)"))
    with pytest.raises(Exception, match="share no columns"):
        await service_session.execute(
            text(
                "SELECT * FROM platform.merge_entity('t_wrong'::regclass,"
                " 'platform.nodes'::regclass, ARRAY['tenant_id','provider','key'])"
            )
        )
