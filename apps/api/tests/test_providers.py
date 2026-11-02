"""Provider enablement (0029_tenant_providers) + the fan-out predicate: config
validation, the upsert round-trip against real Postgres, the enabled-tenants join
(requires active storage), and RLS."""

import uuid
from typing import Any
from urllib.parse import quote

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import object_store, providers, provisioning
from app.config import settings
from tests.conftest import principal_conn

# ---- config validation + predicate (pure) ----


def test_enabled_custodians_are_the_switched_on_custodians_only() -> None:
    rows = [
        {"provider": "sei", "name": "SEI", "enabled": True},
        {"provider": "seccl", "name": "Seccl", "enabled": False},
        {"provider": "morgan-lloyd", "name": "Morgan Lloyd", "enabled": True},
    ]
    assert providers.enabled_custodians(rows) == [{"code": "sei", "name": "SEI"}]
    rows[1]["enabled"] = True
    assert providers.enabled_custodians(rows) == [
        {"code": "sei", "name": "SEI"},
        {"code": "seccl", "name": "Seccl"},
    ]


def test_validate_config_shapes() -> None:
    providers.validate_config("morgan-lloyd", {"tenant_ids": ["ms-guid-1", "ms-guid-2"]})
    providers.validate_config("seccl", {"node_ids": ["44", "30"]})
    providers.validate_config("sei", {"firm_id": "3106", "dsr_ids": ["59261"]})
    with pytest.raises(ValueError):
        providers.validate_config("morgan-lloyd", {})  # missing tenant_ids
    with pytest.raises(ValueError):
        providers.validate_config("morgan-lloyd", {"tenant_ids": []})  # empty
    with pytest.raises(ValueError):
        providers.validate_config("morgan-lloyd", {"tenant_ids": ["x"], "nope": 1})  # unknown key
    with pytest.raises(ValueError):
        providers.validate_config("acme", {"tenant_ids": ["x"]})  # unknown provider


def test_validate_config_sei_narrowing_keys() -> None:
    """firm_id is one string; both narrowing axes are lists and both optional."""
    providers.validate_config("sei", {"firm_id": "3106"})
    providers.validate_config(
        "sei", {"firm_id": "3106", "subfirm_ids": ["704254", "704766"], "dsr_ids": ["200119"]}
    )
    with pytest.raises(ValueError, match="firm_id"):
        providers.validate_config("sei", {})  # firm_id is mandatory
    with pytest.raises(ValueError, match="firm_id must be a non-empty string"):
        providers.validate_config("sei", {"firm_id": ["3106"]})  # a list is not a firm
    with pytest.raises(ValueError, match="firm_id must be a non-empty string"):
        providers.validate_config("sei", {"firm_id": "  "})
    with pytest.raises(ValueError, match="subfirm_ids must be a non-empty list"):
        providers.validate_config("sei", {"firm_id": "3106", "subfirm_ids": []})
    with pytest.raises(ValueError, match="subfirm_ids must be a non-empty list"):
        providers.validate_config("sei", {"firm_id": "3106", "subfirm_ids": "704254"})
    # The old singular key must be rejected, not silently ignored — it would have
    # widened every tenant's slice to the whole firm.
    with pytest.raises(ValueError, match="unknown config keys"):
        providers.validate_config("sei", {"firm_id": "3106", "subfirm_id": "704254"})


def test_validate_config_sei_custody_orgs() -> None:
    """The write map is onshore/offshore → a selected subfirm, not a free-form org."""
    providers.validate_config(
        "sei",
        {
            "firm_id": "3106",
            "subfirm_ids": ["704254", "704766"],
            "custody_orgs": {"onshore": "704254", "offshore": "704766"},
        },
    )
    providers.validate_config("sei", {"firm_id": "3106", "custody_orgs": {"onshore": "704254"}})
    with pytest.raises(ValueError, match="unknown custody_orgs entries"):
        providers.validate_config(
            "sei", {"firm_id": "3106", "custody_orgs": {"on-shore": "704254"}}
        )
    with pytest.raises(ValueError, match="custody_orgs must name a selected subfirm"):
        providers.validate_config(
            "sei",
            {
                "firm_id": "3106",
                "subfirm_ids": ["704254"],
                "custody_orgs": {"onshore": "704766"},
            },
        )


def test_validate_config_sei_client_template() -> None:
    """The client template is one id on the platform connection, not a product field."""
    providers.validate_config("sei", {"firm_id": "3108", "client_template_id": "2981279"})
    with pytest.raises(ValueError, match="client_template_id must be a non-empty string"):
        providers.validate_config("sei", {"firm_id": "3108", "client_template_id": "  "})


def test_validate_config_sei_custody_dsrs() -> None:
    """A custody may name DSR teams with a subfirm, or without one."""
    providers.validate_config(
        "sei",
        {
            "firm_id": "3106",
            "subfirm_ids": ["704766"],
            "custody_orgs": {"offshore": "704766"},
            "custody_dsrs": {"offshore": ["704718"], "onshore": ["704079"]},
        },
    )
    providers.validate_config("sei", {"firm_id": "3106", "custody_dsrs": {"offshore": ["704718"]}})
    with pytest.raises(ValueError, match="unknown custody_dsrs entries"):
        providers.validate_config(
            "sei", {"firm_id": "3106", "custody_dsrs": {"on-shore": ["704718"]}}
        )
    with pytest.raises(ValueError, match="custody_dsrs.onshore must be a non-empty list"):
        providers.validate_config("sei", {"firm_id": "3106", "custody_dsrs": {"onshore": []}})
    with pytest.raises(ValueError, match="custody_dsrs must be a non-empty object"):
        providers.validate_config("sei", {"firm_id": "3106", "custody_dsrs": {}})


def test_validate_config_sei_model_dsrs() -> None:
    providers.validate_config(
        "sei", {"firm_id": "3106", "model_dsrs": {"PDdVYya8B8hb": ["704718"]}}
    )
    with pytest.raises(ValueError, match="model_dsrs keys must be model ids"):
        providers.validate_config(
            "sei", {"firm_id": "3106", "model_dsrs": {"not-a-uid": ["704718"]}}
        )
    with pytest.raises(ValueError, match="model_dsrs.PDdVYya8B8hb must be a non-empty list"):
        providers.validate_config("sei", {"firm_id": "3106", "model_dsrs": {"PDdVYya8B8hb": []}})


def test_infer_custody_orgs_from_sei_subfirm_names() -> None:
    subfirms = [
        ("704254", "Select Platform - Onshore"),
        ("704766", "Select Platform - Offshore"),
        ("704724", "Alpha Investment Office - Onshore"),
    ]
    assert providers.infer_custody_orgs(subfirms, ["704254", "704766"]) == {
        "onshore": "704254",
        "offshore": "704766",
    }
    assert providers.infer_custody_orgs(subfirms, ["704254"]) == {"onshore": "704254"}
    assert providers.infer_custody_orgs([]) == {}
    assert providers.infer_custody_orgs([("9001", "Desk A")]) == {"onshore": "9001"}
    assert providers.infer_custody_orgs([("a", "Desk A"), ("b", "Desk B")]) == {}
    assert providers.custody_from_label("UCAP Asset Management (UK) Ltd - Offshore") == "offshore"
    assert providers.custody_from_label("Not a split") is None


def test_openable_custodies_and_account_org() -> None:
    assert providers.openable_custodies("seccl") == frozenset({"onshore"})
    assert providers.openable_custodies("sei", {}) == frozenset({"onshore", "offshore"})
    mapped = {"custody_orgs": {"onshore": "704254"}}
    assert providers.openable_custodies("sei", mapped) == frozenset({"onshore"})
    assert providers.account_org_id("sei", mapped, "onshore") == "704254"
    assert providers.account_org_id("sei", mapped, "offshore") is None
    assert providers.account_org_id("seccl", mapped, "onshore") is None
    assert providers.openable_custodies(None) == frozenset({"onshore", "offshore"})


def test_consumption_schema_is_the_same_for_every_provider() -> None:
    """One schema name across all custodians, so "clients" is spelt the same whichever
    one it came from. Naming it after the provider collides with the catalogue, which
    interlace attaches under the project name — DuckDB then rejects every reference as
    an ambiguous catalog-or-schema."""
    from book_sink.entities import PLATFORM_SCHEMA

    assert PLATFORM_SCHEMA == "platform"
    # The catalogue (metadata schema) still IS per provider — that is what keeps the three
    # warehouses apart — and since 0134 it is the provider CODE, so the two names cannot drift.
    assert {providers.provider_schema(p) for p in providers.PROVIDERS} == {
        "morgan_lloyd",
        "seccl",
        "sei",
    }


# ---- DB round-trip (real Postgres, fake store admin for the storage rows) ----


class FakeAdmin:
    def ensure_bucket(self, bucket: str) -> None:
        pass

    def ensure_public_prefix(self, bucket: str, prefix: str) -> bool:
        return True

    def ensure_tenant_principal(
        self, bucket: str, name: str, policy: dict[str, Any] | None = None
    ) -> tuple[str, str] | None:
        return None

    def credential_works(self, bucket: str, access_key: str, secret_key: str) -> bool:
        return True

    def create_user_credential(
        self, access_key: str, secret_key: str, policy: dict[str, Any]
    ) -> None:
        pass

    def delete_user_credential(self, access_key: str) -> None:
        pass


@pytest.fixture
def s3_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "s3_endpoint_url", "http://rustfs:9000")
    monkeypatch.setattr(settings, "s3_access_key_id", "root")
    monkeypatch.setattr(settings, "s3_secret_access_key", "root")
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())
    monkeypatch.setattr(object_store, "admin_for", lambda kind, endpoint, region: FakeAdmin())


async def _mk_tenant(session: AsyncSession) -> int:
    alias = f"prov-{uuid.uuid4().hex[:10]}"
    row = await provisioning.provision_tenant(session, name=alias, alias=alias, from_model=False)
    return int(row["id"])


async def test_set_provider_round_trip(service_session: AsyncSession, s3_configured: None) -> None:
    tid = await _mk_tenant(service_session)
    row = await providers.set_provider(
        service_session, tid, "morgan-lloyd", config={"tenant_ids": ["g-1"]}, enabled=True
    )
    assert row["enabled"] is True and row["config"] == {"tenant_ids": ["g-1"]}

    # Upsert updates in place (unique tenant+provider).
    row = await providers.set_provider(
        service_session, tid, "morgan-lloyd", config={"tenant_ids": ["g-1", "g-2"]}, enabled=False
    )
    assert row["enabled"] is False and row["config"]["tenant_ids"] == ["g-1", "g-2"]
    assert len(await providers.list_for_tenant(service_session, tid)) == 1

    with pytest.raises(ValueError):
        await providers.set_provider(service_session, tid, "morgan-lloyd", config={}, enabled=True)


def test_only_a_sipp_is_an_administered_wrapper() -> None:
    """`is_pension` is the display flag and includes TPPENSION. Morgan Lloyd runs the firm's
    own SIPP, so the attach must not treat those as the same set."""
    assert providers.ADMINISTERED_PRODUCTS == ("SIPP",)
    assert providers.administered("SIPP")
    assert providers.administered("sipp")
    assert not providers.administered("TPPENSION")
    assert not providers.administered("SSAS")
    assert not providers.administered("PENSION")
    assert not providers.administered("GIA")
    assert not providers.administered(None)


@pytest.mark.asyncio
async def test_enabling_morgan_lloyd_maps_pension_products(
    service_session: AsyncSession, s3_configured: None
) -> None:
    """A SIPP on the shelf must name Morgan Lloyd as scheme administrator the moment the
    firm enables it. Waiting for an operator to tag each product left `scheme_administrator`
    null on every onboarding SIPP, which is exactly the mapping `cam_createscheme` and the
    account record both read."""
    from app import catalogue

    tid = await _mk_tenant(service_session)
    sipp = await catalogue.upsert_product(
        service_session, tid, {"name": "Personal Pension", "product_type": "SIPP"}
    )
    gia = await catalogue.upsert_product(
        service_session, tid, {"name": "General account", "product_type": "GIA"}
    )
    await providers.set_provider(
        service_session,
        tid,
        "morgan-lloyd",
        config={"tenant_ids": ["g-1"], "scheme_type": 17, "sipp_type": 809130002},
        enabled=True,
    )
    rows = (
        (
            await service_session.execute(
                text(
                    "SELECT product_id, provider::text AS provider, role, enabled, data, "
                    "       external_id "
                    "FROM platform.product_platforms WHERE tenant_id = :t"
                ),
                {"t": tid},
            )
        )
        .mappings()
        .all()
    )
    by_product = {int(r["product_id"]): dict(r) for r in rows}
    mapped = by_product[int(sipp["id"])]
    assert mapped["provider"] == "morgan-lloyd"
    assert mapped["role"] == "scheme_administrator"
    assert mapped["enabled"] is True
    assert mapped["data"] == {"ml_scheme_type": 17, "ml_sipp_type": 809130002}
    # The administrator's reference for the wrapper is the wrapper itself, so the shelf shows
    # "Morgan Lloyd administrator SIPP" rather than a bare "—".
    assert mapped["external_id"] == "SIPP"
    assert int(gia["id"]) not in by_product

    tpp = await catalogue.upsert_product(
        service_session,
        tid,
        {"name": "Third-party Pension", "product_type": "TPPENSION", "is_pension": True},
    )
    await providers.attach_scheme_administrators(service_session, tid)
    leftover = (
        (
            await service_session.execute(
                text(
                    "SELECT product_id FROM platform.product_platforms "
                    "WHERE tenant_id = :t AND provider = 'morgan-lloyd'"
                ),
                {"t": tid},
            )
        )
        .scalars()
        .all()
    )
    assert {int(pid) for pid in leftover} == {int(sipp["id"])}
    assert int(tpp["id"]) not in {int(pid) for pid in leftover}

    # Hand-set pairings survive a re-enable that would otherwise copy the tenant default.
    await catalogue.set_product_platform(
        service_session,
        tid,
        int(sipp["id"]),
        "morgan-lloyd",
        {"role": "scheme_administrator", "data": {"ml_scheme_type": 16, "ml_sipp_type": 1}},
    )
    await providers.set_provider(
        service_session, tid, "morgan-lloyd", config={"tenant_ids": ["g-1"]}, enabled=True
    )
    kept = (
        await service_session.execute(
            text("SELECT data FROM platform.product_platforms WHERE product_id = :p"),
            {"p": int(sipp["id"])},
        )
    ).scalar_one()
    assert kept == {"ml_scheme_type": 16, "ml_sipp_type": 1}

    # Disable does not delete the mapping — expand-only.
    await providers.set_provider(
        service_session, tid, "morgan-lloyd", config={"tenant_ids": ["g-1"]}, enabled=False
    )
    still = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM platform.product_platforms "
                "WHERE product_id = :p AND provider = 'morgan-lloyd'"
            ),
            {"p": int(sipp["id"])},
        )
    ).scalar_one()
    assert int(still) == 1


@pytest.mark.asyncio
async def test_enabling_sei_connects_the_wrappers_it_opens(
    service_session: AsyncSession, s3_configured: None
) -> None:
    """The product list and the journey read product_platforms.role = 'custodian'.

    Enabling SEI used to write only tenant_platforms, so the shelf looked
    unconnected and every new account was stored with no custodian. A template
    save that had already created the row left role null, which the same
    readers skip. Third-party wrappers stay unmapped. A disabled mapping stays
    disabled. An account is stamped only when SEI is the only custodian it
    could take.
    """
    from app import catalogue

    tid = await _mk_tenant(service_session)
    suffix = uuid.uuid4().hex[:8]
    products = {}
    for product_type, name in (
        ("GIA", "General"),
        ("ISA", "ISA"),
        ("SIPP", "Pension"),
        ("TPPENSION", "Third-party pension"),
        ("TPONBOND", "Onshore bond"),
    ):
        products[product_type] = await catalogue.upsert_product(
            service_session, tid, {"name": name, "product_type": product_type}
        )
    await service_session.execute(
        text(
            "INSERT INTO platform.product_platforms "
            "  (tenant_id, product_id, provider, external_id, account_open_reference, "
            "   enabled) "
            "VALUES (:t, :p, 'sei', '172', '2981141', false)"
        ),
        {"t": tid, "p": int(products["ISA"]["id"])},
    )
    await service_session.execute(
        text(
            "INSERT INTO platform.product_platforms "
            "  (tenant_id, product_id, provider, role, enabled) "
            "VALUES (:t, :p, 'seccl', 'custodian', true)"
        ),
        {"t": tid, "p": int(products["SIPP"]["id"])},
    )
    contact_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, :u, 'individual', 'Ada', 'Nwosu') RETURNING id"
                ),
                {"t": tid, "u": f"c{suffix}"[:12]},
            )
        ).scalar_one()
    )

    async def account(product_type: str, custodian: str | None) -> int:
        uid = uuid.uuid4().hex[:12]
        return int(
            (
                await service_session.execute(
                    text(
                        "INSERT INTO platform.accounts "
                        "  (tenant_id, uid, contact_id, product_id, name, status, custodian) "
                        "VALUES (:t, :u, :c, :p, :n, 'draft', "
                        "        CAST(:custodian AS platform.provider_code)) RETURNING id"
                    ),
                    {
                        "t": tid,
                        "u": uid,
                        "c": contact_id,
                        "p": int(products[product_type]["id"]),
                        "n": product_type,
                        "custodian": custodian,
                    },
                )
            ).scalar_one()
        )

    gia_account = await account("GIA", None)
    sipp_account = await account("SIPP", None)
    named = await account("GIA", "seccl")

    # Seccl has to be enabled or it is not a custodian the account can take,
    # and SEI would be the only one left.
    await providers.set_provider(
        service_session, tid, "seccl", config={"node_ids": ["44"]}, enabled=True
    )
    await providers.set_provider(
        service_session, tid, "sei", config={"firm_id": "3108"}, enabled=True
    )

    rows = (
        (
            await service_session.execute(
                text(
                    "SELECT p.product_type::text AS product_type, pp.role, pp.enabled, "
                    "       pp.external_id, pp.account_open_reference "
                    "FROM platform.product_platforms pp "
                    "JOIN platform.products p ON p.id = pp.product_id "
                    "WHERE pp.tenant_id = :t AND pp.provider = 'sei'"
                ),
                {"t": tid},
            )
        )
        .mappings()
        .all()
    )
    by_type = {str(row["product_type"]): dict(row) for row in rows}
    assert set(by_type) == {"GIA", "ISA", "SIPP"}
    assert by_type["GIA"]["role"] == "custodian"
    assert by_type["GIA"]["enabled"] is True
    # A null external id is filled with the wrapper itself — the custodian's own reference for the
    # product, so the shelf shows "SEI custodian GIA" rather than a bare "—".
    assert by_type["GIA"]["external_id"] == "GIA"
    assert by_type["SIPP"]["external_id"] == "SIPP"
    assert by_type["ISA"]["role"] == "custodian"
    assert by_type["ISA"]["enabled"] is False
    # A value already set (here the merge's SEI code) is kept, not overwritten by the default.
    assert by_type["ISA"]["external_id"] == "172"
    assert by_type["ISA"]["account_open_reference"] == "2981141"
    assert by_type["SIPP"]["role"] == "custodian"

    later = await catalogue.upsert_product(
        service_session, tid, {"name": "Junior ISA", "product_type": "JISA"}
    )
    later_role = (
        await service_session.execute(
            text(
                "SELECT role FROM platform.product_platforms "
                "WHERE product_id = :p AND provider = 'sei'"
            ),
            {"p": int(later["id"])},
        )
    ).scalar_one()
    assert later_role == "custodian"

    async def custodian_of(account_id: int) -> str | None:
        return (
            await service_session.execute(
                text("SELECT custodian::text FROM platform.accounts WHERE id = :a"),
                {"a": account_id},
            )
        ).scalar_one()

    assert await custodian_of(gia_account) == "sei"
    assert await custodian_of(sipp_account) is None
    assert await custodian_of(named) == "seccl"

    await providers.set_provider(
        service_session, tid, "sei", config={"firm_id": "3108"}, enabled=False
    )
    remaining = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM platform.product_platforms "
                "WHERE tenant_id = :t AND provider = 'sei'"
            ),
            {"t": tid},
        )
    ).scalar_one()
    assert int(remaining) == 4


@pytest.mark.asyncio
async def test_reattach_drops_morgan_lloyd_from_a_third_party_pension(
    service_session: AsyncSession, s3_configured: None
) -> None:
    """The previous attach used `is_pension`, so a TPPENSION already on the shelf kept the
    mapping after the filter narrowed to SIPP. Re-attach (enablement, product save, enrol)
    must take it off rather than leave Drawdown lit on a scheme Morgan Lloyd does not run."""
    from app import catalogue

    tid = await _mk_tenant(service_session)
    tpp = await catalogue.upsert_product(
        service_session,
        tid,
        {"name": "Third-party Pension", "product_type": "TPPENSION", "is_pension": True},
    )
    await service_session.execute(
        text(
            "INSERT INTO platform.product_platforms "
            "  (tenant_id, product_id, provider, role, enabled) "
            "VALUES (:t, :p, 'morgan-lloyd', 'scheme_administrator', true)"
        ),
        {"t": tid, "p": int(tpp["id"])},
    )
    await providers.set_provider(
        service_session, tid, "morgan-lloyd", config={"tenant_ids": ["g-1"]}, enabled=True
    )
    left = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM platform.product_platforms "
                "WHERE product_id = :p AND provider = 'morgan-lloyd'"
            ),
            {"p": int(tpp["id"])},
        )
    ).scalar_one()
    assert int(left) == 0


@pytest.mark.asyncio
async def test_a_sipp_created_after_enablement_is_mapped(
    service_session: AsyncSession, s3_configured: None
) -> None:
    """The attach on enablement is only half: a SIPP added to the shelf afterwards must
    still name the administrator without a second visit to the product page."""
    from app import catalogue

    tid = await _mk_tenant(service_session)
    await providers.set_provider(
        service_session,
        tid,
        "morgan-lloyd",
        config={"tenant_ids": ["g-1"], "scheme_type": 17, "sipp_type": 809130002},
        enabled=True,
    )
    sipp = await catalogue.upsert_product(
        service_session, tid, {"name": "Personal Pension", "product_type": "SIPP"}
    )
    row = (
        (
            await service_session.execute(
                text(
                    "SELECT role, data FROM platform.product_platforms "
                    "WHERE product_id = :p AND provider = 'morgan-lloyd'"
                ),
                {"p": int(sipp["id"])},
            )
        )
        .mappings()
        .one()
    )
    assert row["role"] == "scheme_administrator"
    assert row["data"] == {"ml_scheme_type": 17, "ml_sipp_type": 809130002}


@pytest.mark.asyncio
async def test_enabling_a_custodian_does_not_map_it_as_administrator(
    service_session: AsyncSession, s3_configured: None
) -> None:
    """Seccl holds the assets. Mapping it as scheme administrator on every SIPP would
    make `GET /crm/accounts` publish the custodian as the administrator and light
    drawdown for a platform that has no member journey."""
    from app import catalogue

    tid = await _mk_tenant(service_session)
    sipp = await catalogue.upsert_product(
        service_session, tid, {"name": "Personal Pension", "product_type": "SIPP"}
    )
    await providers.set_provider(
        service_session, tid, "seccl", config={"node_ids": ["44"]}, enabled=True
    )
    count = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM platform.product_platforms "
                "WHERE product_id = :p AND role = 'scheme_administrator'"
            ),
            {"p": int(sipp["id"])},
        )
    ).scalar_one()
    assert int(count) == 0


async def test_the_enum_and_the_registry_cannot_drift(service_session: AsyncSession) -> None:
    """`platform.provider_code` (0131) is what the database admits; `PROVIDERS` is what the code
    knows. They are two halves of one closed vocabulary held in two languages.

    This replaces `test_sync_platforms_seeds_one_row_per_provider`. `platform.platforms` was a
    table whose whole purpose was to be a foreign-key target for a member of this set, and 0002
    created it while nothing wrote to it — so `_sync_products` rejected every product for every
    provider, and an account with no product has no `product_type`, which is what the drawdown
    journey gates on. A value cannot fail to be seeded, which is why the table went.

    Adding a *custodian* to `PROVIDERS` alone makes every write of it a `DatatypeMismatch`;
    adding it to the enum alone makes it unreachable from enablement. Both failures land
    somewhere other than the change that caused them, so they are asserted here.

    `graphene` is a deliberate extra member: outbound pending transactions (0179), not a
    platform a firm enables, so it is on the enum and not in `PROVIDERS`. `intelliflo` (0208) is
    the same shape for a different reason: it is Graphene-ops's own adviser-identity connection
    (`platform.links.provider`), configured once via `INTELLIFLO_*` env rather than per tenant, so
    it is never a custodian/scheme-administrator a firm enables either.
    """
    members = (
        (
            await service_session.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                    "JOIN pg_namespace n ON n.oid = t.typnamespace "
                    "WHERE n.nspname = 'platform' AND t.typname = 'provider_code' "
                    "ORDER BY e.enumlabel"
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(providers.PROVIDERS) == set(members) - {"graphene", "intelliflo"}, (
        members,
        providers.PROVIDERS,
    )
    assert "graphene" in members
    assert "intelliflo" in members
    # Graphene is the outbound-ledger origin (0179); Intelliflo is the adviser-identity
    # connection (0208) -- neither is a custodian a firm enables.

    # The hyphen, everywhere. It was `morgan_lloyd` in the database and in every Python dict,
    # `morgan-lloyd` in the URL and on disk, and `ml` in the warehouse — and Morgan Lloyd was
    # therefore the only provider a missing conversion could break, which it did.
    assert "morgan-lloyd" in providers.PROVIDERS
    assert "morgan_lloyd" not in providers.PROVIDERS
    assert providers.meta("morgan-lloyd")["name"] == "Morgan Lloyd"


# `test_enabled_tenants_requires_active_storage` moved to tests/test_book_sink.py, to the
# function that now decides: `book_sink.tenants.enabled()`. The RULE is unchanged and is what
# mattered — a firm with no active bucket registers no models on the daemon at all, so nothing
# runs for it, silently, while the admin page shows it enabled and green.


def test_tenant_providers_rls(super_engine: Engine, app_engine: Engine) -> None:
    """Lineage-scoped SELECT: a principal of tenant A never sees B's enablements. No
    TRUNCATE — lineage scoping makes leftover rows from other tests invisible anyway
    (and truncating auth.tenants would nuke the migration-seeded model tenant)."""
    suffix = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant_a = c.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES (:uid, :alias, 'A') RETURNING id"
            ),
            {"uid": f"ten_pa{suffix}", "alias": f"prov-a-{suffix}"},
        ).scalar_one()
        tenant_b = c.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name) "
                "VALUES (:uid, :alias, 'B') RETURNING id"
            ),
            {"uid": f"ten_pb{suffix}", "alias": f"prov-b-{suffix}"},
        ).scalar_one()
        for tid in (tenant_a, tenant_b):
            c.execute(
                text(
                    "INSERT INTO platform.tenant_platforms (tenant_id, provider, config) "
                    "VALUES (:t, 'morgan-lloyd', '{}')"
                ),
                {"t": tid},
            )
    claims = {
        "sub": "wos_x",
        "role": "adviser",
        # 0107: lineage is a whitelist on the portal claim — no portal, no reach.
        "portal": "platform",
        "permissions": [],
        "node_id": "org_x",
        "tenant_id": tenant_a,
        "tenant_ids": [tenant_a],
        "contact_ids": [],
        "account_ids": [],
    }
    with principal_conn(app_engine, claims) as conn:
        rows = conn.execute(text("SELECT tenant_id FROM platform.tenant_platforms")).all()
        assert {r.tenant_id for r in rows} == {tenant_a}
        assert tenant_b not in {r.tenant_id for r in rows}


async def test_the_vendor_values_ride_on_the_firms_own_enablement(
    service_session: AsyncSession,
) -> None:
    """0133: one row, one config. `set_source` and `platform.providers` are gone — the values
    Morgan Lloyd issues in its API Access Document sit beside the slice filter, on the row that
    names the firm.

    Round-tripped through `set_provider`, which is now the only writer, so the same call that
    enables a firm is the one that configures its connection. That is the whole simplification:
    the two-step "register the Microsoft tenant, then enable a firm on it" was a step whose only
    write is where the `morgan-lloyd` slug bug lived.
    """
    tid = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name)"
                    " VALUES ('4' || encode(gen_random_bytes(7), 'hex'),"
                    "         'conn-' || encode(gen_random_bytes(4), 'hex'), 'Connected Firm')"
                    " RETURNING id"
                )
            )
        ).scalar_one()
    )
    firm = "6ac39921-d8b7-f011-bbd3-7ced8d6b85ff"
    await providers.set_provider(
        service_session,
        tid,
        "morgan-lloyd",
        config={"tenant_ids": ["ms-guid-x"], "firm_id": firm, "scheme_type": 16},
        enabled=True,
    )

    found = await providers.resolve_source_config(service_session, "morgan-lloyd", tid)
    assert found == {"firm_id": firm, "scheme_type": 16}
    # The SLICE key is not a vendor value and must never be handed back as one: it is the Azure
    # directory, which is exactly the GUID the shape check exists to stop being sent as a firm.
    assert "tenant_ids" not in found


class TestTheRegistryIsOneThing:
    """`PROVIDERS` + `PROVIDER_META` is the source of truth, and everything reads it.

    It used to be seven places, none derived from the API: the admin app carried
    `LIVE_PROVIDERS`, `LIVE_META`, three fixture `meta` blocks, `PROVIDER_DEFS`, `PROVIDER_NAMES`,
    `PLATFORM_NAMES` and its own `PROVIDERS` tuple. They had already drifted — Seccl was described
    three different ways depending on which page you were on — and adding a fourth custodian meant
    finding all seven.
    """

    def test_every_provider_has_complete_metadata(self) -> None:
        for provider in providers.PROVIDERS:
            found = providers.meta(provider)
            for key in ("name", "role", "description", "transports", "source_label"):
                assert found[key], f"{provider} has no {key}"

    def test_the_code_is_the_url_form_so_there_is_nothing_to_convert(self) -> None:
        """THE BUG THIS CLOSES. `by_slug` and `PROVIDER_META['slug']` used to bridge the key and
        the URL form, declared rather than computed precisely because an implicit substitution is
        how `provider_schema` went wrong. Declaring it did not help: four of nineteen admin routes
        never crossed the bridge, and the one provider it existed for was the one that broke.

        So the code IS the URL form. Every member has to be usable in a path segment unescaped,
        which is what makes the bridge unnecessary rather than merely mended."""
        for provider in providers.PROVIDERS:
            assert provider == quote(provider, safe=""), provider
            assert "_" not in provider, provider
        assert not hasattr(providers, "by_slug")
        assert all("slug" not in providers.meta(p) for p in providers.PROVIDERS)

    def test_an_unlisted_provider_is_unnamed_never_misnamed(self) -> None:
        """THE RULE THAT MATTERS. The UI's fallback used to be `?? LIVE_META.morgan_lloyd`, so
        every SEI page carried Morgan Lloyd's name and description above SEI's own data and its own
        "Send to..." list -- which is the confusion that gets an instruction sent to the wrong
        custodian."""
        found = providers.meta("brand-new-custodian")
        assert found["name"] == "brand new custodian"
        for provider in providers.PROVIDERS:
            assert found["name"] != providers.meta(provider)["name"]

    def test_the_kpi_relations_are_conformed_names(self) -> None:
        """A KPI names a warehouse relation the overview counts. `platform.*` is the conformed
        layer every provider publishes (docs/platform-model.md section 3), so a typo here shows as
        a permanently blank figure rather than an error."""
        for provider in providers.PROVIDERS:
            for label, relation in providers.meta(provider)["kpis"]:
                assert label
                assert relation.startswith("platform."), f"{provider}: {relation}"

    def test_the_warehouse_schema_is_derived_and_not_mapped(self) -> None:
        """NO MAP LEFT (0134). `provider_schema` carried `{"morgan-lloyd": "ml"}` because ML's
        catalogue attached under a name of its own, and that map was forgotten once —
        `materialise._read_conformed` read the bare provider name, so it read schema
        `morgan_lloyd` while the data was in `ml`, and the tenant's whole slice reported as
        unavailable. Which reads as "this firm has no data here".

        What remains is an ENCODING, not a second name: `-` is not legal in an unquoted SQL
        identifier, so the schema is the code with the hyphen replaced. A fourth custodian needs
        no entry anywhere."""
        assert providers.provider_schema("morgan-lloyd") == "morgan_lloyd"
        assert providers.provider_schema("seccl") == "seccl"
        assert providers.provider_schema("sei") == "sei"
        assert providers.provider_schema("brand-new") == "brand_new"
        # The function's own source carries no lookup table — a map is what got forgotten.
        import inspect

        assert "{" not in inspect.getsource(providers.provider_schema).split('"""')[-1]


class TestTheSeiContributionCodes:
    """`platform.tenant_platforms.config` holds SEI's contribution reference codes, and the
    allowlist refused to store them.

    `app/integrations/sei/cash_receipts.py` reads `contribution_description_code` and
    `contribution_treatment_codes` from this column and `docs/contributions.md` documents them as
    living here — but neither was in `_CONFIG_KEYS`, so `set_provider()` answered "unknown config
    keys for sei" and the only way to configure contribution lodgement was to write the row by hand.
    """

    def test_the_codes_are_accepted(self) -> None:
        providers.validate_config(
            "sei",
            {
                "firm_id": "3106",
                "contribution_description_code": "21121",
                "contribution_treatment_codes": {"personal": "4958", "employer": "4959"},
            },
        )

    def test_the_member_names_match_what_cash_receipts_reads(self) -> None:
        """Declared in `providers` and consumed in `cash_receipts`; pinned so a rename in either
        cannot drift. A misspelt member stores cleanly and then resolves to nothing, which surfaces
        as a 422 naming a code the operator can see configured."""
        from app.integrations.sei import cash_receipts

        assert providers._MAPPING_KEYS["contribution_treatment_codes"] == frozenset(
            cash_receipts.TREATMENT_CODE_FOR.values()
        )

    def test_an_unknown_treatment_category_is_refused_by_name(self) -> None:
        with pytest.raises(ValueError, match="unknown contribution_treatment_codes entries"):
            providers.validate_config(
                "sei",
                {"firm_id": "3106", "contribution_treatment_codes": {"personel": "4958"}},
            )

    def test_a_blank_code_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be non-empty strings"):
            providers.validate_config(
                "sei", {"firm_id": "3106", "contribution_treatment_codes": {"personal": "  "}}
            )

    def test_the_description_code_must_be_a_string(self) -> None:
        with pytest.raises(ValueError, match="contribution_description_code"):
            providers.validate_config(
                "sei", {"firm_id": "3106", "contribution_description_code": 21121}
            )

    def test_they_are_not_slice_keys(self) -> None:
        """They configure a CALL, not a narrowing — counting slices by them would be wrong."""
        assert providers.slice_config_keys("sei") == ("firm_id",)


class TestTheEnablementConfigIsNotEmptiedByAFormThatDoesNotCarryIt:
    """0140 folded `platform.providers` into `tenant_platforms`, so the vendor reference values now
    live on the same `config` as the slice filter — and the only UI writer builds the slice keys and
    nothing else. `config = EXCLUDED.config` therefore deleted them on every enablement write.
    """

    @pytest.mark.asyncio
    async def test_a_slice_write_preserves_the_vendor_values(
        self, service_session: AsyncSession
    ) -> None:
        tenant = await _mk_tenant(service_session)
        await providers.set_provider(
            service_session,
            tenant,
            "morgan-lloyd",
            config={
                "tenant_ids": ["02492031-c828-4a6e-9e27-e07493fb397e"],
                "scheme_type": 16,
                "sipp_type": 809130016,
            },
            enabled=True,
        )
        # What the admin card posts: the slice, and nothing else.
        row = await providers.set_provider(
            service_session,
            tenant,
            "morgan-lloyd",
            config={"tenant_ids": ["02492031-c828-4a6e-9e27-e07493fb397e", "other-tenant"]},
            enabled=True,
        )
        assert row["config"]["scheme_type"] == 16
        assert row["config"]["sipp_type"] == 809130016
        assert len(row["config"]["tenant_ids"]) == 2

    @pytest.mark.asyncio
    async def test_dropping_a_narrowing_axis_still_widens(
        self, service_session: AsyncSession
    ) -> None:
        """The other half, and why this is a merge with an exception rather than a plain merge: a
        SEI firm widened back from two subfirms to the whole firm says so by sending no
        `subfirm_ids` at all, and keeping the old list would silently keep narrowing."""
        tenant = await _mk_tenant(service_session)
        await providers.set_provider(
            service_session,
            tenant,
            "sei",
            config={
                "firm_id": "3102",
                "subfirm_ids": ["a", "b"],
                "contribution_description_code": "21121",
            },
            enabled=True,
        )
        row = await providers.set_provider(
            service_session, tenant, "sei", config={"firm_id": "3102"}, enabled=True
        )
        assert "subfirm_ids" not in row["config"]
        assert row["config"]["contribution_description_code"] == "21121"

    @pytest.mark.asyncio
    async def test_a_firm_switch_drops_the_previous_custody_map(
        self, service_session: AsyncSession
    ) -> None:
        tenant = await _mk_tenant(service_session)
        await providers.set_provider(
            service_session,
            tenant,
            "sei",
            config={
                "firm_id": "3106",
                "subfirm_ids": ["704254"],
                "custody_orgs": {"onshore": "704254"},
            },
            enabled=True,
        )
        row = await providers.set_provider(
            service_session, tenant, "sei", config={"firm_id": "3102"}, enabled=True
        )
        assert row["config"]["firm_id"] == "3102"
        assert "custody_orgs" not in row["config"]

    @pytest.mark.asyncio
    async def test_narrowing_subfirms_prunes_the_custody_map(
        self, service_session: AsyncSession
    ) -> None:
        tenant = await _mk_tenant(service_session)
        await providers.set_provider(
            service_session,
            tenant,
            "sei",
            config={
                "firm_id": "3106",
                "subfirm_ids": ["704254", "704766"],
                "custody_orgs": {"onshore": "704254", "offshore": "704766"},
            },
            enabled=True,
        )
        row = await providers.set_provider(
            service_session,
            tenant,
            "sei",
            config={"firm_id": "3106", "subfirm_ids": ["704254"]},
            enabled=True,
        )
        assert row["config"]["custody_orgs"] == {"onshore": "704254"}

    @pytest.mark.asyncio
    async def test_clearing_custody_dsrs_drops_them(self, service_session: AsyncSession) -> None:
        tenant = await _mk_tenant(service_session)
        await providers.set_provider(
            service_session,
            tenant,
            "sei",
            config={
                "firm_id": "3106",
                "custody_dsrs": {"offshore": ["704718"]},
                "contribution_description_code": "21121",
            },
            enabled=True,
        )
        row = await providers.set_provider(
            service_session, tenant, "sei", config={"firm_id": "3106"}, enabled=True
        )
        assert "custody_dsrs" not in row["config"]
        assert row["config"]["contribution_description_code"] == "21121"

    @pytest.mark.asyncio
    async def test_clearing_the_client_template_drops_it(
        self, service_session: AsyncSession
    ) -> None:
        tenant = await _mk_tenant(service_session)
        await providers.set_provider(
            service_session,
            tenant,
            "sei",
            config={
                "firm_id": "3108",
                "client_template_id": "2981279",
                "contribution_description_code": "21121",
            },
            enabled=True,
        )
        row = await providers.set_provider(
            service_session, tenant, "sei", config={"firm_id": "3108"}, enabled=True
        )
        assert "client_template_id" not in row["config"]
        assert row["config"]["contribution_description_code"] == "21121"


def test_the_client_template_and_custody_dsrs_are_form_owned_not_slice_filters() -> None:
    """A blank control clears them. They do not narrow the read slice, and a card
    save must still leave the contribution codes the card does not carry.
    """
    assert providers.slice_config_keys("sei") == ("firm_id",)
    replaced = providers.replaced_config_keys("sei")
    assert "client_template_id" in replaced
    assert "client_template_individual_id" in replaced
    assert "client_template_organisation_id" in replaced
    assert "custody_dsrs" in replaced
    # `model_dsrs` is PRESERVED, not form-owned: a model's SEI DSR teams are edited on the model
    # itself now (the catalogue's Models editor via `set_model_dsrs`), so a card save must leave
    # them the way it leaves the contribution codes.
    assert "model_dsrs" not in replaced
    assert "dsr_ids" in replaced
    assert "subfirm_ids" in replaced
    assert "contribution_description_code" not in replaced
    assert "custody_orgs" not in replaced


@pytest.mark.asyncio
async def test_set_model_dsrs_edits_one_model_and_preserves_the_rest(
    service_session: AsyncSession,
) -> None:
    """The model editor's writer touches one model's DSR teams and leaves the slice config —
    and every other model — alone; an empty list clears the one entry."""
    tenant = await _mk_tenant(service_session)
    await providers.set_provider(
        service_session,
        tenant,
        "sei",
        config={"firm_id": "3108", "subfirm_ids": ["111"]},
        enabled=True,
    )

    async def sei_config() -> dict[str, object]:
        rows = await providers.list_for_tenant(service_session, tenant)
        return next(row["config"] for row in rows if row["provider"] == "sei")

    await providers.set_model_dsrs(service_session, tenant, "PDdVYya8B8hb", ["704718"])
    await providers.set_model_dsrs(service_session, tenant, "AAAAAAAAAAAA", ["999"])
    config = await sei_config()
    # The firm and its slice are untouched; both models carry their teams.
    assert config["firm_id"] == "3108"
    assert config["subfirm_ids"] == ["111"]
    assert config["model_dsrs"] == {"PDdVYya8B8hb": ["704718"], "AAAAAAAAAAAA": ["999"]}

    # Clearing one model removes only its entry.
    await providers.set_model_dsrs(service_session, tenant, "PDdVYya8B8hb", [])
    config = await sei_config()
    assert config["model_dsrs"] == {"AAAAAAAAAAAA": ["999"]}

    # A value that is not a model public id is refused.
    with pytest.raises(ValueError, match="model public id"):
        await providers.set_model_dsrs(service_session, tenant, "not-a-uid", ["1"])


class TestFirmIdIsAGuidForMorganLloydAndNotForSei:
    """One key name, two meanings — which is why the check is keyed on (provider, key).

    `firm_id` is Morgan Lloyd's adviser-firm GUID and also SEI's slice firm, a short numeric code.
    The per-provider config shapes that expressed this were replaced by key-name-keyed tuples, so
    the GUID check was silently dropped: `platform_actions` maps one value under three vendor field
    names from `firm_id`, so a malformed one prefills all three and the vendor either refuses the
    format or accepts a wrong GUID and 403s every later `cam_createscheme`.
    """

    def test_a_malformed_morgan_lloyd_firm_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be a GUID"):
            providers.validate_config(
                "morgan-lloyd",
                {
                    "tenant_ids": ["02492031-0000-0000-0000-000000000000"],
                    "firm_id": "the-adviser-firm",
                },
            )

    def test_a_well_formed_one_passes(self) -> None:
        providers.validate_config(
            "morgan-lloyd",
            {
                "tenant_ids": ["02492031-0000-0000-0000-000000000000"],
                "firm_id": "6ac39921-d8b7-f011-bbd3-7ced8d6b85ff",
            },
        )

    def test_seis_firm_id_is_still_a_plain_code(self) -> None:
        """SEI's is `3102`. Refusing it would block every SEI enablement."""
        providers.validate_config("sei", {"firm_id": "3102"})
