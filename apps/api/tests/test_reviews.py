"""Four-eye checks — policy, hold, approve, and the redact trail (docs/reviews.md)."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.reviews import catalogue
from app.reviews.apply import (
    HANDLERS,
    _account_model_describe,
    _bank_describe,
)
from app.reviews.service import (
    ReviewError,
    list_policies,
    maybe_hold,
    reject,
    set_policy,
)


def _payload(contact_id: int) -> dict[str, object]:
    return {
        "contact_id": contact_id,
        "bank_account": {
            "scheme": "bacs",
            "name": "Cleo Client",
            "sort_code": "20-00-00",
            "account_number": "12345678",
        },
    }


@pytest.fixture
def reviewbed(super_engine: Engine) -> dict[str, object]:
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        ten = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"rta_{s}", "a": f"revfirm-{s}", "n": f"RevFirm {s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  platform_client_id) VALUES (:t, 'dev', :env, :app)"
            ),
            {"t": ten, "env": f"env_{ten}_{s}", "app": f"app_platform_rwos_{s}"},
        )
        compliance = int(
            c.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                    "VALUES (:t, 'compliance', 'Compliance', 'platform') RETURNING id"
                ),
                {"t": ten},
            ).scalar_one()
        )
        adviser_role = int(
            c.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                    "VALUES (:t, 'adviser', 'Adviser', 'platform') RETURNING id"
                ),
                {"t": ten},
            ).scalar_one()
        )
        proposer_wos = f"rwosa_{s}"
        reviewer_wos = f"rwosc_{s}"
        proposer = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, first_name, "
                    "  last_name, enabled) "
                    "VALUES (:uid, :t, :e, :w, 'Ada', 'Adviser', true) RETURNING id"
                ),
                {
                    "uid": f"rusr_a_{s}",
                    "t": ten,
                    "e": f"ada_{s}@rev.test",
                    "w": proposer_wos,
                },
            ).scalar_one()
        )
        reviewer = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, first_name, "
                    "  last_name, enabled) "
                    "VALUES (:uid, :t, :e, :w, 'Cara', 'Compliance', true) RETURNING id"
                ),
                {
                    "uid": f"rusr_c_{s}",
                    "t": ten,
                    "e": f"cara_{s}@rev.test",
                    "w": reviewer_wos,
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships "
                "  (user_id, tenant_id, portal, role_id, status) "
                "VALUES (:u, :t, 'platform', :r, 'active')"
            ),
            {"u": proposer, "t": ten, "r": adviser_role},
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships "
                "  (user_id, tenant_id, portal, role_id, status) "
                "VALUES (:u, :t, 'platform', :r, 'active')"
            ),
            {"u": reviewer, "t": ten, "r": compliance},
        )
        contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, email, bank_account) "
                    "VALUES (:t, :u, 'individual', 'Cleo Client', :e, "
                    "  CAST(:b AS jsonb)) RETURNING id"
                ),
                {
                    "t": ten,
                    "u": f"rc1_{s}"[:12],
                    "e": f"cleo_{s}@client.test",
                    "b": json.dumps(
                        {
                            "name": "Old Name",
                            "sort_code": "11-11-11",
                            "account_number": "11111111",
                        }
                    ),
                },
            ).scalar_one()
        )
        contact_uid = c.execute(
            text("SELECT uid FROM crm.contacts WHERE id = :c"), {"c": contact}
        ).scalar_one()
        c.commit()
    return {
        "tenant_id": ten,
        "compliance_role": compliance,
        "adviser_role": adviser_role,
        "proposer": proposer,
        "reviewer": reviewer,
        "proposer_wos": proposer_wos,
        "reviewer_wos": reviewer_wos,
        "contact_id": contact,
        "contact_uid": contact_uid,
    }


def test_wired_handlers_cover_the_catalogue() -> None:
    assert set(HANDLERS) == {a.key for a in catalogue.WIRED}


def test_a_snapshot_compares_the_keys_it_captured() -> None:
    """A hold proposed before a capture function grew a key must still apply after it: the
    record did not move, the shape did. `names` is for the reviewer, never a conflict."""
    from app.reviews.apply import conflicts

    old_fee = {"fee_package_ids": [1]}
    new_fee = {"fee_package_ids": [1], "names": {"1": "Platform fee"}}
    assert conflicts(old_fee, new_fee) is False
    assert conflicts(new_fee, {"fee_package_ids": [1], "names": {"1": "Renamed"}}) is False
    assert conflicts(old_fee, {"fee_package_ids": [2], "names": {}}) is True
    old_vuln = {"is_vulnerable": False, "vulnerability_reasons": None}
    assert conflicts(old_vuln, {**old_vuln, "vulnerability_characteristics": None}) is False
    assert conflicts(old_vuln, {**old_vuln, "is_vulnerable": True}) is True
    assert conflicts(None, None) is False
    assert conflicts({"a": 1}, None) is True


def test_describe_prints_money_and_model_names() -> None:
    model = _account_model_describe(
        {"model_name": "Cautious"}, {"model_id": 9, "model_name": "Balanced"}
    )
    assert model["before"] == "Cautious"
    assert model["after"] == "Balanced"
    unnamed = _account_model_describe(None, {"model_id": 9})
    assert unnamed["after"] == "A different model"
    flat = _bank_describe(
        {"value": None},
        {
            "contact_id": 1,
            "name": "Cleo Client",
            "sort_code": "20-00-00",
            "account_number": "12345678",
        },
    )
    assert flat["before"] == "No bank details"
    assert "20-00-00" in flat["after"]
    assert "12345678" in flat["after"]
    assert "••••" not in flat["after"]


@pytest.mark.asyncio
async def test_policy_off_does_not_hold(
    service_session: AsyncSession, reviewbed: dict[str, object]
) -> None:
    session = service_session
    result = await maybe_hold(
        session,
        tenant_id=int(reviewbed["tenant_id"]),
        workos_user_id=str(reviewbed["proposer_wos"]),
        action_key="contact.bank_details_changed",
        subject_type="contact",
        subject_id=str(reviewbed["contact_uid"]),
        contact_id=int(reviewbed["contact_id"]),
        payload=_payload(int(reviewbed["contact_id"])),
        justification="Client called",
        idempotency_key="k1",
    )
    assert result.held is False


@pytest.mark.asyncio
async def test_enable_refused_without_another_reviewer(
    service_session: AsyncSession, reviewbed: dict[str, object]
) -> None:
    session = service_session
    with pytest.raises(ReviewError) as exc:
        await set_policy(
            session,
            tenant_id=int(reviewbed["tenant_id"]),
            action_key="contact.bank_details_changed",
            mode="four_eye",
            reviewer_role_id=int(reviewbed["adviser_role"]),
            actor_user_id=str(reviewbed["proposer"]),
        )
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_reject_does_not_apply(
    service_session: AsyncSession, reviewbed: dict[str, object]
) -> None:
    tenant = int(reviewbed["tenant_id"])
    contact = int(reviewbed["contact_id"])
    payload = _payload(contact)
    session = service_session
    await set_policy(
        session,
        tenant_id=tenant,
        action_key="contact.bank_details_changed",
        mode="four_eye",
        reviewer_role_id=int(reviewbed["compliance_role"]),
        actor_user_id=str(reviewbed["proposer"]),
    )
    held = await maybe_hold(
        session,
        tenant_id=tenant,
        workos_user_id=str(reviewbed["proposer_wos"]),
        action_key="contact.bank_details_changed",
        subject_type="contact",
        subject_id=str(reviewbed["contact_uid"]),
        contact_id=contact,
        payload=payload,
        justification="Client phoned",
        idempotency_key="k-rej",
    )
    await reject(
        session,
        tenant_id=tenant,
        uid=str(held.review_uid),
        workos_user_id=str(reviewbed["reviewer_wos"]),
        note="Sort code does not match the statement",
        role_ids=[int(reviewbed["compliance_role"])],
    )
    live = (
        await session.execute(
            text("SELECT bank_account FROM crm.contacts WHERE id = :c"), {"c": contact}
        )
    ).scalar_one()
    assert live["account_number"] == "11111111"


async def _account(super_engine: Engine, *, tenant: int, contact: int, payments: object) -> str:
    account_uid = f"acc{uuid.uuid4().hex[:9]}"
    with super_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.accounts "
                "(tenant_id, uid, contact_id, status, name, payments) "
                "VALUES (:t, :u, :c, 'active', 'Oakden GIA', CAST(:p AS jsonb))"
            ),
            {"t": tenant, "u": account_uid, "c": contact, "p": json.dumps(payments)},
        )
        conn.commit()
    return account_uid


async def test_client_bank_approvers_hold_the_book_change(
    service_session: AsyncSession, reviewbed: dict[str, object]
) -> None:
    """Approvers on "Update client bank account" hold the client-record save."""
    from app.actions.runs import set_policy as set_action_policy

    tenant = int(reviewbed["tenant_id"])
    contact = int(reviewbed["contact_id"])
    session = service_session
    await set_action_policy(
        session,
        tenant_id=tenant,
        action_key="client.bank",
        provider=None,
        enabled=True,
        approval_enabled=True,
        approval_role_slug="compliance",
        role_slugs=[],
    )
    policies = await list_policies(session, tenant_id=tenant)
    bank = next(row for row in policies if row["action_key"] == "contact.bank_details_changed")
    assert bank["mode"] == "four_eye"
    held = await maybe_hold(
        session,
        tenant_id=tenant,
        workos_user_id=str(reviewbed["proposer_wos"]),
        action_key="contact.bank_details_changed",
        subject_type="contact",
        subject_id=str(reviewbed["contact_uid"]),
        contact_id=contact,
        payload=_payload(contact),
        justification="New nominated account",
        idempotency_key="k-client-bank",
    )
    assert held.held is True
    assert held.body is not None
    assert held.body["status"] == "pending_review"
    assert bank["require_justification"] is False
    account = next(row for row in policies if row["action_key"] == "account.bank_details_changed")
    assert account["mode"] == "off"


@pytest.mark.asyncio
async def test_client_bank_reason_switch_holds_the_book_form(
    service_session: AsyncSession, reviewbed: dict[str, object]
) -> None:
    """The reason switch on Update client bank account is what the client form reads."""
    from app.actions.runs import set_policy as set_action_policy

    tenant = int(reviewbed["tenant_id"])
    contact = int(reviewbed["contact_id"])
    session = service_session
    await set_action_policy(
        session,
        tenant_id=tenant,
        action_key="client.bank",
        provider=None,
        enabled=True,
        approval_enabled=True,
        approval_role_slug="compliance",
        role_slugs=[],
        require_justification=True,
    )
    policies = await list_policies(session, tenant_id=tenant)
    bank = next(row for row in policies if row["action_key"] == "contact.bank_details_changed")
    assert bank["mode"] == "four_eye"
    assert bank["require_justification"] is True
    with pytest.raises(ReviewError) as missing:
        await maybe_hold(
            session,
            tenant_id=tenant,
            workos_user_id=str(reviewbed["proposer_wos"]),
            action_key="contact.bank_details_changed",
            subject_type="contact",
            subject_id=str(reviewbed["contact_uid"]),
            contact_id=contact,
            payload=_payload(contact),
            justification=None,
            idempotency_key="k-client-bank-reason",
        )
    assert missing.value.field == "justification"


@pytest.mark.asyncio
async def test_hold_without_a_reason_when_the_switch_is_off(
    service_session: AsyncSession, reviewbed: dict[str, object]
) -> None:
    tenant = int(reviewbed["tenant_id"])
    contact = int(reviewbed["contact_id"])
    session = service_session
    await set_policy(
        session,
        tenant_id=tenant,
        action_key="contact.bank_details_changed",
        mode="four_eye",
        reviewer_role_id=int(reviewbed["compliance_role"]),
        actor_user_id=str(reviewbed["proposer"]),
    )
    held = await maybe_hold(
        session,
        tenant_id=tenant,
        workos_user_id=str(reviewbed["proposer_wos"]),
        action_key="contact.bank_details_changed",
        subject_type="contact",
        subject_id=str(reviewbed["contact_uid"]),
        contact_id=contact,
        payload=_payload(contact),
        justification=None,
        idempotency_key="k-no-reason",
    )
    assert held.held is True
