"""Kickoff's CLIENT-FIRST path: the name parts, the duplicate guard, and the unset currency.

Onboarding is for people not yet on the book, so kickoff creates the client — and three things
about that were wrong. It took ONE name string and filed everything after the first token as the
surname; it created a twin without complaint for someone already a client; and it stamped a
currency on the account before the step that asks for one had run.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.db import FirmContext
from app.flows import planning
from app.permissions import CASES_WRITE
from app.routers import applications as api
from app.routers.applications import NewAccount, NewApplication
from app.routers.applications import OwnerRef as OwnerRefModel
from tests.conftest import SECCL_PLATFORM_SQL


def OwnerRef(**values: Any) -> OwnerRefModel:
    """Keep legacy fixtures focused while satisfying kickoff's adult-owner contract."""
    if values.get("contact_id") is None and values.get("type", "individual") == "individual":
        if values.get("role", "owner") in {"owner", "registered_contact"}:
            values.setdefault("dob", "1980-01-01")
            values.setdefault("email", "fixture-owner@example.test")
    return OwnerRefModel(**values)


def _principal() -> Principal:
    return Principal(
        user_id="wos_staff",
        organisation_id="org_1",
        role="adviser",
        permissions=[CASES_WRITE.slug],
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
            u=f"kc_{s}",
            a=f"kc-{s}",
            n=f"Kickoff {s}",
        )
    )
    platform = str(await _scalar(sess, SECCL_PLATFORM_SQL, n=f"Seccl {s}"))
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


async def _new_contact(sess: AsyncSession, fx: dict[str, Any], **over: Any) -> int:
    fields = {
        "type": "individual",
        "name": "Existing Client",
        "first_name": "Existing",
        "surname": "Client",
        "email": "existing@t.test",
        "dob": None,
        "data": None,
        **over,
    }
    return int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts "
            "  (tenant_id, uid, type, name, first_name, surname, email, dob, data) "
            "VALUES (:t, :u, CAST(:ty AS crm.contact_type), :n, :f, :s, :e, CAST(:dob AS date), "
            "  CAST(:d AS jsonb)) "
            "RETURNING id",
            t=fx["tenant"],
            u=uuid.uuid4().hex[:12],
            ty=fields["type"],
            n=fields["name"],
            f=fields["first_name"],
            s=fields["surname"],
            e=fields["email"],
            dob=fields["dob"],
            d=fields["data"],
        )
    )


@pytest.mark.asyncio
async def test_kickoff_files_the_name_parts_it_was_given(
    service_session: AsyncSession,
) -> None:
    """Three parts in, three parts filed — no splitting, and no guessing."""
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
                            first_name="Mary",
                            middle_name="Jane",
                            surname="Watson",
                            email="mary@t.test",
                        )
                    ],
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
                    "SELECT name, first_name, middle_name, surname FROM crm.contacts "
                    "WHERE tenant_id = :t ORDER BY id DESC LIMIT 1"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .one()
    )
    assert (row["first_name"], row["middle_name"], row["surname"]) == ("Mary", "Jane", "Watson")
    assert row["name"] == "Mary Jane Watson"


@pytest.mark.asyncio
async def test_a_bare_name_splits_on_the_LAST_token_not_the_first(
    service_session: AsyncSession,
) -> None:
    """The defect this replaces: "Mary Jane Watson" was filed under the surname "Jane Watson"."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[NewAccount(product_type="GIA", owners=[OwnerRef(name="Mary Jane Watson")])],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    row = (
        (
            await sess.execute(
                text(
                    "SELECT first_name, middle_name, surname FROM crm.contacts "
                    "WHERE tenant_id = :t ORDER BY id DESC LIMIT 1"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .one()
    )
    assert (row["first_name"], row["middle_name"], row["surname"]) == ("Mary", "Jane", "Watson")


@pytest.mark.asyncio
async def test_a_one_word_name_has_no_surname_to_invent(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[NewAccount(product_type="GIA", owners=[OwnerRef(name="Prince")])],
        ),
        principal=_principal(),
        ctx=ctx,
    )
    row = (
        (
            await sess.execute(
                text(
                    "SELECT name, first_name, surname FROM crm.contacts "
                    "WHERE tenant_id = :t ORDER BY id DESC LIMIT 1"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["first_name"] == "Prince"
    assert row["surname"] is None
    assert row["name"] == "Prince"


@pytest.mark.asyncio
async def test_an_organisation_keeps_its_one_name(service_session: AsyncSession) -> None:
    """An organisation is one name, not three — nothing is split off it."""
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
                    owners=[OwnerRef(name="Vale Family Trust Ltd", type="organisation")],
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
                    "SELECT type::text AS type, name, first_name, surname FROM crm.contacts "
                    "WHERE tenant_id = :t ORDER BY id DESC LIMIT 1"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["type"] == "organisation"
    assert row["name"] == "Vale Family Trust Ltd"
    assert row["first_name"] is None
    assert row["surname"] is None


@pytest.mark.asyncio
async def test_onboarding_an_existing_client_is_refused_not_duplicated(
    service_session: AsyncSession,
) -> None:
    """The email is already on the book: a twin is refused, and the refusal names them."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    await _new_contact(sess, fx, email="Harriet@T.test", first_name="Harriet", surname="Vale")
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    with pytest.raises(HTTPException) as exc:
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider=fx["platform"],
                accounts=[
                    NewAccount(
                        product_type="GIA",
                        owners=[
                            # Same person: the email AND the name, both case-folded.
                            OwnerRef(first_name="harriet", surname="VALE", email="harriet@t.test")
                        ],
                    )
                ],
            ),
            principal=_principal(),
            ctx=ctx,
        )
    assert exc.value.status_code == 409
    assert "Harriet Vale" in str(exc.value.detail)
    assert "already a client" in str(exc.value.detail)
    # And nothing was written: the refusal must not leave an orphan contact behind.
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM crm.contacts WHERE tenant_id = :t AND lower(email) = :e",
            t=fx["tenant"],
            e="harriet@t.test",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_a_half_onboarded_person_is_named_as_such(
    service_session: AsyncSession,
) -> None:
    """Kickoff writes the contact on day one, so the SAME check catches an in-flight journey — and
    "already being onboarded" is a different sentence from "already a client"."""
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
                    owners=[OwnerRef(first_name="Sam", surname="Reed", email="sam@t.test")],
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    with pytest.raises(HTTPException) as exc:
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider=fx["platform"],
                accounts=[
                    NewAccount(
                        product_type="GIA",
                        owners=[OwnerRef(first_name="Sam", surname="Reed", email="sam@t.test")],
                    )
                ],
            ),
            principal=_principal(),
            ctx=ctx,
        )
    assert exc.value.status_code == 409
    assert "already being onboarded" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_a_new_party_with_no_email_is_still_created(
    service_session: AsyncSession,
) -> None:
    """Only an email can be matched on, so a party without one is not blocked by the guard: a
    trustee or attorney legitimately has no address at kickoff."""
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
                        OwnerRef(first_name="Ada", surname="Kemp", email="ada@t.test"),
                        OwnerRef(first_name="Bo", surname="Kemp", role="trustee"),
                    ],
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM crm.contacts WHERE tenant_id = :t AND surname = 'Kemp'",
            t=fx["tenant"],
        )
        == 2
    )


@pytest.mark.asyncio
async def test_kickoff_leaves_the_account_currency_unset(
    service_session: AsyncSession,
) -> None:
    """Nothing asks for a currency at kickoff, so nothing may state one.

    It was inserted as 'GBP' and printed on the journey card beside a real product type, where the
    two looked alike — an assumption rendered as a decision, before the Account details step that
    settles it.
    """
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    out = await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    product_type="GIA",
                    owners=[OwnerRef(first_name="Cy", surname="Ward", email="cy@t.test")],
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )
    assert "execution_uid" in out
    assert (
        await _scalar(
            sess,
            "SELECT currency FROM platform.accounts WHERE tenant_id = :t ORDER BY id DESC LIMIT 1",
            t=fx["tenant"],
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_couple_sharing_an_email_are_two_clients(
    service_session: AsyncSession,
) -> None:
    """The email alone is NOT the duplicate test.

    ``OwnerRef``'s own docstring names this as supported — "a shared-email couple = one login →
    two clients" — so refusing the second of them would make it impossible to onboard, with no
    override anywhere. Two different people never share both the email and the name; the same
    person's second record always does.
    """
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
                        OwnerRef(first_name="Alan", surname="Vale", email="vale@t.test"),
                        OwnerRef(
                            first_name="Bess", surname="Vale", email="vale@t.test", role="owner"
                        ),
                    ],
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM crm.contacts WHERE tenant_id = :t AND lower(email) = :e",
            t=fx["tenant"],
            e="vale@t.test",
        )
        == 2
    )


@pytest.mark.asyncio
async def test_a_third_party_wrapper_can_be_raised(service_session: AsyncSession) -> None:
    """The three TP* wrappers were refused, which made the Account details form's third-party
    provider section — conditioned on exactly those products — unreachable."""
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            accounts=[
                NewAccount(
                    product_type="TPPENSION",
                    owners=[OwnerRef(first_name="Ivy", surname="Doe", email="ivy@t.test")],
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
                    "SELECT p.product_type::text AS type, p.name, p.is_pension "
                    "FROM platform.accounts a JOIN platform.products p ON p.id = a.product_id "
                    "WHERE a.tenant_id = :t ORDER BY a.id DESC LIMIT 1"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["type"] == "TPPENSION"
    assert row["name"] == "Third-Party Pension"
    # A third-party pension is still a pension: the contribution and drawdown paths key on it.
    assert row["is_pension"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner", "message"),
    [
        (
            OwnerRefModel(first_name="No", surname="Birth", email="birth@t.test"),
            "date of birth",
        ),
        (
            OwnerRefModel(first_name="No", surname="Email", dob="1980-01-01"),
            "adult individual owner needs an email",
        ),
    ],
)
async def test_individual_owner_safety_is_checked_before_contact_creation(
    service_session: AsyncSession,
    owner: OwnerRefModel,
    message: str,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    with pytest.raises(HTTPException, match=message):
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider=fx["platform"],
                accounts=[NewAccount(product_type="GIA", owners=[owner])],
            ),
            principal=_principal(),
            ctx=ctx,
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
async def test_child_owner_needs_a_valid_adult_registered_contact(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())
    child = OwnerRefModel(first_name="Jo", surname="Young", dob="2015-01-01")
    invalid_adult = OwnerRefModel(
        first_name="Teen",
        surname="Contact",
        dob="2010-01-01",
        email="teen@t.test",
        role="registered_contact",
    )

    with pytest.raises(HTTPException, match="registered contact must be aged 18"):
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider=fx["platform"],
                accounts=[
                    NewAccount(product_type="GIA", owners=[child, invalid_adult]),
                ],
            ),
            principal=_principal(),
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_product_age_bounds_cover_every_owner(service_session: AsyncSession) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    await sess.execute(
        text(
            "UPDATE platform.products SET max_age = 70 "
            "WHERE tenant_id = :t AND product_type = 'GIA'"
        ),
        {"t": fx["tenant"]},
    )
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    with pytest.raises(HTTPException, match="age 70"):
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider=fx["platform"],
                accounts=[
                    NewAccount(
                        product_type="GIA",
                        owners=[
                            OwnerRefModel(
                                first_name="First",
                                surname="Owner",
                                dob="1980-01-01",
                                email="first@t.test",
                            ),
                            OwnerRefModel(
                                first_name="Second",
                                surname="Owner",
                                dob="1940-01-01",
                                email="second@t.test",
                            ),
                        ],
                    )
                ],
            ),
            principal=_principal(),
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_existing_adult_without_email_is_blocked(service_session: AsyncSession) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    contact_id = await _new_contact(
        sess,
        fx,
        email=None,
        dob="1980-01-01",
        first_name="Missing",
        surname="Email",
    )
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    with pytest.raises(HTTPException, match="adult individual owner needs an email"):
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider=fx["platform"],
                accounts=[
                    NewAccount(
                        product_type="GIA",
                        owners=[OwnerRefModel(contact_id=contact_id)],
                    )
                ],
            ),
            principal=_principal(),
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_child_registered_contact_relationship_is_exact_and_persisted(
    service_session: AsyncSession,
) -> None:
    """The request-local keys preserve who acts for whom; the account role alone cannot."""
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
                        OwnerRefModel(
                            first_name="Mia",
                            surname="Young",
                            dob="2016-04-12",
                            party_key="child-mia",
                        ),
                        OwnerRefModel(
                            first_name="Alex",
                            surname="Young",
                            dob="1984-03-02",
                            email="alex.young@t.test",
                            role="registered_contact",
                            party_key="adult-alex",
                            registered_contact_for=["child-mia"],
                        ),
                    ],
                )
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    relationship = (
        (
            await sess.execute(
                text(
                    "SELECT adult.first_name AS adult, child.first_name AS child "
                    "FROM crm.relationships r "
                    "JOIN crm.contacts adult ON adult.id = r.from_contact_id "
                    "JOIN crm.contacts child ON child.id = r.to_contact_id "
                    "WHERE r.tenant_id = :t AND r.relationship_type::text = 'registered_contact'"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .one()
    )
    assert dict(relationship) == {"adult": "Alex", "child": "Mia"}


@pytest.mark.asyncio
async def test_registered_contact_cannot_target_someone_else(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    with pytest.raises(HTTPException, match="not an owner of this account"):
        await api.create_application(
            NewApplication(
                flow_code="onboarding",
                provider=fx["platform"],
                accounts=[
                    NewAccount(
                        product_type="GIA",
                        owners=[
                            OwnerRefModel(
                                first_name="Mia",
                                surname="Young",
                                dob="2016-04-12",
                                party_key="child-mia",
                            ),
                            OwnerRefModel(
                                first_name="Alex",
                                surname="Young",
                                dob="1984-03-02",
                                email="alex.young@t.test",
                                role="registered_contact",
                                party_key="adult-alex",
                                registered_contact_for=["child-not-here"],
                            ),
                        ],
                    )
                ],
            ),
            principal=_principal(),
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_a_whole_family_is_one_journey_of_five_accounts(
    service_session: AsyncSession,
) -> None:
    """The shape a real household actually arrives in, in one kickoff.

    A child's JISA with one parent as registered contact, the couple's joint GIA, and three
    single-owner wrappers. Three people, five accounts, and every adult named on more than one of
    them — which is the part that has to hold: a party repeated across accounts is ONE contact, so
    a family is not onboarded as six strangers who happen to share a surname.
    """
    sess = service_session
    fx = await _seed_published_flow(sess)
    ctx = FirmContext(session=sess, tenant_id=fx["tenant"], principal=_principal())

    david = {"first_name": "David", "surname": "Pavey", "dob": "2010-02-18"}
    lisa = {
        "first_name": "Lisa",
        "surname": "Pavey",
        "dob": "1981-08-02",
        "email": "lisa.pavey@t.test",
    }
    john_id = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, dob, email) "
            "VALUES (:t, 'johnpavey001', 'individual', 'John', 'Pavey', "
            "DATE '1979-10-22', 'john.pavey@t.test') RETURNING id",
            t=fx["tenant"],
        )
    )
    illustration_uid = "johnsippill1"
    await sess.execute(
        text(
            "INSERT INTO platform.illustrations "
            "  (uid, tenant_id, contact_id, name, product_type, quotation_type, inputs) "
            "VALUES (:u, :t, :c, 'John SIPP illustration', 'SIPP', 'Accumulation', "
            "'{\"engine_errors\": []}'::jsonb)"
        ),
        {"u": illustration_uid, "t": fx["tenant"], "c": john_id},
    )

    await api.create_application(
        NewApplication(
            flow_code="onboarding",
            provider=fx["platform"],
            custody="onshore",
            accounts=[
                NewAccount(
                    product_type="JISA",
                    owners=[
                        OwnerRefModel(**david, party_key="child-david"),
                        OwnerRefModel(
                            **lisa,
                            role="registered_contact",
                            party_key="adult-lisa",
                            registered_contact_for=["child-david"],
                        ),
                    ],
                ),
                NewAccount(
                    product_type="GIA",
                    owners=[
                        OwnerRefModel(contact_id=john_id, party_key="adult-john"),
                        OwnerRefModel(**lisa, party_key="adult-lisa"),
                    ],
                ),
                NewAccount(
                    product_type="SIPP",
                    illustration_uid=illustration_uid,
                    owners=[OwnerRefModel(contact_id=john_id, party_key="adult-john")],
                ),
                NewAccount(
                    product_type="ISA",
                    owners=[OwnerRefModel(contact_id=john_id, party_key="adult-john")],
                ),
                NewAccount(
                    product_type="ISA",
                    owners=[OwnerRefModel(**lisa, party_key="adult-lisa")],
                ),
            ],
        ),
        principal=_principal(),
        ctx=ctx,
    )

    people = (
        (
            await sess.execute(
                text(
                    "SELECT first_name, dob::text AS dob FROM crm.contacts "
                    "WHERE tenant_id = :t ORDER BY first_name"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .all()
    )
    assert [dict(person) for person in people] == [
        {"first_name": "David", "dob": "2010-02-18"},
        {"first_name": "John", "dob": "1979-10-22"},
        {"first_name": "Lisa", "dob": "1981-08-02"},
    ]

    application_id = int(
        await _scalar(
            sess,
            "SELECT id FROM crm.applications WHERE tenant_id = :t",
            t=fx["tenant"],
        )
    )
    rows = (
        (
            await sess.execute(
                text(
                    "SELECT p.product_type::text AS product, c.first_name, ac.role::text AS role "
                    "FROM crm.account_contacts ac "
                    "JOIN crm.contacts c ON c.id = ac.contact_id "
                    "JOIN platform.accounts a ON a.id = ac.account_id "
                    "JOIN platform.products p ON p.id = a.product_id "
                    "WHERE ac.application_id = :a "
                    "ORDER BY product, c.first_name, role"
                ),
                {"a": application_id},
            )
        )
        .mappings()
        .all()
    )
    assert [tuple(row.values()) for row in rows] == [
        ("GIA", "John", "owner"),
        ("GIA", "Lisa", "owner"),
        ("ISA", "John", "owner"),
        ("ISA", "Lisa", "owner"),
        ("JISA", "David", "owner"),
        ("JISA", "Lisa", "registered_contact"),
        ("SIPP", "John", "owner"),
    ]

    # How many accounts each person is ON, whatever role puts them there. Lisa reaches three by a
    # different route from John: two she owns plus the JISA she is registered contact for, where
    # his three are all ownership. A count that only read `role = 'owner'` would say two and read
    # as the child's account having no adult attached to it.
    per_person = (
        (
            await sess.execute(
                text(
                    "SELECT c.first_name, count(DISTINCT ac.account_id) AS accounts "
                    "FROM crm.account_contacts ac "
                    "JOIN crm.contacts c ON c.id = ac.contact_id "
                    "WHERE ac.application_id = :a "
                    "GROUP BY c.first_name ORDER BY c.first_name"
                ),
                {"a": application_id},
            )
        )
        .mappings()
        .all()
    )
    assert [dict(row) for row in per_person] == [
        {"first_name": "David", "accounts": 1},
        {"first_name": "John", "accounts": 3},
        {"first_name": "Lisa", "accounts": 3},
    ]
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM platform.accounts WHERE tenant_id = :t",
            t=fx["tenant"],
        )
        == 5
    )

    # One directed edge, from the parent the adviser designated — not from John, who is on three of
    # the five accounts and on none of them acts for the child.
    relationships = (
        (
            await sess.execute(
                text(
                    "SELECT adult.first_name AS adult, child.first_name AS child "
                    "FROM crm.relationships r "
                    "JOIN crm.contacts adult ON adult.id = r.from_contact_id "
                    "JOIN crm.contacts child ON child.id = r.to_contact_id "
                    "WHERE r.tenant_id = :t AND r.relationship_type::text = 'registered_contact'"
                ),
                {"t": fx["tenant"]},
            )
        )
        .mappings()
        .all()
    )
    assert [dict(row) for row in relationships] == [{"adult": "Lisa", "child": "David"}]

    # The child is the JISA's account holder and is NOT the person we screen and invite; both
    # parents are. Read that off the graph the engine plans from, not off the wrapper label.
    graph = await api._graph(sess, fx["tenant"], application_id)
    by_name: dict[str, planning.Participant] = {}
    for participant in graph.participants:
        name = await _scalar(
            sess, "SELECT first_name FROM crm.contacts WHERE id = :c", c=participant.contact_id
        )
        by_name[str(name)] = participant
    assert not planning.is_kyc_relevant(by_name["David"], graph)
    assert planning.is_kyc_relevant(by_name["John"], graph)
    assert planning.is_kyc_relevant(by_name["Lisa"], graph)
