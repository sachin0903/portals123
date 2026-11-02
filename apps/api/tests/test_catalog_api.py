"""Endpoint-behaviour tests for the /catalog router.

Drives the router coroutines against a real ``authenticated`` session (role + pre-resolved claims
set exactly like db.py::tenant_session, via ``auth.resolve_claims``), so RLS decides visibility.
Proves the global product catalogue is visible to any tenant, while models/fee_packages are scoped
to the caller's own tenant.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import Principal
from app.routers import catalog as catalog_router
from tests.conftest import SECCL_PLATFORM_SQL


def _principal(perms: list[str]) -> Principal:
    """A staff principal for the endpoint permission guard. RLS visibility is still decided by the
    ``_authed`` session's claims, not this object — it only satisfies ``require_any_permission``."""
    return Principal(user_id="x", organisation_id="x", role="adviser", permissions=perms)


@asynccontextmanager
async def _authed(app_url: str, workos_user: str, client_id: str) -> AsyncIterator[AsyncSession]:
    """A session acting as a request principal — SET ROLE authenticated + claims minted by
    auth.resolve_claims, exactly like db.py::tenant_session. App-only portal (0107): the token's
    application client id (arg 3) is what classifies the portal, so it rides along here."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        resolved = (
            await session.execute(
                text("SELECT auth.resolve_claims(:w, NULL, :cid)"),
                {"w": workos_user, "cid": client_id},
            )
        ).scalar_one()
        claims = {"sub": workos_user, "role": "adviser", "permissions": [], **resolved}
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)}
        )
        yield session
    await engine.dispose()


@pytest.fixture
def catalogued(super_engine: Engine) -> dict[str, object]:
    """A platform + product, and (for one tenant) a model + fee package, plus a staff login in
    that tenant — seeded past RLS. Returns ids + the staff WorkOS id."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"caten_{s}", "a": f"cafirm-{s}", "n": f"CAFirm {s}"},
            ).scalar_one()
        )
        wos = f"cawos_{s}"
        user = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, first_name, "
                    "  last_name, enabled) "
                    "VALUES (:uid, :t, :email, :wos, 'Ada', 'Staff', true) RETURNING id"
                ),
                {"uid": f"causr_{s}", "t": tenant, "email": f"staff_{s}@cafirm.test", "wos": wos},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": user, "t": tenant},
        )
        # App-only portal (0107): the environment declares the platform application, and the
        # session resolves its portal by signing in through it (arg 3 of resolve_claims).
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  platform_client_id) VALUES (:t, 'dev', :env, :app)"
            ),
            {"t": tenant, "env": f"env_ca_{s}", "app": f"app_ca_platform_{s}"},
        )
        platform = str(
            c.execute(
                text(SECCL_PLATFORM_SQL),
                {"n": f"Seccl {s}"},
            ).scalar_one()
        )
        # SEEDED THE WAY A REAL BOOK HOLDS IT, which the previous fixture did not and that is
        # why an empty shelf went unnoticed for as long as it did: it set the legacy
        # `products.platform_id` (0002, superseded by 0044) and NO `product_platforms` row, so
        # the endpoint's inner join found the product here and nothing at all in production,
        # where `_PRODUCTS` writes the mapping and never that column.
        product = int(
            c.execute(
                text(
                    "INSERT INTO platform.products (tenant_id, product_type, code, name) "
                    "VALUES (:t, 'ISA', 'ISA', :n) RETURNING id"
                ),
                {"t": tenant, "n": f"Stocks & Shares ISA {s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO platform.product_platforms "
                "  (tenant_id, product_id, provider, external_id, role) "
                "VALUES (:t, :pr, :p, 'ISA', 'custodian')"
            ),
            {"t": tenant, "pr": product, "p": platform},
        )
        # A shelf item with NO custodian mapped — the state `0128`'s two third-party bonds are in
        # on every book. It must still appear, or the endpoint claims the firm does not offer it.
        unmapped = int(
            c.execute(
                text(
                    "INSERT INTO platform.products (tenant_id, product_type, code, name) "
                    "VALUES (:t, 'TPONBOND', 'TPONBOND', :n) RETURNING id"
                ),
                {"t": tenant, "n": f"Third-party Onshore Bond {s}"},
            ).scalar_one()
        )
        model = int(
            c.execute(
                text(
                    "INSERT INTO platform.models (provider, tenant_id, name) "
                    "VALUES (:p, :t, :n) RETURNING id"
                ),
                {"p": platform, "t": tenant, "n": f"Balanced {s}"},
            ).scalar_one()
        )
        fee = int(
            c.execute(
                text(
                    "INSERT INTO platform.fee_packages "
                    "  (tenant_id, provider, product_id, name, category, frequency, fee_type, "
                    "   rule) "
                    "VALUES (:t, :p, :pr, :n, 'platform', 'ongoing', 'percentage', :rule) "
                    "RETURNING id"
                ),
                {
                    "t": tenant,
                    "p": platform,
                    "pr": product,
                    "n": f"Standard {s}",
                    "rule": json.dumps({"rate": 0.0035}),
                },
            ).scalar_one()
        )
        c.commit()
    return {
        "tenant": tenant,
        "wos": wos,
        "app": f"app_ca_platform_{s}",
        "platform": platform,
        "product": product,
        "unmapped": unmapped,
        "model": model,
        "fee": fee,
    }


@pytest.mark.asyncio
async def test_the_shelf_names_its_custodian_from_the_mapping(
    db: dict[str, str], catalogued: dict[str, object]
) -> None:
    """THE JOIN THAT EMPTIED THIS ENDPOINT. `products.platform_id` is 0002's nullable legacy
    column, superseded by `product_platforms` in 0044 and written by nothing since — so an INNER
    JOIN on it returned zero rows for a firm with seven products, and the accounts filter, the
    new-journey picker and the flow builder's `setup_account` step each showed an empty shelf.

    A failure that reads as "this firm offers nothing" cannot be left to a type: the platform must
    come from the mapping, and the value must be the real custodian's."""
    async with _authed(db["app"], str(catalogued["wos"]), str(catalogued["app"])) as session:
        rows = await catalog_router.list_products(session=session)

    shelf = {r["id"]: r for r in rows}
    mapped = shelf[catalogued["product"]]
    # The provider CODE is the whole answer since 0131 — there is no `platform_name` to join for,
    # because `platform.platforms` was a surrogate id for exactly this value.
    assert mapped["provider"] == catalogued["platform"]
    assert mapped["provider"] == "seccl"
    assert mapped["role"] == "custodian"
    # `code` beside the enum, because that is the value every provider mapping is keyed on and
    # what the account surfaces render.
    assert mapped["code"] == "ISA"
    assert mapped["is_pension"] is False
    assert mapped["allowed_custodies"] == ["onshore", "offshore"]
    assert mapped["allowed_client_types"] == ["individual"]
    assert mapped["max_parties"] == 2
    assert mapped["bank_details_allowed"] is True
    assert mapped["custodians"] == []  # mapped, but the firm has not enabled it yet
    assert all(r["enabled"] is not False for r in rows)


@pytest.mark.asyncio
async def test_a_product_with_no_custodian_is_still_on_the_shelf(
    db: dict[str, str], catalogued: dict[str, object]
) -> None:
    """A firm offers a product before a provider is wired to it — which is exactly where 0128's
    two third-party bonds sit, since no custodian in the estate publishes them. Excluding it would
    be the emptied-shelf bug again, one row at a time."""
    async with _authed(db["app"], str(catalogued["wos"]), str(catalogued["app"])) as session:
        rows = await catalog_router.list_products(session=session)

    unmapped = next(r for r in rows if r["id"] == catalogued["unmapped"])
    assert unmapped["provider"] is None
    assert unmapped["provider"] is None, "and it names no custodian rather than guessing one"


@pytest.mark.asyncio
async def test_narrowing_to_one_platform_reads_the_mapping_too(
    db: dict[str, str], catalogued: dict[str, object]
) -> None:
    """`?platform_id=` narrows on the MAPPING, so it excludes the unmapped shelf item — a caller
    asking "what does this custodian hold" is asking about mappings, and a product with none is
    not an answer."""
    async with _authed(db["app"], str(catalogued["wos"]), str(catalogued["app"])) as session:
        rows = await catalog_router.list_products(
            provider=str(catalogued["platform"]), session=session
        )
    ids = {r["id"] for r in rows}
    assert catalogued["product"] in ids
    assert catalogued["unmapped"] not in ids


@pytest.mark.asyncio
async def test_models_and_fees_scoped_to_own_tenant(
    db: dict[str, str], catalogued: dict[str, object]
) -> None:
    """models/fee_packages are tenant-scoped: the owning tenant's staff see them."""
    principal = _principal(["contacts:read"])
    async with _authed(db["app"], str(catalogued["wos"]), str(catalogued["app"])) as session:
        models = await catalog_router.list_models(principal=principal, session=session)
        fees = await catalog_router.list_fee_packages(principal=principal, session=session)
    assert any(m["id"] == catalogued["model"] for m in models)
    fee = next(f for f in fees if f["id"] == catalogued["fee"])
    # The picker states what a package costs, so what it is taken OF, who pays it and the limits
    # around it come back with the rule — a tick-box naming a package alone asks an adviser to
    # apply pricing they cannot read.
    assert {
        "fee_type",
        "rule",
        "frequency",
        "currency",
        "vatable",
        "charge_basis",
        "payer",
        "minimum_amount",
        "maximum_amount",
    } <= fee.keys()


@pytest.mark.asyncio
async def test_models_and_fees_hidden_from_other_tenant(
    db: dict[str, str], catalogued: dict[str, object], super_engine: Engine
) -> None:
    """A different tenant's staff must NOT see this tenant's models or fee packages (RLS)."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        other = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"caten2_{s}", "a": f"cafirm2-{s}", "n": f"CAFirm2 {s}"},
            ).scalar_one()
        )
        wos = f"cawos2_{s}"
        user2 = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :wos, true) RETURNING id"
                ),
                {"uid": f"causr2_{s}", "t": other, "email": f"staff2_{s}@cafirm2.test", "wos": wos},
            ).scalar_one()
        )
        # A REAL platform session in the other tenant (membership + app, 0107), so what hides
        # the rows is RLS's tenant scoping — not an unresolved portal emptying the lineage.
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": user2, "t": other},
        )
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  platform_client_id) VALUES (:t, 'dev', :env, :app)"
            ),
            {"t": other, "env": f"env_ca2_{s}", "app": f"app_ca2_platform_{s}"},
        )
        c.commit()
    principal = _principal(["contacts:read"])
    async with _authed(db["app"], wos, f"app_ca2_platform_{s}") as session:
        models = await catalog_router.list_models(principal=principal, session=session)
        fees = await catalog_router.list_fee_packages(principal=principal, session=session)
    assert all(m["id"] != catalogued["model"] for m in models)
    assert all(f["id"] != catalogued["fee"] for f in fees)


@pytest.mark.asyncio
async def test_custodians_are_the_firms_own_enabled_platforms(
    db: dict[str, str], catalogued: dict[str, object], super_engine: Engine
) -> None:
    """A journey can only be started with a custodian this firm is enabled on.

    It used to return every custodian in the registry, arguing that a firm with no shelf for one
    could still start a journey with it. That reversed: an account addressed to a custodian the
    firm is not enabled on is an account no pipeline ever reaches, so it is a choice that cannot
    be honoured. An empty answer is a real answer here — the caller says which of "none enabled"
    and "could not load" it is rather than rendering an empty dropdown.
    """
    principal = _principal(["contacts:read"])
    tenant = int(catalogued["tenant"])
    async with _authed(db["app"], str(catalogued["wos"]), str(catalogued["app"])) as session:
        before = await catalog_router.list_custodians(session=session, principal=principal)
    assert before == []

    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO platform.tenant_platforms (tenant_id, provider, enabled) "
                "VALUES (:t, 'seccl', true)"
            ),
            {"t": tenant},
        )
        c.commit()
    async with _authed(db["app"], str(catalogued["wos"]), str(catalogued["app"])) as session:
        rows = await catalog_router.list_custodians(session=session, principal=principal)
    assert [r["id"] for r in rows] == [catalogued["platform"]]
    assert rows[0]["openable_custodies"] == ["onshore"]
    async with _authed(db["app"], str(catalogued["wos"]), str(catalogued["app"])) as session:
        products = await catalog_router.list_products(session=session)
    mapped = next(row for row in products if row["id"] == catalogued["product"])
    assert mapped["custodians"] == ["seccl"]

    # Disabled is not enabled: turning the provider off takes the custodian off the picker.
    with super_engine.connect() as c:
        c.execute(
            text(
                "UPDATE platform.tenant_platforms SET enabled = false "
                "WHERE tenant_id = :t AND provider = 'seccl'"
            ),
            {"t": tenant},
        )
        c.commit()
    async with _authed(db["app"], str(catalogued["wos"]), str(catalogued["app"])) as session:
        rows = await catalog_router.list_custodians(session=session, principal=principal)
    assert rows == []


@pytest.mark.asyncio
async def test_custodians_exclude_a_scheme_administrator(
    db: dict[str, str], catalogued: dict[str, object], super_engine: Engine
) -> None:
    """Narrower than the firm's enablements: Morgan Lloyd is a scheme administrator, and an
    account cannot be addressed to something that cannot hold it."""
    principal = _principal(["contacts:read"])
    tenant = int(catalogued["tenant"])
    with super_engine.connect() as c:
        # No registry row to seed: the provider is a member of `platform.provider_code`, so the
        # enablement IS the whole statement that this firm is connected to Morgan Lloyd.
        c.execute(
            text(
                "INSERT INTO platform.tenant_platforms (tenant_id, provider, enabled) "
                "VALUES (:t, 'morgan-lloyd', true)"
            ),
            {"t": tenant},
        )
        c.commit()
    async with _authed(db["app"], str(catalogued["wos"]), str(catalogued["app"])) as session:
        rows = await catalog_router.list_custodians(session=session, principal=principal)
    assert all(r["provider"] != "morgan-lloyd" for r in rows)


@pytest.mark.asyncio
async def test_custodians_are_not_a_sub_tenants_enablements(
    db: dict[str, str], catalogued: dict[str, object], super_engine: Engine
) -> None:
    """A DESCENDANT tenant's enablement must not appear in the parent's picker.

    RLS scopes `tenant_platforms` by `auth.tenant_lineage()`, which is the caller's tenant plus
    its descendants — so leaving the scoping to RLS offered a parent firm a custodian only its
    sub-tenant is enabled on (the account-no-pipeline-reaches this endpoint exists to prevent) and
    listed a shared one once per enabling tenant.
    """
    principal = _principal(["contacts:read"])
    parent = int(catalogued["tenant"])
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        child = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name, parent_id) "
                    "VALUES (:u, :a, :n, :p) RETURNING id"
                ),
                {"u": f"casub_{s}", "a": f"casub-{s}", "n": f"CASub {s}", "p": parent},
            ).scalar_one()
        )
        # Only the CHILD is enabled, and both are enabled on the same provider in the second case.
        c.execute(
            text(
                "INSERT INTO platform.tenant_platforms (tenant_id, provider, enabled) "
                "VALUES (:t, 'seccl', true)"
            ),
            {"t": child},
        )
        c.commit()
    async with _authed(db["app"], str(catalogued["wos"]), str(catalogued["app"])) as session:
        rows = await catalog_router.list_custodians(session=session, principal=principal)
    assert rows == []

    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO platform.tenant_platforms (tenant_id, provider, enabled) "
                "VALUES (:t, 'seccl', true)"
            ),
            {"t": parent},
        )
        c.commit()
    async with _authed(db["app"], str(catalogued["wos"]), str(catalogued["app"])) as session:
        rows = await catalog_router.list_custodians(session=session, principal=principal)
    # Once, not once per enabling tenant.
    assert [r["id"] for r in rows] == [catalogued["platform"]]
