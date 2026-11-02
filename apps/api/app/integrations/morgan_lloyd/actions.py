"""Morgan Lloyd steps for a firm action. The registry wires these; it does not own them."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.attempt import Attempt, Handler, OperationError
from app.actions.payload import _ids


async def update_morgan_lloyd_contact(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """PATCH a Dynamics contact. ``safe``: the body is absolute values."""
    del session, subjects
    external_id = payload.get("external_id")
    fields = payload.get("fields")
    if not isinstance(external_id, str) or not external_id or not isinstance(fields, dict):
        raise OperationError(
            "Updating a Morgan Lloyd client needs external_id and fields.", permanent=True
        )
    from app.integrations.morgan_lloyd.benefits import configured

    if not configured():
        raise OperationError("Morgan Lloyd is not configured.", permanent=True)
    from morgan_lloyd import MorganLloydError
    from morgan_lloyd.client import MorganLloydClient

    from app.integrations.morgan_lloyd.benefits import _settings

    try:
        async with MorganLloydClient(settings=_settings()) as api:
            entity_id = await api.update_contact(external_id, fields)
    except MorganLloydError as exc:
        raise OperationError(str(exc), status_code=getattr(exc, "status_code", None)) from exc
    return Attempt(
        ok=True,
        status_code=200,
        method="PATCH",
        url=f"contacts({external_id})",
        request=fields,
        response={"entity_id": entity_id},
    )


async def sync_morgan_lloyd(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """Onboarding passes ``enrol``; the Sync button passes ``contact_id``."""
    if payload.get("enrol"):
        return await sync_scheme_administrator(session, payload=payload, subjects=subjects)
    return await sync_morgan_lloyd_client(session, payload=payload, subjects=subjects)


async def sync_scheme_administrator(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """The onboarding step that used to call Morgan Lloyd inline."""
    del subjects
    from app import account_enrol

    account_id = payload.get("account_id")
    tenant_id = payload.get("tenant_id")
    if account_id is None or tenant_id is None:
        raise OperationError("client.sync needs an account.", permanent=True)
    report = await account_enrol.sync_activated_account(
        session, tenant_id=int(tenant_id), account_id=int(account_id)
    )
    return Attempt(ok=True, method="POST", url="morgan-lloyd/sync", response=report)


async def sync_morgan_lloyd_client(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    """The Sync button: ``sync_client``, which is idempotent and resumable."""
    del subjects
    from app.integrations.morgan_lloyd.sync import sync_client

    contact_id = payload.get("contact_id")
    tenant_id = payload.get("tenant_id")
    if contact_id is None or tenant_id is None:
        raise OperationError("client.sync needs a client.", permanent=True)
    report = await sync_client(
        session,
        tenant_id=int(tenant_id),
        contact_id=int(contact_id),
        account_id=int(payload["account_id"]) if payload.get("account_id") else None,
    )
    return Attempt(ok=True, method="POST", url="morgan-lloyd/sync-client", response=report)


async def _ml_bank(
    session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
) -> Attempt:
    del session, payload, subjects
    raise OperationError(
        "Morgan Lloyd's contact has no bank account in the mapped contract. "
        "The nominated account stays on the Graphene client until the scheme's "
        "bank block is confirmed.",
        permanent=True,
    )


def _ml_part(part: str) -> Handler:
    async def handler(
        session: AsyncSession, *, payload: dict[str, Any], subjects: list[dict[str, Any]]
    ) -> Attempt:
        from app.integrations.morgan_lloyd.contacts import contact_slice
        from app.integrations.seccl.instruct import _client_row

        tenant_id, account_id, contact_id = await _ids(payload)
        if contact_id is None and account_id is not None:
            contact_id = (
                await session.execute(
                    text(
                        "SELECT contact_id FROM platform.accounts WHERE id = :a AND tenant_id = :t"
                    ),
                    {"a": account_id, "t": tenant_id},
                )
            ).scalar_one_or_none()
        if contact_id is None:
            raise OperationError("Updating a Morgan Lloyd client needs the client.", permanent=True)
        client = await _client_row(session, tenant_id=tenant_id, contact_id=int(contact_id))
        external_id = (
            await session.execute(
                text(
                    "SELECT external_id FROM platform.links "
                    "WHERE tenant_id = :t AND provider = 'morgan-lloyd' "
                    "  AND entity_type = 'contact' AND entity_id = :id AND link_type = 'primary'"
                ),
                {"t": tenant_id, "id": int(contact_id)},
            )
        ).scalar_one_or_none()
        if not external_id:
            raise OperationError(
                "Sync the client to Morgan Lloyd before updating them.", permanent=True
            )
        try:
            fields = contact_slice(client, part=part)
        except (ValueError, KeyError) as exc:
            raise OperationError(str(exc), permanent=True) from exc
        return await update_morgan_lloyd_contact(
            session,
            payload={"external_id": str(external_id), "fields": fields},
            subjects=subjects,
        )

    return handler
