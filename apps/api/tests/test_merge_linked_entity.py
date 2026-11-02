"""platform.merge_linked_entity (0065): identity through platform.links, against a real Postgres.

The four entities that carry an identity of their own -- contacts, accounts, models, instruments --
have no natural key and deliberately never get one, because one person legitimately exists at both
SEI and Morgan Lloyd. Every test here is a way of getting that wrong:

* a second run must not create a second person (the whole reason identity is resolved, not assumed);
* a link the merge did not mint -- a second provider's, or one an operator established -- must
  survive its runs untouched;
* the entity's typed columns are Graphene's, so a provider must not put its own values back;
* a landing table arrives over Arrow as TEXT, so the enum/date/numeric columns must still bind;
* `uid` must be filled -- by the column default now, not by the merge -- or a contact is
  unaddressable by every portal surface;
* a provider publishing the same external_id twice must still produce ONE entity.
"""

import re

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Every landing column is TEXT on purpose: interlace delivers `inbound.*` over its Arrow path, so
# the enum (`type`), the date (`dob`) and the payload (`data`) all arrive as text and the merge is
# what casts them. A temp table declaring the real types would hide exactly that.
_STAGE_CONTACTS = """
CREATE TEMP TABLE t_contacts (
  tenant_id int, external_id text, type text, name text, email text, dob text,
  data text, source_updated_at timestamptz)
"""


async def _tenant(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(text("SELECT id FROM auth.tenants ORDER BY id LIMIT 1"))
        ).scalar_one()
    )


async def _stage(session: AsyncSession, tenant_id: int, rows: list[dict[str, object]]) -> None:
    await session.execute(text("DROP TABLE IF EXISTS t_contacts"))
    await session.execute(text(_STAGE_CONTACTS))
    for row in rows:
        await session.execute(
            text(
                "INSERT INTO t_contacts"
                " (tenant_id, external_id, type, name, email, dob, data, source_updated_at)"
                " VALUES (:t, :x, 'individual', :n, :e, :dob, :d, :u)"
            ),
            {
                "t": tenant_id,
                "x": row["external_id"],
                "n": row.get("name"),
                "e": row.get("email"),
                "dob": row.get("dob", "1980-04-01"),
                "d": row.get("data", "{}"),
                "u": row.get("source_updated_at"),
            },
        )


_MERGE_SQL = text(
    "SELECT inserted, updated FROM platform.merge_linked_entity("
    "  't_contacts'::regclass, 'crm.contacts'::regclass, 'contact', :tenant, :provider, :upd)"
)


async def _merge(
    session: AsyncSession,
    tenant_id: int,
    *,
    provider: str = "seccl",
    updatable: list[str] | None = None,
) -> tuple[int, int]:
    row = (
        (
            await session.execute(
                _MERGE_SQL,
                {"tenant": tenant_id, "provider": provider, "upd": updatable or []},
            )
        )
        .mappings()
        .one()
    )
    return int(row["inserted"]), int(row["updated"])


async def _link(session: AsyncSession, tenant_id: int, provider: str, external_id: str) -> dict:
    return (
        (
            await session.execute(
                text(
                    "SELECT entity_id, data FROM platform.links"
                    " WHERE tenant_id = :t AND provider = :p AND entity_type = 'contact'"
                    "   AND link_type = 'primary' AND external_id = :x"
                ),
                {"t": tenant_id, "p": provider, "x": external_id},
            )
        )
        .mappings()
        .one()
    )


@pytest.mark.asyncio
async def test_first_run_creates_the_entity_and_its_link(service_session: AsyncSession) -> None:
    tenant_id = await _tenant(service_session)
    await _stage(
        service_session,
        tenant_id,
        [{"external_id": "mle-c1", "name": "Ada Lovelace", "data": '{"v": 1}'}],
    )
    assert await _merge(service_session, tenant_id) == (1, 0)

    link = await _link(service_session, tenant_id, "seccl", "mle-c1")
    row = (
        (
            await service_session.execute(
                text("SELECT uid, name, type, dob FROM crm.contacts WHERE id = :id"),
                {"id": link["entity_id"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["name"] == "Ada Lovelace"
    assert link["data"] == {"v": 1}, "the provider payload lives on the link, not the entity"
    # The cast pass: TEXT in, the target's own types out.
    assert str(row["type"]) == "individual"
    assert str(row["dob"]) == "1980-04-01"
    # Every portal surface addresses a contact by uid, and no provider publishes one. The merge no
    # longer mints it either (0113): it omits the column and the DEFAULT supplies it, so what this
    # asserts is the platform-wide shape from app/ids.py -- nanoid(12), NOT the 16 hex characters
    # the merge used to append. A row of the wrong length here means the default did not fire.
    assert re.fullmatch(r"[A-Za-z0-9]{12}", str(row["uid"])), row["uid"]


@pytest.mark.asyncio
async def test_a_second_run_creates_no_second_person(service_session: AsyncSession) -> None:
    """Identity is resolved through the link, so re-running the same drop is a no-op. Getting this
    wrong duplicates the client -- the bug the Morgan Lloyd contact sync actually shipped with."""
    tenant_id = await _tenant(service_session)
    rows = [{"external_id": "mle-c2", "name": "Grace Hopper", "data": '{"v": 1}'}]
    await _stage(service_session, tenant_id, rows)
    assert await _merge(service_session, tenant_id) == (1, 0)
    await _stage(service_session, tenant_id, rows)
    assert await _merge(service_session, tenant_id) == (0, 0), "nothing changed, so nothing written"
    count = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM platform.links WHERE tenant_id = :t"
                "  AND entity_type = 'contact' AND external_id = 'mle-c2'"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_the_payload_is_refreshed_but_the_person_is_not_rewritten(
    service_session: AsyncSession,
) -> None:
    """The policy: the link's data is the provider's and always current; the entity's typed
    columns are Graphene's, set once from what the provider first published."""
    tenant_id = await _tenant(service_session)
    await _stage(
        service_session,
        tenant_id,
        [{"external_id": "mle-c3", "name": "Provider Name", "data": '{"v": 1}'}],
    )
    await _merge(service_session, tenant_id)
    link = await _link(service_session, tenant_id, "seccl", "mle-c3")
    await service_session.execute(
        text("UPDATE crm.contacts SET name = 'What The Firm Calls Her' WHERE id = :id"),
        {"id": link["entity_id"]},
    )

    await _stage(
        service_session,
        tenant_id,
        [{"external_id": "mle-c3", "name": "Provider Name", "data": '{"v": 2}'}],
    )
    assert await _merge(service_session, tenant_id) == (0, 1)

    name = (
        await service_session.execute(
            text("SELECT name FROM crm.contacts WHERE id = :id"), {"id": link["entity_id"]}
        )
    ).scalar_one()
    assert name == "What The Firm Calls Her", "a provider must not overwrite a human edit"
    assert (await _link(service_session, tenant_id, "seccl", "mle-c3"))["data"] == {"v": 2}


@pytest.mark.asyncio
async def test_one_person_holds_a_link_per_provider(service_session: AsyncSession) -> None:
    """The shape the whole design exists for: the same person at Seccl AND at Morgan Lloyd, one
    crm.contacts row, a payload each. A merge must resolve through its OWN provider's link and
    leave the other alone -- a per-provider unique key on the entity could not express this.
    """
    tenant_id = await _tenant(service_session)
    await _stage(
        service_session,
        tenant_id,
        [{"external_id": "mle-c4", "name": "Joan Clarke", "data": '{"seccl": 1}'}],
    )
    await _merge(service_session, tenant_id)
    contact_id = (await _link(service_session, tenant_id, "seccl", "mle-c4"))["entity_id"]

    # The second provider's link, as an operator (or a later reconcile) would establish it:
    # cross-provider resolution is deliberately not guessed from names.
    await service_session.execute(
        text(
            "INSERT INTO platform.links"
            "  (tenant_id, entity_type, entity_id, provider, link_type, external_id, data)"
            " VALUES (:t, 'contact', :id, 'morgan-lloyd', 'primary', 'ML-778',"
            "         '{\"ml\": 1}'::jsonb)"
        ),
        {"t": tenant_id, "id": contact_id},
    )

    await _stage(
        service_session,
        tenant_id,
        [{"external_id": "mle-c4", "name": "Joan Clarke", "data": '{"seccl": 2}'}],
    )
    assert await _merge(service_session, tenant_id) == (0, 1)

    assert (await _link(service_session, tenant_id, "seccl", "mle-c4"))["data"] == {"seccl": 2}
    # `morgan-lloyd`, the provider code. This said `ml` — which was never a provider key at all,
    # only the WAREHOUSE SCHEMA name, and a text column let the confusion sit here unnoticed.
    # 0131's enum refuses it, which is the point of an enum.
    ml = await _link(service_session, tenant_id, "morgan-lloyd", "ML-778")
    assert ml["data"] == {"ml": 1}, "another provider's payload is not this run's to touch"
    assert ml["entity_id"] == contact_id, "one person, two providers"


@pytest.mark.asyncio
async def test_a_pre_minted_link_is_adopted_not_duplicated(service_session: AsyncSession) -> None:
    """How a globally-identified entity works: platform.instruments is deduped by ISIN, so the
    orchestrator mints the link to the existing row and this function must then update THROUGH it
    rather than inserting a second instrument (which the unique ISIN index would refuse anyway)."""
    tenant_id = await _tenant(service_session)
    instrument_id = (
        await service_session.execute(
            text(
                "INSERT INTO platform.instruments (isin, name) VALUES ('GB00MLE0001', 'A Fund')"
                " RETURNING id"
            )
        )
    ).scalar_one()
    await service_session.execute(
        text(
            "INSERT INTO platform.links"
            "  (tenant_id, entity_type, entity_id, provider, link_type, external_id, data)"
            " VALUES (:t, 'instrument', :id, 'seccl', 'primary', 'mle-i1', '{}'::jsonb)"
        ),
        {"t": tenant_id, "id": instrument_id},
    )
    before = (
        await service_session.execute(text("SELECT count(*) FROM platform.instruments"))
    ).scalar_one()

    await service_session.execute(text("DROP TABLE IF EXISTS t_instruments"))
    await service_session.execute(
        text("CREATE TEMP TABLE t_instruments (external_id text, isin text, name text, data text)")
    )
    await service_session.execute(
        text(
            "INSERT INTO t_instruments VALUES"
            " ('mle-i1', 'GB00MLE0001', 'A Fund, renamed upstream', '{\"v\": 2}')"
        )
    )
    row = (
        (
            await service_session.execute(
                text(
                    "SELECT inserted, updated FROM platform.merge_linked_entity("
                    "  't_instruments'::regclass, 'platform.instruments'::regclass,"
                    "  'instrument', :t, 'seccl')"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    assert (row["inserted"], row["updated"]) == (0, 1)
    after = (
        await service_session.execute(text("SELECT count(*) FROM platform.instruments"))
    ).scalar_one()
    assert after == before, "the ISIN already existed; adopting it is the point"


@pytest.mark.asyncio
async def test_a_declared_exception_is_provider_owned(service_session: AsyncSession) -> None:
    """p_updatable is the exception list -- empty here by default, where merge_entity defaults to
    {data}, because for a linked entity the payload IS links.data. Its one real member is
    platform.accounts.value: the balance is the provider's to state."""
    tenant_id = await _tenant(service_session)
    await _stage(
        service_session,
        tenant_id,
        [{"external_id": "mle-c5", "name": "First", "email": "first@example.com", "data": "{}"}],
    )
    await _merge(service_session, tenant_id, updatable=["email"])
    contact_id = (await _link(service_session, tenant_id, "seccl", "mle-c5"))["entity_id"]
    await service_session.execute(
        text("UPDATE crm.contacts SET email = 'edited@example.com', name = 'Edited' WHERE id = :i"),
        {"i": contact_id},
    )

    await _stage(
        service_session,
        tenant_id,
        [{"external_id": "mle-c5", "name": "Second", "email": "second@example.com", "data": "{}"}],
    )
    assert await _merge(service_session, tenant_id, updatable=["email"]) == (0, 1)

    row = (
        (
            await service_session.execute(
                text("SELECT name, email FROM crm.contacts WHERE id = :i"), {"i": contact_id}
            )
        )
        .mappings()
        .one()
    )
    assert row["email"] == "second@example.com", "a column named in p_updatable is the provider's"
    assert row["name"] == "Edited", "one named column does not hand over the rest of the row"


@pytest.mark.asyncio
async def test_a_duplicate_external_id_yields_one_person(service_session: AsyncSession) -> None:
    """Provider feeds really do publish one id twice (the conformed layer found 6,123 duplicate
    assets when key checks were added). Two entities for one thing is the failure links exist to
    prevent, so the survivor is the provider's latest statement rather than an arbitrary row."""
    tenant_id = await _tenant(service_session)
    await _stage(
        service_session,
        tenant_id,
        [
            {
                "external_id": "mle-c6",
                "name": "Stale",
                "data": '{"v": 1}',
                "source_updated_at": "2026-01-01T00:00:00Z",
            },
            {
                "external_id": "mle-c6",
                "name": "Current",
                "data": '{"v": 2}',
                "source_updated_at": "2026-06-01T00:00:00Z",
            },
        ],
    )
    assert await _merge(service_session, tenant_id) == (1, 0)
    link = await _link(service_session, tenant_id, "seccl", "mle-c6")
    name = (
        await service_session.execute(
            text("SELECT name FROM crm.contacts WHERE id = :i"), {"i": link["entity_id"]}
        )
    ).scalar_one()
    assert name == "Current"
    assert link["data"] == {"v": 2}


@pytest.mark.asyncio
async def test_a_source_with_no_external_id_is_refused(service_session: AsyncSession) -> None:
    """It has no identity, so every run would insert the whole slice again. Failing loudly beats
    a book that grows a duplicate book every night."""
    await service_session.execute(text("DROP TABLE IF EXISTS t_anon"))
    await service_session.execute(text("CREATE TEMP TABLE t_anon (name text, data text)"))
    with pytest.raises(Exception, match="no external_id"):
        await service_session.execute(
            text(
                "SELECT * FROM platform.merge_linked_entity('t_anon'::regclass,"
                " 'crm.contacts'::regclass, 'contact', 1, 'seccl')"
            )
        )
