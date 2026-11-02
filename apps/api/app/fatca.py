"""A client's signed W-9 or W-8: the document behind ``crm.contacts.fatca_status`` (0261).

``fatca_status`` records which self-certification the client holds. ``fatca_document_id`` points
at the signed form, and only while the answer is a W-9 or a W-8 (0261's trigger clears it when the
answer changes). Two doors file the form:

* staff, on the client record (``POST /crm/contacts/{uid}/fatca-document``). That upload also
  answers any request still open for the form, so the client is not asked again. A journey step
  raised after it completes at once with the form held (``step_handlers._HELD_UPLOADS``);
* the client, fulfilling a request item of category ``fatca_form``. The onboarding journey raises
  one when Additional information records a W-9 or a W-8 (``onboarding_seed``'s ``fatca_form``
  step). ``documents.store_request_upload`` links the file here.

When the record's answer changes to anything other than a W-9 or a W-8, a journey step still
asking for the form is withdrawn (``withdraw_unneeded_requests``).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows import orchestrator

#: ``documents.documents.document_type`` and request-item category for the signed form (0261).
FORM_DOCUMENT_TYPE = "fatca_form"

#: The answers a signed form is filed for, and the name a form filed on the record takes.
FORM_NAMES: dict[str, str] = {"w9": "W-9", "w8": "W-8"}

#: Why a withdrawn journey step stopped asking (``case_steps.data.skipped_reason``).
WITHDRAWN_REASON = "fatca_status_changed"


async def link_signed_form(
    session: AsyncSession, *, tenant_id: int, contact_id: int, document_id: object
) -> bool:
    """Point the client's answer at the signed form. Returns whether it was linked.

    Linked only while the stored answer is a W-9 or a W-8. A form that arrives for any other answer
    stays in the client's documents and is not linked. A later form replaces an earlier link."""
    linked = (
        await session.execute(
            text(
                "UPDATE crm.contacts SET fatca_document_id = :d "
                "WHERE id = :c AND tenant_id = :t AND fatca_status = ANY(:codes) RETURNING id"
            ),
            {"d": document_id, "c": contact_id, "t": tenant_id, "codes": list(FORM_NAMES)},
        )
    ).first()
    return linked is not None


async def settle_open_requests(
    session: AsyncSession, *, tenant_id: int, contact_id: int, document_id: object
) -> int:
    """Answer the client's open requests for the form with a form staff filed on the record.

    Each pending ``fatca_form`` upload item is fulfilled with that document and routed through
    ``orchestrator.on_item_fulfilled``, so a journey step completes as it does when the client
    uploads. Returns how many items were answered."""
    items = (
        await session.execute(
            text(
                "UPDATE documents.request_items AS ri "
                "SET status = 'fulfilled', fulfilled_document_id = :d, fulfilled_at = now() "
                "FROM documents.requests AS r "
                "WHERE r.id = ri.request_id AND r.tenant_id = :t AND r.contact_id = :c "
                "  AND ri.action = 'upload' AND ri.category = :cat AND ri.status = 'pending' "
                "RETURNING ri.id, ri.request_id"
            ),
            {"d": document_id, "t": tenant_id, "c": contact_id, "cat": FORM_DOCUMENT_TYPE},
        )
    ).all()
    for item in items:
        await orchestrator.on_item_fulfilled(session, int(item.id))
    requests = sorted({int(item.request_id) for item in items})
    if requests:
        await session.execute(
            text(
                "UPDATE documents.requests SET status = 'fulfilled', completed_at = now() "
                "WHERE id = ANY(:ids) AND status = 'pending' AND NOT EXISTS ("
                "  SELECT 1 FROM documents.request_items ri "
                "  WHERE ri.request_id = documents.requests.id AND ri.status = 'pending')"
            ),
            {"ids": requests},
        )
    return len(items)


async def withdraw_unneeded_requests(
    session: AsyncSession, *, tenant_id: int, contact_id: int, status: object
) -> int:
    """Withdraw the journey steps that still ask for a signed form the answer no longer needs.

    A no-op while ``status`` is a W-9 or a W-8. Otherwise every open ``fatca_form`` upload step for
    this client, on a journey still running, is withdrawn (``orchestrator.withdraw_step``): its
    request item is cancelled, so the client portal stops asking. Returns how many were withdrawn.
    A form the client already sent is kept."""
    if status in FORM_NAMES:
        return 0
    steps = (
        await session.execute(
            text(
                "SELECT cs.case_id, cs.step_key, cs.participant_subject_id "
                "FROM flows.case_steps cs "
                "JOIN flows.cases c ON c.id = cs.case_id "
                "JOIN flows.executions e ON e.id = c.execution_id "
                "JOIN documents.request_items ri ON ri.id = cs.request_item_id "
                "JOIN documents.requests r ON r.id = ri.request_id "
                "WHERE cs.tenant_id = :t AND r.contact_id = :c "
                "  AND cs.step_type = 'upload_document' AND ri.category = :cat "
                "  AND ri.status = 'pending' AND cs.status IN ('pending', 'waiting', 'blocked') "
                "  AND e.status NOT IN ('completed', 'cancelled', 'failed') "
                "ORDER BY cs.id"
            ),
            {"t": tenant_id, "c": contact_id, "cat": FORM_DOCUMENT_TYPE},
        )
    ).all()
    withdrawn = 0
    for step in steps:
        if await orchestrator.withdraw_step(
            session,
            tenant_id=tenant_id,
            case_id=int(step.case_id),
            step_key=str(step.step_key),
            participant_subject_id=step.participant_subject_id,
            reason=WITHDRAWN_REASON,
        ):
            withdrawn += 1
    return withdrawn
