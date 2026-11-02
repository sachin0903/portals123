"""What a MANUAL identity check actually asks the client for.

The digital path (``app/idv/capture.py``) photographs an identity document and the client's face
in the portal and has ComplyCube check them. The manual path is a person checking documents, and
it needs a place to record them.

So the choice raises the same two pieces of evidence every UK firm asks for, as ordinary
``documents.request_items`` on the client's own request: **one government-issued photo ID** and
**one proof of address**. The client or an acting adviser can fulfil them, the files land as
`identity`-typed documents on the client record, and the reviewer sees the evidence on the journey.

The two are deliberately SEPARATE items. One combined "send us your documents" cannot say which of
the two is outstanding, and "we have one of the two" is the state a chase is actually about.

The two documents ARE the check. Neither item is linked as the step's own ``request_item_id`` —
one file landing must not complete a check that asks for two — but once BOTH are on the client's
record ``orchestrator._settle_identity_from_evidence`` completes the step and records the pass
with the documents behind it. There is no Pass / Refer: identity is a mandatory check, satisfied
by the digital provider's result or by this evidence, never by a verdict typed at the step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ids import new_id
from app.idv import held as idv_held

#: `documents.documents.document_type` for anything uploaded here — the fulfilment path files an
#: item's `category` as the document's type, so both land on the client's record as identity
#: evidence rather than as "other".
CATEGORY = "identity"


@dataclass(frozen=True)
class ManualIdentityDocument:
    """One thing the client is asked to send."""

    #: Stable key, so a caller can tell the two apart without matching on the title.
    key: str
    #: What the client sees.
    title: str
    #: What counts, in their words. Named examples beat "a suitable document", which is the copy
    #: that produces a photograph of a bank card.
    guidance: str
    format_hint: str


#: Photo ID first: it is the harder one to find, and a proof of address is worthless without it.
DOCUMENTS: tuple[ManualIdentityDocument, ...] = (
    ManualIdentityDocument(
        key="photo_id",
        title="Photo of a government-issued ID",
        guidance=(
            "A passport, a photocard driving licence or a national identity card. Photograph or "
            "scan the whole page — all four corners, no glare over the photo or the machine-"
            "readable lines — and check it has not expired."
        ),
        format_hint="JPG, PNG or PDF",
    ),
    ManualIdentityDocument(
        key="proof_of_address",
        title="Proof of your address",
        guidance=(
            "A utility bill, bank or building-society statement, council-tax bill or mortgage "
            "statement from the last three months, showing your name and your current address. "
            "A mobile-phone bill is not accepted."
        ),
        format_hint="JPG, PNG or PDF",
    ),
)


def as_json() -> list[dict[str, str]]:
    """The spec as the API hands it to a portal — one definition, not three.

    Read by the adviser's kickoff (so the Identity step can say what a manual check collects
    BEFORE the journey starts) and by the client's own dialog (so they can send them). Neither
    keeps its own copy of the titles.
    """
    return [
        {
            "key": d.key,
            "title": d.title,
            "guidance": d.guidance,
            "format_hint": d.format_hint,
        }
        for d in DOCUMENTS
    ]


async def journey_request_id(
    session: AsyncSession, *, tenant_id: int, execution_id: int, contact_id: int
) -> int:
    """The ``documents.requests`` row this client's journey items hang off, creating it if the
    journey has not needed one yet.

    Joined rather than minted, so the two documents land on the checklist the client is already
    working through instead of opening a second one beside it. Found through the items linked to
    this execution's steps, or recorded as their identity photos (``app/idv/capture.py``), because
    the table records only the contact — nothing on the row says
    which journey it belongs to, and ``orchestrator._ensure_request`` builds a fresh cache per
    recompute pass, so a long-running journey can legitimately have more than one: the newest is
    the one its later steps were attached to.

    The INSERT is a deliberate second copy of that orchestrator one rather than a call into it —
    ``orchestrator`` reaches this module through the step handlers, so importing it back would
    close a cycle.
    """
    existing = (
        await session.execute(
            text(
                "SELECT r.id FROM documents.requests r "
                "WHERE r.tenant_id = :t AND r.contact_id = :c "
                "  AND EXISTS (SELECT 1 FROM documents.request_items ri "
                # A step's own item, or one of the identity photos a step records by uid.
                "                JOIN flows.case_steps cs ON cs.request_item_id = ri.id "
                "                  OR (cs.data -> 'identity_capture')::text "
                "                     LIKE '%\"' || ri.uid || '\"%' "
                "                JOIN flows.cases c ON c.id = cs.case_id "
                "               WHERE ri.request_id = r.id AND c.execution_id = :e) "
                "ORDER BY r.id DESC LIMIT 1"
            ),
            {"t": tenant_id, "c": contact_id, "e": execution_id},
        )
    ).scalar_one_or_none()
    if existing is not None:
        return int(existing)
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO documents.requests "
                    "  (uid, tenant_id, contact_id, requested_by, priority, message) "
                    "VALUES (:u, :t, :c, NULL, 'normal', 'Onboarding') RETURNING id"
                ),
                {"u": new_id(), "t": tenant_id, "c": contact_id},
            )
        ).scalar_one()
    )


async def hand_to_adviser(
    session: AsyncSession,
    *,
    tenant_id: int,
    execution_id: int,
    case_id: int,
    step_key: str,
    contact_id: int,
    kyc_check_id: int | None,
) -> list[dict[str, object]]:
    """Move an open digital identity check onto the two documents a person checks.

    The client can ask for this themselves. The adviser can take it from the journey when the
    documents are already in front of them, which is the same write: the choice is recorded, the
    step moves to staff, the pending check points at ``manual``, and the two evidence items are
    raised. Photos held for the digital try are released.
    """
    await session.execute(
        text(
            "UPDATE flows.case_steps "
            "SET data = COALESCE(data, '{}'::jsonb) || CAST(:d AS jsonb), assignee = 'staff' "
            "WHERE case_id = :c AND step_key = :k "
            "  AND (CAST(:psid AS int) IS NULL OR participant_subject_id = CAST(:psid AS int))"
        ),
        {
            "d": json.dumps({"identity_choice": "manual"}),
            "c": case_id,
            "k": step_key,
            "psid": contact_id,
        },
    )
    await idv_held.release_finished(session, tenant_id=tenant_id)
    if kyc_check_id is not None:
        await session.execute(
            text(
                "UPDATE crm.kyc_checks SET provider = 'manual' "
                "WHERE id = :id AND tenant_id = :t AND status = 'pending'"
            ),
            {"id": kyc_check_id, "t": tenant_id},
        )
    return await raise_request(
        session,
        tenant_id=tenant_id,
        execution_id=execution_id,
        contact_id=contact_id,
        case_id=case_id,
        step_key=step_key,
    )


async def raise_request(
    session: AsyncSession,
    *,
    tenant_id: int,
    execution_id: int,
    contact_id: int,
    case_id: int,
    step_key: str,
) -> list[dict[str, object]]:
    """Ask this client for the two documents, and record them on their identity step.

    Idempotent: a step that has already asked returns what it asked for rather than a second pair.
    Both doors reach this — the adviser requiring a manual check at kickoff, and the client
    choosing one mid-journey — and neither may end up asking twice.

    Deliberately does NOT set the step's ``request_item_id``: that link is what the generic
    document-fulfilment hook routes back through, and it can only name ONE item — the check needs
    both. ``orchestrator._settle_identity_from_evidence`` reads the recorded pair instead and
    completes the step only once neither is pending.
    """
    recorded = (
        await session.execute(
            text(
                "SELECT data -> 'identity_documents' FROM flows.case_steps "
                "WHERE case_id = :c AND step_key = :k "
                "  AND (CAST(:psid AS int) IS NULL OR participant_subject_id = CAST(:psid AS int))"
            ),
            {"c": case_id, "k": step_key, "psid": contact_id},
        )
    ).scalar_one_or_none()
    already = await documents_for(session, tenant_id=tenant_id, recorded=recorded)
    if already:
        return already

    request_id = await journey_request_id(
        session, tenant_id=tenant_id, execution_id=execution_id, contact_id=contact_id
    )
    request_uid = (
        await session.execute(
            text("SELECT uid FROM documents.requests WHERE id = :r"), {"r": request_id}
        )
    ).scalar_one()
    out: list[dict[str, object]] = []
    for spec in DOCUMENTS:
        item_uid = new_id()
        await session.execute(
            text(
                "INSERT INTO documents.request_items "
                "  (uid, request_id, tenant_id, action, title, category, format_hint) "
                "VALUES (:u, :r, :t, 'upload', :title, :cat, :fmt)"
            ),
            {
                "u": item_uid,
                "r": request_id,
                "t": tenant_id,
                "title": spec.title,
                "cat": CATEGORY,
                "fmt": spec.format_hint,
            },
        )
        out.append(
            {
                "key": spec.key,
                "title": spec.title,
                "guidance": spec.guidance,
                "format_hint": spec.format_hint,
                "request_uid": request_uid,
                "item_uid": item_uid,
                "status": "pending",
            }
        )
    # The client's checklist has to be OPEN for them to fulfil anything on it. The identity step
    # sits after the fact-find and terms in the spine, so by the time it asks the parent request
    # has usually rolled up to `fulfilled` (documents.py does that once nothing is pending) — and
    # every surface that offers an upload gates on `requests.status = 'pending'`, so two brand-new
    # items on a completed request are visible and unfulfillable. Same reopen the correction path
    # performs for the same reason.
    await session.execute(
        text(
            "UPDATE documents.requests SET status = 'pending', completed_at = NULL "
            "WHERE id = :r AND status <> 'pending'"
        ),
        {"r": request_id},
    )
    # Recorded on the STEP keyed BY SPEC, so a reload — and the adviser reviewing the check — finds
    # the same two items. Keyed rather than a bare list because pairing a title to a uid by
    # position mislabels them the moment the list is not exactly the two, in order.
    await session.execute(
        text(
            "UPDATE flows.case_steps "
            "SET data = COALESCE(data, '{}'::jsonb) || CAST(:d AS jsonb) "
            "WHERE case_id = :c AND step_key = :k "
            "  AND (CAST(:psid AS int) IS NULL OR participant_subject_id = CAST(:psid AS int))"
        ),
        {
            "d": json.dumps(
                {"identity_documents": {str(i["key"]): str(i["item_uid"]) for i in out}}
            ),
            "c": case_id,
            "k": step_key,
            "psid": contact_id,
        },
    )
    return out


async def documents_for(
    session: AsyncSession, *, tenant_id: int, recorded: Any
) -> list[dict[str, object]]:
    """The recorded evidence items, joined to their live fulfilment state.

    The SPEC (title, guidance, what counts) comes from ``DOCUMENTS`` and the STATUS from the
    request item — so re-wording the guidance reaches a client who was asked last week, and a file
    that has landed says so.

    ``recorded`` is what the step holds: a ``{spec key: item uid}`` mapping, or a bare list from
    before it was keyed, which can only be read positionally.
    """
    if isinstance(recorded, str):
        recorded = json.loads(recorded)
    by_key: dict[str, str]
    if isinstance(recorded, dict):
        by_key = {str(k): str(v) for k, v in recorded.items()}
    elif isinstance(recorded, list):
        by_key = {
            spec.key: str(uid)
            for spec, uid in zip(DOCUMENTS, recorded, strict=False)
            if uid is not None
        }
    else:
        return []
    uids = list(by_key.values())
    if not uids:
        return []
    rows = (
        (
            await session.execute(
                text(
                    "SELECT ri.uid AS item_uid, ri.title, ri.status, r.uid AS request_uid, "
                    "       d.uid AS document_uid "
                    "FROM documents.request_items ri "
                    "JOIN documents.requests r ON r.id = ri.request_id "
                    "LEFT JOIN documents.documents d ON d.id = ri.fulfilled_document_id "
                    "WHERE ri.tenant_id = :t AND ri.uid = ANY(:uids)"
                ),
                {"t": tenant_id, "uids": [str(u) for u in uids]},
            )
        )
        .mappings()
        .all()
    )
    by_uid = {str(r["item_uid"]): r for r in rows}
    out: list[dict[str, object]] = []
    # Ordered by the SPEC, not by the query: photo ID first, because a proof of address is
    # worthless without it and the order is the order they are asked for.
    for spec in DOCUMENTS:
        uid = by_key.get(spec.key)
        item = by_uid.get(uid) if uid is not None else None
        if item is None:
            continue
        out.append(
            {
                "key": spec.key,
                "title": spec.title,
                "guidance": spec.guidance,
                "format_hint": spec.format_hint,
                "request_uid": item["request_uid"],
                "item_uid": uid,
                "status": item["status"],
                "document_uid": item["document_uid"],
            }
        )
    return out
