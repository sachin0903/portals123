"""0257 adds the W-9 or W-8 question to every firm's seeded Additional information form.

Run as SQL against a real Postgres, because `documents.form_fields` and `flows.flow_versions`
are FORCE RLS, and an UPDATE that runs as the wrong role matches zero rows and still exits 0.
0239 shipped exactly that.
"""

import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_ROOT = Path(__file__).resolve().parent.parent / "migrations"
# Escaped the way `upgrade()` escapes it: the driver reads `%` as a placeholder.
_SQL = (_ROOT / "sql/0257_fatca_status_question.sql").read_text(encoding="utf-8").replace("%", "%%")

_spec = importlib.util.spec_from_file_location(
    "m0257", _ROOT / "versions/0257_fatca_status_question.py"
)
assert _spec is not None and _spec.loader is not None
_migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration)

_OLD_HINT = "Yes records a W-9. No records a W-8."
_US_MAPPING = {"source": "us_person", "target": "us_person"}
_HELD_MAPPING = {"source": "fatca_status", "target": "fatca_status"}
_PEP_MAPPING = {"source": "pep", "target": "pep"}


async def _tenant(sess: AsyncSession, uid: str) -> int:
    return int(
        (
            await sess.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:u, :u, 'FATCA question') RETURNING id"
                ),
                {"u": uid},
            )
        ).scalar_one()
    )


async def _form(sess: AsyncSession, tenant: int, uid: str, code: str) -> int:
    return int(
        (
            await sess.execute(
                text(
                    "INSERT INTO documents.forms (uid, tenant_id, code, name, status, entity_type) "
                    "VALUES (:u, :t, :c, 'Additional information', 'published', 'applicant') "
                    "RETURNING id"
                ),
                {"u": uid, "t": tenant, "c": code},
            )
        ).scalar_one()
    )


async def _field(
    sess: AsyncSession, tenant: int, form: int, key: str, order: int, hint: str | None = None
) -> None:
    await sess.execute(
        text(
            "INSERT INTO documents.form_fields "
            "  (uid, tenant_id, form_id, field_key, field_type, label, description, required, "
            "   sort_order) "
            "VALUES (:u, :t, :f, :k, 'select', :k, :d, true, :o)"
        ),
        {"u": f"m0256_{form}_{key}", "t": tenant, "f": form, "k": key, "d": hint, "o": order},
    )


async def _fields(sess: AsyncSession, form: int) -> list[tuple[str, int, bool, str | None]]:
    rows = await sess.execute(
        text(
            "SELECT field_key, sort_order, required, description FROM documents.form_fields "
            "WHERE form_id = :f ORDER BY sort_order, field_key"
        ),
        {"f": form},
    )
    return [(r.field_key, r.sort_order, r.required, r.description) for r in rows]


async def _flow(sess: AsyncSession, tenant: int, uid: str, mapping: list[dict[str, str]]) -> int:
    flow = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO flows.flows (uid, tenant_id, code, name) "
                    "VALUES (:u, :t, :u, 'Onboarding') RETURNING id"
                ),
                {"u": uid, "t": tenant},
            )
        ).scalar_one()
    )
    definition = {"steps": [{"id": "additional_info", "config": {"mapping": mapping}}]}
    return int(
        (
            await sess.execute(
                text(
                    "INSERT INTO flows.flow_versions "
                    "  (uid, tenant_id, flow_id, version, status, definition) "
                    "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb)) RETURNING id"
                ),
                {"u": f"{uid}_v1", "t": tenant, "f": flow, "d": json.dumps(definition)},
            )
        ).scalar_one()
    )


async def _mapping(sess: AsyncSession, version: int) -> list[dict[str, str]]:
    definition = (
        await sess.execute(
            text("SELECT definition FROM flows.flow_versions WHERE id = :v"), {"v": version}
        )
    ).scalar_one()
    return list(definition["steps"][0]["config"]["mapping"])


async def _run(sess: AsyncSession, sql: str) -> None:
    connection = await sess.connection()
    await connection.exec_driver_sql(sql)
    # The migration's own `RESET ROLE` hands the session back to the request role.
    await sess.execute(text("SET LOCAL ROLE service_role"))


@pytest.mark.asyncio
async def test_the_question_lands_after_us_person_and_comes_off_again(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    tenant = await _tenant(sess, "m0256_t")
    seeded = await _form(sess, tenant, "m0256_f1", "applicant_offshore")
    await _field(sess, tenant, seeded, "source_of_wealth", 0)
    await _field(sess, tenant, seeded, "us_person", 1, _OLD_HINT)
    await _field(sess, tenant, seeded, "pep", 2)
    # A form a firm authored under another code is not the seeded one.
    authored = await _form(sess, tenant, "m0256_f2", "firm_kyc")
    await _field(sess, tenant, authored, "us_person", 0, _OLD_HINT)
    version = await _flow(sess, tenant, "m0256_flow", [_US_MAPPING, _PEP_MAPPING])
    before_seeded, before_authored = await _fields(sess, seeded), await _fields(sess, authored)

    await _run(sess, _SQL)

    assert await _fields(sess, seeded) == [
        ("source_of_wealth", 0, True, None),
        ("us_person", 1, True, "A US citizen or US tax resident."),
        ("fatca_status", 2, False, "Only needed for US assets."),
        ("pep", 3, True, None),
    ]
    assert await _fields(sess, authored) == before_authored
    assert await _mapping(sess, version) == [_US_MAPPING, _HELD_MAPPING, _PEP_MAPPING]

    # Running it again adds nothing: the form and the mapping already have the question.
    await _run(sess, _SQL)
    assert [key for key, *_ in await _fields(sess, seeded)].count("fatca_status") == 1
    assert (await _mapping(sess, version)).count(_HELD_MAPPING) == 1

    await _run(sess, _migration._DOWN)
    assert await _fields(sess, seeded) == before_seeded
    assert await _mapping(sess, version) == [_US_MAPPING, _PEP_MAPPING]
