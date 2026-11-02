"""Domain actions — catalogue, policies, planners, the step queue, and approval."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.catalogue import ACTIONS, CATEGORY_IDS, SOURCE_CODES, settings_rows
from app.actions.payload import transfer_attempt
from app.actions.runs import (
    ActionError,
    set_policy,
)


def test_catalogue_keys_are_unique_and_sources_are_known() -> None:
    keys = [action.key for action in ACTIONS]
    assert len(keys) == len(set(keys))
    for action in ACTIONS:
        assert set(action.providers) <= SOURCE_CODES
        assert action.category in CATEGORY_IDS
    listed = settings_rows()
    assert len(listed) == len({action.key for action in listed})
    # No book row is listed on its own any more: each one a firm configures is the provider
    # action that holds the record (bank, model, fee packages). The retired four are gone.
    assert not [action.key for action in listed if action.kind == "book"]
    listed_keys = {action.key for action in listed}
    for key in (
        "contact.bank_details_changed",
        "account.bank_details_changed",
        "account.model_changed",
        "account.fee_packages_changed",
    ):
        assert key not in listed_keys
    all_keys = {action.key for action in ACTIONS}
    for key in (
        "instruction.raised",
        "instruction.cancelled",
        "account.regular_movements_changed",
        "account.beneficiaries_changed",
    ):
        assert key not in all_keys
    for key in ("client.bank", "account.bank", "model.attach", "fee.attach"):
        holder = next(action for action in listed if action.key == key)
        assert holder.holds_record is True
    assert next(a for a in listed if a.key == "model.attach").name == "Change model"
    assert next(a for a in listed if a.key == "fee.attach").name == "Change fee packages"
    assert not any("io" in action.providers for action in ACTIONS)


@pytest.mark.asyncio
@pytest.mark.parametrize("action_key", ["client.bank", "account.bank"])
async def test_bank_approval_needs_someone_else_who_can_approve(
    service_session: AsyncSession, action_key: str
) -> None:
    """Approvers on a bank update hold the record, so the book preflight applies."""
    tenant_id = await _firm(service_session)
    await _role(service_session, tenant_id, "compliance")
    with pytest.raises(ActionError, match="No one else") as exc:
        await set_policy(
            service_session,
            tenant_id=tenant_id,
            action_key=action_key,
            provider=None,
            enabled=False,
            approval_enabled=True,
            approval_role_slug="compliance",
            approval_role_slugs=["compliance"],
            role_slugs=[],
        )
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_saving_the_settings_row_retires_its_paired_book_row(
    service_session: AsyncSession,
) -> None:
    """Migration 0270 left the model / fee book rows in place for the rollout window; the first
    save of "Change model" / "Change fee packages" is the contract step, so a stale book row
    cannot keep an approval or a reason switch alive that the editor shows as off."""
    from sqlalchemy import text

    tenant_id = await _firm(service_session)
    await service_session.execute(
        text(
            "INSERT INTO actions.policies (tenant_id, action_key, provider, enabled, "
            "  approval_enabled, require_justification) "
            "VALUES (:t, 'account.model_changed', NULL, true, true, true)"
        ),
        {"t": tenant_id},
    )
    await set_policy(
        service_session,
        tenant_id=tenant_id,
        action_key="model.attach",
        provider=None,
        enabled=True,
        approval_enabled=False,
        approval_role_slug=None,
        approval_role_slugs=[],
        role_slugs=[],
    )
    left = (
        await service_session.execute(
            text(
                "SELECT action_key FROM actions.policies WHERE tenant_id = :t "
                "  AND action_key IN ('account.model_changed', 'model.attach') ORDER BY 1"
            ),
            {"t": tenant_id},
        )
    ).scalars()
    assert list(left) == ["model.attach"]


def test_a_transfer_hold_is_sent_and_a_refusal_is_not() -> None:
    held = transfer_attempt(
        {
            "status": "manual_intervention",
            "sei_accepted": True,
            "provider_error": "SEI accepted this instruction but did not return an activity id",
        },
        method="POST",
        url="/transferIn",
    )
    assert held.ok is True
    assert held.error is None

    refused = transfer_attempt(
        {"status": "requested", "provider_error": "SEI could not be reached."},
        method="POST",
        url="/transferIn",
    )
    assert refused.ok is False
    assert refused.error == "SEI could not be reached."

    placed = transfer_attempt(
        {"status": "submitted", "seccl_transfer_id": "00009BN1G", "provider_error": None},
        method="POST",
        url="/portfoliotransactiongroup",
    )
    assert placed.ok is True


async def _firm(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES ('ten_' || encode(gen_random_bytes(8), 'hex'), "
                    "        'act-' || encode(gen_random_bytes(4), 'hex'), 'Actions Firm') "
                    "RETURNING id"
                )
            )
        ).scalar_one()
    )


async def _role(session: AsyncSession, tenant_id: int, slug: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
                    "VALUES (:t, :s, :s, 'organisation', 'platform') RETURNING id"
                ),
                {"t": tenant_id, "s": slug},
            )
        ).scalar_one()
    )


async def _user(session: AsyncSession, tenant_id: int) -> str:
    nonce = uuid.uuid4().hex[:8]
    return str(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, workos_user_id, email, first_name) "
                    "VALUES (:uid, :t, :w, :e, 'Ada') RETURNING id"
                ),
                {
                    "uid": f"usr_{nonce}",
                    "t": tenant_id,
                    "w": f"user_{nonce}",
                    "e": f"ada.{nonce}@example.test",
                },
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_enabling_an_action_is_one_policy_whatever_source_was_posted(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _firm(service_session)
    first = await set_policy(
        service_session,
        tenant_id=tenant_id,
        action_key="client.details",
        provider="io",
        enabled=True,
        approval_enabled=False,
        approval_role_slug=None,
        role_slugs=[],
    )
    second = await set_policy(
        service_session,
        tenant_id=tenant_id,
        action_key="client.details",
        provider="sei",
        enabled=False,
        approval_enabled=False,
        approval_role_slug=None,
        role_slugs=[],
    )
    assert first["key"] == second["key"] == "client.details"
    assert "provider" not in first
    assert second["enabled"] is False
    count = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM actions.policies "
                "WHERE tenant_id = :t AND action_key = 'client.details'"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    assert int(count) == 1
    with pytest.raises(ActionError):
        await set_policy(
            service_session,
            tenant_id=tenant_id,
            action_key="not.an.action",
            provider=None,
            enabled=True,
            approval_enabled=False,
            approval_role_slug=None,
            role_slugs=[],
        )


@pytest.mark.asyncio
async def test_a_book_policy_is_unique_including_a_null_provider(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _firm(service_session)
    first = await set_policy(
        service_session,
        tenant_id=tenant_id,
        action_key="contact.bank_details_changed",
        provider=None,
        enabled=True,
        approval_enabled=False,
        approval_role_slug=None,
        role_slugs=[],
    )
    second = await set_policy(
        service_session,
        tenant_id=tenant_id,
        action_key="contact.bank_details_changed",
        provider=None,
        enabled=True,
        approval_enabled=False,
        approval_role_slug=None,
        role_slugs=[],
    )
    assert first["key"] == second["key"] == "contact.bank_details_changed"
    count = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM actions.policies "
                "WHERE tenant_id = :t AND action_key = 'contact.bank_details_changed'"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    assert int(count) == 1
