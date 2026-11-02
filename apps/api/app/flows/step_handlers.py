"""Concrete step-type handlers — the module adapters behind the ``StepHandler`` contract.

Each is independent and cross-functional (usable outside onboarding). The engine never branches on
step type; it calls the handler. Registered on import (``app.flows`` imports this module).

Design boundary (docs/flows.md §0): a handler owns only *its* module's behaviour and **returns
events** — it never derives case/execution state or calls recompute. The DB-touching paths
(``instantiate``/``handle`` creating artifacts) are exercised by the CI DB suites; the decision
logic (what signal to emit) is kept pure and unit-tested here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import (
    bank_check,
    catalogue,
    contributions,
    instructions,
    national_insurance,
    regular_movements,
    storage,
    tenant_storage,
)
from app import terms as terms_svc
from app.contacts import contact_phone, validate_contact_phone
from app.flows import graph as graph_mod
from app.flows import handlers as handlers_mod
from app.flows import kyc as kyc_mod
from app.flows import mapping as mapping_mod
from app.flows import providers
from app.flows.document_context import letter_inputs
from app.flows.domain_events import StepEvent, StepSignal
from app.flows.errors import StepTransitionRefused
from app.flows.handlers import BaseHandler, StepContext, register
from app.flows.terms_content import declaration_html
from app.flows.triggers import AutomationContext
from app.ids import new_id
from app.idv import manual as idv_manual
from app.logging import report_error

logger = logging.getLogger("flows.actions")


async def _create_request_item(
    ctx: StepContext,
    *,
    action: str,
    title: str,
    form_uid: str | None = None,
    document_uid: str | None = None,
    category: str | None = None,
    format_hint: str | None = None,
) -> int:
    """Create the documents.request_item the client fulfils, and link it onto the case step (so the
    fulfilment hook can route back to this step). Returns the item id."""
    item_id = int(
        (
            await ctx.session.execute(
                text(
                    "INSERT INTO documents.request_items "
                    "  (uid, request_id, tenant_id, action, title, category, format_hint, "
                    "   form_uid, target_document_id) "
                    "VALUES (:u, :r, :t, :a, :title, :cat, :fmt, :form, "
                    "  (SELECT id FROM documents.documents WHERE uid = :doc AND tenant_id = :t)) "
                    "RETURNING id"
                ),
                {
                    "u": new_id(),
                    "r": ctx.request_id,
                    "t": ctx.tenant_id,
                    "a": action,
                    "title": title,
                    "cat": category,
                    "fmt": format_hint,
                    "form": form_uid,
                    "doc": document_uid,
                },
            )
        ).scalar_one()
    )
    await ctx.session.execute(
        text(
            "UPDATE flows.case_steps SET request_item_id = :i WHERE case_id = :c AND step_key = :k "
            "  AND (CAST(:psid AS int) IS NULL OR participant_subject_id = CAST(:psid AS int))"
        ),
        {"i": item_id, "c": ctx.case_id, "k": ctx.step_key, "psid": ctx.participant_subject_id},
    )
    return item_id


def _as_str(v: object) -> str | None:
    return v if isinstance(v, str) and v else None


async def _store_inline_terms(
    session: AsyncSession,
    tenant_id: int,
    *,
    name: str,
    content: str,
    term_uid: str | None = None,
    term_version_uid: str | None = None,
) -> str:
    """Snapshot the authored declaration as a ``terms`` document this request item can point at.

    Created at instantiate so a later edit of the flow cannot change what this client agreed to.
    """
    uid = new_id()
    html = declaration_html(content)
    meta: dict[str, Any] = {"content": html, "audience": "client"}
    if term_uid:
        meta["term_uid"] = term_uid
    if term_version_uid:
        meta["term_version_uid"] = term_version_uid
    await session.execute(
        text(
            "INSERT INTO documents.documents "
            "  (uid, tenant_id, document_type, source, name, status, metadata) "
            "VALUES (:u, :t, 'terms', 'generated', :n, 'generated', CAST(:m AS jsonb))"
        ),
        {
            "u": uid,
            "t": tenant_id,
            "n": name,
            "m": json.dumps(meta),
        },
    )
    return uid


async def _store_terms_pdf(
    session: AsyncSession,
    tenant_id: int,
    *,
    name: str,
    term: dict[str, Any],
) -> str | None:
    """Point a ``terms`` document at the published PDF, so the request item has something to show.

    ``_store_inline_terms`` is the HTML half of this. Without the PDF half a catalogue term
    authored as a file produced an acknowledge item with NO target: the client's ``/terms`` lookup
    found no document and ``/target`` found no file, and the dialog said the document could not be
    loaded — for a term the firm had published and could read in its own editor.

    The bytes are not copied. ``documents.files`` records the version's own object in the tenant
    bucket, which is the exact file the version pinned; the same-origin proxy the client already
    uses (``/target/content``) then streams it. Shared across every client asked to accept this
    version — ``files_bucket_key_key`` makes the file row one per object. ``None`` when the
    firm's store cannot be resolved, so the caller can hold the step rather than mint an
    acknowledgement of a document nobody can open.
    """
    file_key = _as_str(term.get("published_file_key"))
    if not file_key:
        return None
    store = await tenant_storage.get_tenant_store(session, tenant_id)
    if store is None:
        return None
    content_type = _as_str(term.get("published_file_content_type")) or "application/pdf"
    file_id = (
        await session.execute(
            text("SELECT id FROM documents.files WHERE bucket = :b AND storage_key = :k"),
            {"b": store.bucket, "k": file_key},
        )
    ).scalar_one_or_none()
    if file_id is None:
        data, _ = await storage.get_tenant_object(store, file_key)
        file_id = (
            await session.execute(
                text(
                    "INSERT INTO documents.files "
                    "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                    "VALUES (:t, :b, :k, :ct, :size, :sha) "
                    "ON CONFLICT (bucket, storage_key) DO UPDATE SET bucket = EXCLUDED.bucket "
                    "RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "b": store.bucket,
                    "k": file_key,
                    "ct": content_type,
                    "size": len(data),
                    "sha": hashlib.sha256(data).hexdigest(),
                },
            )
        ).scalar_one()
    uid = new_id()
    meta: dict[str, Any] = {
        "audience": "client",
        "term_uid": term.get("uid"),
        "term_version_uid": term.get("current_version_uid"),
    }
    await session.execute(
        text(
            "INSERT INTO documents.documents "
            "  (uid, tenant_id, document_type, source, name, status, "
            "   original_file_id, pdf_file_id, metadata) "
            "VALUES (:u, :t, 'terms', 'generated', :n, 'generated', :f, :f, CAST(:m AS jsonb))"
        ),
        {"u": uid, "t": tenant_id, "n": name, "f": file_id, "m": json.dumps(meta)},
    )
    return uid


def _event(ctx: StepContext, signal: StepSignal, *, reason: str | None = None) -> StepEvent:
    return StepEvent(
        signal=signal,
        case_id=ctx.case_id,
        step_key=ctx.step_key,
        participant_subject_id=ctx.participant_subject_id,
        reason=reason,
    )


async def _record_terms_acceptance(ctx: StepContext) -> None:
    """Stamp ``crm.kyc_checks.kind = 'terms'`` when this contact has no remaining required terms.

    ``0139`` opened the kind and nothing wrote it, so Compliance stayed "Not recorded" after every
    declaration. Written here (not only on the last of the five) so a GIA that never plans ISA
    still records acceptance when General + DFM + Platform are done. ``valid_until`` uses the
    terms default so the Compliance tick stays green; ``raw`` holds the document refs actually
    agreed, including acknowledgements that had no PDF blob.
    """
    contact_id = ctx.participant_subject_id
    if contact_id is None:
        return
    outstanding = (
        await ctx.session.execute(
            text(
                "SELECT count(*) FROM flows.case_steps "
                "WHERE case_id = :c AND step_type = 'accept_terms' "
                "  AND required IS NOT FALSE "
                "  AND status NOT IN ('fulfilled', 'skipped', 'cancelled') "
                "  AND NOT (step_key = :k AND ("
                "        CAST(:psid AS int) IS NULL "
                "        OR participant_subject_id IS NOT DISTINCT FROM CAST(:psid AS int)))"
            ),
            {"c": ctx.case_id, "k": ctx.step_key, "psid": contact_id},
        )
    ).scalar_one()
    if int(outstanding):
        return
    now = datetime.now(UTC)
    docs = (
        (
            await ctx.session.execute(
                text(
                    "SELECT cs.step_key, ri.title, "
                    "       td.uid AS document_uid, "
                    "       td.name AS document_name, "
                    "       NULLIF(td.metadata ->> 'content', '') IS NOT NULL AS has_html "
                    "FROM flows.case_steps cs "
                    "LEFT JOIN documents.request_items ri ON ri.id = cs.request_item_id "
                    "LEFT JOIN documents.documents td ON td.id = ri.target_document_id "
                    "  AND td.tenant_id = :t "
                    "WHERE cs.case_id = :c AND cs.step_type = 'accept_terms' "
                    "  AND cs.status = 'fulfilled' "
                    "  AND (cs.participant_subject_id IS NOT DISTINCT FROM CAST(:psid AS int) "
                    "       OR (cs.participant_subject_id IS NULL "
                    "           AND CAST(:psid AS int) IS NOT NULL))"
                ),
                {"c": ctx.case_id, "t": ctx.tenant_id, "psid": contact_id},
            )
        )
        .mappings()
        .all()
    )
    # This step is not fulfilled yet — include it in the refs.
    agreed = [
        {
            "step_key": r["step_key"],
            "title": r["title"],
            "document_uid": r["document_uid"],
            "document_name": r["document_name"],
            "kind": "html" if r["has_html"] else "acknowledgement",
        }
        for r in docs
    ]
    agreed.append(
        {
            "step_key": ctx.step_key,
            "title": ctx.config.get("title"),
            "document_uid": ctx.config.get("document_uid"),
            "kind": "acknowledgement",
        }
    )
    payload = json.dumps({"accepted": agreed, "source": "accept_terms"})
    existing = (
        await ctx.session.execute(
            text(
                "SELECT id FROM crm.kyc_checks "
                "WHERE tenant_id = :t AND contact_id = :c AND kind = :k "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"t": ctx.tenant_id, "c": contact_id, "k": kyc_mod.KIND_TERMS},
        )
    ).scalar_one_or_none()
    valid_until = kyc_mod.compute_valid_until(now, kyc_mod.KIND_TERMS)
    if existing is not None:
        await ctx.session.execute(
            text(
                "UPDATE crm.kyc_checks SET status = 'passed', provider = 'manual', "
                "  verified_at = :v, valid_until = :vu, raw = raw || CAST(:raw AS jsonb) "
                "WHERE id = :id"
            ),
            {"v": now, "vu": valid_until, "raw": payload, "id": int(existing)},
        )
        return
    await ctx.session.execute(
        text(
            "INSERT INTO crm.kyc_checks "
            "  (uid, tenant_id, contact_id, kind, status, provider, verified_at, valid_until, raw) "
            "VALUES (:u, :t, :c, :k, 'passed', 'manual', :v, :vu, CAST(:raw AS jsonb))"
        ),
        {
            "u": new_id(),
            "t": ctx.tenant_id,
            "c": contact_id,
            "k": kyc_mod.KIND_TERMS,
            "v": now,
            "vu": valid_until,
            "raw": payload,
        },
    )


# --------------------------------------------------------------------------------------------------
# task — a generic staff/client to-do (adviser declaration, offshore approval, "open in provider").
# --------------------------------------------------------------------------------------------------
class TaskHandler(BaseHandler):
    type = "task"
    assignee_default = "staff"
    # Must match definitions.STEP_TYPES["task"].config_keys exactly — a key declared here but not
    # there is rejected at save, so the two allowlists are one vocabulary, not two.
    _config_keys = frozenset({"description", "invite_clients"})

    async def instantiate(self, ctx: StepContext) -> StepEvent | None:
        return None  # nothing to create; a person marks it done → apply_step_event(COMPLETED)

    async def handle(self, ctx: StepContext, detail: dict[str, Any]) -> StepEvent:
        """Complete the to-do. When ``invite_clients`` is set, also invite each KYC-relevant owner
        on the execution's application to the client portal (legacy declare → email parity).

        Invites are best-effort: a WorkOS/email failure is logged and counted, never raised — the
        adviser's declaration must stick even when the identity provider is down. A contact with
        no email is skipped (same refusal the firm portal-invite endpoint makes, without failing
        the whole handoff for the cohort that *can* be mailed).
        """
        if not ctx.config.get("invite_clients"):
            return _event(ctx, StepSignal.COMPLETED)
        if detail.get("adviser_confirmed") is not True:
            raise StepTransitionRefused(
                "Confirm that the application details are correct, complete and not misleading "
                "before sending them to the client."
            )

        from app.flows.planning import KYC_ROLES
        from app.provisioning import provision_client_login

        await ctx.session.execute(
            text(
                "UPDATE crm.applications a "
                "SET adviser_declaration = true, adviser_declaration_on = now(), "
                "    adviser_declaration_by = auth.uid() "
                "FROM flows.executions e "
                "WHERE e.id = :e AND a.id = e.crm_application_id AND a.tenant_id = :t"
            ),
            {"e": ctx.execution_id, "t": ctx.tenant_id},
        )

        # Distinct contacts: a joint owner can appear once per account/role row.
        rows = (
            (
                await ctx.session.execute(
                    text(
                        "SELECT DISTINCT ON (c.id) c.id, c.email, c.first_name, c.surname, "
                        "       c.type::text AS type "
                        "FROM crm.account_contacts ac "
                        "JOIN flows.executions e ON e.crm_application_id = ac.application_id "
                        "JOIN crm.contacts c ON c.id = ac.contact_id "
                        "  AND c.tenant_id = ac.tenant_id "
                        "WHERE e.id = :e AND ac.tenant_id = :t "
                        "  AND c.deleted_at IS NULL "
                        "  AND c.type = 'individual' "
                        "  AND ac.role = ANY(:roles) "
                        "  AND NOT ("
                        "    ac.role = 'owner' "
                        "    AND EXISTS ("
                        "      SELECT 1 FROM platform.accounts a "
                        "      JOIN platform.products p ON p.id = a.product_id "
                        "      WHERE a.id = ac.account_id AND a.tenant_id = ac.tenant_id "
                        "        AND p.product_type::text = 'JISA')"
                        "    AND NOT EXISTS ("
                        "      SELECT 1 FROM crm.account_contacts ac2 "
                        "      WHERE ac2.contact_id = c.id AND ac2.tenant_id = ac.tenant_id "
                        "        AND ac2.role IN "
                        "          ('decision_maker', 'trustee', 'registered_contact'))"
                        "  ) "
                        "ORDER BY c.id"
                    ),
                    {"e": ctx.execution_id, "t": ctx.tenant_id, "roles": list(KYC_ROLES)},
                )
            )
            .mappings()
            .all()
        )

        invited = 0
        skipped = 0
        failed = 0
        for row in rows:
            email = row["email"]
            if not isinstance(email, str) or not email.strip():
                skipped += 1
                continue
            try:
                await provision_client_login(
                    ctx.session,
                    tenant_id=ctx.tenant_id,
                    role_slug="client",
                    email=email.strip(),
                    first_name=row["first_name"],
                    last_name=row["surname"],
                    contact_id=int(row["id"]),
                    contact_type=str(row["type"] or "individual"),
                )
                invited += 1
            except Exception as exc:  # noqa: BLE001 — handoff must not roll back on invite faults
                failed += 1
                logger.warning(
                    "declaration invite failed for contact %s on execution %s: %s",
                    row["id"],
                    ctx.execution_id,
                    exc,
                    exc_info=True,
                )

        return StepEvent(
            signal=StepSignal.COMPLETED,
            case_id=ctx.case_id,
            step_key=ctx.step_key,
            participant_subject_id=ctx.participant_subject_id,
            reason=f"invited={invited} skipped={skipped} failed={failed}",
            detail={"invited": invited, "skipped": skipped, "failed": failed},
        )


# --------------------------------------------------------------------------------------------------
# review — a staff compliance decision (e.g. the offshore enhanced-due-diligence review). On a
# `passed` outcome it can raise an application-level gate flag (config `gate`), driving the legacy
# offshore_under_review → processing progression; `failed`/`referred` holds the case.
# --------------------------------------------------------------------------------------------------
_REVIEW_GATE_COLUMNS = {
    "offshore_approved"
}  # whitelisted crm.applications gate flags a review sets


class ReviewDecisionHandler(BaseHandler):
    type = "review"
    assignee_default = "staff"
    _config_keys = frozenset({"title", "category", "gate"})

    async def handle(self, ctx: StepContext, detail: dict[str, Any]) -> StepEvent:
        outcome = str(detail.get("outcome", "passed"))
        if outcome != "passed":
            return _event(ctx, StepSignal.FAILED, reason=f"review_{outcome}")
        gate = ctx.config.get("gate")
        if isinstance(gate, str) and gate in _REVIEW_GATE_COLUMNS:
            # `gate` is validated against a fixed whitelist above — the column name is never
            # user-controlled text reaching the SQL.
            await ctx.session.execute(
                text(
                    f"UPDATE crm.applications a SET {gate} = true, {gate}_on = now() "  # noqa: S608
                    "FROM flows.executions e WHERE e.id = :e AND a.id = e.crm_application_id"
                ),
                {"e": ctx.execution_id},
            )
        return _event(ctx, StepSignal.COMPLETED, reason="review_passed")


# --------------------------------------------------------------------------------------------------
# verify_identity / aml_check — the KYC check seam. Neither takes a staff verdict: `manual` identity
# is settled when both of its documents land (orchestrator._settle_identity_from_evidence), a
# ComplyCube identity check by its result, and a ComplyCube AML screen by its result or the AML
# console's decision — both through app.complycube.routing.settle_portal_check. Either way a
# contact-scoped valid prior check short-circuits to COMPLETED at instantiate, so nobody is
# screened — or charged for — twice.
# --------------------------------------------------------------------------------------------------
#: ``flows.case_steps.data`` key holding the ``crm.kyc_checks`` row THIS step instance opened.
_KYC_CHECK_KEY = "kyc_check_id"

#: ``flows.case_steps.data`` key holding the decision about HOW this identity is checked. Whether a
#: check is automatic or manual is the client's to make — they hold the document — so where it is
#: set it BEATS the provider the flow author pinned into the definition. Only ``manual`` is ever
#: written: choosing the digital path is simply doing it, and needs nothing recorded.
#:
#: TWO doors write it, and they are the same decision from different ends: the client
#: (``POST /applications/me/identity-manual``), and the adviser at kickoff, who may REQUIRE a
#: manual check for a client they already know cannot photograph a document
#: (``intake.identity_mode``, read below). The adviser's door narrows what the client is offered;
#: it never takes a choice away from a client who had one, because "let the client choose" is the
#: kickoff default wherever a digital provider exists.
_IDENTITY_CHOICE_KEY = "identity_choice"

#: ``flows.executions.data.intake`` key the kickoff writes. ``manual`` means the adviser required a
#: document check; anything else (including absent) leaves the choice where it belongs.
_INTAKE_IDENTITY_MODE = "identity_mode"

#: The check THIS step instance owns: the id recorded at instantiate when there is one, else (a row
#: created before that was recorded) the newest pending check for the person + kind. The fallback is
#: the old behaviour, kept only for journeys already in flight — see ReviewHandler.instantiate.
#: A fixed literal with bound parameters only; the `id = (…)` wrappers below add no interpolation.
_TARGET_CHECK_SQL = (
    "SELECT id FROM crm.kyc_checks "
    "WHERE tenant_id = :t AND contact_id = :c AND kind = :k "
    "  AND (CAST(:check_id AS bigint) IS NULL OR id = CAST(:check_id AS bigint)) "
    "  AND (CAST(:check_id AS bigint) IS NOT NULL OR status = 'pending') "
    "ORDER BY id DESC LIMIT 1"
)

_EXPIRE_CHECK_SQL = (
    "UPDATE crm.kyc_checks SET status = 'expired' "  # noqa: S608 (fixed literal, see above)
    "WHERE status = 'pending' AND id = (" + _TARGET_CHECK_SQL + ")"
)

_RECORD_CHECK_SQL = (
    "UPDATE crm.kyc_checks SET status = :st, "  # noqa: S608 (fixed literal, see above)
    "  verified_at = COALESCE(:verified, verified_at), "
    "  valid_until = COALESCE(:vu, valid_until), "
    "  external_ref = COALESCE(:ref, external_ref), raw = raw || CAST(:raw AS jsonb) "
    "WHERE id = (" + _TARGET_CHECK_SQL + ")"
)


class ReviewHandler(BaseHandler):
    assignee_default = "staff"
    _config_keys = frozenset({"provider", "category"})

    _KIND = {"verify_identity": kyc_mod.KIND_IDENTITY, "aml_check": kyc_mod.KIND_AML}

    def __init__(self, step_type: str) -> None:
        self.type = step_type

    async def instantiate(self, ctx: StepContext) -> StepEvent | None:
        # DEDUP: if this person already holds a currently-valid check of this kind (from another
        # account/application), reuse it — no duplicate identity/AML. Otherwise open a pending check
        # and wait for the result (human or provider webhook).
        kind = self._KIND.get(self.type, kyc_mod.KIND_IDENTITY)
        if ctx.participant_subject_id is None:
            return _event(ctx, StepSignal.WAITING)
        # The reuse DECISION is kyc.pick_valid — the unit-tested rule — not a second copy of it in
        # SQL. The query only fetches the candidates (and the DB's clock, so validity is judged
        # against the same `now()` the store stamps rows with, never the app server's).
        rows = (
            (
                await ctx.session.execute(
                    text(
                        "SELECT contact_id, kind, status, verified_at, valid_until, "
                        "       now() AS db_now "
                        "FROM crm.kyc_checks "
                        "WHERE tenant_id = :t AND contact_id = :c AND kind = :k"
                    ),
                    {"t": ctx.tenant_id, "c": ctx.participant_subject_id, "k": kind},
                )
            )
            .mappings()
            .all()
        )
        if rows:
            now = rows[0]["db_now"]
            reusable = kyc_mod.pick_valid(
                [
                    kyc_mod.KycCheck(
                        contact_id=int(r["contact_id"]),
                        kind=str(r["kind"]),
                        status=str(r["status"]),
                        verified_at=r["verified_at"],
                        valid_until=r["valid_until"],
                    )
                    for r in rows
                ],
                kind,
                now,
            )
            if reusable is not None:
                return _event(ctx, StepSignal.COMPLETED, reason="already_verified")
        # The adviser's kickoff choice, applied before anything else looks at the provider: a
        # required manual check must not ask the client for photos first and abandon them.
        if self.type == "verify_identity" and await self._kickoff_requires_manual(ctx):
            await self._record_identity_choice(ctx)
        check_id = (
            await ctx.session.execute(
                text(
                    "INSERT INTO crm.kyc_checks "
                    "  (uid, tenant_id, contact_id, kind, status, provider) "
                    "VALUES (:u, :t, :c, :k, 'pending', :p) RETURNING id"
                ),
                {
                    "u": new_id(),
                    "t": ctx.tenant_id,
                    "c": ctx.participant_subject_id,
                    "k": kind,
                    "p": self._provider_label(ctx),
                },
            )
        ).scalar_one()
        # Remember WHICH check this instance opened. Without it the outcome could only be written to
        # "the latest pending check for this person and kind" — so two live journeys for the same
        # client (a new account while an existing one is mid-onboarding) cross-wrote each other's
        # identity/AML results. Same idiom as generate_document's `document_id`.
        await ctx.session.execute(
            text(
                "UPDATE flows.case_steps SET data = data || CAST(:d AS jsonb) "
                "WHERE case_id = :c AND step_key = :k "
                "  AND (CAST(:psid AS int) IS NULL OR participant_subject_id = CAST(:psid AS int))"
            ),
            {
                "d": json.dumps({_KYC_CHECK_KEY: int(check_id)}),
                "c": ctx.case_id,
                "k": ctx.step_key,
                "psid": ctx.participant_subject_id,
            },
        )
        ctx.data[_KYC_CHECK_KEY] = int(check_id)
        # Hand off to the configured provider. `manual` is a no-op and the step simply waits for a
        # staff member; ComplyCube does its setup here and the result releases the step later
        # through app.complycube.routing.settle_portal_check — identity by asking the client for
        # their photos, AML by queuing its screen for the worker. The call is inside the step
        # transaction on purpose: work that committed without the step, or vice versa, would leave
        # a check nobody is waiting on or a step nothing will ever release.
        await providers.get_provider(self._provider(ctx), self.type).start(
            tenant_id=ctx.tenant_id,
            case_id=ctx.case_id,
            step_id=ctx.step_key,
            subject_contact_id=ctx.participant_subject_id,
            session=ctx.session,
            kyc_check_id=int(check_id),
        )
        # A manual identity check IS two documents (`app/idv/manual.py`). Asked for here rather
        # than only when a client picks the manual path, because the adviser can require it at
        # kickoff — and a client who is never offered the digital check must still be told what to
        # send. `manual` is the no-op provider, so without this the step waited on nothing.
        if self.type == "verify_identity" and self._provider(ctx) == "manual":
            await idv_manual.raise_request(
                ctx.session,
                tenant_id=ctx.tenant_id,
                execution_id=ctx.execution_id,
                contact_id=int(ctx.participant_subject_id),
                case_id=ctx.case_id,
                step_key=ctx.step_key,
            )
        return _event(ctx, StepSignal.WAITING)

    async def _kickoff_requires_manual(self, ctx: StepContext) -> bool:
        """Whether the adviser asked for a document check when they raised the journey."""
        mode = (
            await ctx.session.execute(
                text(
                    "SELECT COALESCE("
                    "  data -> 'intake' -> 'identity_modes' ->> CAST(:contact AS text), "
                    "  data -> 'intake' ->> :legacy"
                    ") FROM flows.executions WHERE id = :e"
                ),
                {
                    "contact": ctx.participant_subject_id,
                    "legacy": _INTAKE_IDENTITY_MODE,
                    "e": ctx.execution_id,
                },
            )
        ).scalar_one_or_none()
        return str(mode or "").strip().lower() == "manual"

    async def _record_identity_choice(self, ctx: StepContext) -> None:
        """Stamp the manual decision on the step, and move it to the people who will act on it.

        The same key and the same reassignment the client's own choice makes — one state, whichever
        end asked for it, so `_provider` and every surface read one answer.
        """
        ctx.data[_IDENTITY_CHOICE_KEY] = "manual"
        await ctx.session.execute(
            text(
                "UPDATE flows.case_steps "
                "SET data = COALESCE(data, '{}'::jsonb) || CAST(:d AS jsonb), assignee = 'staff' "
                "WHERE case_id = :c AND step_key = :k "
                "  AND (CAST(:psid AS int) IS NULL OR participant_subject_id = CAST(:psid AS int))"
            ),
            {
                "d": json.dumps({_IDENTITY_CHOICE_KEY: "manual"}),
                "c": ctx.case_id,
                "k": ctx.step_key,
                "psid": ctx.participant_subject_id,
            },
        )

    @staticmethod
    def _provider(ctx: StepContext) -> str:
        """Which provider actually runs this check.

        The flow author's ``config.provider`` is the DEFAULT; a recorded client choice wins. It
        only ever moves in one direction — a client asking for a person to check their documents —
        so this cannot resurrect a digital check the client declined, which matters on a retry:
        re-instantiating the step would otherwise ask for fresh photos against a check an adviser
        may already be reviewing.
        """
        if str(ctx.data.get(_IDENTITY_CHOICE_KEY) or "") == "manual":
            return "manual"
        return str(ctx.config.get("provider") or "manual")

    @classmethod
    def _provider_label(cls, ctx: StepContext) -> str:
        """``crm.kyc_checks.provider`` for the check this step opens: the provider that actually
        runs it, so a retired name on an in-flight version is recorded as ComplyCube."""
        name = cls._provider(ctx)
        return providers.COMPLYCUBE if name in providers.RETIRED else name

    async def preflight(
        self, session: AsyncSession, tenant_id: int, config: dict[str, Any]
    ) -> list[str]:
        """Refuse to publish a flow whose provider cannot actually run.

        A `provider` naming a vendor that is not registered, or one whose credentials are absent in
        this environment, would fail at RUNTIME — mid-onboarding, on a real client. Catching it at
        publish is the difference between an authoring error and an incident. A retired name
        (``providers.RETIRED``) still RUNS for a version already published, on ComplyCube, but a
        new flow may not name it.

        Also the natural home for a per-tenant AML entitlement check, which does not exist yet:
        screening spend from a tenant flow is currently unmetered, bounded only by
        `kyc.pick_valid` reusing a pass the person already holds.
        """
        name = str(config.get("provider") or "manual")
        if name == "manual":
            return []
        available = providers.names(self.type) - providers.RETIRED
        if name not in available:
            known = ", ".join(sorted(available))
            # The unwanted value is NAMED: it is the author's own choice rather than anything
            # internal, and a flow with several checks in it cannot be corrected from a refusal
            # that does not say which one is wrong.
            return [f"“{name}” is not an available check service. Choose one of: {known}."]
        if name == providers.COMPLYCUBE:
            from app.complycube.client import complycube_configured

            if not complycube_configured():
                # The credential is OURS, so the author is told to contact support and the setting
                # goes to the log. Naming it here would put an environment variable in a firm's
                # publish dialog, where nobody can act on it.
                logger.warning("flow publish refused: COMPLYCUBE_API_KEY unset")
                return [
                    "ComplyCube is unavailable. Contact support before publishing this journey."
                ]
        return []

    async def handle(self, ctx: StepContext, detail: dict[str, Any]) -> StepEvent:
        # `outcome` == 'passed' clears the gate + records a contact-scoped, reusable pass (with the
        # firm's validity horizon); 'failed'/'referred' fails the step (blocks the case until
        # remediated) — mirrors legacy *_under_review handling.
        #
        # For `verify_identity` / `aml_check` the only caller that reaches this is the engine's own
        # evidence hook (`orchestrator._settle_identity_from_evidence`): a typed Pass / Refer is
        # refused before the handler runs (`_assert_check_not_decided_by_hand`). `review` still
        # takes a person's verdict here.
        outcome = str(detail.get("outcome", "passed"))
        kind = self._KIND.get(self.type, kyc_mod.KIND_IDENTITY)
        await self._record_result(ctx, kind, outcome, detail)
        if outcome == "passed":
            from_evidence = bool(detail.get("evidence_complete"))
            reason = "identity_documents_received" if from_evidence else "review_passed"
            return _event(ctx, StepSignal.COMPLETED, reason=reason)
        return _event(ctx, StepSignal.FAILED, reason=f"review_{outcome}")

    async def cancel(self, ctx: StepContext) -> None:
        """Retire the pending check this step opened, so an abandoned journey leaves no eternal
        `pending` row a LATER journey's outcome could attach itself to. `expired` is the store's
        vocabulary for "no longer live" (0026 allows no `cancelled`)."""
        if ctx.participant_subject_id is None:
            return
        kind = self._KIND.get(self.type, kyc_mod.KIND_IDENTITY)
        await ctx.session.execute(text(_EXPIRE_CHECK_SQL), self._target_params(ctx, kind))

    @staticmethod
    def _target_params(ctx: StepContext, kind: str) -> dict[str, Any]:
        recorded = ctx.data.get(_KYC_CHECK_KEY)
        return {
            "t": ctx.tenant_id,
            "c": ctx.participant_subject_id,
            "k": kind,
            "check_id": int(recorded) if isinstance(recorded, (int, str)) and recorded else None,
        }

    async def _record_result(
        self, ctx: StepContext, kind: str, outcome: str, detail: dict[str, Any]
    ) -> None:
        if ctx.participant_subject_id is None:
            return
        status = "passed" if outcome == "passed" else outcome  # 'failed' | 'referred'
        # The validity horizon is kyc.compute_valid_until (the unit-tested rule), applied to the
        # DB's clock — one implementation of "how long a pass lasts", not one here and one in SQL.
        verified_at = (await ctx.session.execute(text("SELECT now()"))).scalar_one()
        valid_until = kyc_mod.compute_valid_until(verified_at, kind) if status == "passed" else None
        await ctx.session.execute(
            text(_RECORD_CHECK_SQL),
            {
                "st": status,
                "verified": verified_at if status == "passed" else None,
                "vu": valid_until,
                "ref": detail.get("external_ref"),
                "raw": json.dumps(detail.get("raw", {})),
                **self._target_params(ctx, kind),
            },
        )


class AmlCheckHandler(ReviewHandler):
    """``aml_check``: the screen always runs by itself on ComplyCube; there is no manual AML step.

    It has no ``provider`` to choose. One stored on a flow saved before is ignored
    (``definitions._RENAMED_CONFIG_KEYS``). Publish never refuses the step: without a ComplyCube
    key the screen goes to operations as a "Screen {client}" task (``app.complycube.screen``).
    """

    _config_keys = frozenset({"category"})

    def __init__(self) -> None:
        super().__init__("aml_check")

    @staticmethod
    def _provider(ctx: StepContext) -> str:
        return providers.COMPLYCUBE

    async def preflight(
        self, session: AsyncSession, tenant_id: int, config: dict[str, Any]
    ) -> list[str]:
        return []


# --------------------------------------------------------------------------------------------------
# fill_form — data-gathering or review-and-confirm. On submission, gathered datapoints are written
# back to crm.contacts via the mapper (the profile IS the client). Config `mode`: 'gather'|'review'.
# --------------------------------------------------------------------------------------------------
class FillFormHandler(BaseHandler):
    type = "fill_form"
    assignee_default = "client"
    _config_keys = frozenset({"form_uid", "category", "mapping", "mode", "title"})

    async def preflight(
        self, session: AsyncSession, tenant_id: int, config: dict[str, Any]
    ) -> list[str]:
        issues: list[str] = []
        if not config.get("form_uid"):
            issues.append("Choose a form to fill in.")
        try:
            mapping_mod.parse_mapping(config.get("mapping"))
        except mapping_mod.MappingError as exc:
            issues.append(f"Invalid field mapping: {exc}")
        return issues

    async def instantiate(self, ctx: StepContext) -> StepEvent | None:
        if ctx.request_id is None:
            # No contact to bind the request to (an application-scoped form, or an account case with
            # no per-owner fan-out): there is no artifact and nobody to fulfil it. Block LOUDLY with
            # a reason rather than sitting `pending` forever with no actor — publish preflight
            # rejects this shape, so it only reaches here on a legacy/seeded definition.
            return _event(ctx, StepSignal.BLOCKED, reason="no_actor")
        await _create_request_item(
            ctx,
            action="complete_form",
            title=str(ctx.config.get("title") or "Complete your details"),
            form_uid=_as_str(ctx.config.get("form_uid")),
            category=_as_str(ctx.config.get("category")),
        )
        return None  # pending until the client submits the form → on_item_fulfilled

    async def handle(self, ctx: StepContext, detail: dict[str, Any]) -> StepEvent:
        # Reaching here means the orchestrator has already established that this step's request item
        # is fulfilled and that the stored answers cover the form's required questions
        # (``orchestrator._assert_fulfilment_supported``) — the handler maps, it does not police.
        answers = detail.get("answers")
        if (
            ctx.config.get("mode") == "account_preferences"
            and ctx.subject_type == "account"
            and ctx.subject_id is not None
            and isinstance(answers, dict)
        ):
            try:
                await _write_account_preferences(
                    ctx.session, ctx.tenant_id, int(ctx.subject_id), answers
                )
            except ValueError as exc:
                return _event(ctx, StepSignal.BLOCKED, reason=str(exc))
            return _event(ctx, StepSignal.COMPLETED)
        mappings = mapping_mod.parse_mapping(ctx.config.get("mapping"))
        target = ctx.participant_subject_id or (
            ctx.subject_id if ctx.subject_type == "contact" else None
        )
        if mappings and isinstance(answers, dict) and target is not None:
            result = mapping_mod.apply_mapping(mappings, answers)
            if result.errors:
                return _event(ctx, StepSignal.BLOCKED, reason="; ".join(result.errors))
            # A client submission fills a blank and leaves a value the record already holds.
            # The answers stay on the form either way. Staff write-back still replaces: that
            # submission is the adviser editing the record.
            try:
                await _write_contact_patch(
                    ctx.session,
                    ctx.tenant_id,
                    int(target),
                    result.patch,
                    fill_blanks_only=await _caller_is_client(ctx.session),
                )
            # Every ValueError the write raises names the answer at fault (a rejected bank
            # account, a W-8 for a US person, a phone number), so it goes back to the form.
            except ValueError as exc:
                raise StepTransitionRefused(str(exc)) from exc
        return _event(ctx, StepSignal.COMPLETED)


async def _caller_is_client(session: AsyncSession) -> bool:
    """Whether this submission came through the client portal.

    Read from the session's own claims, the same belt the document routes use: a missing portal
    claim must not be treated as staff, and a legacy ``role = client`` session counts too.
    """
    scope = (
        (await session.execute(text("SELECT auth.portal() AS portal, auth.role() AS role")))
        .mappings()
        .one()
    )
    return bool(scope["portal"] == "client" or scope["role"] == "client")


async def _compatible_account_fees(
    session: AsyncSession, *, tenant_id: int, product_id: int | None, model_id: int | None
) -> tuple[list[int], set[int], set[int]]:
    """Display (plus hidden-default) ids that always apply, and selectable ids the adviser may pick.

    Hidden non-default and custom packages stay off this form: they are not auto-applied and cannot
    be chosen here, so a stale payload that names one is stripped rather than stored.
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, access, is_default FROM platform.fee_packages "
                    "WHERE tenant_id = :t AND enabled IS NOT FALSE "
                    "  AND (product_id IS NULL OR product_id = CAST(:p AS int)) "
                    "  AND (model_id IS NULL OR model_id = CAST(:m AS int)) "
                    "ORDER BY sort_order, name"
                ),
                {"t": tenant_id, "p": product_id, "m": model_id},
            )
        )
        .mappings()
        .all()
    )
    display: list[int] = []
    selectable: set[int] = set()
    compatible: set[int] = set()
    for row in rows:
        fee_id = int(row["id"])
        compatible.add(fee_id)
        access = str(row["access"] or "hidden")
        if access == "display" or (access == "hidden" and row["is_default"] is True):
            display.append(fee_id)
        elif access == "select":
            selectable.add(fee_id)
    return display, selectable, compatible


async def _write_account_preferences(
    session: AsyncSession, tenant_id: int, account_id: int, answers: dict[str, Any]
) -> None:
    """Persist the choices deliberately moved out of journey INITIALISATION.

    This runs before the terms fork. A transfer was not known when the journey was planned, so
    the declaration is not on the plan yet; the walk creates it when this fork is taken
    (``orchestrator._materialize_omitted_step``). Contributions retain kickoff's semantics:
    every wrapper gets the expectation bag; a pension also gets the real contribution-ledger row.
    """
    name = str(answers.get("account_name") or "").strip()
    if len(name) < 2:
        raise ValueError("Enter an account name.")
    currency = str(answers.get("currency") or "").strip().upper()
    capacity = str(answers.get("investment_capacity") or "").strip().lower()
    if capacity not in {"advisory", "discretionary", "execution_only"}:
        raise ValueError("Choose a valid investment capacity.")
    raw_model = answers.get("model_id")
    model_id = int(raw_model) if raw_model not in {None, ""} else None

    dividend = str(answers.get("dividend_treatment") or "").strip().lower() or None
    if dividend not in {None, "reinvest", "cash", "pay_away"}:
        raise ValueError("Choose a valid dividend treatment.")
    settlement = str(answers.get("dividend_settlement_currency") or "").strip().lower() or None
    if settlement not in {None, "security", "account_base"}:
        raise ValueError("Choose a valid dividend settlement currency.")

    has_transfer = answers.get("has_transfer") is True
    transfer: dict[str, Any] | None = None
    if has_transfer:
        transfer_type = str(answers.get("transfer_type") or "").strip().lower()
        if transfer_type not in {"cash", "in_specie"}:
            raise ValueError("Choose whether the transfer is cash or in-specie.")
        extent = str(answers.get("transfer_extent") or "").strip().lower()
        if extent not in {"full", "partial"}:
            raise ValueError("Choose whether the transfer is full or partial.")
        transfer = {"intent": True, "transfer_type": transfer_type, "extent": extent}

    account = (
        (
            await session.execute(
                text(
                    "SELECT a.data, a.product_id, a.custodian, a.dividend_treatment, "
                    "       COALESCE(a.custodian, app.provider)::text AS form_custodian, "
                    "       p.product_type::text AS product_type, "
                    "       COALESCE(p.is_pension, false) AS is_pension, "
                    "       COALESCE(p.allowed_currencies, t.allowed_currencies) "
                    "         AS allowed_currencies "
                    "FROM platform.accounts a "
                    "JOIN auth.tenants t ON t.id = a.tenant_id "
                    "LEFT JOIN crm.applications app ON app.id = a.application_id "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "WHERE a.id = :a AND a.tenant_id = :t"
                ),
                {"a": account_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if account is None:
        raise ValueError("The account is no longer available.")
    product = str(account["product_type"] or "").upper()
    # A pension is sterling. The picker is hidden on those wrappers; a leftover answer is ignored.
    if product in {"SIPP", "TPPENSION"}:
        currency = "GBP"
    elif len(currency) != 3:
        raise ValueError("Choose the account currency.")
    custodian = str(account["custodian"] or "").strip().lower()
    existing_dividend = str(account["dividend_treatment"] or "").strip().lower() or None
    # Pay away is Epworth's option. SEI (and the other custodians) offer Reinvest or Cash.
    # An account that already stores pay away may keep it.
    if dividend == "pay_away" and custodian != "epworth" and existing_dividend != "pay_away":
        raise ValueError("Pay away is only available on an Epworth account.")
    # The preferred settlement currency is an SEI choice; the form asks it only when the custodian
    # it was shown (the account's, else the application's) is SEI. A leftover answer is ignored.
    if str(account["form_custodian"] or "").strip().lower() != "sei":
        settlement = None
    allowed_currencies = set(account["allowed_currencies"] or [])
    if currency not in allowed_currencies:
        raise ValueError(f"{currency} is not allowed for this product.")
    data = account["data"]
    if isinstance(data, str):
        data = json.loads(data)
    data = dict(data) if isinstance(data, dict) else {}
    if model_id is not None:
        valid_model = (
            await session.execute(
                text(
                    "SELECT 1 FROM platform.models "
                    "WHERE id = :m AND tenant_id = :t AND enabled IS NOT FALSE "
                    "  AND (provider IS NULL "
                    "       OR provider = CAST(:platform AS platform.provider_code)) "
                    "  AND (products IS NULL OR cardinality(products) = 0 "
                    "       OR :product = ANY(products::text[]))"
                ),
                {
                    "m": model_id,
                    "t": tenant_id,
                    "product": str(account["product_type"] or "").upper(),
                    "platform": account["custodian"],
                },
            )
        ).scalar_one_or_none()
        if valid_model is None:
            raise ValueError("That investment model isn't available for this account.")

    if transfer is not None:
        raw_provider = answers.get("transfer_provider")
        provider_id = int(raw_provider) if raw_provider not in {None, ""} else None
        provider_name: str | None = None
        if provider_id is not None:
            provider_name = (
                await session.execute(
                    text(
                        "SELECT name FROM platform.transfer_providers "
                        "WHERE id = :p AND tenant_id = :t AND enabled IS NOT FALSE"
                    ),
                    {"p": provider_id, "t": tenant_id},
                )
            ).scalar_one_or_none()
            if provider_name is None:
                raise ValueError("That transfer provider isn't available for this account.")
        transfer.update(
            {
                "amount": answers.get("transfer_amount"),
                "provider_id": provider_id,
                "provider_name": provider_name,
                "provider_reference": str(answers.get("transfer_reference") or "").strip() or None,
            }
        )
        # The same pair the raise drawer asks, at the bottom of the transfer fields. Unchecked
        # is No. The FCA reference is what Seccl requires once safeguarded rights are set.
        if product in contributions.PENSION_PRODUCTS or account["is_pension"]:
            safeguarded = answers.get("safeguarded_benefits") is True
            transfer["safeguarded_benefits"] = safeguarded
            if safeguarded:
                fca_reference = str(answers.get("fca_reference") or "").strip()
                if not fca_reference:
                    raise ValueError(
                        "Enter the FCA reference of the firm that advised on these "
                        "safeguarded rights."
                    )
                transfer["fca_reference"] = fca_reference
            # The ceding scheme's product type, and for a drawdown transfer the crystallised
            # split — the same shape the account's own transfer drawer records, so a transfer
            # raised from this journey carries the facts Morgan Lloyd needs.
            pension_type_raw = str(answers.get("pension_type") or "").strip()
            transfer["pension_type"] = int(pension_type_raw) if pension_type_raw else None
            if answers.get("is_crystallised") == "Yes":
                crystallised = answers.get("crystallised_amount")
                crystallised_value = float(crystallised) if crystallised not in {None, ""} else 0.0
                total = float(answers.get("transfer_amount") or 0)
                transfer["crystallised_value"] = crystallised_value
                transfer["crystallisation"] = (
                    "crystallised" if total and crystallised_value >= total else "partial"
                )
            else:
                transfer["crystallised_value"] = 0
                transfer["crystallisation"] = "uncrystallised"

    if settlement:
        data["dividend_settlement_currency"] = settlement
    else:
        data.pop("dividend_settlement_currency", None)
    data["crystallisation_status"] = answers.get("crystallisation_status") or None
    # The drawdown access route (UFPLS / FAD), kept only while the basis is drawdown.
    data["access_option"] = answers.get("access_option") or None
    data["money_purchase_trigger"] = answers.get("money_purchase_trigger") or None
    data["money_purchase_trigger_date"] = answers.get("money_purchase_trigger_date") or None
    data["sipp_employer_scheme"] = answers.get("sipp_employer_scheme") or None
    if answers.get("has_third_party_provider") is True:
        data["third_party_provider_bank_details"] = {
            "provider": str(answers.get("third_party_provider") or "").strip() or None,
            "account_name": str(answers.get("third_party_account_name") or "").strip() or None,
            "account_number": str(answers.get("third_party_account_number") or "").strip() or None,
            "sort_code": str(answers.get("third_party_sort_code") or "").strip() or None,
        }
    else:
        data.pop("third_party_provider_bank_details", None)
    data["account_activity"] = {
        "nature": answers.get("account_activity_nature"),
        "standing_orders": answers.get("account_activity_standing_orders"),
        "funds_in": answers.get("account_activity_funds_in"),
        "funds_out": answers.get("account_activity_funds_out"),
        "max_turnover": answers.get("account_activity_max_turnover"),
        "transaction_scale": answers.get("account_activity_transaction_scale"),
    }

    beneficiaries = answers.get("beneficiaries")
    if beneficiaries:
        if not isinstance(beneficiaries, list):
            raise ValueError("Beneficiaries must be a list.")
        cleaned_beneficiaries: list[dict[str, Any]] = []
        total = 0.0
        for beneficiary in beneficiaries:
            if not isinstance(beneficiary, dict):
                raise ValueError("Each beneficiary needs a name and percentage.")
            first_name = str(beneficiary.get("first_name") or "").strip()
            surname = str(beneficiary.get("surname") or "").strip()
            portion = float(beneficiary.get("portion") or 0)
            if not first_name or not surname or portion <= 0:
                raise ValueError("Each beneficiary needs a name and percentage greater than zero.")
            total += portion
            cleaned_beneficiaries.append(
                {"first_name": first_name, "surname": surname, "portion": portion}
            )
        if abs(total - 100) > 0.001:
            raise ValueError("Beneficiary percentages must total 100%.")
        data["beneficiaries"] = cleaned_beneficiaries
    else:
        data.pop("beneficiaries", None)

    has_contribution = answers.get("has_contribution") is True
    if has_contribution:
        amount = answers.get("contribution_amount")
        if amount is None or float(amount) <= 0:
            raise ValueError("Enter a contribution amount greater than zero.")
        frequency = str(answers.get("contribution_frequency") or "single")
        if frequency not in contributions.FREQUENCIES:
            raise ValueError("Choose a valid contribution frequency.")
        expected = str(answers.get("contribution_expected_by") or "").strip() or None
        expected_date = date.fromisoformat(expected) if expected else None
        data["regular_contribution"] = {
            "intent": True,
            "amount": amount,
            "frequency": frequency,
            "expected_by": expected,
        }
    else:
        data.pop("regular_contribution", None)

    # The opening cash is a one-off, so it sits on `data`, not in the payments list. `data` is
    # also the firm's own: a provider sync rewrites `payments` and would drop it.
    if answers.get("has_initial_cash") is True:
        initial_amount = answers.get("initial_cash_amount")
        if initial_amount is None or float(initial_amount) <= 0:
            raise ValueError("Enter an initial cash amount greater than zero.")
        data["initial_cash_deposit"] = {"investment_amount": initial_amount}
    else:
        data.pop("initial_cash_deposit", None)
    payments: list[dict[str, object]] = []
    if has_contribution:
        payments.append(
            regular_movements.register_row(
                amount=answers["contribution_amount"],
                frequency=str(answers.get("contribution_frequency") or "single"),
                next_date=answers.get("contribution_expected_by"),
            )
        )

    withdrawals: list[dict[str, object]] = []
    if answers.get("has_regular_withdrawal") is True:
        withdrawal_amount = answers.get("withdrawal_amount")
        withdrawal_frequency = str(answers.get("withdrawal_frequency") or "")
        withdrawal_date = str(answers.get("withdrawal_start_date") or "")
        if withdrawal_amount is None or float(withdrawal_amount) <= 0:
            raise ValueError("Enter a withdrawal amount greater than zero.")
        if withdrawal_frequency not in {"monthly", "quarterly", "half_yearly", "annually"}:
            raise ValueError("Choose a valid withdrawal frequency.")
        if not withdrawal_date:
            raise ValueError("Choose the first withdrawal date.")
        date.fromisoformat(withdrawal_date)
        withdrawals.append(
            regular_movements.register_row(
                amount=withdrawal_amount,
                frequency=withdrawal_frequency,
                next_date=withdrawal_date,
            )
        )

    await session.execute(
        text(
            "UPDATE platform.accounts "
            "SET name = :name, currency = :currency, investment_capacity = :capacity, "
            "    model_id = CAST(:model AS int), dividend_treatment = :div, "
            "    transfer = CAST(:transfer AS jsonb), payments = CAST(:payments AS jsonb), "
            "    withdrawals = CAST(:withdrawals AS jsonb), data = CAST(:data AS jsonb) "
            "WHERE id = :a AND tenant_id = :t"
        ),
        {
            "name": name,
            "currency": currency,
            "capacity": capacity,
            "model": model_id,
            "div": dividend,
            "transfer": json.dumps(transfer) if transfer is not None else None,
            "payments": json.dumps(payments) if payments else None,
            "withdrawals": json.dumps(withdrawals) if withdrawals else None,
            "data": json.dumps(data) if data else None,
            "a": account_id,
            "t": tenant_id,
        },
    )
    raw_fee_ids = answers.get("fee_package_ids") or []
    if not isinstance(raw_fee_ids, list):
        raise ValueError("Choose fee packages from the available list.")
    try:
        submitted_ids = [int(value) for value in raw_fee_ids]
    except (TypeError, ValueError) as exc:
        raise ValueError("Choose fee packages from the available list.") from exc
    display_ids, selectable_ids, compatible_ids = await _compatible_account_fees(
        session,
        tenant_id=tenant_id,
        product_id=int(account["product_id"]) if account["product_id"] is not None else None,
        model_id=model_id,
    )
    if any(fee_id not in compatible_ids for fee_id in submitted_ids):
        raise ValueError("That fee package isn't available for this account.")
    chosen = [fee_id for fee_id in submitted_ids if fee_id in selectable_ids]
    fee_ids = list(dict.fromkeys([*display_ids, *chosen]))
    await instructions.set_account_fee_packages(
        session,
        tenant_id=tenant_id,
        account_id=account_id,
        fee_package_ids=fee_ids,
    )

    # The client-level MPAA fact is shared by every pension account and drives contribution relief
    # calculations elsewhere in the product.
    if str(answers.get("money_purchase_trigger") or "").lower() == "yes":
        await session.execute(
            text(
                "UPDATE crm.contacts SET flexi_access_taken = true "
                "WHERE id = (SELECT contact_id FROM platform.accounts "
                "WHERE id = :a AND tenant_id = :t)"
            ),
            {"a": account_id, "t": tenant_id},
        )

    is_pension = str(account["product_type"] or "").upper() in contributions.PENSION_PRODUCTS
    if has_contribution and is_pension:
        # A request item is fulfilled once, but guard the domain row as well so an operational
        # replay cannot book the same expectation twice.
        existing = (
            (
                await session.execute(
                    text(
                        "SELECT uid, expectation_id, status::text AS status "
                        "FROM platform.contributions "
                        "WHERE tenant_id = :t AND account_id = :a AND status <> 'cancelled' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"t": tenant_id, "a": account_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is None:
            created_by = (await session.execute(text("SELECT auth.uid()"))).scalar_one_or_none()
            await contributions.create_contribution(
                session,
                tenant_id=tenant_id,
                created_by=str(created_by) if created_by is not None else None,
                account_id=account_id,
                category="personal_contribution",
                amount=answers["contribution_amount"],
                frequency=str(answers.get("contribution_frequency") or "single"),
                paid_by="client",
                due_date=expected_date,
                lodge=False,
            )
        elif str(existing["status"]) == "awaiting_confirmation":
            amount = answers["contribution_amount"]
            await session.execute(
                text(
                    "UPDATE platform.contributions "
                    "SET amount = :amount, gross_amount = :gross, relief_amount = :relief, "
                    "    frequency = :frequency "
                    "WHERE uid = :u AND tenant_id = :t"
                ),
                {
                    "amount": amount,
                    "gross": contributions.gross_of(amount, "personal_contribution"),
                    "relief": contributions.relief_of(amount, "personal_contribution"),
                    "frequency": str(answers.get("contribution_frequency") or "single"),
                    "u": existing["uid"],
                    "t": tenant_id,
                },
            )
            await session.execute(
                text(
                    "UPDATE platform.transaction_expectations "
                    "SET amount = :amount, due_date = :due "
                    "WHERE id = :e AND tenant_id = :t AND status = 'expected'"
                ),
                {
                    "amount": amount,
                    "due": expected_date or date.today(),
                    "e": existing["expectation_id"],
                    "t": tenant_id,
                },
            )
    elif is_pension:
        existing_uid = (
            await session.execute(
                text(
                    "SELECT uid FROM platform.contributions "
                    "WHERE tenant_id = :t AND account_id = :a "
                    "  AND status = 'awaiting_confirmation' "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"t": tenant_id, "a": account_id},
            )
        ).scalar_one_or_none()
        if existing_uid is not None:
            await contributions.cancel(
                session,
                tenant_id=tenant_id,
                uid=str(existing_uid),
                reason="Removed while amending the onboarding fact-find",
            )


# Real jsonb COLUMNS that accept dotted mapping targets (read-modify-write, like `data`).
# Seed field `bank_account.account_name` aliases to CRM leaf `name` when nesting.
# `employer_address` (0139) is the COMPANY's address and a separate column from `address` for that
# reason; it takes the same dotted leaves, so the fact-find's employer step merges into it the way
# the core step merges into the client's own.
_JSONB_COLUMNS = frozenset({"address", "bank_account", "employer_address", "employer_bank_account"})
_BANK_LEAF_ALIASES = {"account_name": "name"}


def _as_text_array(value: object) -> list[str] | None:
    """Normalise for a ``text[]`` column: list stripped, string → singleton."""
    if value is None:
        return None
    if isinstance(value, list | tuple):
        out = [str(v).strip() for v in value if v is not None and str(v).strip()]
        return out or None
    s = str(value).strip()
    return [s] if s else None


def _set_jsonb_path(
    root: dict[str, Any], path: str, value: object, *, leaf_aliases: dict[str, str]
) -> None:
    """Set a dotted path inside a jsonb object, aliasing the final leaf when needed."""
    *parents, leaf = path.split(".")
    leaf = leaf_aliases.get(leaf, leaf)
    node = root
    for part in parents:
        child = node.get(part)
        node[part] = child if isinstance(child, dict) else {}
        node = node[part]
    node[leaf] = value


def _exclusive_bank_rail(merged: dict[str, Any], paths: dict[str, object]) -> dict[str, Any]:
    """Keep the rail the form just answered, and do not store BACS.

    The fact-find merges dotted leaves into the existing jsonb, so switching from a sort code to
    an IBAN would otherwise leave the old sort code in place and the record would still read as
    UK. BACS is the default and is not written — the same rule as ``normalise_bank_account`` —
    so a stored ``method`` of ``bacs`` is dropped and only Faster Payment remains.
    """
    touched = set(paths)
    uk = bool(touched & {"sort_code", "account_number", "method"})
    international = bool(touched & {"iban", "bic"})
    if international and not uk:
        for key in ("sort_code", "account_number", "method", "bank"):
            merged.pop(key, None)
    elif uk and not international:
        for key in ("iban", "bic"):
            merged.pop(key, None)
        if str(merged.get("method") or "").strip().lower() != "faster_payment":
            merged.pop("method", None)
    return merged


def _merge_jsonb_column(
    current: dict[str, Any],
    *,
    whole: object | None,
    paths: dict[str, object],
    leaf_aliases: dict[str, str],
) -> dict[str, Any]:
    """Merge a whole-object patch and/or relative dotted paths into one jsonb column value."""
    merged: dict[str, Any] = dict(current)
    if isinstance(whole, dict):
        for leaf, leaf_val in whole.items():
            merged[leaf_aliases.get(str(leaf), str(leaf))] = leaf_val
    for rel, val in paths.items():
        _set_jsonb_path(merged, rel, val, leaf_aliases=leaf_aliases)
    return merged


#: Columns whose VALUE has a rule of its own, beyond the type the cast gives it.
#:
#: `mobile` and `phone` are here because the two write paths onto a number disagreed and the
#: disagreement was invisible. The client record's PATCH runs every number through
#: `validate_contact_phone`, which compacts the separators — so an adviser typing
#: "+44 7700 900123" stores `+447700900123`. The onboarding mapper does not: its pipeline is
#: `str -> str` and `trim` is all the seed asks for, so the SAME number captured by the fact-find
#: stored with its spaces intact. Naming the columns here rather than adding a transform to the
#: seed keeps the rule where the column is. `contact_phone` also accepts a leftover
#: `[{"label", "number"}]` answer from before the list became one field.
_NORMALISERS: dict[str, Callable[[object], object]] = {
    "mobile": validate_contact_phone,
    "phone": contact_phone,
}


def _us_person_answer(value: object) -> bool | None:
    """The Additional information answer, as a boolean. Yes is a US person."""
    if isinstance(value, bool):
        return value
    text_value = str(value).strip().lower()
    if text_value in {"yes", "true", "1"}:
        return True
    if text_value in {"no", "false", "0"}:
        return False
    return None


#: `contacts_fatca_status_check` (0134). The form offers `w9` and `w8`; the rest arrive from SEI.
_FATCA_STATUSES = frozenset(
    {"w9", "w8", "w8_cure", "other_individual", "other_entity", "not_certified"}
)


async def _refuse_incoherent_fatca(
    session: AsyncSession, contact_id: int, sets: dict[str, object]
) -> None:
    """Refuse a US-person answer and a W-9 / W-8 that disagree (`contacts_fatca_coherent`, 0134).

    A W-9 is for a US person and a W-8 is not. One half can arrive without the other, so the
    missing half is read off the record, and the message then says the record holds it.
    """
    us, held = sets.get("us_person"), sets.get("fatca_status")
    held_on_record = False
    if "us_person" not in sets or "fatca_status" not in sets:
        stored = (
            await session.execute(
                text("SELECT us_person, fatca_status FROM crm.contacts WHERE id = :c"),
                {"c": contact_id},
            )
        ).one_or_none()
        if stored is not None:
            if "us_person" not in sets:
                us = stored.us_person
            if "fatca_status" not in sets:
                held, held_on_record = stored.fatca_status, True
    if us is True and held == "w8":
        lead = "The client record holds a W-8. " if held_on_record else ""
        raise ValueError(lead + "A US person has a W-9, not a W-8.")
    if us is False and held == "w9":
        lead = "The client record holds a W-9. " if held_on_record else ""
        raise ValueError(lead + "A W-9 is only for a US person.")


def _held(value: object) -> bool:
    """Whether the record already has this fact. Blank, null and an empty list do not count."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | tuple | dict):
        return len(value) > 0
    return True


def _jsonb_leaf(current: dict[str, Any], path: str, aliases: dict[str, str]) -> object:
    *parents, leaf = path.split(".")
    leaf = aliases.get(leaf, leaf)
    node: object = current
    for part in parents:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    if not isinstance(node, dict):
        return None
    return node.get(leaf)


async def _omit_held_facts(
    session: AsyncSession,
    contact_id: int,
    sets: dict[str, object],
    data_patch: dict[str, object],
    jsonb_whole: dict[str, object],
    jsonb_paths: dict[str, dict[str, object]],
) -> None:
    """Drop any mapped fact the record already holds. Mutates the four bags in place.

    A client-submitted form may complete a column that is still empty. It must not replace a
    name, a date of birth, an address or any other fact already on the record — that edit belongs
    on the client record itself. What the client wrote stays on the form response.
    """
    touched_jsonb = [c for c in _JSONB_COLUMNS if c in jsonb_whole or jsonb_paths[c]]
    if not sets and not data_patch and not touched_jsonb:
        return
    cols = [c for c in sets if c not in _JSONB_COLUMNS]
    cols += [f"COALESCE({c}, '{{}}'::jsonb) AS {c}" for c in touched_jsonb]
    if data_patch:
        cols.append("COALESCE(data, '{}'::jsonb) AS data")
    row: Any = (
        (
            await session.execute(
                text(f"SELECT {', '.join(cols)} FROM crm.contacts WHERE id = :c"),  # noqa: S608
                {"c": contact_id},
            )
        )
        .mappings()
        .one_or_none()
    ) or {}
    for col in [c for c in sets if _held(row.get(c))]:
        # A stored false is "not recorded as vulnerable", not a decision that blocks the
        # fact-find from recording that they are. A recorded true still stands.
        if col == "is_vulnerable" and row.get(col) is False and sets.get(col) is True:
            continue
        del sets[col]
    for col in touched_jsonb:
        current = row.get(col) or {}
        if not isinstance(current, dict):
            current = {}
        aliases = _BANK_LEAF_ALIASES if col == "bank_account" else {}
        if col in jsonb_whole and any(_held(v) for v in current.values()):
            del jsonb_whole[col]
        for rel in [rel for rel in jsonb_paths[col] if _held(_jsonb_leaf(current, rel, aliases))]:
            del jsonb_paths[col][rel]
    if data_patch:
        current_data = row.get("data") or {}
        if not isinstance(current_data, dict):
            current_data = {}
        for key in [key for key in data_patch if _held(_jsonb_leaf(current_data, key, {}))]:
            del data_patch[key]


async def _apply_national_insurance(
    session: AsyncSession,
    tenant_id: int,
    contact_id: int,
    sets: dict[str, object],
) -> None:
    """Store a National Insurance number compact, and refuse one another client already holds."""
    if "national_identifier" not in sets and "national_identifier_type" not in sets:
        return
    identifier = sets.get("national_identifier")
    id_type = sets.get("national_identifier_type")
    if "national_identifier" not in sets or "national_identifier_type" not in sets:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT national_identifier, national_identifier_type FROM crm.contacts "
                        "WHERE id = :c AND tenant_id = :t"
                    ),
                    {"c": contact_id, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return
        if "national_identifier" not in sets:
            identifier = row["national_identifier"]
        if "national_identifier_type" not in sets:
            id_type = row["national_identifier_type"]
    if not national_insurance.is_nino_type(id_type):
        return
    if identifier is None or not str(identifier).strip():
        return
    compact = national_insurance.normalise(identifier)
    if compact is None:
        raise StepTransitionRefused(national_insurance.FIELD_MESSAGE)
    sets["national_identifier"] = compact
    taken = (
        await session.execute(
            text(
                "SELECT 1 FROM crm.contacts "
                "WHERE tenant_id = :t "
                "  AND upper(translate(coalesce(national_identifier, ''), ' -', '')) = :ni "
                "  AND id <> :c LIMIT 1"
            ),
            {"t": tenant_id, "ni": compact, "c": contact_id},
        )
    ).first()
    if taken:
        raise StepTransitionRefused(national_insurance.IN_USE)


async def _apply_extra_national_insurance(
    session: AsyncSession,
    tenant_id: int,
    contact_id: int,
    data_patch: dict[str, object],
) -> None:
    """The NINO asked when the nationality identifier is a different number.

    It lives on ``data`` because ``national_identifier`` is already the other
    scheme. The same compact form and the same uniqueness rule apply.
    """
    if "uk_national_insurance" not in data_patch:
        return
    raw = data_patch.get("uk_national_insurance")
    if raw is None or not str(raw).strip():
        data_patch.pop("uk_national_insurance", None)
        return
    compact = national_insurance.normalise(raw)
    if compact is None:
        raise StepTransitionRefused(national_insurance.FIELD_MESSAGE)
    taken = (
        await session.execute(
            text(
                "SELECT 1 FROM crm.contacts "
                "WHERE tenant_id = :t AND id <> :c AND ("
                "  upper(translate(coalesce(national_identifier, ''), ' -', '')) = :ni "
                "  OR upper(translate(coalesce(data->>'uk_national_insurance', ''), "
                "                   ' -', '')) = :ni"
                ") LIMIT 1"
            ),
            {"t": tenant_id, "ni": compact, "c": contact_id},
        )
    ).first()
    if taken:
        raise StepTransitionRefused(national_insurance.IN_USE)
    data_patch["uk_national_insurance"] = compact


async def _confirm_bank_column(
    stored: object, merged: dict[str, Any], *, user_id: str | None
) -> dict[str, Any]:
    """Ask the directory about a UK pair the record does not already hold.

    A 7-digit account number is padded first, which is the Bacs convention the stored
    column already uses. An unreachable directory leaves the pair as it is.
    """
    sort = "".join(ch for ch in str(merged.get("sort_code") or "") if ch.isdigit())
    number = "".join(ch for ch in str(merged.get("account_number") or "") if ch.isdigit())
    if len(sort) == 6 and len(number) in (7, 8):
        merged = dict(merged)
        merged["sort_code"] = bank_check.format_sort_code(sort)
        merged["account_number"] = number.zfill(8)
    if not bank_check.uk_pair(merged):
        return merged
    if bank_check.same_pair(stored, merged):
        if isinstance(stored, dict) and stored.get("bank") and not merged.get("bank"):
            return {**merged, "bank": stored["bank"]}
        return merged
    confirmed = await bank_check.verified_bank(merged, user_id=user_id)
    return confirmed


async def _write_contact_patch(
    session: AsyncSession,
    tenant_id: int,
    contact_id: int,
    patch: dict[str, object],
    *,
    fill_blanks_only: bool = False,
) -> None:
    """Apply a mapped patch to crm.contacts.

    Whitelisted top-level columns are set directly. Targets under ``address`` / ``bank_account`` /
    ``employer_address`` / ``employer_bank_account`` (the key itself, or a dotted path such as
    ``address.line1``) merge into those real jsonb COLUMNS — not into ``data``. Any other dotted
    target nests into ``data`` (offshore KYC, …).

    ``fill_blanks_only`` is the client-portal submission. A column or leaf the record already
    holds is left as it is; a blank is still filled. Staff submissions pass ``False`` and replace.
    """
    # column -> the type a mapped value must be cast to, or None for text.
    #
    # The mapper is string-typed BY DESIGN — its transform pipeline is `str -> str` — so a column
    # that is not text has to say so here. Without the cast Postgres refuses the write outright
    # ("column is of type smallint but expression is of type text"), which would fail the step
    # rather than quietly storing the wrong thing; stating the type is the fix, not coercing in the
    # mapper, because the mapper has no idea what a target is.
    columns: dict[str, str | None] = {
        "first_name": None,
        "surname": None,
        "middle_name": None,
        "title": None,
        "email": None,
        # The mobile, and the other number. Both are text. A leftover list answer is flattened
        # by `contact_phone` before it is stored.
        "mobile": None,
        "phone": None,
        "dob": "date",
        "nationality": "text[]",
        "tax_residency": "text[]",
        "national_identifier": None,
        "national_identifier_type": None,
        # 0052-0054 added these FOR the onboarding fact-find, which then never wrote to them.
        "gender": None,
        "marital_status": None,
        "employment_status": None,
        "retirement_age": "smallint",
        "source_of_wealth": None,
        # 0116 did the same for the offshore fact-find's identity answers: onboarding has asked
        # every one of these since the fact-find existed and each folded into `data`, where it was
        # unqueryable, uneditable and reached a screen only as a dotted path. `occupation` is a
        # separate column from `employment_status` on purpose — one is what they do, the other
        # whether they work, and Morgan Lloyd models exactly that split.
        "occupation": None,
        "occupation_detail": None,
        "country_of_birth": None,
        "town_of_birth": None,
        "source_of_funds": None,
        "source_of_funds_detail": None,
        "tax_relief": None,
        # 0139 gave the client record's Employer card its own columns, and the fact-find's
        # employer step kept writing to `data.pension.employerContributions.*` — so an adviser
        # who answered the employer questions saw three em dashes on the card and
        # "HasEmployerContributions: Yes" under "more from the fact-find". These are what the card
        # reads. `employer_contributions` is the gate the checkbox answers; a mapped checkbox
        # arrives as a real bool (`FieldMapping.run`), and the cast also accepts the string form.
        "employer_contributions": "boolean",
        "employer_name": None,
        "employer_registration_number": None,
        # The "Other" words behind `source_of_wealth` — same migration, same omission: the
        # additional-details step mapped to it from the day the column existed, and every answer
        # landed in `data` as a top-level key of that name.
        "source_of_wealth_detail": None,
        # Asked on Additional information, each on its own. The certification date is
        # stamped below, and a pair that disagrees is refused.
        "us_person": "boolean",
        "is_vulnerable": "boolean",
        "vulnerability_reasons": "text[]",
        "vulnerability_characteristics": "text[]",
        "vulnerability_notes": None,
        "fatca_status": None,
        "fatca_certified_at": "date",
        # `pep` is deliberately ABSENT and stays in `data` — 0116 says why: a self-declaration and
        # a screening outcome are different facts, and `crm.contacts` is readable by the client
        # themselves where `aml.*` deliberately is not.
    }
    sets: dict[str, object] = {}
    data_patch: dict[str, object] = {}
    # Per jsonb column: optional whole-object value + relative dotted paths (e.g. "line1").
    jsonb_whole: dict[str, object] = {}
    jsonb_paths: dict[str, dict[str, object]] = {c: {} for c in _JSONB_COLUMNS}
    for key, value in patch.items():
        root = key.split(".", 1)[0]
        if key in columns:
            if columns[key] == "text[]":
                sets[key] = _as_text_array(value)
            elif columns[key] == "boolean" and key == "is_vulnerable":
                sets[key] = _us_person_answer(value)
            elif key in _NORMALISERS:
                sets[key] = _NORMALISERS[key](value)
            else:
                sets[key] = value
        elif root in _JSONB_COLUMNS:
            if key == root:
                jsonb_whole[root] = value
            else:
                jsonb_paths[root][key[len(root) + 1 :]] = value
        else:
            data_patch[key] = value

    # contacts_vulnerability_coherent (0054) and contacts_vulnerability_characteristics_coherent
    # (0129): a driver or a characteristic without the flag is rejected. The reasons question is
    # required on the form, so a submission that never ticked the checkbox still carries an answer
    # for it; that answer is not a decision that the client is vulnerable.
    if sets.get("is_vulnerable") is not True:
        sets.pop("vulnerability_reasons", None)
        sets.pop("vulnerability_characteristics", None)

    if fill_blanks_only:
        await _omit_held_facts(session, contact_id, sets, data_patch, jsonb_whole, jsonb_paths)

    if "gender" in sets and sets["gender"] not in {None, ""}:
        if str(sets["gender"]) not in {"male", "female"}:
            raise ValueError("Legal gender on this form is Male or Female.")

    await _apply_national_insurance(session, tenant_id, contact_id, sets)
    await _apply_extra_national_insurance(session, tenant_id, contact_id, data_patch)

    if "us_person" in sets:
        answered = _us_person_answer(sets["us_person"])
        if answered is None:
            raise ValueError("Is a US person? is Yes or No.")
        sets["us_person"] = answered
    if "fatca_status" in sets:
        held = str(sets["fatca_status"]).strip().lower()
        if held not in _FATCA_STATUSES:
            raise ValueError("Pick W-9 or W-8.")
        sets["fatca_status"] = held
        # SEI enrolment sends this code as the client's FATCA certification. The date moves only
        # when the answer changes, so a W-8 given again keeps the date it was first given.
        on_record = (
            await session.execute(
                text("SELECT fatca_status, fatca_certified_at FROM crm.contacts WHERE id = :c"),
                {"c": contact_id},
            )
        ).one_or_none()
        if (
            on_record is None
            or on_record.fatca_status != held
            or on_record.fatca_certified_at is None
        ):
            sets["fatca_certified_at"] = date.today()
    if "us_person" in sets or "fatca_status" in sets:
        await _refuse_incoherent_fatca(session, contact_id, sets)

    if "dob" in sets:
        dob_raw = sets["dob"]
        dob = None
        if isinstance(dob_raw, date):
            dob = date(dob_raw.year, dob_raw.month, dob_raw.day)
        elif dob_raw not in {None, ""}:
            try:
                dob = date.fromisoformat(str(dob_raw)[:10])
            except ValueError:
                dob = None
        products = (
            (
                await session.execute(
                    text(
                        "SELECT COALESCE(p.name, p.product_type::text) AS name, "
                        "       p.min_age, p.max_age "
                        "FROM crm.account_contacts ac "
                        "JOIN platform.accounts a ON a.id = ac.account_id "
                        "JOIN platform.products p ON p.id = a.product_id "
                        "WHERE ac.contact_id = :c AND ac.role = 'owner' "
                        "  AND (p.min_age IS NOT NULL OR p.max_age IS NOT NULL)"
                    ),
                    {"c": contact_id},
                )
            )
            .mappings()
            .all()
        )
        for product in products:
            err = catalogue.product_age_error(
                name=str(product["name"]),
                min_age=int(product["min_age"]) if product["min_age"] is not None else None,
                max_age=int(product["max_age"]) if product["max_age"] is not None else None,
                dob=dob,
            )
            if err:
                raise ValueError(err)

    assignments = ", ".join(
        f"{c} = CAST(:{c} AS {columns[c]})" if columns[c] else f"{c} = :{c}" for c in sets
    )
    params: dict[str, object] = {"cid": contact_id, "t": tenant_id, **sets}
    clauses = [assignments] if assignments else []

    jsonb_needed = [c for c in _JSONB_COLUMNS if c in jsonb_whole or jsonb_paths[c]]
    if data_patch or jsonb_needed:
        # One read for data + the jsonb columns we are about to merge — same advisory-lock safety
        # as the data-only path (orchestrator holds the execution lock).
        cols_sql = ", ".join(
            ["COALESCE(data, '{}'::jsonb) AS data"]
            + [f"COALESCE({c}, '{{}}'::jsonb) AS {c}" for c in jsonb_needed]
        )
        row: Any = (
            (
                await session.execute(
                    text(f"SELECT {cols_sql} FROM crm.contacts WHERE id = :c"),  # noqa: S608
                    {"c": contact_id},
                )
            )
            .mappings()
            .one_or_none()
        ) or {}

        for col in jsonb_needed:
            aliases = _BANK_LEAF_ALIASES if col == "bank_account" else {}
            merged = _merge_jsonb_column(
                dict(row.get(col) or {}),
                whole=jsonb_whole.get(col),
                paths=jsonb_paths[col],
                leaf_aliases=aliases,
            )
            if col == "bank_account" and not (
                fill_blanks_only and any(_held(v) for v in dict(row.get(col) or {}).values())
            ):
                # Filling a blank leaf on a bank account that already exists must not drop the
                # other rail. The exclusive-rail rule is for a submission that is replacing it.
                merged = _exclusive_bank_rail(merged, jsonb_paths[col])
            if col == "bank_account":
                merged = await _confirm_bank_column(
                    row.get(col), merged, user_id=await bank_check.acting_user(session)
                )
            clauses.append(f"{col} = CAST(:{col} AS jsonb)")
            params[col] = json.dumps(merged)

        if data_patch:
            # A dotted target NESTS. `pension.employerContributions.sortCode` means a path into
            # `data`, which is where the legacy app kept exactly these answers, and a flat merge
            # would instead create a top-level key with dots in its NAME — readable by nothing.
            #
            # Read-modify-write rather than SQL. `jsonb_set(create_missing => true)` creates only
            # the FINAL key of a path, so setting `{pension,employerContributions,name}` on a row
            # with no `pension` yet silently changes nothing; and a plain `||` merge is shallow, so
            # writing a nested object would REPLACE any existing `pension` wholesale and drop what
            # an earlier step in the same journey put there. Merging in Python is the version that
            # is obviously correct, and it is safe here because the orchestrator holds the
            # execution's advisory lock.
            merged_data: dict[str, Any] = dict(row.get("data") or {})
            for key, value in data_patch.items():
                node = merged_data
                *parents, leaf = key.split(".")
                for part in parents:
                    child = node.get(part)
                    # A parent that is not an object (a scalar left by an older shape) is replaced
                    # rather than crashed on — the path the author asked for is what must exist.
                    node[part] = child if isinstance(child, dict) else {}
                    node = node[part]
                node[leaf] = value
            clauses.append("data = CAST(:data AS jsonb)")
            params["data"] = json.dumps(merged_data)
    if not clauses:
        return
    # `clauses` interpolates ONLY hard-coded column names from the `columns` whitelist (and fixed
    # jsonb clauses); every value is a bound parameter — no user input reaches the SQL text.
    await session.execute(
        text(f"UPDATE crm.contacts SET {', '.join(clauses)} WHERE id = :cid AND tenant_id = :t"),  # noqa: S608
        params,
    )


# --------------------------------------------------------------------------------------------------
# accept_terms / upload_document — document request items (fulfilled through the existing endpoints,
# which trip apply_step_event). Artifact creation is wired with the request-item plumbing in the
# document-fulfilment integration; the handler carries its config + preflight now.
# --------------------------------------------------------------------------------------------------
class AcceptTermsHandler(BaseHandler):
    type = "accept_terms"
    assignee_default = "client"
    _config_keys = frozenset({"document_uid", "content", "category", "title", "term_uid"})

    async def preflight(
        self, session: AsyncSession, tenant_id: int, config: dict[str, Any]
    ) -> list[str]:
        has_pdf = bool(config.get("document_uid"))
        has_copy = bool(str(config.get("content") or "").strip())
        has_term = bool(config.get("term_uid"))
        return (
            []
            if has_pdf or has_copy or has_term
            else ["Choose a library term, write the declaration, or choose a PDF to agree to."]
        )

    async def instantiate(self, ctx: StepContext) -> StepEvent | None:
        if ctx.request_id is None:
            # An account-scoped terms step without "one per owner" has nobody to ask. Block with
            # a reason so the journey shows why, instead of silently never completing.
            return _event(ctx, StepSignal.BLOCKED, reason="no_actor")
        term_uid = _as_str(ctx.config.get("term_uid"))
        if term_uid and ctx.participant_subject_id is not None:
            if await terms_svc.has_accepted_current(
                ctx.session, term_uid=term_uid, contact_id=ctx.participant_subject_id
            ):
                await _record_terms_acceptance(ctx)
                return _event(ctx, StepSignal.COMPLETED, reason="already_accepted")
        document_uid = _as_str(ctx.config.get("document_uid"))
        content = _as_str(ctx.config.get("content"))
        if term_uid and not document_uid and not content:
            term = await terms_svc.get_term(ctx.session, term_uid)
            if term is None or term.get("status") != "published":
                return _event(ctx, StepSignal.BLOCKED, reason="term_missing")
            kind = term.get("published_body_kind") or term.get("body_kind")
            name = str(ctx.config.get("title") or term["title"])
            html = (
                str(term.get("published_body_html") or "").strip()
                or str(term.get("body_md") or "").strip()
            )
            if kind == "html" and html:
                document_uid = await _store_inline_terms(
                    ctx.session,
                    ctx.tenant_id,
                    name=name,
                    content=html,
                    term_uid=term_uid,
                    term_version_uid=term.get("current_version_uid"),
                )
            elif kind == "pdf":
                document_uid = await _store_terms_pdf(
                    ctx.session, ctx.tenant_id, name=name, term=term
                )
                if document_uid is None:
                    # The version names a file this firm's store cannot serve. Hold, with the
                    # reason, rather than ask the client to acknowledge a document they cannot
                    # open — that is the failure the dialog reported as "couldn't load".
                    return _event(ctx, StepSignal.BLOCKED, reason="term_file_unavailable")
        if not document_uid and content:
            document_uid = await _store_inline_terms(
                ctx.session,
                ctx.tenant_id,
                name=str(ctx.config.get("title") or ctx.step_key or "Declaration"),
                content=content,
                term_uid=term_uid,
            )
        await _create_request_item(
            ctx,
            # checkbox-now, signature-later: the client acknowledges the declaration (an e-signature
            # step can supersede this with action 'sign'). 'agree' is not a valid item action.
            action="acknowledge",
            title=str(ctx.config.get("title") or "Accept the terms"),
            document_uid=document_uid,
            category=_as_str(ctx.config.get("category")),
        )
        return None

    async def handle(self, ctx: StepContext, detail: dict[str, Any]) -> StepEvent:
        term_uid = _as_str(ctx.config.get("term_uid"))
        if term_uid and ctx.participant_subject_id is not None:
            term = await terms_svc.get_term(ctx.session, term_uid)
            version_id = term.get("current_published_version_id") if term else None
            try:
                await terms_svc.accept_term(
                    ctx.session,
                    term_uid,
                    subject="contact",
                    tenant_id=ctx.tenant_id,
                    contact_id=ctx.participant_subject_id,
                    user_id=None,
                    accepted_by=None,
                    channel="in_person" if detail.get("in_person") else "portal",
                    application_id=None,
                    attested_by=str(detail["attested_by"]) if detail.get("attested_by") else None,
                    version_id=int(version_id) if version_id is not None else None,
                    # The signed copy the adviser uploaded (the in-person route requires one), so
                    # the register row points at its own evidence.
                    evidence_document_id=(
                        str(detail["evidence_document_id"])
                        if detail.get("evidence_document_id")
                        else None
                    ),
                )
            except terms_svc.TermsError:
                # Already accepted this version, or the term was archived mid-journey — the
                # step still completes; the register is the source of truth.
                pass
        await _record_terms_acceptance(ctx)
        return StepEvent(
            signal=StepSignal.COMPLETED,
            case_id=ctx.case_id,
            step_key=ctx.step_key,
            participant_subject_id=ctx.participant_subject_id,
            # Internal orchestration hint: one catalogue acceptance settles every old, already
            # instantiated account row for this client and term. Inline/PDF steps deliberately
            # carry no hint because their copy may be account-specific.
            detail={"catalogue_term_uid": term_uid} if term_uid else {},
        )


class UploadDocumentHandler(BaseHandler):
    type = "upload_document"
    assignee_default = "client"
    # `title` is accepted by definitions.STEP_TYPES and read below — keep the two lists identical.
    _config_keys = frozenset({"category", "format_hint", "title"})

    async def instantiate(self, ctx: StepContext) -> StepEvent | None:
        if ctx.request_id is None:
            return _event(ctx, StepSignal.BLOCKED, reason="no_actor")
        category = _as_str(ctx.config.get("category"))
        item_id = await _create_request_item(
            ctx,
            action="upload",
            title=str(ctx.config.get("title") or "Upload a document"),
            category=category,
            format_hint=_as_str(ctx.config.get("format_hint")),
        )
        held = _HELD_UPLOADS.get(category or "")
        document_id = await held(ctx.session, ctx.request_id) if held is not None else None
        if document_id is None:
            return None
        # The client's record already holds this document: the item is answered with it, as a
        # reused KYC pass completes its step, and the client is not asked again.
        await ctx.session.execute(
            text(
                "UPDATE documents.request_items SET status = 'fulfilled', "
                "  fulfilled_document_id = :d, fulfilled_at = now() WHERE id = :i"
            ),
            {"d": document_id, "i": item_id},
        )
        return _event(ctx, StepSignal.COMPLETED, reason="already_held")


async def _held_fatca_form(session: AsyncSession, request_id: int) -> object | None:
    """The signed W-9 / W-8 the client's answer already points at (0261), if any.

    An archived file is not held, as on the client record: the step then asks the client."""
    return (
        await session.execute(
            text(
                "SELECT d.id FROM documents.requests r "
                "JOIN crm.contacts c ON c.id = r.contact_id AND c.tenant_id = r.tenant_id "
                "JOIN documents.documents d ON d.id = c.fatca_document_id "
                "WHERE r.id = :r AND c.fatca_status IN ('w9', 'w8') AND d.status <> 'archived'"
            ),
            {"r": request_id},
        )
    ).scalar_one_or_none()


#: Upload categories whose document may already be on the client's record when the step is raised,
#: and how to find it.
_HELD_UPLOADS: dict[str, Callable[[AsyncSession, int], Awaitable[object | None]]] = {
    "fatca_form": _held_fatca_form,
}


# --------------------------------------------------------------------------------------------------
# generate_document — render a template to a document via the existing render pipeline.
# --------------------------------------------------------------------------------------------------
class GenerateDocumentHandler(BaseHandler):
    type = "generate_document"
    assignee_default = "staff"
    # `title` names the generated document (read below) — declared here AND in
    # definitions.STEP_TYPES so the validator doesn't reject a config the handler reads.
    _config_keys = frozenset({"template_uid", "dataset", "category", "title"})

    async def preflight(
        self, session: AsyncSession, tenant_id: int, config: dict[str, Any]
    ) -> list[str]:
        template_uid = config.get("template_uid")
        if not template_uid:
            return ["Choose a template to generate."]
        published = (
            await session.execute(
                text(
                    "SELECT 1 FROM documents.template_versions tv "
                    "JOIN documents.templates t ON t.id = tv.template_id "
                    "WHERE t.uid = :u AND t.tenant_id = :tid "
                    "  AND tv.id = t.current_published_version_id"
                ),
                {"u": template_uid, "tid": tenant_id},
            )
        ).first()
        if published is None:
            return ["Its template has no published version to render."]
        return []

    async def instantiate(self, ctx: StepContext) -> StepEvent | None:
        """Create the document row and ENQUEUE its render job, then wait for the worker.

        The render pipeline is the same one staff generation uses (documents.documents 'queued' +
        a documents.render_jobs row drained by the DocumentWorker); the worker reports back through
        ``orchestrator.on_document_rendered``, which completes or fails this step. Without a
        published template version there is nothing to render, so the step FAILS loudly rather than
        waiting forever.
        """
        template_uid = _as_str(ctx.config.get("template_uid"))
        version = (
            (
                await ctx.session.execute(
                    text(
                        "SELECT t.current_published_version_id AS vid, t.document_type, "
                        "       t.name AS template_name "
                        "FROM documents.templates t "
                        "WHERE t.uid = :u AND t.tenant_id = :t"
                    ),
                    {"u": template_uid, "t": ctx.tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if version is None or version["vid"] is None:
            return _event(ctx, StepSignal.FAILED, reason="template_not_published")
        target_contact = ctx.participant_subject_id or (
            ctx.subject_id if ctx.subject_type == "contact" else None
        )
        if target_contact is None:
            # Bind the document to a real client even at account/application scope (the account's
            # contact, else the application's primary owner). Without it a generated suitability
            # report renders `{{ client_name }}` blank and every client-scoped dataset the template
            # draws on fails to resolve — the same "no actor resolved" dead end the account-scoped
            # fill_form had. An application-scoped step's case is the `group` case, whose subject is
            # null, so the application is reached through the execution.
            if ctx.subject_type == "account" and ctx.subject_id is not None:
                target_contact = (
                    await ctx.session.execute(
                        text(
                            "SELECT contact_id FROM platform.accounts "
                            "WHERE id = :i AND tenant_id = :t"
                        ),
                        {"i": ctx.subject_id, "t": ctx.tenant_id},
                    )
                ).scalar_one_or_none()
            else:
                target_contact = (
                    await ctx.session.execute(
                        text(
                            "SELECT ac.contact_id FROM crm.account_contacts ac "
                            "JOIN flows.executions e ON e.crm_application_id = ac.application_id "
                            "WHERE e.id = :e AND ac.tenant_id = :t AND ac.role = 'owner' "
                            "ORDER BY ac.id LIMIT 1"
                        ),
                        {"e": ctx.execution_id, "t": ctx.tenant_id},
                    )
                ).scalar_one_or_none()
                if target_contact is None:
                    # A person-subject journey has no account and no owner. Bind the letter to a
                    # party contact so {{ client_name }} is a person in the meeting, not blank.
                    target_contact = (
                        await ctx.session.execute(
                            text(
                                "SELECT ac.contact_id FROM crm.account_contacts ac "
                                "JOIN flows.executions e "
                                "  ON e.crm_application_id = ac.application_id "
                                "WHERE e.id = :e AND ac.tenant_id = :t AND ac.role = 'party' "
                                "ORDER BY ac.id LIMIT 1"
                            ),
                            {"e": ctx.execution_id, "t": ctx.tenant_id},
                        )
                    ).scalar_one_or_none()
        inputs = await letter_inputs(
            ctx.session, tenant_id=ctx.tenant_id, execution_id=ctx.execution_id
        )
        document_id = (
            await ctx.session.execute(
                text(
                    "INSERT INTO documents.documents "
                    "  (uid, tenant_id, document_type, source, name, status, inputs, metadata, "
                    "   template_version_id, intended_contact_id) "
                    "VALUES (:uid, :t, :dtype, 'generated', :name, 'queued', "
                    "  CAST(:inputs AS jsonb), "
                    "  CAST(:meta AS jsonb), :vid, :cid) RETURNING id"
                ),
                {
                    "uid": new_id(),
                    "t": ctx.tenant_id,
                    "dtype": version["document_type"],
                    "name": str(ctx.config.get("title") or version["template_name"]),
                    "meta": json.dumps(
                        {
                            "source": "flow",
                            "execution_id": ctx.execution_id,
                            "step_key": ctx.step_key,
                            "dataset": ctx.config.get("dataset"),
                        }
                    ),
                    "inputs": json.dumps(inputs),
                    "vid": version["vid"],
                    "cid": target_contact,
                },
            )
        ).scalar_one()
        await ctx.session.execute(
            text("INSERT INTO documents.render_jobs (tenant_id, document_id) VALUES (:t, :doc)"),
            {"t": ctx.tenant_id, "doc": document_id},
        )
        # NOT linked to the client here. `target_contact` is stored as `intended_contact_id` above,
        # and `worker._persist` files the client's 'subject' link once the render SUCCEEDS -- the
        # client branch of `documents_read` has no status predicate, so linking at enqueue time
        # would put a queued (or permanently failed) document in the client's portal with no
        # adviser having reviewed it.
        # The worker's callback finds the step by this id (orchestrator.on_document_rendered).
        await ctx.session.execute(
            text(
                "UPDATE flows.case_steps SET data = data || CAST(:d AS jsonb) "
                "WHERE case_id = :c AND step_key = :k "
                "  AND (CAST(:psid AS int) IS NULL OR participant_subject_id = CAST(:psid AS int))"
            ),
            {
                "d": json.dumps({"document_id": str(document_id)}),
                "c": ctx.case_id,
                "k": ctx.step_key,
                "psid": ctx.participant_subject_id,
            },
        )
        return _event(ctx, StepSignal.WAITING, reason="render_queued")


# --------------------------------------------------------------------------------------------------
# setup_account — the account already exists as a draft from journey kickoff. This step activates it
# in Graphene's book (draft → active) and stamps an ops-visible ``custodian_open`` marker on the
# account's data bag. It then enqueues ``client.setup`` and drains it
# (``app.actions.setup.settle_account_setup``). A pension whose product names Morgan Lloyd as
# scheme administrator gets a ``client.sync`` child. Custodian steps the plan cannot send are
# recorded and skipped; a later retry sends them. An administrator refusal is reported on
# ``data.platform_sync`` rather than failing the journey — staff retry from the account.
# --------------------------------------------------------------------------------------------------
class SetupAccountHandler(BaseHandler):
    type = "setup_account"
    assignee_default = "staff"
    _config_keys = frozenset({"provider", "product_id", "model_id", "fee_package_id", "role"})

    async def handle(self, ctx: StepContext, detail: dict[str, Any]) -> StepEvent:
        # Local book activation — the account case's subject is the platform.accounts row.
        if ctx.subject_type == "account" and ctx.subject_id is not None:
            await ctx.session.execute(
                text(
                    "UPDATE platform.accounts "
                    "SET status = CAST('active' AS platform.account_status), updated_at = now() "
                    "WHERE id = :a AND tenant_id = :t "
                    "  AND status = CAST('draft' AS platform.account_status)"
                ),
                {"a": int(ctx.subject_id), "t": ctx.tenant_id},
            )
            # Ops-visible seam until provider enrolment lands — merge, never replace the data bag
            # (kickoff already parks jisa_child / beneficiaries / regular_contribution here).
            marker = {
                "custodian_open": {
                    "status": "pending_ops",
                    "requested_at": datetime.now(UTC).isoformat(),
                    "local_activated": True,
                }
            }
            await ctx.session.execute(
                text(
                    "UPDATE platform.accounts "
                    "SET data = COALESCE(data, '{}'::jsonb) || CAST(:marker AS jsonb), "
                    "    updated_at = now() "
                    "WHERE id = :a AND tenant_id = :t"
                ),
                {
                    "a": int(ctx.subject_id),
                    "t": ctx.tenant_id,
                    "marker": json.dumps(marker),
                },
            )
            # The plan (client.setup) replaces the inline Morgan Lloyd call. A mock
            # session cannot load the account; settle reports the administrator as
            # skipped and the local activation still stands.
            from app.actions.setup import settle_account_setup

            config = ctx.config if isinstance(ctx.config, dict) else {}
            settled = await settle_account_setup(
                ctx.session,
                tenant_id=ctx.tenant_id,
                account_id=int(ctx.subject_id),
                config=config,
            )
            sync_report = settled.get("platform_sync") or {}
        else:
            settled = {"status": "succeeded"}
            sync_report = {}
        # Application-level hand-off stamp (not a custodian ack — see module note above).
        await ctx.session.execute(
            text(
                "UPDATE crm.applications a SET synced = true, synced_on = now() "
                "FROM flows.executions e "
                "WHERE e.id = :e AND a.id = e.crm_application_id"
            ),
            {"e": ctx.execution_id},
        )
        if settled.get("status") == "waiting" and settled.get("run_uid"):
            await ctx.session.execute(
                text(
                    "UPDATE flows.case_steps SET data = data || CAST(:d AS jsonb) "
                    "WHERE case_id = :c AND step_key = :k "
                    "  AND (CAST(:psid AS int) IS NULL "
                    "       OR participant_subject_id = CAST(:psid AS int))"
                ),
                {
                    "d": json.dumps({"action_run_uid": settled["run_uid"]}),
                    "c": ctx.case_id,
                    "k": ctx.step_key,
                    "psid": ctx.participant_subject_id,
                },
            )
            # A held plan is not a second hand-off. The journey reads this reason and
            # stops offering Record hand-off until the approval (or the send) settles.
            run_status = str(settled.get("run_status") or "")
            return StepEvent(
                signal=StepSignal.WAITING,
                case_id=ctx.case_id,
                step_key=ctx.step_key,
                participant_subject_id=ctx.participant_subject_id,
                reason="awaiting_approval"
                if run_status == "awaiting_approval"
                else "action_queued",
                detail={
                    "local_activated": True,
                    "custodian_open": "pending_ops",
                    "platform_sync": sync_report,
                    "action_run_uid": settled["run_uid"],
                },
            )
        return StepEvent(
            signal=StepSignal.COMPLETED,
            case_id=ctx.case_id,
            step_key=ctx.step_key,
            participant_subject_id=ctx.participant_subject_id,
            reason="account_activated",
            detail={
                "local_activated": True,
                "custodian_open": "pending_ops",
                "platform_sync": sync_report,
            },
        )


# --------------------------------------------------------------------------------------------------
# action nodes — unattended engine work sitting inline in a journey (`kind: "action"`)
# --------------------------------------------------------------------------------------------------
#
# An action node is NOT a work item. Nobody is assigned it, nothing waits for a person, and it never
# appears in a queue: the engine performs it the moment its prerequisites settle and the case moves
# on. It is a step-shaped row purely so the thing that happened is visible on the journey and so a
# failure has somewhere to live.
#
# THE FAILURE DECISION (and why it is not `failed` on the case). A failing action emits
# ``StepSignal.FAILED``, which persists the STEP as `failed` and — through the existing derivation
# rule 4 (``app/flows/derivation.py``) — parks the CASE at `blocked`. Deliberately not the case's
# terminal `failed` state, which is latched: a latched case cannot be retried, and
# ``orchestrator.retry_step`` (which accepts exactly `failed`/`blocked` steps) is the escape hatch
# an operator needs, because most action failures are transient — a mail provider blip, a template
# unpublished mid-flight, an endpoint down. So the journey stops where a human can see it, with the
# reason on the step, and one click re-runs it. Silence was never an option: an action that quietly
# did nothing and let the case walk past is the failure mode this whole tier has to not have.
#
# THE SAVEPOINT. The action runs inside ``session.begin_nested()``. A refusal or a fault rolls back
# to it, so a half-applied action (a request row with no items, a document with no render job)
# cannot survive; the FAILED status is then written by the orchestrator OUTSIDE the savepoint, which
# is what makes "the effect rolled back but the failure is recorded" true rather than a hope.


class ActionStepHandler(BaseHandler):
    """One action type, as a flow node. Registered per type under ``handlers.action_step_type``."""

    # Never 'client': the column's vocabulary is client|staff, and an action is neither — 'staff'
    # is the one that keeps it out of a client's portal. It completes at instantiate, so it never
    # sits in a staff queue either.
    assignee_default = "staff"

    def __init__(self, action_type: str) -> None:
        self.action_type = action_type
        self.type = handlers_mod.action_step_type(action_type)
        self._config_keys = graph_mod.ACTION_CONFIG_KEYS.get(action_type, frozenset())

    async def preflight(
        self, session: AsyncSession, tenant_id: int, config: dict[str, Any]
    ) -> list[str]:
        """Nothing here on purpose: an action node's publish checks (its permission, its template,
        its endpoint key) are ``effects.preflight``'s, and the router's publish path asks it
        directly (``routers/flows.py::_automation_issues``) so the message can name the ACTION
        rather than call it a step. Answering here as well would print every refusal twice."""
        return []

    async def instantiate(self, ctx: StepContext) -> StepEvent | None:
        # Imported here rather than at module scope: the executor reaches into tasks, contacts,
        # tags, email and (lazily) the documents service, and this module is imported for its
        # registration side-effect by ``app.flows`` itself.
        from app.flows import effects

        automation = await _action_context(ctx, self.action_type)
        spec = {"type": self.action_type, "config": dict(ctx.config)}
        savepoint = await ctx.session.begin_nested()
        try:
            result = await effects.execute(ctx.session, automation, spec, source="flow")
        except Exception as exc:  # noqa: BLE001 — a fault must fail the STEP, not the transaction
            await savepoint.rollback()
            report_error(
                "a flow action step faulted; the case is parked for retry",
                exc=exc,
                log=logger,
                event="flow_action_failed",
                tenant_id=ctx.tenant_id,
                case_id=ctx.case_id,
                step_key=ctx.step_key,
                action_type=self.action_type,
            )
            return _event(ctx, StepSignal.FAILED, reason=f"action_failed: {exc}"[:500])
        if not result.ok:
            # A considered refusal (no template, nobody to email, an endpoint that is gone). The
            # reason is the author's, so it goes on the step verbatim — that string is what the
            # journey shows the operator who has to fix it.
            await savepoint.rollback()
            return _event(ctx, StepSignal.FAILED, reason=f"action_refused: {result.detail}"[:500])
        await savepoint.commit()
        return _event(ctx, StepSignal.COMPLETED, reason=result.detail)


async def _action_context(ctx: StepContext, action_type: str) -> AutomationContext:
    """The firing an action node's execution runs as.

    One query, because an action node is instantiated once: the case's uid (actions that act on a
    case need it), the flow's code and this node's title (the audit event's actor is named
    ``flow:{code}:{node}`` and labelled with the node's own title, so the trail says "Tag as
    onboarded" rather than an opaque id), and the chain depth of the firing that CREATED this case.

    That last one is READ, never counted again here: the cap is A1's and the execution's own depth
    is threaded by the orchestrator (``flows.executions.data.chain_depth``), so this takes the
    larger of that and the depth of the automation firing that opened the case (A1's delivery
    ledger). If an automation started this journey, everything the journey does is a link in that
    automation's chain and the events these nodes emit must say so — otherwise the self-trigger
    guard's depth cap is walked around simply by putting the action inside a flow. A human-started
    case has neither and is depth 0.

    ``facts`` is the RUNTIME context (``orchestrator.case_context``) — the same mapping the branch
    node beside this one is evaluated against, memory included. It used to be empty, which meant
    an action node's copy could name nothing: ``{{ contact.service_type }}-client`` in a tag and
    ``{{ memory.risk_band }}`` in an email both rendered blank on this path while working on the
    rule tier's, and the step-level ``tasks`` side-effect (which reads the same
    ``case_context``) already behaved the third way. One context, one vocabulary, wherever the
    action is authored.
    """
    row: dict[str, Any] = dict(
        (
            await ctx.session.execute(
                text(
                    "SELECT c.uid AS case_uid, f.code AS flow_code, f.name AS flow_name, "
                    "       cs.title AS step_title, "
                    "       GREATEST("
                    "         CASE WHEN e.data ->> 'chain_depth' ~ '^[0-9]+$' "
                    "              THEN (e.data ->> 'chain_depth')::int ELSE 0 END, "
                    "         COALESCE((SELECT MAX(d.chain_depth) "
                    "                     FROM flows.automation_deliveries d "
                    "                    WHERE d.tenant_id = c.tenant_id "
                    "                      AND d.case_uid = c.uid), 0)) AS depth "
                    "FROM flows.cases c "
                    "LEFT JOIN flows.executions e ON e.id = c.execution_id "
                    "LEFT JOIN flows.flows f ON f.id = e.flow_id "
                    "LEFT JOIN flows.case_steps cs ON cs.case_id = c.id AND cs.step_key = :k "
                    "WHERE c.id = :c"
                ),
                {"c": ctx.case_id, "k": ctx.step_key},
            )
        )
        .mappings()
        .one_or_none()
        or {}
    )
    code = row.get("flow_code") or "flow"
    contact_id = ctx.participant_subject_id or (
        ctx.subject_id if ctx.subject_type == "contact" else None
    )
    # Lazily, for the reason the executor import below is lazy: this module is imported by
    # `app.flows` for its registration side-effect, and the orchestrator sits at the centre of the
    # engine's import graph.
    from app.flows import orchestrator as orchestrator_mod

    facts = await orchestrator_mod.case_context(
        ctx.session, tenant_id=ctx.tenant_id, case_id=ctx.case_id
    )
    return AutomationContext(
        tenant_id=ctx.tenant_id,
        # There is no automations row behind a flow node, and inventing an id that looked like one
        # would send an operator hunting for a rule that does not exist. 0 + a `flow:` uid says
        # what this is.
        automation_id=0,
        automation_uid=f"flow:{code}:{ctx.step_key}",
        automation_name=str(row.get("step_title") or row.get("flow_name") or action_type),
        # Not a real trigger key: nothing fired: the engine reached this node. Named so it reads
        # honestly in a delivery/audit row, and prefixed `case.` because that is what it is about.
        trigger_key="case.step_reached",
        event_id=None,
        event_type=None,
        subject_kind=("contact" if contact_id is not None else ctx.subject_type),
        subject_id=contact_id if contact_id is not None else ctx.subject_id,
        case_uid=str(row["case_uid"]) if row.get("case_uid") else None,
        chain_depth=int(row.get("depth") or 0),
        actor_id=None,
        config={},
        facts=facts,
    )


def register_all() -> None:
    """Register every concrete handler. Idempotent-safe only once (register() rejects dupes), so it
    is called exactly once at import."""
    register(TaskHandler())
    register(ReviewDecisionHandler())
    register(ReviewHandler("verify_identity"))
    register(AmlCheckHandler())
    register(FillFormHandler())
    register(AcceptTermsHandler())
    register(UploadDocumentHandler())
    register(GenerateDocumentHandler())
    register(SetupAccountHandler())
    # One handler per action type in the contract — including the ones this build cannot run
    # (`lookup`, Phase D). Registering it and letting the executor refuse at runtime is the honest
    # shape: publish preflight already refuses to publish it, so the only way to reach the refusal
    # is a definition published by a future build and run by an older one.
    for action_type in sorted(graph_mod.ACTION_CONFIG_KEYS):
        register(ActionStepHandler(action_type))


register_all()
