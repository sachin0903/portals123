"""A SEEDED PRESET IS A SNAPSHOT, and until now nothing could move it.

`load_defaults` is idempotent by SKIPPING — "skips codes the tenant already has" — so a preset
seeded on one day stays as it was for ever, whatever `email/defaults.py` grows afterwards. That is
correct for a firm's own authoring and wrong for the MODEL tenant, because every new firm CLONES
the model tenant's published presets (`clone_published`): the model tenant's age is the age of
every firm created after it.

Measured on a real deployment before this: the model tenant's seven presets were three weeks older
than the shipped defaults and their `schema` held only `{css, html}` — no editor `source` at all,
where a current default carries one. So a new firm's canvas opened on loose Jinja instead of a
Brand logo component, and three rounds of improving the default reached nobody.

`refresh_defaults` publishes the current default over an UNTOUCHED preset, and the definition of
untouched is read from the data: every version must carry `authored_by_scope = 'operations'`. One
firm-authored version anywhere in the history and the preset belongs to the firm.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import Engine, text

from app.email import catalog as email_catalog
from app.email import defaults as email_defaults
from app.email import templates_service as ets
from tests import email_support

SEEDED_CODE = "invite"

#: Read AT IMPORT. The test that asserts on it is async, and reading a file inside an async test is
#: what ASYNC240 refuses — it blocks the loop. The assertion is about the router's SOURCE, which
#: does not change while the suite runs, so import time is where the read belongs anyway.
ADMIN_ROUTER = (Path(__file__).resolve().parents[1] / "app" / "routers" / "admin.py").read_text(
    encoding="utf-8"
)


@pytest.fixture
def staff(super_engine: Engine) -> dict[str, str]:
    return email_support.make_firm(super_engine)


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], staff: dict[str, str]):
    opener, engine = email_support.session_opener(db, email_support.firm_claims(staff))
    yield opener
    await engine.dispose()


def _actor(staff: dict[str, str]) -> str:
    return staff["user_uuid"]


async def _published(session, tenant_id: int, code: str) -> dict:
    return (
        (
            await session.execute(
                text(
                    "SELECT v.version_number, v.subject, v.authored_by_scope, "
                    "  v.schema ->> 'html' AS html, v.schema ->> 'source' AS source "
                    "FROM email.templates t "
                    "JOIN email.template_versions v ON v.id = t.current_published_version_id "
                    "WHERE t.tenant_id = :t AND t.code = :code"
                ),
                {"t": tenant_id, "code": code},
            )
        )
        .mappings()
        .one()
    )


@pytest.mark.asyncio
async def test_a_stale_untouched_preset_is_brought_up_to_the_shipped_default(
    session_factory, staff: dict[str, str]
) -> None:
    tenant_id = int(staff["tenant_id"])
    actor = _actor(staff)
    default = email_defaults.default_for(SEEDED_CODE)
    assert default is not None

    async with session_factory() as session:
        # SEEDED AS THE OLD SEEDER WOULD HAVE, not aged afterwards: 0062 makes a published version
        # immutable in the DATABASE, so an UPDATE here is refused by a trigger — which is the whole
        # reason a refresh has to publish a new version rather than edit the old one. The stale
        # shape is the real one: last month's markup, and NO editor `source`, which is what left a
        # cloned firm's canvas holding loose Jinja.
        await ets._insert_published(
            session,
            tenant_id=tenant_id,
            actor_id=actor,
            code=SEEDED_CODE,
            name="Invite",
            category="transactional",
            description=None,
            subject="Last month's subject",
            preheader="",
            schema={"html": "<p>last month's masthead</p>", "css": ""},
            scope="operations",
        )

        stale = await _published(session, tenant_id, SEEDED_CODE)
        assert stale["source"] is None  # the state that mattered

        counts = await ets.refresh_defaults(session, tenant_id, actor)
        assert counts["refreshed"] >= 1
        assert counts["authored"] == 0

        fresh = await _published(session, tenant_id, SEEDED_CODE)
        assert fresh["html"] == default.html
        assert fresh["source"] == default.source
        # A NEW version, not an edit: 0062 makes a published version immutable, and the previous
        # text stays readable in the history.
        assert fresh["version_number"] > stale["version_number"]
        assert fresh["authored_by_scope"] == "operations"


@pytest.mark.asyncio
async def test_a_preset_the_firm_has_authored_is_never_overwritten(
    session_factory, staff: dict[str, str]
) -> None:
    """THE SAFETY RULE. An operator pressing "load defaults" must not be able to replace a firm's
    own published client-facing copy — so one firm-authored version in the history is enough to put
    the preset permanently out of reach."""
    tenant_id = int(staff["tenant_id"])
    actor = _actor(staff)

    async with session_factory() as session:
        # The firm's own published copy, stamped as theirs — the state a refresh must not touch.
        await ets._insert_published(
            session,
            tenant_id=tenant_id,
            actor_id=actor,
            code=SEEDED_CODE,
            name="Invite",
            category="transactional",
            description=None,
            subject="Our own words",
            preheader="",
            schema={"html": "<p>ours</p>", "css": ""},
            scope="firm",
        )

        counts = await ets.refresh_defaults(session, tenant_id, actor)
        assert counts["authored"] >= 1

        kept = await _published(session, tenant_id, SEEDED_CODE)
        assert kept["subject"] == "Our own words"
        assert kept["html"] == "<p>ours</p>"


@pytest.mark.asyncio
async def test_force_republishes_an_authored_preset_and_no_route_can_ask_for_it(
    session_factory, staff: dict[str, str]
) -> None:
    """The development escape hatch, and the proof it stays out of reach of an operator.

    A dev box being reset to the shipped set has "firm authoring" that is a developer's own test
    edit, so `force` republishes those too. What must never happen is an operator reaching it: the
    admin route's flag is `refresh`, it calls `refresh_defaults(session, tenant_id, actor)` and
    passes nothing else, so a firm's own published copy cannot be replaced through the API at all.
    Asserted on the router's source, because that is the property — not the value of a parameter.
    """
    tenant_id = int(staff["tenant_id"])
    actor = _actor(staff)
    default = email_defaults.default_for(SEEDED_CODE)
    assert default is not None

    async with session_factory() as session:
        await ets._insert_published(
            session,
            tenant_id=tenant_id,
            actor_id=actor,
            code=SEEDED_CODE,
            name="Invite",
            category="transactional",
            description=None,
            subject="Our own words",
            preheader="",
            schema={"html": "<p>ours</p>", "css": ""},
            scope="firm",
        )
        assert (await ets.refresh_defaults(session, tenant_id, actor))["authored"] == 1

        counts = await ets.refresh_defaults(session, tenant_id, actor, force=True)
        assert counts["authored"] == 0
        assert counts["refreshed"] >= 1
        after = await _published(session, tenant_id, SEEDED_CODE)
        assert after["html"] == default.html
        # The firm's own words are still in the history, so this is reversible from the editor.
        kept = (
            await session.execute(
                text(
                    "SELECT count(*) FROM email.template_versions v "
                    "JOIN email.templates t ON t.id = v.template_id "
                    "WHERE t.tenant_id = :t AND t.code = :c AND v.subject = 'Our own words'"
                ),
                {"t": tenant_id, "c": SEEDED_CODE},
            )
        ).scalar_one()
        assert kept == 1

    call = re.search(r"email_templates_service\.refresh_defaults\([^)]*\)", ADMIN_ROUTER)
    assert call, "the admin route no longer calls refresh_defaults"
    assert "force" not in call.group(0), (
        "the admin route passes `force` — a firm's own published copy must not be replaceable "
        "through the API, and the audit row would say 'load defaults' about it"
    )


@pytest.mark.asyncio
async def test_a_current_preset_costs_nothing_and_says_so(
    session_factory, staff: dict[str, str]
) -> None:
    """`refreshed: 0` has to be distinguishable from "nothing happened because it could not" —
    which is why the answer names every outcome rather than returning one number."""
    tenant_id = int(staff["tenant_id"])
    actor = _actor(staff)

    async with session_factory() as session:
        await ets.load_defaults(session, tenant_id, actor)
        before = await _published(session, tenant_id, SEEDED_CODE)

        counts = await ets.refresh_defaults(session, tenant_id, actor)

        assert counts["refreshed"] == 0
        assert counts["current"] >= 1
        after = await _published(session, tenant_id, SEEDED_CODE)
        assert after["version_number"] == before["version_number"]


@pytest.mark.asyncio
async def test_refresh_also_creates_a_code_the_tenant_never_had(
    session_factory, staff: dict[str, str]
) -> None:
    """It is a superset of seeding: one button, rather than two that must be run in order."""
    tenant_id = int(staff["tenant_id"])
    actor = _actor(staff)
    with_defaults = [
        e.code for e in email_catalog.KNOWN_CODES if email_defaults.default_for(e.code) is not None
    ]
    assert with_defaults, "no catalogue code ships a default — the fixture is wrong"

    async with session_factory() as session:
        counts = await ets.refresh_defaults(session, tenant_id, actor)
        assert counts["created"] == len(with_defaults)
        assert counts["refreshed"] == 0


@pytest.mark.asyncio
async def test_the_shipped_default_carries_the_brand_logo_component(
    session_factory, staff: dict[str, str]
) -> None:
    """What the refresh is FOR, stated as the property that must hold of a freshly seeded row.

    The editor `source` is what the canvas opens on, and its masthead must be the one Brand logo
    component — an `<img>` on `brand.logo_url` carrying the wordmark as its `data-fallback`. A row
    with no `source` (the stale shape) leaves an author looking at raw Jinja.
    """
    tenant_id = int(staff["tenant_id"])
    async with session_factory() as session:
        await ets.refresh_defaults(session, tenant_id, _actor(staff))
        row = await _published(session, tenant_id, SEEDED_CODE)

    source = row["source"] or ""
    assert source, "the seeded row has no editor source"
    assert 'data-gjs-name="Brand logo"' in source
    assert 'data-brand-arm="logo"' in source
    assert 'src="{{ brand.logo_url }}"' in source
    assert "data-fallback=" in source  # the firm's name, for a firm with no logo
    # ONE brand image, not two: there is no second block placing `brand.icon_url`.
    assert source.count("data-brand-arm=") == 1
    assert "brand.icon_url" not in source
    # And the SENT shape keeps both arms, so a firm with no logo still wears its name.
    assert "{% if brand.logo_url %}" in (row["html"] or "")
