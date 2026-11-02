"""Tags — the service, the RLS boundary, the domain fact, and what conditions can see.

The RLS assertions are the important ones. A tag is staff shorthand about a client
(``chaser-sent``, ``slow-payer``, ``complaint-open``) and the client-portal principal must not be
able to read their own — the policies deliberately omit ``auth.contact_scope()``, which is the
opposite of ``crm.kyc_checks`` (0026) and the same reasoning as the AML tipping-off control. That
is an easy thing to "fix" for symmetry by someone who has not read the migration, so it is pinned
both behaviourally and as a policy-text assertion.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import tags as tags_mod
from app.flows import conditions
from tests.conftest import principal_conn

Maker = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class _Bed:
    def __init__(self, maker: Maker, nonce: str, ids: dict[str, Any]) -> None:
        self.maker = maker
        self.nonce = nonce
        self.tenant_id = int(ids["tenant_id"])
        self.other_tenant_id = int(ids["other_tenant_id"])
        self.contact_id = int(ids["contact_id"])
        self.other_contact_id = int(ids["other_contact_id"])
        self.user_id = str(ids["user_id"])


@pytest_asyncio.fixture
async def bed(maker: Maker) -> _Bed:
    nonce = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))

        async def tenant(prefix: str) -> int:
            return int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO auth.tenants (uid, alias, name) "
                            "VALUES (:u, :a, :n) RETURNING id"
                        ),
                        {
                            "u": f"{prefix}_{nonce}",
                            "a": f"{prefix}-{nonce}",
                            "n": f"{prefix} {nonce}",
                        },
                    )
                ).scalar_one()
            )

        async def contact(tenant_id: int, prefix: str) -> int:
            return int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO crm.contacts (tenant_id, uid, name, type, service_type) "
                            "VALUES (:t, :u, 'Ada', 'individual', 'advised') RETURNING id"
                        ),
                        {"t": tenant_id, "u": f"con_{prefix}_{nonce}"},
                    )
                ).scalar_one()
            )

        tenant_id = await tenant("tag")
        other_tenant_id = await tenant("tgo")
        contact_id = await contact(tenant_id, "a")
        other_contact_id = await contact(other_tenant_id, "b")
        user_id = str(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, workos_user_id, email, "
                        "  first_name) VALUES (:uid, :t, :w, :e, 'Grace') RETURNING id"
                    ),
                    {
                        "uid": f"usr_{nonce}",
                        "t": tenant_id,
                        "w": f"user_{nonce}",
                        "e": f"g.{nonce}@example.test",
                    },
                )
            ).scalar_one()
        )
    return _Bed(
        maker,
        nonce,
        {
            "tenant_id": tenant_id,
            "other_tenant_id": other_tenant_id,
            "contact_id": contact_id,
            "other_contact_id": other_contact_id,
            "user_id": user_id,
        },
    )


# ---------------------------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("vip", "vip"),
        ("VIP", "vip"),
        ("  VIP  ", "vip"),
        ("High  Risk", "high risk"),
        ("high\trisk", "high risk"),
        ("high\nrisk", "high risk"),
        ("Chaser-Sent", "chaser-sent"),
        ("ÉLITE", "élite"),
    ],
)
def test_normalise(raw: str, expected: str) -> None:
    """One label set, not one per typist. Without this an idempotent apply is not idempotent and
    no filter can be written."""
    assert tags_mod.normalise(raw) == expected


@pytest.mark.parametrize("bad", ["", "   ", "\t\n", "x" * 65])
def test_normalise_refuses_junk(bad: str) -> None:
    with pytest.raises(tags_mod.TagError):
        tags_mod.normalise(bad)


def test_normalise_refuses_a_non_string() -> None:
    with pytest.raises(tags_mod.TagError):
        tags_mod.normalise(42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------
# apply / remove / list
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_is_idempotent_and_reports_whether_it_changed(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        first = await tags_mod.apply(
            s, tenant_id=bed.tenant_id, target="contact", target_id=bed.contact_id, tag="VIP"
        )
        second = await tags_mod.apply(
            s, tenant_id=bed.tenant_id, target="contact", target_id=bed.contact_id, tag="  vip "
        )
        listed = await tags_mod.list_for(s, target="contact", target_id=bed.contact_id)

    assert first is True
    assert second is False  # the honest "nothing changed" the executor reports on
    assert listed == ["vip"]


@pytest.mark.asyncio
async def test_remove_is_idempotent_too(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await tags_mod.apply(
            s, tenant_id=bed.tenant_id, target="contact", target_id=bed.contact_id, tag="vip"
        )
        first = await tags_mod.remove(
            s, tenant_id=bed.tenant_id, target="contact", target_id=bed.contact_id, tag="VIP"
        )
        second = await tags_mod.remove(
            s, tenant_id=bed.tenant_id, target="contact", target_id=bed.contact_id, tag="vip"
        )
        listed = await tags_mod.list_for(s, target="contact", target_id=bed.contact_id)

    assert first is True
    assert second is False
    assert listed == []


@pytest.mark.asyncio
async def test_list_is_sorted(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        for tag in ("zulu", "alpha", "mike"):
            await tags_mod.apply(
                s, tenant_id=bed.tenant_id, target="contact", target_id=bed.contact_id, tag=tag
            )
        assert await tags_mod.list_for(s, target="contact", target_id=bed.contact_id) == [
            "alpha",
            "mike",
            "zulu",
        ]


@pytest.mark.asyncio
async def test_remove_cannot_reach_another_firms_tag(bed: _Bed) -> None:
    """The service is tenant-scoped even on a service_role session, which has no RLS to lean on."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await tags_mod.apply(
            s,
            tenant_id=bed.other_tenant_id,
            target="contact",
            target_id=bed.other_contact_id,
            tag="vip",
        )
        removed = await tags_mod.remove(
            s,
            tenant_id=bed.tenant_id,  # the WRONG firm
            target="contact",
            target_id=bed.other_contact_id,
            tag="vip",
        )
        still_there = await tags_mod.list_for(s, target="contact", target_id=bed.other_contact_id)

    assert removed is False
    assert still_there == ["vip"]


@pytest.mark.asyncio
async def test_an_unknown_target_is_refused(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        with pytest.raises(tags_mod.TagError):
            await tags_mod.apply(
                s,
                tenant_id=bed.tenant_id,
                target="account",  # type: ignore[arg-type]
                target_id=1,
                tag="vip",
            )


@pytest.mark.asyncio
async def test_an_unknown_source_is_refused(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        with pytest.raises(tags_mod.TagError):
            await tags_mod.apply(
                s,
                tenant_id=bed.tenant_id,
                target="contact",
                target_id=bed.contact_id,
                tag="vip",
                source="whatever",
            )


@pytest.mark.asyncio
async def test_tags_by_contact_batches(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await tags_mod.apply(
            s, tenant_id=bed.tenant_id, target="contact", target_id=bed.contact_id, tag="vip"
        )
        assert await tags_mod.tags_by_contact(
            s, tenant_id=bed.tenant_id, contact_ids=[bed.contact_id]
        ) == {bed.contact_id: ["vip"]}
        assert await tags_mod.tags_by_contact(s, tenant_id=bed.tenant_id, contact_ids=[]) == {}


# ---------------------------------------------------------------------------------------------
# The tags.applied domain fact
# ---------------------------------------------------------------------------------------------


async def _facts(bed: _Bed) -> list[dict[str, Any]]:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT event_type, payload, idempotency_key, case_id, execution_id "
                        "FROM flows.domain_events WHERE tenant_id = :t ORDER BY id"
                    ),
                    {"t": bed.tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_applying_a_tag_emits_the_domain_fact(bed: _Bed) -> None:
    """The contract's ``tag.applied`` trigger reads this. It is written in the SAME transaction as
    the tag row, because composition ("when a client is tagged X, start journey Y") is exactly the
    kind of subscriber that must not silently miss an event."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await tags_mod.apply(
            s,
            tenant_id=bed.tenant_id,
            target="contact",
            target_id=bed.contact_id,
            tag="Ready To Invest",
            source="automation",
        )

    facts = await _facts(bed)
    assert len(facts) == 1
    assert facts[0]["event_type"] == tags_mod.APPLIED_EVENT == "tags.applied"
    assert facts[0]["payload"] == {
        "target": "contact",
        "target_id": bed.contact_id,
        "tag": "ready to invest",
        "source": "automation",
        "applied_by": None,
    }


@pytest.mark.asyncio
async def test_the_fact_is_named_by_the_trigger_registry(bed: _Bed) -> None:
    """Pins the event name against the registry, so renaming one without the other fails here
    rather than by an automation quietly never firing."""
    from app.flows import triggers

    assert triggers.TRIGGERS["tag.applied"].event == tags_mod.APPLIED_EVENT


@pytest.mark.asyncio
async def test_a_no_op_apply_emits_no_second_fact(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        for _ in range(3):
            await tags_mod.apply(
                s, tenant_id=bed.tenant_id, target="contact", target_id=bed.contact_id, tag="vip"
            )
    assert len(await _facts(bed)) == 1


@pytest.mark.asyncio
async def test_removing_a_tag_emits_no_fact(bed: _Bed) -> None:
    """No ``tags.removed`` event, deliberately: nothing consumes one and the contract's trigger
    menu offers no such entry. The DML audit trigger is the evidence."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await tags_mod.apply(
            s, tenant_id=bed.tenant_id, target="contact", target_id=bed.contact_id, tag="vip"
        )
        await tags_mod.remove(
            s, tenant_id=bed.tenant_id, target="contact", target_id=bed.contact_id, tag="vip"
        )
    assert [f["event_type"] for f in await _facts(bed)] == ["tags.applied"]


# ---------------------------------------------------------------------------------------------
# The condition context
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conditions_can_read_a_contacts_tags(bed: _Bed) -> None:
    """The whole point of the substrate: one automation tags, another triggers on the tag. The
    consumer's fact-builder must therefore expose ``contact.tags`` where the evaluator looks."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from app.flows import automation, triggers

    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await tags_mod.apply(
            s, tenant_id=bed.tenant_id, target="contact", target_id=bed.contact_id, tag="vip"
        )
        facts = await automation.build_facts(
            s,
            spec=triggers.TRIGGERS["contact.bank_details_changed"],
            event=None,
            tenant_id=bed.tenant_id,
            subject_id=bed.contact_id,
            tz=ZoneInfo("Europe/London"),
            now=datetime.now(UTC),
        )

    assert facts["contact.tags"] == ["vip"]
    # ...and the evaluator agrees, using the operators an author would reach for.
    assert conditions.evaluate(
        {"field": "contact.tags", "operator": "contains", "value": "vip"}, facts
    )
    assert conditions.evaluate(
        {"field": "contact.tags", "operator": "notContains", "value": "slow-payer"}, facts
    )
    assert conditions.evaluate({"field": "contact.tags", "operator": "isNotEmpty"}, facts)


@pytest.mark.asyncio
async def test_an_untagged_client_reads_as_an_empty_list_not_a_missing_field(bed: _Bed) -> None:
    """An absent field would make ``isEmpty`` behave differently for "no tags" than for "we did
    not look", which is the sort of asymmetry that makes a rule fire at the wrong people."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from app.flows import automation, triggers

    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        facts = await automation.build_facts(
            s,
            spec=triggers.TRIGGERS["contact.bank_details_changed"],
            event=None,
            tenant_id=bed.tenant_id,
            subject_id=bed.contact_id,
            tz=ZoneInfo("Europe/London"),
            now=datetime.now(UTC),
        )

    assert facts["contact.tags"] == []
    assert conditions.evaluate({"field": "contact.tags", "operator": "isEmpty"}, facts)


@pytest.mark.asyncio
async def test_a_missing_contact_gets_no_contact_keys_at_all(bed: _Bed) -> None:
    """Adding ``contact.tags`` must not make a DELETED client look like a resolved one.

    ``build_facts`` fills a subject's scope only when the row exists in this tenant, and the rules
    API's dry-run reports "no client found" by testing whether ANY key in that scope is present.
    Writing the tags key unconditionally (which the first cut of this did) quietly broke that."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from app.flows import automation, triggers

    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        facts = await automation.build_facts(
            s,
            spec=triggers.TRIGGERS["contact.bank_details_changed"],
            event=None,
            tenant_id=bed.tenant_id,
            subject_id=999_999_999,
            tz=ZoneInfo("Europe/London"),
            now=datetime.now(UTC),
        )

    assert not [k for k in facts if k.startswith("contact.")]


@pytest.mark.asyncio
async def test_another_firms_contact_is_equally_invisible(bed: _Bed) -> None:
    """Same invariant, the tenant-isolation case: a real contact id belonging to someone else."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from app.flows import automation, triggers

    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await tags_mod.apply(
            s,
            tenant_id=bed.other_tenant_id,
            target="contact",
            target_id=bed.other_contact_id,
            tag="vip",
        )
        facts = await automation.build_facts(
            s,
            spec=triggers.TRIGGERS["contact.bank_details_changed"],
            event=None,
            tenant_id=bed.tenant_id,
            subject_id=bed.other_contact_id,
            tz=ZoneInfo("Europe/London"),
            now=datetime.now(UTC),
        )

    assert not [k for k in facts if k.startswith("contact.")]


def test_the_tag_fields_are_in_the_condition_registry() -> None:
    assert "contact.tags" in conditions.FIELD_SCOPES["contact"]
    assert "case.tags" in conditions.FIELD_SCOPES["case"]


# ---------------------------------------------------------------------------------------------
# RLS — the tipping-off-adjacent read rule
# ---------------------------------------------------------------------------------------------


def _staff_claims(bed: _Bed) -> dict[str, object]:
    return {
        "tenant_id": bed.tenant_id,
        "tenant_lineage": [bed.tenant_id],
        "contact_scope": [],
        "permissions": ["contacts:write"],
        "portal": "platform",
    }


def _client_claims(bed: _Bed) -> dict[str, object]:
    """A client-portal principal: no lineage (that is what locks them out), their own contact in
    scope."""
    return {
        "tenant_id": bed.tenant_id,
        "tenant_lineage": [],
        "contact_scope": [bed.contact_id],
        "permissions": [],
        "portal": "client",
    }


def _other_firm_claims(bed: _Bed) -> dict[str, object]:
    return {
        "tenant_id": bed.other_tenant_id,
        "tenant_lineage": [bed.other_tenant_id],
        "contact_scope": [],
        "permissions": ["contacts:write"],
        "portal": "platform",
    }


@pytest.fixture
def seeded_tag(bed: _Bed, super_engine: Engine) -> None:
    with super_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO crm.contact_tags (tenant_id, contact_id, tag) "
                "VALUES (:t, :c, 'complaint-open')"
            ),
            {"t": bed.tenant_id, "c": bed.contact_id},
        )


def test_staff_read_their_own_firms_tags(bed: _Bed, app_engine: Engine, seeded_tag: None) -> None:
    with principal_conn(app_engine, _staff_claims(bed)) as conn:
        rows = (
            conn.execute(
                text("SELECT tag FROM crm.contact_tags WHERE contact_id = :c"),
                {"c": bed.contact_id},
            )
            .scalars()
            .all()
        )
    assert list(rows) == ["complaint-open"]


def test_another_firm_reads_nothing(bed: _Bed, app_engine: Engine, seeded_tag: None) -> None:
    with principal_conn(app_engine, _other_firm_claims(bed)) as conn:
        rows = (
            conn.execute(
                text("SELECT tag FROM crm.contact_tags WHERE contact_id = :c"),
                {"c": bed.contact_id},
            )
            .scalars()
            .all()
        )
    assert list(rows) == []


def test_a_client_cannot_read_their_own_tags(
    bed: _Bed, app_engine: Engine, seeded_tag: None
) -> None:
    """DO NOT "fix" this for symmetry with crm.kyc_checks. A tag is staff shorthand about a client
    — 'complaint-open', 'slow-payer' — and the client portal must never surface it. The policy
    omits auth.contact_scope() entirely, and tenant_lineage() is empty for a client principal, so
    the omission alone is the lock."""
    with principal_conn(app_engine, _client_claims(bed)) as conn:
        rows = (
            conn.execute(
                text("SELECT tag FROM crm.contact_tags WHERE contact_id = :c"),
                {"c": bed.contact_id},
            )
            .scalars()
            .all()
        )
    assert list(rows) == []


def test_the_read_policies_do_not_mention_contact_scope(app_engine: Engine) -> None:
    """The behavioural test above could pass for the wrong reason (a claims shape that happens to
    be empty). This asserts the POLICY TEXT, the same belt test_aml_rls.py uses — so adding
    contact_scope to the policy fails here even if no test client happens to notice."""
    with app_engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT tablename, policyname, qual FROM pg_policies "
                    "WHERE (schemaname = 'crm' AND tablename = 'contact_tags') "
                    "   OR (schemaname = 'flows' AND tablename = 'case_tags')"
                )
            )
            .mappings()
            .all()
        )
    assert rows, "the tag policies are missing entirely"
    for row in rows:
        assert "contact_scope" not in (row["qual"] or ""), (
            f"{row['tablename']}.{row['policyname']} exposes tags to the client portal — a tag is "
            "staff shorthand about a client and must not be readable by them (0044_tags.sql)"
        )
        assert "tenant_lineage" in (row["qual"] or "")


def test_a_client_cannot_write_a_tag(bed: _Bed, app_engine: Engine) -> None:
    from sqlalchemy.exc import ProgrammingError

    with principal_conn(app_engine, _client_claims(bed)) as conn, pytest.raises(ProgrammingError):
        conn.execute(
            text(
                "INSERT INTO crm.contact_tags (tenant_id, contact_id, tag) "
                "VALUES (:t, :c, 'self-awarded')"
            ),
            {"t": bed.tenant_id, "c": bed.contact_id},
        )


def test_staff_cannot_tag_another_firms_client(bed: _Bed, app_engine: Engine) -> None:
    from sqlalchemy.exc import ProgrammingError

    with principal_conn(app_engine, _staff_claims(bed)) as conn, pytest.raises(ProgrammingError):
        conn.execute(
            text(
                "INSERT INTO crm.contact_tags (tenant_id, contact_id, tag) "
                "VALUES (:t, :c, 'poached')"
            ),
            {"t": bed.other_tenant_id, "c": bed.other_contact_id},
        )


# ---------------------------------------------------------------------------------------------
# The database's own backstop
# ---------------------------------------------------------------------------------------------


def test_the_check_refuses_an_unnormalised_tag_written_around_the_service(
    bed: _Bed, super_engine: Engine
) -> None:
    """The service normalises; the CHECK is the belt for any writer that bypasses it."""
    from sqlalchemy.exc import IntegrityError

    for bad in ("VIP", " vip", ""):
        with super_engine.begin() as c, pytest.raises(IntegrityError):
            c.execute(
                text(
                    "INSERT INTO crm.contact_tags (tenant_id, contact_id, tag) "
                    "VALUES (:t, :c, :tag)"
                ),
                {"t": bed.tenant_id, "c": bed.contact_id, "tag": bad},
            )


def test_tags_die_with_their_contact(bed: _Bed, super_engine: Engine) -> None:
    """ON DELETE CASCADE, which is why there are two real tables rather than one polymorphic one."""
    with super_engine.begin() as c:
        c.execute(
            text("INSERT INTO crm.contact_tags (tenant_id, contact_id, tag) VALUES (:t, :c, 'x')"),
            {"t": bed.tenant_id, "c": bed.contact_id},
        )
        c.execute(text("DELETE FROM crm.contacts WHERE id = :c"), {"c": bed.contact_id})
        left = c.execute(
            text("SELECT count(*) FROM crm.contact_tags WHERE contact_id = :c"),
            {"c": bed.contact_id},
        ).scalar_one()
    assert left == 0
