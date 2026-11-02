"""Case memory — the pure validation rules, then the store itself against a real Postgres.

The pure half needs nothing; the DB half needs Docker (service_session → PostgresContainer) and
is where the caps, the upsert and the ``carry_memory`` copy are proved, because all three are
statements rather than functions.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows import conditions, interpolation
from app.flows import memory as memory_mod

# ---------------------------------------------------------------------------------------------
# Pure — keys, values, promotion
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["risk_band", "a", "review_2026", "a1_b2"])
def test_good_keys(key: str) -> None:
    assert memory_mod.valid_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "Risk",  # upper case — the CHECK on the column refuses it, so Python must too
        "risk band",
        "risk.band",  # dotted keys are ambiguous against the nested resolvers that read them
        "_risk",
        "risk_",
        "x" * 65,
        None,
        7,
    ],
)
def test_bad_keys(key: object) -> None:
    assert not memory_mod.valid_key(key)
    with pytest.raises(memory_mod.MemoryRefused):
        memory_mod.encode(key, "value")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("high", '"high"'),
        (7, "7"),
        (True, "true"),
        (None, "null"),
        (["a", "b"], '["a", "b"]'),
        ({"a": 1}, '{"a": 1}'),
        (Decimal("12.50"), "12.5"),
        (date(2026, 8, 4), '"2026-08-04"'),
        (datetime(2026, 8, 4, 9, 30), '"2026-08-04T09:30:00"'),
    ],
)
def test_json_safe_values(value: object, expected: str) -> None:
    assert memory_mod.encode("k", value)[1] == expected


def test_an_arbitrary_object_is_refused_not_stringified() -> None:
    """``str(value)`` would store a repr and let a branch compare against it — a value that looks
    like data and is not."""

    class Thing:
        pass

    with pytest.raises(memory_mod.MemoryRefused):
        memory_mod.encode("k", Thing())


def test_infinities_are_refused() -> None:
    with pytest.raises(memory_mod.MemoryRefused):
        memory_mod.encode("k", float("inf"))
    with pytest.raises(memory_mod.MemoryRefused):
        memory_mod.encode("k", float("nan"))


def test_a_value_over_the_per_value_cap_is_refused_not_truncated() -> None:
    with pytest.raises(memory_mod.MemoryRefused) as exc:
        memory_mod.encode("k", "x" * (memory_mod.MAX_VALUE_BYTES + 1))
    assert str(memory_mod.MAX_VALUE_BYTES) in str(exc.value)


def test_deeply_nested_values_are_refused() -> None:
    deep: object = "leaf"
    for _ in range(memory_mod.MAX_DEPTH + 2):
        deep = {"a": deep}
    with pytest.raises(memory_mod.MemoryRefused):
        memory_mod.encode("k", deep)


def test_promotions_take_only_the_authored_keys() -> None:
    """The whole point: a fact-find has hundreds of fields and the mapping names three."""
    answers = {
        "attitude_to_risk": "high",
        "national_insurance": "QQ123456C",
        "salary": 82_000,
    }
    assert memory_mod.promotions({"risk_band": "attitude_to_risk"}, answers) == {
        "risk_band": "high"
    }


def test_an_unanswered_field_is_not_promoted_as_empty() -> None:
    """ "never asked" and "answered blank" are different questions; only the author knows which."""
    assert memory_mod.promotions({"risk_band": "attitude_to_risk"}, {"salary": 1}) == {}


def test_promotions_tolerate_junk() -> None:
    assert memory_mod.promotions(None, {"a": 1}) == {}
    assert memory_mod.promotions({"k": 7}, {"a": 1}) == {}
    assert memory_mod.promotions({"k": "a"}, None) == {}


# ---------------------------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------------------------


async def _bed(sess: AsyncSession) -> tuple[int, int]:
    """A tenant + one bare case to hang memory off. Returns (tenant_id, case_id)."""
    s = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"mem_{s}", "a": f"mem-{s}", "n": f"Mem {s}"},
            )
        ).scalar_one()
    )
    flow = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
                    "VALUES (:u, :t, :c, 'F', 'published') RETURNING id"
                ),
                {"u": f"flw_{s}", "t": tenant, "c": f"code_{s}"},
            )
        ).scalar_one()
    )
    version = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status) "
                    "VALUES (:u, :t, :f, 1, 'published') RETURNING id"
                ),
                {"u": f"flv_{s}", "t": tenant, "f": flow},
            )
        ).scalar_one()
    )
    case = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO flows.cases "
                    "  (uid, tenant_id, flow_id, flow_version_id, status, kind) "
                    "VALUES (:u, :t, :f, :v, 'active', 'profile') RETURNING id"
                ),
                {"u": f"cas_{s}", "t": tenant, "f": flow, "v": version},
            )
        ).scalar_one()
    )
    return tenant, case


@pytest.mark.asyncio
async def test_write_read_and_upsert(service_session: AsyncSession) -> None:
    sess = service_session
    tenant, case = await _bed(sess)
    await memory_mod.write(sess, tenant_id=tenant, case_id=case, key="risk_band", value="high")
    assert await memory_mod.read(sess, case) == {"risk_band": "high"}
    # A variable, not a log: writing again replaces.
    await memory_mod.write(
        sess, tenant_id=tenant, case_id=case, key="risk_band", value="low", source="answers"
    )
    assert await memory_mod.read(sess, case) == {"risk_band": "low"}
    source = (
        await sess.execute(
            text("SELECT source FROM flows.case_memory WHERE case_id = :c"), {"c": case}
        )
    ).scalar_one()
    assert source == "answers"


@pytest.mark.asyncio
async def test_context_is_one_nested_object_both_resolvers_read(
    service_session: AsyncSession,
) -> None:
    """One shape, not two: a branch condition and an interpolated string must never be able to
    disagree about the same value."""
    sess = service_session
    tenant, case = await _bed(sess)
    await memory_mod.write(sess, tenant_id=tenant, case_id=case, key="risk_band", value="high")
    ctx = await memory_mod.context(sess, case)

    assert conditions.resolve_field("memory.risk_band", ctx) == "high"
    assert interpolation.render("{{ memory.risk_band }}", ctx) == "high"
    assert conditions.evaluate(
        {"field": "memory.risk_band", "operator": "equals", "value": "high"}, ctx
    )
    # A key nobody set resolves to nothing, and the fallback is what an author sees.
    assert interpolation.render("{{ memory.absent || unknown }}", ctx) == "unknown"


@pytest.mark.asyncio
async def test_the_key_count_cap_refuses_rather_than_evicting(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    tenant, case = await _bed(sess)
    for i in range(memory_mod.MAX_KEYS):
        await memory_mod.write(sess, tenant_id=tenant, case_id=case, key=f"k{i}", value=i)
    with pytest.raises(memory_mod.MemoryRefused):
        await memory_mod.write(sess, tenant_id=tenant, case_id=case, key="one_too_many", value=1)
    # Overwriting an existing key is still fine at the cap — it adds nothing.
    await memory_mod.write(sess, tenant_id=tenant, case_id=case, key="k0", value="replaced")
    assert (await memory_mod.read(sess, case))["k0"] == "replaced"


@pytest.mark.asyncio
async def test_the_total_size_cap_binds(service_session: AsyncSession) -> None:
    sess = service_session
    tenant, case = await _bed(sess)
    chunk = "x" * 3_000
    with pytest.raises(memory_mod.MemoryRefused) as exc:
        for i in range(memory_mod.MAX_KEYS):
            await memory_mod.write(sess, tenant_id=tenant, case_id=case, key=f"k{i}", value=chunk)
    assert str(memory_mod.MAX_TOTAL_BYTES) in str(exc.value)


@pytest.mark.asyncio
async def test_write_many_keeps_the_good_values_at_runtime(
    service_session: AsyncSession,
) -> None:
    """One unstorable value must not fail a step transition or take the others with it."""
    sess = service_session
    tenant, case = await _bed(sess)
    written = await memory_mod.write_many(
        sess,
        tenant_id=tenant,
        case_id=case,
        values={"good": "yes", "Bad Key": "no", "also_good": 2},
    )
    assert written == ["good", "also_good"]
    assert await memory_mod.read(sess, case) == {"good": "yes", "also_good": 2}


@pytest.mark.asyncio
async def test_write_many_strict_tells_a_caller_who_can_still_answer(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    tenant, case = await _bed(sess)
    with pytest.raises(memory_mod.MemoryRefused):
        await memory_mod.write_many(
            sess, tenant_id=tenant, case_id=case, values={"Bad Key": "no"}, strict=True
        )


@pytest.mark.asyncio
async def test_carry_copies_and_never_overwrites_the_destination(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    tenant, source_case = await _bed(sess)
    _, target_case = await _bed(sess)
    await memory_mod.write_many(
        sess, tenant_id=tenant, case_id=source_case, values={"a": 1, "b": 2}, source="start"
    )
    await memory_mod.write(sess, tenant_id=tenant, case_id=target_case, key="b", value="mine")

    copied = await memory_mod.copy_case(
        sess, tenant_id=tenant, from_case_id=source_case, to_case_id=target_case
    )
    assert copied == 1
    # Whatever seeded the child deliberately is more specific than what it inherited.
    assert await memory_mod.read(sess, target_case) == {"a": 1, "b": "mine"}


@pytest.mark.asyncio
async def test_the_per_value_cap_is_a_database_check_too(service_session: AsyncSession) -> None:
    """The Python cap is a promise; the CHECK is a fact, whatever writes the row."""
    sess = service_session
    tenant, case = await _bed(sess)
    with pytest.raises(Exception) as exc:  # noqa: B017 — any driver error proves the CHECK fired
        async with sess.begin_nested():
            await sess.execute(
                text(
                    "INSERT INTO flows.case_memory (tenant_id, case_id, key, value) "
                    "VALUES (:t, :c, 'huge', CAST(:v AS jsonb))"
                ),
                {"t": tenant, "c": case, "v": '"' + "x" * 5_000 + '"'},
            )
    assert "case_memory_value_size" in str(exc.value)
