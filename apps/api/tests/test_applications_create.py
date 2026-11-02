"""Kickoff create-application: transfer intent, contribution intent, catalogue ids, JISA child,
pension beneficiaries, flexi-access stamp, org owners, account-party roles (owner /
decision_maker / trustee), investment capacity / dividend treatment.

The planning graph keys ``account.has_transfer`` on ``platform.accounts.transfer IS NOT NULL``,
and organisation owners must land as ``crm.contacts.type = 'organisation'``. Model / fee package
ids must be tenant-scoped; a JISA child is a real ``crm.contacts`` owner (``individual``) with a
``registered_contact`` adult. Kickoff still snapshots ``accounts.data.jisa_child`` from that owner.
non-pension keeps the data bag only. Flexi-access intent stamps ``crm.contacts.flexi_access_taken``
on individual parties when True. All are set at journey kickoff (``POST /applications``).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.db import FirmContext
from app.permissions import CASES_READ, CASES_WRITE
from app.routers import applications as api
from app.routers import illustrations
from app.routers.applications import (
    BeneficiaryIn,
    NewAccount,
    NewApplication,
)
from app.routers.applications import (
    OwnerRef as OwnerRefModel,
)
from tests.conftest import SECCL_PLATFORM_SQL


def OwnerRef(**values: Any) -> OwnerRefModel:
    """Keep unrelated create fixtures valid under the adult-owner safety floor."""
    if values.get("contact_id") is None and values.get("type", "individual") == "individual":
        if values.get("role", "owner") in {"owner", "registered_contact"}:
            values.setdefault("dob", "1980-01-01")
            values.setdefault("email", "fixture-owner@example.test")
    return OwnerRefModel(**values)


def _principal(*perms: Any) -> Principal:
    use = perms or (CASES_WRITE,)
    return Principal(
        user_id="wos_staff",
        organisation_id="org_1",
        role="adviser",
        permissions=[p.slug if hasattr(p, "slug") else str(p) for p in use],
    )


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


async def _seed_published_flow(sess: AsyncSession) -> dict[str, Any]:
    """A tenant + platform + minimal published flow — enough for create_application to start."""
    s = uuid.uuid4().hex[:8]
    tenant = int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"cr_{s}",
            a=f"cr-{s}",
            n=f"Create {s}",
        )
    )
    platform = str(await _scalar(sess, SECCL_PLATFORM_SQL))
    await sess.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id, provider, enabled) "
            "VALUES (:t, CAST(:p AS platform.provider_code), true)"
        ),
        {"t": tenant, "p": platform},
    )
    await sess.execute(
        text(
            "WITH products AS ("
            "  INSERT INTO platform.products "
            "    (tenant_id, product_type, name, code, is_pension, enabled) "
            "  SELECT :t, value, "
            "    CASE value WHEN 'TPPENSION' THEN 'Third-Party Pension' ELSE value::text END, "
            "    value::text, value IN ('SIPP', 'TPPENSION'), true "
            "  FROM unnest(ARRAY['GIA','JISA','ISA','SIPP','TPONBOND','TPOFFBOND','TPPENSION']"
            "    ::platform.product_type[]) value RETURNING id"
            ") INSERT INTO platform.product_platforms "
            "  (tenant_id, product_id, provider, external_id, role, enabled) "
            "SELECT :t, id, CAST(:p AS platform.provider_code), id::text, 'custodian', true "
            "FROM products"
        ),
        {"t": tenant, "p": platform},
    )
    definition = {
        "steps": [
            {
                "id": "declare",
                "type": "task",
                "title": "Declare and send",
                "scope": "application",
                "assignee": "staff",
                "config": {"description": "Kickoff stub"},
            }
        ],
        "milestones": [],
    }
    flow_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, 'onboarding', 'Onboarding', 'published') RETURNING id",
            u=f"flw_{s}",
            t=tenant,
        )
    )
    version_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flow_versions "
            "  (uid, tenant_id, flow_id, version, status, definition, published_at) "
            "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id",
            u=f"flv_{s}",
            t=tenant,
            f=flow_id,
            d=json.dumps(definition),
        )
    )
    await sess.execute(
        text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :f"),
        {"v": version_id, "f": flow_id},
    )
    return {"tenant": tenant, "platform": platform, "suffix": s}


async def _seed_catalogue(sess: AsyncSession, fx: dict[str, Any]) -> dict[str, int]:
    """Model + fee package + transfer provider + JISA product on the fixture tenant."""
    s = fx["suffix"]
    product = int(
        await _scalar(
            sess,
            "SELECT id FROM platform.products WHERE tenant_id = :t AND product_type = 'JISA'",
            t=fx["tenant"],
        )
    )
    model = int(
        await _scalar(
            sess,
            "INSERT INTO platform.models (provider, tenant_id, name) "
            "VALUES (:p, :t, :n) RETURNING id",
            p=fx["platform"],
            t=fx["tenant"],
            n=f"Balanced {s}",
        )
    )
    fee = int(
        await _scalar(
            sess,
            "INSERT INTO platform.fee_packages "
            "  (tenant_id, provider, product_id, name, category, frequency, fee_type, rule) "
            "VALUES (:t, :p, :pr, :n, 'platform', 'ongoing', 'percentage', CAST(:rule AS jsonb)) "
            "RETURNING id",
            t=fx["tenant"],
            p=fx["platform"],
            pr=product,
            n=f"Standard {s}",
            rule=json.dumps({"rate": 0.0035}),
        )
    )
    provider = int(
        await _scalar(
            sess,
            "INSERT INTO platform.transfer_providers "
            "  (tenant_id, name, masterparty_id) VALUES (:t, :n, :mp) RETURNING id",
            t=fx["tenant"],
            n=f"Ceding Co {s}",
            mp=f"mp_{s}",
        )
    )
    return {"product": product, "model": model, "fee": fee, "provider": provider}


async def _seed_sipp_illustration(
    sess: AsyncSession, fx: dict[str, Any], *, inputs: dict[str, Any] | None = None
) -> tuple[int, str]:
    suffix = uuid.uuid4().hex[:8]
    contact_id = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email, dob) "
            "VALUES (:t, :u, 'individual', 'Illustrated', 'Client', :e, DATE '1980-01-01') "
            "RETURNING id",
            t=fx["tenant"],
            u=f"ct{suffix}xx",
            e=f"illustrated-{suffix}@example.test",
        )
    )
    illustration_uid = f"il{suffix}xx"
    await sess.execute(
        text(
            "INSERT INTO platform.illustrations "
            "  (uid, tenant_id, contact_id, name, product_type, quotation_type, inputs) "
            "VALUES (:u, :t, :c, 'SIPP illustration', 'SIPP', 'Accumulation', CAST(:i AS jsonb))"
        ),
        {
            "u": illustration_uid,
            "t": fx["tenant"],
            "c": contact_id,
            "i": json.dumps({"engine_errors": [], **(inputs or {})}),
        },
    )
    return contact_id, illustration_uid


@pytest.mark.asyncio
async def test_kickoff_supports_mixed_custodians_and_custody_per_account(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    await sess.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id, provider, enabled) "
            "VALUES (:t, 'sei', true)"
        ),
        {"t": fx["tenant"]},
    )
    isa = int(
        await _scalar(
            sess,
            "SELECT id FROM platform.products WHERE tenant_id = :t AND product_type = 'ISA'",
            t=fx["tenant"],
        )
    )
    await sess.execute(
        text(
            "INSERT INTO platform.product_platforms "
            "  (tenant_id, product_id, provider, external_id, role, enabled) "
            "VALUES (:t, :p, 'sei', 'ISA', 'custodian', true)"
        ),
        {"t": fx["tenant"], "p": isa},
    )
    person = {
        "first_name": "Ada",
        "surname": "Lovelace",
        "email": f"mixed_{fx['suffix']}@t.test",
    }
    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            accounts=[
                NewAccount(
                    product_type="GIA",
                    provider="seccl",
                    custody="onshore",
                    owners=[OwnerRef(**person, identity_mode="default")],
                ),
                NewAccount(
                    product_type="ISA",
                    provider="sei",
                    custody="offshore",
                    owners=[OwnerRef(**person, identity_mode="default")],
                ),
            ],
        ),
        principal=_principal(),
        ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
    )
    app = (
        await sess.execute(
            text(
                "SELECT provider::text, custody::text FROM crm.applications "
                "WHERE tenant_id = :t ORDER BY id DESC LIMIT 1"
            ),
            {"t": fx["tenant"]},
        )
    ).one()
    assert app == (None, None)
    account_rows = (
        await sess.execute(
            text(
                "SELECT custodian::text, custody::text FROM platform.accounts "
                "WHERE tenant_id = :t ORDER BY id"
            ),
            {"t": fx["tenant"]},
        )
    ).all()
    assert account_rows == [("seccl", "onshore"), ("sei", "offshore")]
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM crm.contacts WHERE tenant_id = :t",
            t=fx["tenant"],
        )
        == 1
    )
    intake = await _scalar(
        sess,
        "SELECT data -> 'intake' FROM flows.executions "
        "WHERE tenant_id = :t ORDER BY id DESC LIMIT 1",
        t=fx["tenant"],
    )
    assert list(intake["identity_modes"].values()) == ["default"]


@pytest.mark.asyncio
async def test_seccl_cannot_open_an_offshore_account(service_session: AsyncSession) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    with pytest.raises(HTTPException, match="not available with offshore at seccl"):
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                accounts=[
                    NewAccount(
                        product_type="GIA",
                        provider="seccl",
                        custody="offshore",
                        owners=[OwnerRef(first_name="Seccl", surname="Offshore", role="owner")],
                    )
                ],
            ),
            principal=_principal(),
            ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
        )


@pytest.mark.asyncio
async def test_sei_offshore_is_refused_when_only_onshore_is_mapped(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    await sess.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id, provider, enabled, config) "
            "VALUES (:t, 'sei', true, CAST(:c AS jsonb))"
        ),
        {
            "t": fx["tenant"],
            "c": json.dumps(
                {
                    "firm_id": "3106",
                    "subfirm_ids": ["704254"],
                    "custody_orgs": {"onshore": "704254"},
                }
            ),
        },
    )
    isa = int(
        await _scalar(
            sess,
            "SELECT id FROM platform.products WHERE tenant_id = :t AND product_type = 'ISA'",
            t=fx["tenant"],
        )
    )
    await sess.execute(
        text(
            "INSERT INTO platform.product_platforms "
            "  (tenant_id, product_id, provider, external_id, role, enabled) "
            "VALUES (:t, :p, 'sei', 'ISA', 'custodian', true)"
        ),
        {"t": fx["tenant"], "p": isa},
    )
    with pytest.raises(HTTPException, match="not available with offshore at sei"):
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                accounts=[
                    NewAccount(
                        product_type="ISA",
                        provider="sei",
                        custody="offshore",
                        owners=[OwnerRef(first_name="Select", surname="Onshore", role="owner")],
                    )
                ],
            ),
            principal=_principal(),
            ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
        )


@pytest.mark.asyncio
async def test_omitted_custody_defaults_to_the_only_openable_location(
    service_session: AsyncSession,
) -> None:
    """Onshore is the default only when it is open. An offshore-only intersection used to
    leave custody blank and then 422, even though offshore is the one location that can open."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    await sess.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id, provider, enabled, config) "
            "VALUES (:t, 'sei', true, CAST(:c AS jsonb))"
        ),
        {
            "t": fx["tenant"],
            "c": json.dumps(
                {
                    "firm_id": "3106",
                    "subfirm_ids": ["704255"],
                    "custody_orgs": {"offshore": "704255"},
                }
            ),
        },
    )
    isa = int(
        await _scalar(
            sess,
            "SELECT id FROM platform.products WHERE tenant_id = :t AND product_type = 'ISA'",
            t=fx["tenant"],
        )
    )
    await sess.execute(
        text(
            "INSERT INTO platform.product_platforms "
            "  (tenant_id, product_id, provider, external_id, role, enabled) "
            "VALUES (:t, :p, 'sei', 'ISA', 'custodian', true)"
        ),
        {"t": fx["tenant"], "p": isa},
    )
    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            accounts=[
                NewAccount(
                    product_type="ISA",
                    provider="sei",
                    owners=[OwnerRef(first_name="Only", surname="Offshore", role="owner")],
                )
            ],
        ),
        principal=_principal(),
        ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
    )
    assert (
        await _scalar(
            sess,
            "SELECT custody::text FROM platform.accounts "
            "WHERE tenant_id = :t ORDER BY id DESC LIMIT 1",
            t=fx["tenant"],
        )
        == "offshore"
    )


@pytest.mark.asyncio
async def test_a_product_with_no_custodian_mapping_still_starts_a_journey(
    service_session: AsyncSession,
) -> None:
    """A shelf item no provider is mapped to yet is an ordinary state, not a refusal.

    The account records no custodian and an operator maps the product later; refusing here
    stranded the adviser on a kickoff screen nothing on it could fix.
    """
    sess = service_session
    fx = await _seed_published_flow(sess)
    await sess.execute(
        text(
            "DELETE FROM platform.product_platforms pp USING platform.products pr "
            "WHERE pp.product_id = pr.id AND pr.tenant_id = :t AND pr.product_type = 'TPONBOND'"
        ),
        {"t": fx["tenant"]},
    )
    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            accounts=[
                NewAccount(
                    product_type="TPONBOND",
                    custody="onshore",
                    owners=[OwnerRef(first_name="Unmapped", surname="Shelf", role="owner")],
                )
            ],
        ),
        principal=_principal(),
        ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
    )
    assert (
        await _scalar(
            sess,
            "SELECT custodian::text FROM platform.accounts "
            "WHERE tenant_id = :t ORDER BY id DESC LIMIT 1",
            t=fx["tenant"],
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_custodian_the_product_does_not_offer_is_still_refused(
    service_session: AsyncSession,
) -> None:
    """The missing mapping is tolerated; a WRONG custodian is not — that is a wrong answer rather
    than an absent one, and it would file the account with a provider whose pipeline never reads
    it."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    with pytest.raises(HTTPException, match="is not an enabled custodian"):
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                accounts=[
                    NewAccount(
                        product_type="GIA",
                        provider="sei",
                        custody="onshore",
                        owners=[OwnerRef(first_name="Wrong", surname="Custodian", role="owner")],
                    )
                ],
            ),
            principal=_principal(),
            ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
        )


@pytest.mark.asyncio
async def test_one_person_cannot_have_conflicting_idv_modes_across_accounts(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    person = {
        "first_name": "Shared",
        "surname": "Person",
        "email": f"shared_{fx['suffix']}@t.test",
    }
    with pytest.raises(HTTPException, match="different identity methods"):
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider="seccl",
                accounts=[
                    NewAccount(
                        product_type="GIA",
                        owners=[OwnerRef(**person, identity_mode="default")],
                    ),
                    NewAccount(
                        product_type="ISA",
                        owners=[OwnerRef(**person, identity_mode="manual")],
                    ),
                ],
            ),
            principal=_principal(),
            ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
        )


@pytest.mark.asyncio
async def test_product_party_limit_is_enforced_before_contacts_are_created(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    with pytest.raises(HTTPException, match="at most 2 parties"):
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider="seccl",
                accounts=[
                    NewAccount(
                        product_type="GIA",
                        owners=[
                            OwnerRef(name="One", role="owner"),
                            OwnerRef(name="Two", role="trustee"),
                            OwnerRef(name="Three", role="attorney"),
                        ],
                    )
                ],
            ),
            principal=_principal(),
            ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
        )
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM crm.contacts WHERE tenant_id = :t",
            t=fx["tenant"],
        )
        == 0
    )


@pytest.mark.asyncio
async def test_kickoff_assigns_the_chosen_adviser_to_every_client_record(
    service_session: AsyncSession,
) -> None:
    """The kickoff picker wrote only ``crm.applications.adviser_id``. Client records, adviser
    scoping and the client portal's adviser card all read ``crm.client_advisers``, so the choice
    disappeared after onboarding and a scoped adviser could not see their newly assigned clients.
    """
    sess = service_session
    fx = await _seed_published_flow(sess)
    suffix = fx["suffix"]
    adviser = str(
        await _scalar(
            sess,
            "INSERT INTO auth.users "
            "  (uid, tenant_id, email, workos_user_id, enabled, type, is_adviser) "
            "VALUES (:uid, :t, :email, :wos, true, '{staff}', true) RETURNING id",
            uid=f"adv_{suffix}",
            t=fx["tenant"],
            email=f"adviser_{suffix}@firm.test",
            wos=f"wos_adviser_{suffix}",
        )
    )

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            adviser_id=adviser,
            accounts=[
                NewAccount(
                    product_type="GIA",
                    owners=[
                        OwnerRef(
                            first_name="Ada",
                            surname="Lovelace",
                            email=f"ada_{suffix}@t.test",
                        ),
                        OwnerRef(
                            first_name="Grace",
                            surname="Hopper",
                            email=f"grace_{suffix}@t.test",
                            role="decision_maker",
                        ),
                    ],
                )
            ],
        ),
        principal=_principal(),
        ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
    )

    assigned = (
        await sess.execute(
            text(
                "SELECT c.email, ca.role FROM crm.client_advisers ca "
                "JOIN crm.contacts c ON c.id = ca.contact_id "
                "WHERE ca.tenant_id = :t AND ca.adviser_id = CAST(:a AS uuid) "
                "ORDER BY c.email"
            ),
            {"t": fx["tenant"], "a": adviser},
        )
    ).all()
    assert assigned == [
        (f"ada_{suffix}@t.test", "adviser"),
        (f"grace_{suffix}@t.test", "adviser"),
    ]
    # 0158 once backfilled this for journeys started before the fix, as `lead`. It cannot be re-run
    # on today's schema — 0245 turned every `lead` into `adviser` and refuses the old word — so
    # tests/test_one_adviser_per_client.py pins that hand-over instead.


@pytest.mark.asyncio
async def test_kickoff_defaults_the_adviser_and_assignee_to_the_clients_own_adviser(
    service_session: AsyncSession,
) -> None:
    """FB-20260923-05. With no adviser picked, the journey's adviser AND its assignee (0188) used
    to be whoever was signed in — so an administrator starting onboarding for an adviser's client
    became that journey's adviser and took every staff task of it. The client's own adviser
    (``crm.client_advisers``) is the default; a joint applicant with nobody joins that book."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    suffix = fx["suffix"]
    adviser = str(
        await _scalar(
            sess,
            "INSERT INTO auth.users "
            "  (uid, tenant_id, email, workos_user_id, enabled, type, is_adviser) "
            "VALUES (:uid, :t, :email, :wos, true, '{staff}', true) RETURNING id",
            uid=f"adv_{suffix}",
            t=fx["tenant"],
            email=f"adviser_{suffix}@firm.test",
            wos=f"wos_adviser_{suffix}",
        )
    )
    existing = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email, dob) "
            "VALUES (:t, :u, 'individual', 'Ada', 'Lovelace', :e, DATE '1980-01-01') RETURNING id",
            t=fx["tenant"],
            u=f"ct{suffix}ad",
            e=f"ada_{suffix}@t.test",
        )
    )
    await sess.execute(
        text(
            "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
            "VALUES (:t, :c, CAST(:a AS uuid), 'adviser')"
        ),
        {"t": fx["tenant"], "c": existing, "a": adviser},
    )

    out = await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    product_type="GIA",
                    owners=[
                        OwnerRef(contact_id=existing),
                        OwnerRef(
                            first_name="Grace",
                            surname="Hopper",
                            email=f"grace_{suffix}@t.test",
                        ),
                    ],
                )
            ],
        ),
        principal=_principal(),
        ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
    )

    row = (
        (
            await sess.execute(
                text(
                    "SELECT a.adviser_id::text AS adviser_id, "
                    "       e.assignee_user_id::text AS assignee_user_id "
                    "FROM flows.executions e JOIN crm.applications a "
                    "  ON a.id = e.crm_application_id WHERE e.uid = :u"
                ),
                {"u": out["execution_uid"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["adviser_id"] == adviser
    assert row["assignee_user_id"] == adviser
    grace_adviser = await _scalar(
        sess,
        "SELECT ca.adviser_id::text FROM crm.client_advisers ca "
        "JOIN crm.contacts c ON c.id = ca.contact_id "
        "WHERE ca.tenant_id = :t AND c.email = :e AND ca.role = 'adviser'",
        t=fx["tenant"],
        e=f"grace_{suffix}@t.test",
    )
    assert grace_adviser == adviser


@pytest.mark.asyncio
async def test_a_new_client_is_assigned_the_signed_in_adviser(
    service_session: AsyncSession,
) -> None:
    """A brand-new client has no lead adviser, so the journey records whoever is signed in.
    That person has to land on ``crm.client_advisers`` too: enrolment reads the client record
    for the adviser's SEI employee id, and an application-only adviser is invisible to it.
    """
    sess = service_session
    fx = await _seed_published_flow(sess)
    suffix = fx["suffix"]
    adviser = str(
        await _scalar(
            sess,
            "INSERT INTO auth.users "
            "  (uid, tenant_id, email, workos_user_id, enabled, type, is_adviser) "
            "VALUES (:uid, :t, :email, :wos, true, '{staff}', true) RETURNING id",
            uid=f"me_{suffix}",
            t=fx["tenant"],
            email=f"me_{suffix}@firm.test",
            wos=f"wos_me_{suffix}",
        )
    )
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"user_uuid": adviser, "role": "authenticated"})},
    )
    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    product_type="GIA",
                    owners=[
                        OwnerRef(
                            first_name="Ada",
                            surname="Lovelace",
                            email=f"ada_{suffix}@t.test",
                        )
                    ],
                )
            ],
        ),
        principal=_principal(),
        ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
    )
    assigned = await _scalar(
        sess,
        "SELECT ca.adviser_id::text FROM crm.client_advisers ca "
        "JOIN crm.contacts c ON c.id = ca.contact_id "
        "WHERE ca.tenant_id = :t AND c.email = :e AND ca.role = 'adviser'",
        t=fx["tenant"],
        e=f"ada_{suffix}@t.test",
    )
    assert assigned == adviser


async def _sign_in(sess: AsyncSession, user_id: str) -> None:
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"user_uuid": user_id, "role": "authenticated"})},
    )


@pytest.mark.asyncio
async def test_kickoff_refuses_when_the_signed_in_user_is_not_an_adviser(
    service_session: AsyncSession,
) -> None:
    """A blank picker and a new client used to record the signed-in user, then
    ``assign_adviser`` raised ``LookupError`` because they were not an adviser. Nothing
    caught it, so the wizard said the request failed with a 500."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    suffix = fx["suffix"]
    staff = str(
        await _scalar(
            sess,
            "INSERT INTO auth.users "
            "  (uid, tenant_id, email, workos_user_id, enabled, type, is_adviser) "
            "VALUES (:uid, :t, :email, :wos, true, '{staff}', false) RETURNING id",
            uid=f"stf_{suffix}",
            t=fx["tenant"],
            email=f"staff_{suffix}@firm.test",
            wos=f"wos_staff_{suffix}",
        )
    )
    await _sign_in(sess, staff)
    with pytest.raises(HTTPException) as exc:
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider=fx["platform"],
                accounts=[
                    NewAccount(
                        product_type="GIA",
                        owners=[
                            OwnerRef(
                                first_name="Ada",
                                surname="Lovelace",
                                email=f"ada_{suffix}@t.test",
                            )
                        ],
                    )
                ],
            ),
            principal=_principal(),
            ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == api._NO_ADVISER
    assert (
        await _scalar(
            sess, "SELECT count(*) FROM crm.applications WHERE tenant_id = :t", t=fx["tenant"]
        )
        == 0
    )


@pytest.mark.asyncio
async def test_kickoff_refuses_a_malformed_adviser_id(service_session: AsyncSession) -> None:
    """A non-uuid used to die in ``CAST(:u AS uuid)`` and reach the wizard as a 500."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    suffix = fx["suffix"]
    with pytest.raises(HTTPException) as exc:
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider=fx["platform"],
                adviser_id="not-a-uuid",
                accounts=[
                    NewAccount(
                        product_type="GIA",
                        owners=[
                            OwnerRef(
                                first_name="Ada",
                                surname="Lovelace",
                                email=f"ada_{suffix}@t.test",
                            )
                        ],
                    )
                ],
            ),
            principal=_principal(),
            ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == api._ADVISER_REQUIRED


@pytest.mark.asyncio
async def test_a_non_adviser_starts_a_journey_for_a_client_who_already_has_one(
    service_session: AsyncSession,
) -> None:
    """Blank still means the client's own adviser. The signed-in user being someone else,
    and not an adviser, must not refuse the journey or take it over."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    suffix = fx["suffix"]
    adviser = str(
        await _scalar(
            sess,
            "INSERT INTO auth.users "
            "  (uid, tenant_id, email, workos_user_id, enabled, type, is_adviser) "
            "VALUES (:uid, :t, :email, :wos, true, '{staff}', true) RETURNING id",
            uid=f"adv_{suffix}",
            t=fx["tenant"],
            email=f"adviser_{suffix}@firm.test",
            wos=f"wos_adviser_{suffix}",
        )
    )
    staff = str(
        await _scalar(
            sess,
            "INSERT INTO auth.users "
            "  (uid, tenant_id, email, workos_user_id, enabled, type, is_adviser) "
            "VALUES (:uid, :t, :email, :wos, true, '{staff}', false) RETURNING id",
            uid=f"stf_{suffix}",
            t=fx["tenant"],
            email=f"staff_{suffix}@firm.test",
            wos=f"wos_staff_{suffix}",
        )
    )
    existing = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email, dob) "
            "VALUES (:t, :u, 'individual', 'Ada', 'Lovelace', :e, DATE '1980-01-01') RETURNING id",
            t=fx["tenant"],
            u=f"ct{suffix}ad",
            e=f"ada_{suffix}@t.test",
        )
    )
    await sess.execute(
        text(
            "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
            "VALUES (:t, :c, CAST(:a AS uuid), 'adviser')"
        ),
        {"t": fx["tenant"], "c": existing, "a": adviser},
    )
    await _sign_in(sess, staff)
    out = await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[NewAccount(product_type="GIA", owners=[OwnerRef(contact_id=existing)])],
        ),
        principal=_principal(),
        ctx=FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal()),
    )
    row = (
        (
            await sess.execute(
                text(
                    "SELECT a.adviser_id::text AS adviser_id, "
                    "       e.assignee_user_id::text AS assignee_user_id "
                    "FROM flows.executions e JOIN crm.applications a "
                    "  ON a.id = e.crm_application_id WHERE e.uid = :u"
                ),
                {"u": out["execution_uid"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["adviser_id"] == adviser
    assert row["assignee_user_id"] == adviser

    # The wizard's picker rule reads the same fact from the clients list: a client with an
    # adviser carries it, one with nobody — or whose adviser lost the flag — does not.
    from app.permissions import DOCUMENTS_WRITE
    from app.routers import documents

    orphan = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email, dob) "
            "VALUES (:t, :u, 'individual', 'Bob', 'Orphan', :e, DATE '1980-01-01') RETURNING id",
            t=fx["tenant"],
            u=f"ct{suffix}bo",
            e=f"bob_{suffix}@t.test",
        )
    )
    await sess.execute(
        text(
            "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
            "VALUES (:t, :c, CAST(:a AS uuid), 'adviser')"
        ),
        {"t": fx["tenant"], "c": orphan, "a": staff},
    )
    listed = {
        c.contact_id: c.adviser_id
        for c in await documents.list_clients(principal=_principal(DOCUMENTS_WRITE), session=sess)
    }
    assert listed[existing] == adviser
    assert listed[orphan] is None


@pytest.mark.asyncio
async def test_a_second_journey_names_the_live_one_it_is_refused_for(
    service_session: AsyncSession,
) -> None:
    """Outstanding Issues 7.10.26 row 17. The 409 said "cancel it before starting another" and
    offered no way to do so. It now carries the live journey's uid so the wizard can open it or
    stop it from the refusal itself."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    suffix = fx["suffix"]
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())
    # `cases_one_active_per_subject` guards PROFILE cases, which only a participant-scoped step
    # plans; the fixture's application-scoped stub raises none.
    await sess.execute(
        text(
            "UPDATE flows.flow_versions SET definition = CAST(:d AS jsonb) "
            "WHERE tenant_id = :t AND flow_id = "
            "  (SELECT id FROM flows.flows WHERE tenant_id = :t AND code = 'onboarding')"
        ),
        {
            "t": fx["tenant"],
            "d": json.dumps(
                {
                    "steps": [
                        {
                            "id": "details",
                            "type": "task",
                            "title": "Applicant details",
                            "scope": "participant",
                            "assignee": "staff",
                            "config": {"description": "Kickoff stub"},
                        }
                    ],
                    "milestones": [],
                }
            ),
        },
    )
    existing = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email, dob) "
            "VALUES (:t, :u, 'individual', 'Ada', 'Lovelace', :e, DATE '1980-01-01') RETURNING id",
            t=fx["tenant"],
            u=f"ct{suffix}lv",
            e=f"live_{suffix}@t.test",
        )
    )

    def body() -> NewApplication:
        return NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[NewAccount(product_type="GIA", owners=[OwnerRef(contact_id=existing)])],
        )

    first = await api.create_application(body(), principal=_principal(), ctx=ctx)
    with pytest.raises(HTTPException) as exc:
        async with sess.begin_nested():
            await api.create_application(body(), principal=_principal(), ctx=ctx)
    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "message": (
            "This client is already on a live journey of this flow. "
            "Cancel it before starting another."
        ),
        "execution_uid": first["execution_uid"],
    }


@pytest.mark.asyncio
async def _legacy_create_application_has_transfer_writes_transfer_stub(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    out = await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            custody="onshore",
            accounts=[
                NewAccount(
                    owners=[OwnerRef(name="Transfer Client", email="xfer@t.test")],
                    has_transfer=True,
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )
    assert "execution_uid" in out

    row = (
        (
            await sess.execute(
                text(
                    "SELECT transfer IS NOT NULL AS has_xfer, transfer "
                    "FROM platform.accounts WHERE tenant_id = :t AND application_id IS NOT NULL "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["has_xfer"] is True
    xfer = row["transfer"]
    if isinstance(xfer, str):
        xfer = json.loads(xfer)
    assert xfer.get("intent") is True


@pytest.mark.asyncio
async def test_create_application_without_transfer_leaves_transfer_null(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    product_type="GIA",
                    owners=[OwnerRef(name="Cash Client")],
                    has_transfer=False,
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    has_xfer = await _scalar(
        sess,
        "SELECT transfer IS NOT NULL FROM platform.accounts "
        "WHERE tenant_id = :t AND application_id IS NOT NULL ORDER BY id DESC LIMIT 1",
        t=fx["tenant"],
    )
    assert has_xfer is False
    assert (
        await _scalar(
            sess,
            "SELECT adviser_declaration FROM crm.applications "
            "WHERE tenant_id = :t ORDER BY id DESC LIMIT 1",
            t=fx["tenant"],
        )
        is False
    )


@pytest.mark.asyncio
async def test_create_application_resolves_fixed_product_type_without_catalogue_setup(
    service_session: AsyncSession,
) -> None:
    """Kickoff's four wrapper choices work before the firm's richer product shelf is configured."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    contact_id, illustration_uid = await _seed_sipp_illustration(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    owners=[OwnerRef(contact_id=contact_id)],
                    product_type="sipp",
                    illustration_uid=illustration_uid,
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    row = (
        (
            await sess.execute(
                text(
                    "SELECT p.product_type::text AS product_type, p.tenant_id, p.is_pension "
                    "FROM platform.accounts a "
                    "JOIN platform.products p ON p.id = a.product_id "
                    "WHERE a.tenant_id = :t AND a.application_id IS NOT NULL "
                    "ORDER BY a.id DESC LIMIT 1"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .one()
    )
    assert dict(row) == {
        "product_type": "SIPP",
        "tenant_id": fx["tenant"],
        "is_pension": True,
    }
    linkage = (
        (
            await sess.execute(
                text(
                    "SELECT a.id AS account_id, a.onboarding_illustration_id, "
                    "       i.account_id AS linked "
                    "FROM platform.accounts a "
                    "JOIN platform.illustrations i ON i.id = a.onboarding_illustration_id "
                    "WHERE a.tenant_id = :t AND i.uid = :u"
                ),
                {"t": fx["tenant"], "u": illustration_uid},
            )
        )
        .mappings()
        .one()
    )
    assert linkage["onboarding_illustration_id"] is not None
    assert linkage["linked"] == linkage["account_id"]


@pytest.mark.asyncio
async def test_sipp_requires_a_prior_illustration_and_other_products_refuse_one(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    contact_id, illustration_uid = await _seed_sipp_illustration(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())
    with pytest.raises(HTTPException, match="Choose a successful SIPP illustration"):
        await api.create_application(
            NewApplication(
                provider=fx["platform"],
                accounts=[
                    NewAccount(product_type="SIPP", owners=[OwnerRef(contact_id=contact_id)])
                ],
            ),
            principal=_principal(),
            ctx=ctx,
        )
    with pytest.raises(HTTPException, match="Only a SIPP account"):
        await api.create_application(
            NewApplication(
                provider=fx["platform"],
                accounts=[
                    NewAccount(
                        product_type="ISA",
                        illustration_uid=illustration_uid,
                        owners=[OwnerRef(contact_id=contact_id)],
                    )
                ],
            ),
            principal=_principal(),
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_onboarding_illustration_options_exclude_placeholder_and_used_runs(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    contact_id, available_uid = await _seed_sipp_illustration(sess, fx)
    _other_contact, placeholder_uid = await _seed_sipp_illustration(sess, fx)
    await sess.execute(
        text("UPDATE platform.illustrations SET placeholder = true WHERE uid = :u"),
        {"u": placeholder_uid},
    )
    options = await illustrations.list_onboarding_illustrations(
        principal=_principal(CASES_READ), session=sess
    )
    by_uid = {option.uid: option for option in options}
    assert by_uid[available_uid].contact_id == contact_id
    assert by_uid[available_uid].document_uid is None
    assert placeholder_uid not in by_uid

    doc_uid = f"doc_{uuid.uuid4().hex[:8]}"
    doc_id = await _scalar(
        sess,
        "INSERT INTO documents.documents "
        "  (uid, tenant_id, document_type, source, name, status) "
        "VALUES (:u, :t, 'illustration', 'generated', 'SIPP illustration', 'generated') "
        "RETURNING id",
        u=doc_uid,
        t=fx["tenant"],
    )
    await sess.execute(
        text("UPDATE platform.illustrations SET document_id = :d WHERE uid = :u"),
        {"d": doc_id, "u": available_uid},
    )
    filed = await illustrations.list_onboarding_illustrations(
        principal=_principal(CASES_READ), session=sess
    )
    assert {option.uid: option.document_uid for option in filed}[available_uid] == doc_uid


@pytest.mark.asyncio
async def test_create_application_organisation_owner_sets_contact_type(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    product_type="GIA",
                    owners=[
                        OwnerRef(
                            name="Acme Trustees Ltd",
                            email="ops@acme.test",
                            type="organisation",
                        )
                    ],
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    ctype = await _scalar(
        sess,
        "SELECT type::text FROM crm.contacts WHERE tenant_id = :t AND name = :n",
        t=fx["tenant"],
        n="Acme Trustees Ltd",
    )
    assert ctype == "organisation"
    # Organisation names are not split into first/surname.
    first = await _scalar(
        sess,
        "SELECT first_name FROM crm.contacts WHERE tenant_id = :t AND name = :n",
        t=fx["tenant"],
        n="Acme Trustees Ltd",
    )
    assert first is None


@pytest.mark.asyncio
async def test_existing_organisation_cannot_open_an_isa(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())
    organisation_id = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, name) "
            "VALUES (:t, :u, 'organisation', 'Acme Holdings Ltd') RETURNING id",
            t=fx["tenant"],
            u=uuid.uuid4().hex[:12],
        )
    )

    with pytest.raises(HTTPException, match="only be opened for an individual"):
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider=fx["platform"],
                accounts=[
                    NewAccount(
                        product_type="ISA",
                        owners=[OwnerRef(contact_id=organisation_id)],
                    )
                ],
            ),
            principal=_principal(),
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_create_application_org_owner_plus_trustee_roles(
    service_session: AsyncSession,
) -> None:
    """Org/trust kickoff: organisation owner + individual trustee land as distinct roles."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    product_type="GIA",
                    owners=[
                        OwnerRef(
                            name="Acme Trust",
                            type="organisation",
                            role="owner",
                        ),
                        OwnerRef(
                            name="Jane Trustee",
                            email="jane@t.test",
                            type="individual",
                            role="trustee",
                        ),
                    ],
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    rows = (
        (
            await sess.execute(
                text(
                    "SELECT c.name, ac.role::text AS role "
                    "FROM crm.account_contacts ac "
                    "JOIN crm.contacts c ON c.id = ac.contact_id "
                    "WHERE ac.tenant_id = :t "
                    "ORDER BY ac.role::text, c.name"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .all()
    )
    assert [(r["name"], r["role"]) for r in rows] == [
        ("Acme Trust", "owner"),
        ("Jane Trustee", "trustee"),
    ]
    # Primary account contact prefers the first owner-role party (the org).
    primary = await _scalar(
        sess,
        "SELECT c.name FROM platform.accounts a "
        "JOIN crm.contacts c ON c.id = a.contact_id "
        "WHERE a.tenant_id = :t ORDER BY a.id DESC LIMIT 1",
        t=fx["tenant"],
    )
    assert primary == "Acme Trust"


def test_owner_ref_defaults_role_to_owner() -> None:
    assert OwnerRef(name="Kickoff Client").role == "owner"


def test_new_account_keeps_kickoff_structural() -> None:
    assert set(NewAccount.model_fields) == {
        "product_type",
        "product_id",
        "provider",
        "custody",
        "illustration_uid",
        "owners",
        "child_name",
        "child_dob",
    }
    # The MODEL, not the fixture shim above it — the shim's job is to fill in the adult-owner
    # floor, and asking a function for its pydantic fields is how this assertion stopped checking
    # anything at all.
    assert {"identity_mode", "party_key", "registered_contact_for"} <= set(
        OwnerRefModel.model_fields
    )


def test_owner_ref_accepts_attorney_and_other() -> None:
    assert OwnerRef(name="PoA", role="attorney").role == "attorney"
    assert OwnerRef(name="Helper", role="other").role == "other"


def test_new_account_requires_at_least_one_owner_role() -> None:
    with pytest.raises(ValidationError) as exc:
        NewAccount(
            owners=[
                OwnerRef(name="Only Trustee", role="trustee"),
                OwnerRef(name="Only DM", role="decision_maker"),
            ]
        )
    assert "role owner" in str(exc.value).lower()


def test_new_account_attorney_alone_is_not_enough() -> None:
    with pytest.raises(ValidationError) as exc:
        NewAccount(owners=[OwnerRef(name="Only Attorney", role="attorney")])
    assert "role owner" in str(exc.value).lower()


def test_organisation_with_trustee_role_refused() -> None:
    with pytest.raises(ValidationError) as exc:
        OwnerRef(name="Acme", type="organisation", role="trustee")
    assert "organisation" in str(exc.value).lower()
    assert "owner" in str(exc.value).lower()


def test_owner_ref_rejects_beneficiary_role() -> None:
    with pytest.raises(ValidationError) as exc:
        OwnerRef(name="Wish", role="beneficiary")
    assert "role" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_create_application_with_attorney_role(
    service_session: AsyncSession,
) -> None:
    """Attorney is recordable at kickoff but is not a KYC-invite role (planning.KYC_ROLES)."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    product_type="GIA",
                    owners=[
                        OwnerRef(name="Primary Owner", email="owner@t.test", role="owner"),
                        OwnerRef(name="Pat Attorney", email="poa@t.test", role="attorney"),
                    ],
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    rows = (
        (
            await sess.execute(
                text(
                    "SELECT c.name, ac.role::text AS role "
                    "FROM crm.account_contacts ac "
                    "JOIN crm.contacts c ON c.id = ac.contact_id "
                    "WHERE ac.tenant_id = :t "
                    "ORDER BY ac.role::text, c.name"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .all()
    )
    assert [(r["name"], r["role"]) for r in rows] == [
        ("Pat Attorney", "attorney"),
        ("Primary Owner", "owner"),
    ]


@pytest.mark.asyncio
async def _legacy_create_application_writes_model_and_fee_package(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    cat = await _seed_catalogue(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    owners=[OwnerRef(name="Model Client")],
                    product_id=cat["product"],
                    model_id=cat["model"],
                    fee_package_id=cat["fee"],
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    row = (
        (
            await sess.execute(
                text(
                    "SELECT model_id, fee_package_id FROM platform.accounts "
                    "WHERE tenant_id = :t AND application_id IS NOT NULL "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["model_id"] == cat["model"]
    assert row["fee_package_id"] == cat["fee"]


@pytest.mark.asyncio
async def _legacy_create_application_refuses_foreign_model_id(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    other = await _seed_published_flow(sess)
    other_cat = await _seed_catalogue(sess, other)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    with pytest.raises(HTTPException) as exc:
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider=fx["platform"],
                accounts=[
                    NewAccount(
                        owners=[OwnerRef(name="Foreign Model")],
                        model_id=other_cat["model"],
                    )
                ],
            ),
            principal=_principal(),
            ctx=ctx,
        )
    assert exc.value.status_code == 422
    assert "model" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def _legacy_create_application_richer_transfer_jsonb(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    cat = await _seed_catalogue(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    owners=[OwnerRef(name="Rich Transfer")],
                    has_transfer=True,
                    transfer_type="in-specie",
                    transfer_amount=12500.5,
                    transfer_provider_id=cat["provider"],
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    raw = await _scalar(
        sess,
        "SELECT transfer FROM platform.accounts "
        "WHERE tenant_id = :t AND application_id IS NOT NULL ORDER BY id DESC LIMIT 1",
        t=fx["tenant"],
    )
    xfer = json.loads(raw) if isinstance(raw, str) else raw
    assert xfer == {
        "intent": True,
        "transfer_type": "in_specie",
        "amount": 12500.5,
        "provider_id": cat["provider"],
        "provider_name": f"Ceding Co {fx['suffix']}",
    }


@pytest.mark.asyncio
async def test_create_application_jisa_child_is_the_owner_contact(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    cat = await _seed_catalogue(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    product_type="JISA",
                    product_id=cat["product"],
                    owners=[
                        OwnerRef(
                            first_name="Sam",
                            surname="Junior",
                            dob="2018-06-15",
                            role="owner",
                        ),
                        OwnerRef(
                            first_name="Louise",
                            surname="Byford",
                            email="louise.byford@example.test",
                            role="registered_contact",
                        ),
                    ],
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    raw = await _scalar(
        sess,
        "SELECT data FROM platform.accounts "
        "WHERE tenant_id = :t AND application_id IS NOT NULL ORDER BY id DESC LIMIT 1",
        t=fx["tenant"],
    )
    data = json.loads(raw) if isinstance(raw, str) else raw
    assert data["jisa_child"]["name"] == "Sam Junior"
    assert data["jisa_child"]["dob"] == "2018-06-15"
    assert data["jisa_child"]["contact_id"]

    child_contacts = await _scalar(
        sess,
        "SELECT COUNT(*) FROM crm.contacts WHERE tenant_id = :t AND surname = :n",
        t=fx["tenant"],
        n="Junior",
    )
    assert child_contacts == 1

    roles = (
        (
            await sess.execute(
                text(
                    "SELECT ac.role::text FROM crm.account_contacts ac "
                    "JOIN platform.accounts a ON a.id = ac.account_id "
                    "WHERE a.tenant_id = :t AND a.application_id IS NOT NULL "
                    "ORDER BY ac.role::text"
                ),
                {"t": fx["tenant"]},
            )
        )
        .scalars()
        .all()
    )
    assert list(roles) == ["owner", "registered_contact"]
    relationship = (
        (
            await sess.execute(
                text(
                    "SELECT r.relationship_type::text, adult.first_name, child.first_name "
                    "FROM crm.relationships r "
                    "JOIN crm.contacts adult ON adult.id = r.from_contact_id "
                    "JOIN crm.contacts child ON child.id = r.to_contact_id "
                    "WHERE r.tenant_id = :t AND r.relationship_type = 'registered_contact'"
                ),
                {"t": fx["tenant"]},
            )
        )
        .tuples()
        .one()
    )
    assert relationship == ("registered_contact", "Louise", "Sam")


@pytest.mark.asyncio
async def test_create_application_jisa_owner_over_age_is_refused(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    cat = await _seed_catalogue(sess, fx)
    await sess.execute(
        text("UPDATE platform.products SET max_age = 17 WHERE id = :p"),
        {"p": cat["product"]},
    )
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())
    with pytest.raises(HTTPException) as exc:
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider=fx["platform"],
                accounts=[
                    NewAccount(
                        product_type="JISA",
                        product_id=cat["product"],
                        owners=[
                            OwnerRef(
                                first_name="Sam",
                                surname="Adult",
                                dob="1990-01-01",
                                role="owner",
                            ),
                            OwnerRef(
                                first_name="Louise",
                                surname="Byford",
                                email="louise.byford@example.test",
                                role="registered_contact",
                            ),
                        ],
                    )
                ],
            ),
            principal=_principal(),
            ctx=ctx,
        )
    assert exc.value.status_code == 422
    assert "age 17" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_create_application_two_jisas_share_one_child(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    cat = await _seed_catalogue(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())
    child = OwnerRef(first_name="Sam", surname="Junior", dob="2018-06-15", role="owner")
    parent = OwnerRef(
        first_name="Louise",
        surname="Byford",
        email="louise.byford@example.test",
        role="registered_contact",
    )
    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(product_type="JISA", product_id=cat["product"], owners=[child, parent]),
                NewAccount(product_type="JISA", product_id=cat["product"], owners=[child, parent]),
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )
    child_contacts = await _scalar(
        sess,
        "SELECT COUNT(*) FROM crm.contacts WHERE tenant_id = :t AND surname = :n",
        t=fx["tenant"],
        n="Junior",
    )
    parent_contacts = await _scalar(
        sess,
        "SELECT COUNT(*) FROM crm.contacts WHERE tenant_id = :t AND surname = :n",
        t=fx["tenant"],
        n="Byford",
    )
    assert child_contacts == 1
    assert parent_contacts == 1


@pytest.mark.asyncio
async def test_create_application_jisa_without_registered_contact_is_refused(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    cat = await _seed_catalogue(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())
    with pytest.raises(HTTPException) as raised:
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider=fx["platform"],
                accounts=[
                    NewAccount(
                        owners=[OwnerRef(name="Sam Junior")],
                        product_id=cat["product"],
                    )
                ],
            ),
            principal=_principal(),
            ctx=ctx,
        )
    assert raised.value.status_code == 422
    assert "registered contact" in str(raised.value.detail).lower()


@pytest.mark.asyncio
async def _legacy_get_application_enriches_account_kickoff_fields(
    service_session: AsyncSession,
) -> None:
    """Staff journey detail must surface what the adviser captured at create — product, model,
    fee, currency, transfer intent, and the JISA child — not only account_ref / platform_name."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    cat = await _seed_catalogue(sess, fx)
    # The create seed's stub flow is application-scoped only. Widen it so planning fans out
    # profile + account cases — the surface this endpoint enriches for staff.
    await sess.execute(
        text(
            "UPDATE flows.flow_versions SET definition = CAST(:d AS jsonb) "
            "WHERE id = ("
            "  SELECT current_published_version_id FROM flows.flows "
            "  WHERE tenant_id = :t AND code = 'onboarding'"
            ")"
        ),
        {
            "t": fx["tenant"],
            "d": json.dumps(
                {
                    "steps": [
                        {
                            "id": "core",
                            "type": "task",
                            "title": "Applicant details",
                            "scope": "participant",
                            "assignee": "staff",
                            "config": {},
                        },
                        {
                            "id": "setup",
                            "type": "setup_account",
                            "title": "Open account",
                            "scope": "account",
                            "assignee": "staff",
                            "config": {},
                        },
                    ],
                    "milestones": [],
                }
            ),
        },
    )
    staff = _principal(CASES_WRITE, CASES_READ)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=staff)

    created = await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    owners=[OwnerRef(name="Kickoff Client")],
                    product_id=cat["product"],
                    model_id=cat["model"],
                    fee_package_id=cat["fee"],
                    currency="EUR",
                    has_transfer=True,
                    transfer_type="cash",
                    transfer_amount=2500.0,
                    transfer_provider_id=cat["provider"],
                    child_name="Sam Junior",
                    child_dob="2018-06-15",
                    investment_capacity="discretionary",
                    dividend_treatment="reinvest",
                    has_contribution=True,
                    contribution_amount=1000.0,
                    contribution_frequency="monthly",
                    contribution_expected_by="2026-09-01",
                )
            ],
        ),
        principal=staff,
        ctx=ctx,
    )
    exec_uid = str(created["execution_uid"])

    out = await api.get_application(exec_uid=exec_uid, principal=staff, ctx=ctx)
    account_cases = [c for c in out["cases"] if c["kind"] == "account"]
    assert len(account_cases) == 1
    case = account_cases[0]
    assert case["product_name"] == f"Junior ISA {fx['suffix']}"
    assert case["product_type"] == "JISA"
    assert case["account_parties"] == [{"name": "Kickoff Client", "role": "owner"}]
    assert case["model_name"] == f"Balanced {fx['suffix']}"
    assert case["fee_package_name"] == f"Standard {fx['suffix']}"
    assert case["currency"] == "EUR"
    assert case["investment_capacity"] == "discretionary"
    assert case["dividend_treatment"] == "reinvest"
    assert case["has_transfer"] is True
    assert case["transfer"] == {
        "intent": True,
        "transfer_type": "cash",
        "amount": 2500.0,
        "provider_id": cat["provider"],
        "provider_name": f"Ceding Co {fx['suffix']}",
    }
    assert case["jisa_child"] == {"name": "Sam Junior", "dob": "2018-06-15"}
    assert case["regular_contribution"] == {
        "intent": True,
        "amount": 1000.0,
        "frequency": "monthly",
        "expected_by": "2026-09-01",
    }
    assert case["platform_name"]
    assert case["account_ref"]

    # Profile cases stay null on the account-only fields so the shape is uniform.
    profile = next(c for c in out["cases"] if c["kind"] == "profile")
    assert profile["product_name"] is None
    assert profile["has_transfer"] is None
    assert profile["transfer"] is None
    assert profile["jisa_child"] is None
    assert profile["regular_contribution"] is None
    assert profile["investment_capacity"] is None
    assert profile["dividend_treatment"] is None


@pytest.mark.asyncio
async def _legacy_create_application_beneficiaries_in_account_data(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    owners=[OwnerRef(name="Pension Client")],
                    beneficiaries=[
                        BeneficiaryIn(first_name="Alex", surname="Smith", portion=60),
                        BeneficiaryIn(first_name="Blair", surname="Jones", portion=40),
                    ],
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    raw = await _scalar(
        sess,
        "SELECT data FROM platform.accounts "
        "WHERE tenant_id = :t AND application_id IS NOT NULL ORDER BY id DESC LIMIT 1",
        t=fx["tenant"],
    )
    data = json.loads(raw) if isinstance(raw, str) else raw
    assert data == {
        "beneficiaries": [
            {"first_name": "Alex", "surname": "Smith", "portion": 60},
            {"first_name": "Blair", "surname": "Jones", "portion": 40},
        ]
    }


@pytest.mark.asyncio
async def _legacy_create_application_flexi_access_stamps_individual_owner(
    service_session: AsyncSession,
) -> None:
    """Pension kickoff with flexi_access_taken=True sets the flag on individual owners only."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    sipp = int(
        await _scalar(
            sess,
            "SELECT id FROM platform.products WHERE tenant_id = :t AND product_type = 'SIPP'",
            t=fx["tenant"],
        )
    )
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    product_id=sipp,
                    owners=[OwnerRef(name="Pension Member")],
                    flexi_access_taken=True,
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    flagged = await _scalar(
        sess,
        "SELECT flexi_access_taken FROM crm.contacts "
        "WHERE tenant_id = :t AND name = 'Pension Member'",
        t=fx["tenant"],
    )
    assert flagged is True


def _legacy_new_account_beneficiary_portions_must_sum_to_100() -> None:
    """FastAPI maps this ValidationError to HTTP 422 on POST /applications."""
    with pytest.raises(ValidationError) as exc:
        NewAccount(
            owners=[OwnerRef(name="Bad Portions")],
            beneficiaries=[
                BeneficiaryIn(first_name="Alex", surname="Smith", portion=60),
                BeneficiaryIn(first_name="Blair", surname="Jones", portion=50),
            ],
        )
    assert "sum to 100" in str(exc.value).lower()


@pytest.mark.asyncio
async def _legacy_create_application_beneficiaries_merge_with_jisa_child(
    service_session: AsyncSession,
) -> None:
    """Both keys coexist on ``accounts.data`` when somehow both are set."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    cat = await _seed_catalogue(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    owners=[OwnerRef(name="Mixed Client")],
                    product_id=cat["product"],
                    child_name="Sam Junior",
                    child_dob="2018-06-15",
                    beneficiaries=[
                        BeneficiaryIn(first_name="Alex", surname="Smith", portion=100),
                    ],
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    raw = await _scalar(
        sess,
        "SELECT data FROM platform.accounts "
        "WHERE tenant_id = :t AND application_id IS NOT NULL ORDER BY id DESC LIMIT 1",
        t=fx["tenant"],
    )
    data = json.loads(raw) if isinstance(raw, str) else raw
    assert data == {
        "jisa_child": {"name": "Sam Junior", "dob": "2018-06-15"},
        "beneficiaries": [
            {"first_name": "Alex", "surname": "Smith", "portion": 100},
        ],
    }


@pytest.mark.asyncio
async def _legacy_create_application_writes_investment_capacity(
    service_session: AsyncSession,
) -> None:
    """Capacity + dividend land on ``platform.accounts`` columns (snake_case)."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    owners=[OwnerRef(name="Capacity Client")],
                    investment_capacity="Execution Only",
                    dividend_treatment="Pay away",
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    row = (
        await sess.execute(
            text(
                "SELECT investment_capacity, dividend_treatment FROM platform.accounts "
                "WHERE tenant_id = :t AND application_id IS NOT NULL ORDER BY id DESC LIMIT 1"
            ),
            {"t": fx["tenant"]},
        )
    ).one()
    assert row.investment_capacity == "execution_only"
    assert row.dividend_treatment == "pay_away"


def _legacy_new_account_rejects_junk_investment_capacity() -> None:
    """FastAPI maps this ValidationError to HTTP 422 on POST /applications."""
    with pytest.raises(ValidationError) as exc:
        NewAccount(owners=[OwnerRef(name="Bad")], investment_capacity="managed")
    assert "investment_capacity" in str(exc.value).lower()


def _legacy_new_account_rejects_junk_dividend_treatment() -> None:
    with pytest.raises(ValidationError) as exc:
        NewAccount(owners=[OwnerRef(name="Bad")], dividend_treatment="accumulate")
    assert "dividend_treatment" in str(exc.value).lower()


def _legacy_new_account_normalises_capacity_aliases() -> None:
    acc = NewAccount(
        owners=[OwnerRef(name="Ok")],
        investment_capacity="advisory",
        dividend_treatment="reinvest",
    )
    assert acc.investment_capacity == "advisory"
    assert acc.dividend_treatment == "reinvest"
    acc2 = NewAccount(owners=[OwnerRef(name="Ok")], investment_capacity="discretionary")
    assert acc2.investment_capacity == "discretionary"


@pytest.mark.asyncio
async def _legacy_create_application_has_contribution_writes_data_bag(
    service_session: AsyncSession,
) -> None:
    """Non-pension — ``accounts.data.regular_contribution`` only, no contributions ledger row."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    cat = await _seed_catalogue(sess, fx)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    owners=[OwnerRef(name="Funding Client")],
                    product_id=cat["product"],  # JISA — not a pension
                    has_contribution=True,
                    contribution_amount=1000.0,
                    contribution_frequency="monthly",
                    contribution_expected_by="2026-09-01",
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    raw = await _scalar(
        sess,
        "SELECT data FROM platform.accounts "
        "WHERE tenant_id = :t AND application_id IS NOT NULL ORDER BY id DESC LIMIT 1",
        t=fx["tenant"],
    )
    data = json.loads(raw) if isinstance(raw, str) else raw
    assert data == {
        "regular_contribution": {
            "intent": True,
            "amount": 1000.0,
            "frequency": "monthly",
            "expected_by": "2026-09-01",
        }
    }
    ledger = await _scalar(
        sess,
        "SELECT COUNT(*) FROM platform.contributions WHERE tenant_id = :t",
        t=fx["tenant"],
    )
    assert ledger == 0


@pytest.mark.asyncio
async def _legacy_create_application_pension_contribution_books_ledger(
    service_session: AsyncSession,
) -> None:
    """Pension + has_contribution → data bag AND a ``platform.contributions`` row."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    sipp = int(
        await _scalar(
            sess,
            "SELECT id FROM platform.products WHERE tenant_id = :t AND product_type = 'SIPP'",
            t=fx["tenant"],
        )
    )
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    owners=[OwnerRef(name="Pension Client")],
                    product_id=sipp,
                    has_contribution=True,
                    contribution_amount=500.0,
                    contribution_frequency="monthly",
                    contribution_expected_by="2026-10-15",
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    account_id = int(
        await _scalar(
            sess,
            "SELECT id FROM platform.accounts "
            "WHERE tenant_id = :t AND application_id IS NOT NULL ORDER BY id DESC LIMIT 1",
            t=fx["tenant"],
        )
    )
    raw = await _scalar(
        sess,
        "SELECT data FROM platform.accounts WHERE id = :a",
        a=account_id,
    )
    data = json.loads(raw) if isinstance(raw, str) else raw
    assert data == {
        "regular_contribution": {
            "intent": True,
            "amount": 500.0,
            "frequency": "monthly",
            "expected_by": "2026-10-15",
        }
    }
    row = (
        (
            await sess.execute(
                text(
                    "SELECT account_id, category, amount::float AS amount, frequency, status "
                    "FROM platform.contributions WHERE tenant_id = :t AND account_id = :a"
                ),
                {"t": fx["tenant"], "a": account_id},
            )
        )
        .mappings()
        .one()
    )
    assert row["account_id"] == account_id
    assert row["category"] == "personal_contribution"
    assert row["amount"] == 500.0
    assert row["frequency"] == "monthly"
    assert row["status"] == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_create_application_without_contribution_omits_data_key(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    product_type="GIA",
                    owners=[OwnerRef(name="No Funding")],
                    has_contribution=False,
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    raw = await _scalar(
        sess,
        "SELECT data FROM platform.accounts "
        "WHERE tenant_id = :t AND application_id IS NOT NULL ORDER BY id DESC LIMIT 1",
        t=fx["tenant"],
    )
    data = json.loads(raw) if isinstance(raw, str) else raw
    assert data is None or "regular_contribution" not in (data or {})


def _legacy_new_account_rejects_junk_contribution_frequency() -> None:
    """FastAPI maps this ValidationError to HTTP 422 on POST /applications."""
    with pytest.raises(ValidationError) as exc:
        NewAccount(
            owners=[OwnerRef(name="Bad")],
            has_contribution=True,
            contribution_amount=100,
            contribution_frequency="weekly",
        )
    assert "contribution_frequency" in str(exc.value).lower()


def _legacy_new_account_requires_positive_amount_when_has_contribution() -> None:
    with pytest.raises(ValidationError) as exc:
        NewAccount(owners=[OwnerRef(name="Bad")], has_contribution=True)
    assert "contribution_amount" in str(exc.value).lower()

    with pytest.raises(ValidationError) as exc:
        NewAccount(
            owners=[OwnerRef(name="Bad")],
            has_contribution=True,
            contribution_amount=0,
        )
    assert "contribution_amount" in str(exc.value).lower()

    with pytest.raises(ValidationError) as exc:
        NewAccount(
            owners=[OwnerRef(name="Bad")],
            has_contribution=True,
            contribution_amount=-10,
        )
    assert "contribution_amount" in str(exc.value).lower()


def _legacy_new_account_defaults_contribution_frequency_to_single() -> None:
    acc = NewAccount(
        owners=[OwnerRef(name="Ok")],
        has_contribution=True,
        contribution_amount=250,
    )
    assert acc.contribution_frequency == "single"
