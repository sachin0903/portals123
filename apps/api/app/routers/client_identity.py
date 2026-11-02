"""A firm's window onto its client's identity checks.

The client's compliance page (platform ``clients/[uid]/compliance``) reads what Graphene's identity
checks of the client were run on: each check's status in the firm's vocabulary, its date, its
document type and who uploaded the photos, and the photos of the current check or an earlier one.
The shown check's analysis comes too: ComplyCube's scores and per-part outcomes, as the AML
console lays them out (``aml.identity_analysis``). Never Graphene's decision or its comment: the
verdict is Graphene's. The AML screen stays status only (``crm.get_contact_screening``;
docs/aml.md §5).

A firm reads; it never uploads. New photos for a client come from the client, or from Graphene
operations in the AML console (``app.complycube.uploads``).

``compliance.*`` grants a firm status columns only, so these routes read it elevated
(``db.as_service_role``), after RLS has resolved the client in the caller's book
(``book_contact_id``). Every elevated statement names the client.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit_actions as actions
from app.addressing import book_contact_id
from app.auth import Principal, get_current_principal, require_permission
from app.complycube import decisions, uploads
from app.complycube import images as complycube_images
from app.complycube import reports as complycube_reports
from app.complycube.outcomes import allowed_past_match, firm_status
from app.db import as_service_role, book_session
from app.permissions import SCREENING_READ
from app.routers.aml import firm_match_item, identity_analysis, match_columns

router = APIRouter(prefix="/crm", tags=["crm"])

#: Who the firm is told uploaded the photos of an operations upload.
GRAPHENE = complycube_reports.GRAPHENE

_CLIENT = "SELECT cl.id FROM compliance.clients cl WHERE cl.tenant_id = :t AND cl.contact_id = :c"

_CHECKS = """
SELECT ch.id, ch.uid, ch.tenant_id, ch.client_id, ch.kind, ch.origin, ch.status, ch.decided_at,
       ch.provider_check_id, ch.document_id, ch.provider_document_id, ch.provider_live_photo_id,
       ch.uploaded_by, ch.uploaded_via, ch.created_at, ch.updated_at, ch.result,
       d.document_type
  FROM compliance.checks ch
  LEFT JOIN compliance.documents d ON d.id = ch.document_id
 WHERE ch.client_id = :client AND ch.kind = 'identity'
   AND (CAST(:uid AS text) IS NULL OR ch.uid = :uid)
 ORDER BY ch.id DESC
"""


def _uploader(row: dict[str, Any]) -> str | None:
    if row["uploaded_via"] == "operations":
        return GRAPHENE
    return str(row["uploaded_by"]) if row["uploaded_by"] else None


async def _client_tenant(session: AsyncSession, contact_id: int) -> int:
    """The client's own firm, read under RLS: ``book_contact_id`` already proved them in the
    caller's book."""
    return int(
        (
            await session.execute(
                text("SELECT tenant_id FROM crm.contacts WHERE id = :c"), {"c": contact_id}
            )
        ).scalar_one()
    )


async def _checks(
    session: AsyncSession, client_pk: int | None, uid: str | None = None
) -> list[dict[str, Any]]:
    if client_pk is None:
        return []
    rows = await session.execute(text(_CHECKS), {"client": client_pk, "uid": uid})
    return [dict(row) for row in rows.mappings().all()]


@router.get("/contacts/{contact_uid}/identity", name="get-contact-identity")
async def get_contact_identity(
    contact_uid: str,
    contact_id: Annotated[int, Depends(book_contact_id)],
    check: str | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> dict[str, object]:
    """The client's identity checks, newest first, each as the firm reads it (``firm_status``,
    the document type, who uploaded the photos), the images of one of them (``shown``: ``check``,
    else the current one), and whether new photos can be uploaded now (``upload``, as the AML
    console's ``identity_upload``). Returning images records that the caller viewed them. 404 when
    ``check`` is not one of this client's identity checks."""
    require_permission(principal, SCREENING_READ)
    tenant_id = await _client_tenant(session, contact_id)
    async with as_service_role(session):
        client_pk = (
            await session.execute(text(_CLIENT), {"t": tenant_id, "c": contact_id})
        ).scalar_one_or_none()
        rows = await _checks(session, client_pk)
        shown = next((row for row in rows if row["uid"] == check), None) if check else None
        if check and shown is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such identity check.")
        shown = shown or (rows[0] if rows else None)
        listing = (
            await decisions.identity_images(
                session, await decisions.load_held(session, int(shown["id"]))
            )
            if shown is not None
            else decisions.ImageListing(images=[], missing=None)
        )
        images = listing.images
        if images and shown is not None:
            actor = (await session.execute(text("SELECT auth.uid()"))).scalar()
            await actions.record_identity_images_view(
                session,
                principal=principal,
                tenant_id=tenant_id,
                target_tenant_id=None,
                actor_id=str(actor) if actor else None,
                method="GET",
                route="/crm/contacts/{contact_uid}/identity",
                check_uid=str(shown["uid"]),
                image_count=len(images),
                event_type=actions.CLIENT_IMAGES_VIEW_EVENT,
                path_params={"contact_uid": contact_uid, "check_uid": str(shown["uid"])},
            )
    return {
        "checks": [
            {
                "uid": row["uid"],
                "status": firm_status(str(row["status"]), row["decided_at"] is not None),
                "document_type": uploads.PORTAL_TYPES.get(str(row["document_type"] or "")),
                "uploaded_by": _uploader(row),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ],
        "shown": shown["uid"] if shown is not None else None,
        "analysis": identity_analysis(shown["result"]) if shown and shown["result"] else None,
        "images": images,
        "images_missing": listing.missing,
    }


@router.get(
    "/contacts/{contact_uid}/identity/checks/{check_uid}/images/{part}",
    name="get-contact-identity-image",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}, "application/pdf": {}}}},
)
async def get_contact_identity_image(
    check_uid: str,
    part: complycube_images.ImagePart,
    contact_id: Annotated[int, Depends(book_contact_id)],
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> Response:
    """One photo of one of the client's identity checks, read from ComplyCube and never cached.
    The view is recorded when the compliance page lists the images, not per photo. 404 when the
    check is not this client's or ComplyCube holds no such image; 502 or 503 when ComplyCube
    cannot be asked."""
    require_permission(principal, SCREENING_READ)
    tenant_id = await _client_tenant(session, contact_id)
    async with as_service_role(session):
        client_pk = (
            await session.execute(text(_CLIENT), {"t": tenant_id, "c": contact_id})
        ).scalar_one_or_none()
        rows = await _checks(session, client_pk, check_uid)
        if not rows:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such image.")
        held = await decisions.load_held(session, int(rows[0]["id"]))
        try:
            image = await decisions.identity_image(session, held, part)
        except complycube_images.ImageMissing as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such image.") from exc
        except decisions.IdentityDecisionError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc
    return complycube_images.response(image, part)


#: The matches Graphene confirmed on the client's latest AML screen, when it let their journey
#: continue past them. As screened: never Graphene's decision or comment.
_CONFIRMED_MATCHES = f"""
SELECT ch.uid AS check_uid, ch.decided_at AS allowed_at, {match_columns("m")}
  FROM compliance.checks ch
  JOIN compliance.matches m ON m.check_id = ch.id AND m.decision = 'confirmed'
 WHERE ch.id = (SELECT latest.id FROM compliance.checks latest
                 WHERE latest.client_id = :client AND latest.kind = 'aml'
                 ORDER BY latest.id DESC LIMIT 1)
   AND ch.origin = 'portal' AND ch.status = 'refer' AND ch.decided_at IS NOT NULL
 ORDER BY m.score DESC NULLS LAST, m.id
"""  # noqa: S608 - the only interpolation is the fixed column list


@router.get("/contacts/{contact_uid}/aml-matches", name="get-contact-aml-matches")
async def get_contact_aml_matches(
    contact_id: Annotated[int, Depends(book_contact_id)],
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> dict[str, object]:
    """The matches Graphene confirmed on the client's latest AML screen, once it let their journey
    continue past them (``aml.allow_journey``; ``outcomes.allowed_past_match``). Until then the
    screen is Graphene's to review and a firm reads nothing of it (docs/aml.md §5). Each match as
    the console shows it (``aml.firm_match_item``) — photos, names, roles, ties, notes, sources and
    links — never Graphene's decision or comment. ``matches`` is empty when the latest screen was
    not allowed past one."""
    require_permission(principal, SCREENING_READ)
    tenant_id = await _client_tenant(session, contact_id)
    async with as_service_role(session):
        client_pk = (
            await session.execute(text(_CLIENT), {"t": tenant_id, "c": contact_id})
        ).scalar_one_or_none()
        rows = (
            [
                dict(row)
                for row in (
                    await session.execute(text(_CONFIRMED_MATCHES), {"client": client_pk})
                ).mappings()
            ]
            if client_pk is not None
            else []
        )
    return {
        "check_uid": rows[0]["check_uid"] if rows else None,
        "allowed_at": rows[0]["allowed_at"] if rows else None,
        "matches": [firm_match_item(row) for row in rows],
    }


#: The reports a firm may download from a client's compliance page (``complycube.reports``).
ReportKind = Literal["client", "identity", "aml"]

#: Why a report is not on offer. None of them says what a check found.
NO_CHECKS_YET = "No checks have run for this client yet."
NO_IDENTITY_CHECK = "No identity check has run for this client."
IDENTITY_RUNNING = "Available once the check has a result."
NO_AML_SCREEN = "No AML screen has run for this client."
AML_NOT_CLEAR = "Available once Graphene has finished the screen."


async def _latest(session: AsyncSession, client_pk: int | None, kind: str) -> dict[str, Any] | None:
    if client_pk is None:
        return None
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, status, origin, decided_at FROM compliance.checks "
                    "WHERE client_id = :client AND kind = :k ORDER BY id DESC LIMIT 1"
                ),
                {"client": client_pk, "k": kind},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def _report_state(
    session: AsyncSession, tenant_id: int, contact_id: int
) -> tuple[int | None, dict[str, dict[str, Any]]]:
    """The client's compliance record, and which reports a firm may download now. Run elevated.

    The client report once a check has run: it gives each status in the firm's own words, so it
    tells the firm nothing withheld. An identity report once its check has a result. The AML report
    only once the latest screen is clear, or allowed past its confirmed matches: until then the
    screen is Graphene's to review (docs/aml.md §5)."""
    client_pk = (
        await session.execute(text(_CLIENT), {"t": tenant_id, "c": contact_id})
    ).scalar_one_or_none()
    identity = await _latest(session, client_pk, "identity")
    aml = await _latest(session, client_pk, "aml")

    def entry(check: dict[str, Any] | None, available: bool, reason: str | None) -> dict[str, Any]:
        return {
            "check_id": check["id"] if check else None,
            "check_uid": check["uid"] if check else None,
            "available": available,
            "reason": None if available else reason,
        }

    client_ok = (identity or aml) is not None
    identity_ok = identity is not None and identity["status"] != "pending"
    aml_ok = aml is not None and (
        aml["status"] == "clear"
        or allowed_past_match(str(aml["status"]), aml["decided_at"] is not None, str(aml["origin"]))
    )
    return client_pk, {
        "client": {"available": client_ok, "reason": None if client_ok else NO_CHECKS_YET},
        "identity": entry(
            identity, identity_ok, IDENTITY_RUNNING if identity else NO_IDENTITY_CHECK
        ),
        "aml": entry(aml, aml_ok, AML_NOT_CLEAR if aml else NO_AML_SCREEN),
    }


@router.get("/contacts/{contact_uid}/compliance-reports", name="get-contact-compliance-reports")
async def get_contact_compliance_reports(
    contact_id: Annotated[int, Depends(book_contact_id)],
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> dict[str, object]:
    """Which reports the firm may download for the client now, each with why not when it may not
    (``_report_state``), and the client's applications, each with its own application summary PDF
    (``GET /applications/{uid}/application.pdf``)."""
    require_permission(principal, SCREENING_READ)
    tenant_id = await _client_tenant(session, contact_id)
    applications = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    "SELECT e.uid, f.name AS title, e.status, e.created_at "
                    "FROM flows.executions e "
                    "JOIN flows.flow_versions fv ON fv.id = e.flow_version_id "
                    "JOIN flows.flows f ON f.id = fv.flow_id "
                    "WHERE e.status <> 'draft' AND EXISTS ("
                    "  SELECT 1 FROM flows.cases c WHERE c.execution_id = e.id "
                    "   AND c.kind = 'profile' AND c.subject_id = :c) "
                    "ORDER BY e.created_at DESC"
                ),
                {"c": contact_id},
            )
        )
        .mappings()
        .all()
    ]
    async with as_service_role(session):
        _client_pk, state = await _report_state(session, tenant_id, contact_id)
    for entry in state.values():
        entry.pop("check_id", None)
    return {**state, "applications": applications}


@router.get(
    "/contacts/{contact_uid}/compliance-reports/{kind}",
    name="get-contact-compliance-report",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def get_contact_compliance_report(
    contact_uid: str,
    kind: ReportKind,
    contact_id: Annotated[int, Depends(book_contact_id)],
    check: str | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> Response:
    """One of Graphene's reports of the client (``complycube.reports``), made as it is asked for
    and never stored: the client report, the identity report of ``check`` (else the latest), or
    the latest AML screen's. Each status reads in the firm's own words; no match, decision or
    comment is in it. 409 with the reason when it is not on offer (``_report_state``); 404 when
    ``check`` is not one of the client's identity checks; 503 when the PDF cannot be made. The
    download is recorded in the firm's own trail (``client.compliance_report_downloaded``)."""
    require_permission(principal, SCREENING_READ)
    tenant_id = await _client_tenant(session, contact_id)
    async with as_service_role(session):
        client_pk, state = await _report_state(session, tenant_id, contact_id)
        entry = state[kind]
        if kind == "identity" and check and check != entry["check_uid"]:
            rows = await _checks(session, client_pk, check)
            if not rows:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "No such identity check.")
            entry = {
                "check_id": rows[0]["id"],
                "check_uid": check,
                "available": rows[0]["status"] != "pending",
                "reason": IDENTITY_RUNNING,
            }
        if not entry["available"] or client_pk is None:
            raise HTTPException(status.HTTP_409_CONFLICT, str(entry["reason"]))
        actor = (await session.execute(text("SELECT auth.uid()"))).scalar()
        reader = await complycube_reports.reader_name(session, str(actor) if actor else None)
        try:
            if kind == "client":
                data = await complycube_reports.client_report(
                    session, client_pk, audience="firm", generated_by=reader
                )
                filename = f"client-report-{contact_uid}.pdf"
            else:
                analysis = None
                if kind == "identity":
                    rows = await _checks(session, client_pk, str(entry["check_uid"]))
                    analysis = identity_analysis(rows[0]["result"]) if rows[0]["result"] else None
                data = await complycube_reports.check_report(
                    session,
                    int(entry["check_id"]),
                    audience="firm",
                    generated_by=reader,
                    analysis=analysis,
                )
                filename = complycube_reports.check_filename(kind, str(entry["check_uid"]))
        except complycube_reports.ReportUnavailable as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "The report could not be made. Try again."
            ) from exc
        await actions.record_report_download(
            session,
            event_type=actions.CLIENT_REPORT_EVENT,
            principal=principal,
            tenant_id=tenant_id,
            target_tenant_id=None,
            actor_id=str(actor) if actor else None,
            route="/crm/contacts/{contact_uid}/compliance-reports/{kind}",
            path_params={"contact_uid": contact_uid, "kind": kind},
            report=kind,
        )
    return complycube_reports.response(data, filename)
