"""Non-production tenant operational-data reset and deterministic demo seed jobs.

Configuration is an explicit allowlist.  Anything tenant-scoped that is neither
preserved nor cleared makes the reset refuse before deleting a row; this turns a
new migration into a required decision instead of accidental data loss.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import datalake, storage, tenant_storage
from app.config import settings
from app.lake import evict_tenant

logger = logging.getLogger(__name__)

JobKind = Literal["reset", "reset_and_seed", "seed_book"]

# Configuration, identity, authoring, and evidence.  The reset may special-case
# client-only auth users, but it never blanket-deletes an auth table.
PRESERVED_TABLES = frozenset(
    {
        "audit.actions",
        "audit.page_views",
        "audit.api_log",
        "audit.log",
        "auth.api_keys",
        "auth.organisation_memberships",
        "auth.roles",
        # The live WorkOS session mirror. It follows the user: a reset keeps the rows, and
        # deleting a client login cascades them. It is not operational book data.
        "auth.sessions",
        "auth.tenant_environments",
        "auth.users",
        "content.categories",
        "content.item_versions",
        "content.items",
        # The book catalogue is a firm's configuration, not its data: it names the portal designs
        # and the money-movement capability. A reset that cleared it would leave the templates
        # pointing at slugs that no longer exist. The per-contact pin goes with crm.contacts.
        "crm.client_classifications",
        # The group catalogue is configuration, like the books: it names the
        # streams a portal design is assigned to. Membership goes with the
        # contacts (crm.contact_groups, cleared).
        "crm.client_groups",
        "documents.dataset_versions",
        "documents.datasets",
        "documents.document_types",
        "documents.form_fields",
        "documents.forms",
        "documents.template_versions",
        "documents.templates",
        "email.template_versions",
        "email.templates",
        # The firm's Resend registration and chosen From address. Clearing it would drop the firm
        # back to the platform sender and leave the domain registered at Resend with no row.
        "email.sender_domains",
        "flows.ai_tenants",
        "flows.ai_usage",
        "flows.automation_settings",
        "flows.automations",
        "flows.domain_events",
        "flows.flow_versions",
        "flows.flows",
        # Graphene-ops config, not per-tenant RLS data (0231_integrations_schema's own header: "not
        # tenant data in the RLS sense"). `tenant_id`/`tenant_name` on `integrations.platforms` are
        # the SEI PLATFORM's own tenant concept -- the string sei_reports and this screen both key
        # on -- not a row scoped to one of OUR `auth.tenants`, so a reset of one of ours must not
        # touch these. `firm_id` (0273_integrations_tenant) IS now a real FK to `auth.tenants`, but
        # that only names which of OUR firms the row is ABOUT, same as any other admin-ops record
        # with a foreign tenant reference -- it does not make the row itself tenant data.
        "integrations.income_statement_items_sent",
        "integrations.income_statements",
        "integrations.platforms",
        "integrations.transactions_sent",
        "l10n.overrides",
        "notifications.preferences",
        "notifications.rule_versions",
        "notifications.rules",
        "notifications.tenant_config",
        # Which actions a firm has switched on, who may run them, and which roles
        # must approve. A reset that cleared it would turn every action back off.
        "actions.policies",
        "platform.fee_package_platforms",
        "platform.fee_packages",
        # The GIA/ISA illustration fee schedule is firm configuration, like a fee package.
        # A reset that cleared it would leave the next illustration with nothing to copy.
        "platform.illustration_fee_schedules",
        "platform.model_allocations",
        "platform.model_commentary",
        "platform.model_platforms",
        "platform.model_versions",
        "platform.models",
        "platform.merge_rejects",
        "platform.merge_runs",
        "platform.product_platforms",
        "platform.products",
        "platform.tenant_data_jobs",
        "platform.tenant_platforms",
        "platform.third_party_providers",
        "platform.transfer_providers",
        "storage.tenant_secrets",
        "storage.tenant_service_user_secrets",
        "storage.tenant_service_users",
        "storage.tenants",
        "storage.user_credentials",
        # A member of staff's own dashboard notes are theirs, not the book's: a reset of the
        # book is not a reason to lose someone's "call Joan". They go with the user (cascade).
        "platform.staff_notes",
        # The firm's legal catalogue is authoring, like content: the header and its published
        # versions survive a reset. They have to — a published version is immutable and the
        # acceptances that pin it are evidence, so clearing the catalogue would strand the
        # `accept_terms` steps in every seeded flow on rows that no longer exist.
        "terms.term_versions",
        "terms.terms",
    }
)

CLEAR_TABLES = frozenset(
    {
        # A baton run is a job against the book the reset is deleting. The run, its attempts, the
        # thin event stream and the subject links go with it. The firm policy stays.
        "baton.events",
        "baton.attempts",
        "baton.runs",
        "baton.subjects",
        # A staff member's read-only view-as session names a contact (cascades with it) — the
        # audit trail, not this row, is the durable record that it happened.
        "auth.view_as_sessions",
        # The one-time link for identity photos. It names a contact and a case the reset is
        # deleting, and the audit trail — not this row — is the record that the link was made.
        "auth.identity_phone_links",
        "comms.message_events",
        "comms.messages",
        # Screening rows. Operational: removing the client removes the checks that were run on
        # them. Unmatched screening rows have a null tenant and stay.
        "compliance.addresses",
        "compliance.checks",
        "compliance.client_notes",
        "compliance.client_risk",
        "compliance.clients",
        "compliance.documents",
        "compliance.events",
        # A client's identity photos, held only until their identity step finishes.
        "compliance.identity_photos",
        "compliance.live_photos",
        "compliance.matches",
        "compliance.webhook_deliveries",
        "compliance.webhooks",
        "crm.account_contacts",
        "crm.applications",
        "crm.beneficiaries",
        "crm.client_advisers",
        "crm.contact_groups",
        "crm.contact_notes",
        "crm.contact_tags",
        "crm.contact_users",
        "crm.contacts",
        "crm.kyc_checks",
        "crm.relationships",
        "documents.attachments",
        "documents.audit_log",
        "documents.document_contacts",
        "documents.documents",
        "documents.files",
        "documents.form_responses",
        "documents.render_jobs",
        "documents.request_items",
        "documents.requests",
        "documents.shares",
        "flows.automation_deliveries",
        "flows.case_listeners",
        "flows.case_memory",
        "flows.case_steps",
        "flows.case_tags",
        "flows.cases",
        "flows.executions",
        "flows.outbox",
        "flows.public_start_throttle",
        "flows.timers",
        "messaging.attachments",
        "messaging.conversations",
        "messaging.group_members",
        "messaging.groups",
        "messaging.messages",
        "notifications.notifications",
        "platform.account_fee_packages",
        "platform.accounts",
        "platform.contributions",
        "platform.illustrations",
        "platform.instructions",
        "platform.links",
        "platform.nodes",
        "platform.portfolios",
        "platform.transaction_expectations",
        "platform.transactions",
        # A SEI payment that may already have left. It names an account the reset
        # is deleting, and it must not survive to block the next payment.
        "platform.sei_money_holds",
        "platform.transfers",
        "platform.valuations",
        "storage.tenant_usage",
        "tasks.tasks",
        # Who agreed to what, on the other hand, is operational evidence about the people the reset
        # is removing — the same class of record as `crm.kyc_checks`, whose `kind = 'terms'` tick is
        # derived from these rows.
        "terms.acceptances",
    }
)


async def tenant_tables(session: AsyncSession) -> set[str]:
    """Current base tables carrying tenant_id, excluding dynamic inbound staging.

    Staging is one schema per custodian (`inbound_seccl` and the rest, migration 0209) plus the
    old shared `inbound`. Those tables are created by the pipeline, not by a migration, so a
    manifest that only names migrated tables can never list them — and must not fail when they
    exist.
    """
    return set(
        (
            await session.execute(
                text(
                    "SELECT table_schema || '.' || table_name "
                    "FROM information_schema.columns c "
                    "WHERE c.column_name = 'tenant_id' "
                    "  AND c.table_schema <> 'inbound' "
                    "  AND c.table_schema NOT LIKE 'inbound\\_%' "
                    "  AND EXISTS (SELECT 1 FROM information_schema.tables t "
                    "    WHERE t.table_schema=c.table_schema AND t.table_name=c.table_name "
                    "      AND t.table_type='BASE TABLE')"
                )
            )
        )
        .scalars()
        .all()
    )


async def assert_manifest_complete(session: AsyncSession) -> None:
    actual = await tenant_tables(session)
    classified = PRESERVED_TABLES | CLEAR_TABLES
    unknown = sorted(actual - classified)
    stale = sorted(classified - actual)
    if unknown or stale:
        parts = []
        if unknown:
            parts.append("unclassified tenant tables: " + ", ".join(unknown))
        if stale:
            parts.append("reset manifest names missing tables: " + ", ".join(stale))
        raise RuntimeError("; ".join(parts))


async def _tenant(session: AsyncSession, tenant_id: int) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, alias, name, enabled, is_model FROM auth.tenants WHERE id=:t"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("tenant not found")
    return dict(row)


async def enqueue(
    session: AsyncSession,
    *,
    tenant_id: int,
    kind: JobKind,
    confirm_alias: str,
    requested_by: str | None,
) -> dict[str, Any]:
    tenant = await _tenant(session, tenant_id)
    if settings.app_stage == "production":
        raise ValueError("tenant data reset is not available in production")
    if tenant["is_model"]:
        raise ValueError("the model tenant cannot be reset")
    if kind in {"reset", "reset_and_seed"} and tenant["enabled"]:
        raise ValueError("suspend the tenant before resetting its data")
    if confirm_alias != tenant["alias"]:
        raise ValueError("the confirmation does not match this firm's alias")

    pause_reason = "demo book seed" if kind == "seed_book" else "tenant data reset"
    await session.execute(
        text(
            "UPDATE platform.tenant_platforms "
            "SET refresh_paused_at=COALESCE(refresh_paused_at, now()), "
            "    refresh_pause_reason=:reason "
            "WHERE tenant_id=:t"
        ),
        {"t": tenant_id, "reason": pause_reason},
    )
    try:
        row = (
            (
                await session.execute(
                    text(
                        "INSERT INTO platform.tenant_data_jobs "
                        "  (tenant_id, kind, requested_by) "
                        "VALUES (:t, :kind, :actor) RETURNING *"
                    ),
                    {"t": tenant_id, "kind": kind, "actor": requested_by},
                )
            )
            .mappings()
            .one()
        )
    except IntegrityError as exc:
        raise ValueError("a reset or demo seed is already running for this tenant") from exc
    return dict(row)


async def latest(session: AsyncSession, tenant_id: int) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT j.*, COALESCE(bool_or(tp.refresh_paused_at IS NOT NULL), false) "
                    "       AS providers_paused "
                    "FROM platform.tenant_data_jobs j "
                    "LEFT JOIN platform.tenant_platforms tp ON tp.tenant_id=j.tenant_id "
                    "WHERE j.tenant_id=:t GROUP BY j.id ORDER BY j.created_at DESC LIMIT 1"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def resume_refreshes(session: AsyncSession, tenant_id: int) -> int:
    job = await latest(session, tenant_id)
    if job and job["status"] in {"queued", "processing"}:
        raise ValueError("provider refreshes cannot resume while a reset is running")
    rows = (
        (
            await session.execute(
                text(
                    "UPDATE platform.tenant_platforms "
                    "SET refresh_paused_at=NULL, refresh_pause_reason=NULL "
                    "WHERE tenant_id=:t AND refresh_paused_at IS NOT NULL RETURNING id"
                ),
                {"t": tenant_id},
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


async def _delete_identities(session: AsyncSession, ids: list[Any]) -> int:
    """Delete these logins, or none of them: the batch is one savepoint."""
    savepoint = await session.begin_nested()
    try:
        await session.execute(
            text("DELETE FROM auth.organisation_memberships WHERE user_id=ANY(:ids)"),
            {"ids": ids},
        )
        await session.execute(text("DELETE FROM auth.users WHERE id=ANY(:ids)"), {"ids": ids})
    except DBAPIError:
        await savepoint.rollback()
        return 0
    await savepoint.commit()
    return len(ids)


async def _clear_client_identities(session: AsyncSession, tenant_id: int) -> int:
    """Remove client-only local identities; staff and operations identities survive.

    Runs AFTER the domain delete, not before it.  Roughly half the columns naming a
    login are ``ON DELETE NO ACTION`` -- ``documents.audit_log.actor_id`` is the one
    that fires first -- so a client's own records hold their login until those records
    have gone, and deleting the login first fails the whole reset.

    A login a PRESERVED table still names (a client who authored a template) is LEFT
    STANDING and counted out: a reset that has already emptied the book must not then
    refuse to finish, and the login without its records is the safer of the two ends.
    """
    ids = list(
        (
            await session.execute(
                text(
                    "SELECT u.id FROM auth.users u "
                    "WHERE u.tenant_id=:t AND 'client'=ANY(u.type) "
                    "  AND NOT ('staff'=ANY(u.type)) "
                    "  AND NOT EXISTS (SELECT 1 FROM auth.organisation_memberships m "
                    "                  WHERE m.user_id=u.id "
                    "                    AND m.portal IN ('platform','operations'))"
                ),
                {"t": tenant_id},
            )
        )
        .scalars()
        .all()
    )
    if not ids:
        return 0
    deleted = await _delete_identities(session, ids)
    if deleted == len(ids):
        return deleted
    for user_id in ids:
        deleted += await _delete_identities(session, [user_id])
    if deleted != len(ids):
        logger.warning(
            "tenant %s: %s client logins are still referenced and were kept",
            tenant_id,
            len(ids) - deleted,
        )
    return deleted


async def _clear_inbound(session: AsyncSession, uid: str) -> int:
    """Drop this firm's landing tables in `inbound` and each `inbound_{provider}` schema.

    `book_writer` owns them. The reset runs as `service_role`, which can read a landing table and
    cannot drop it, so the drop switches to the owner for these statements only. The session user
    (the worker's migrator login) is a member of `book_writer`; a test that wants this path grants
    the same membership.
    """
    rows = (
        await session.execute(
            text(
                "SELECT schemaname, tablename FROM pg_tables "
                "WHERE (schemaname = 'inbound' OR schemaname LIKE 'inbound\\_%') "
                "  AND tablename LIKE :prefix"
            ),
            {
                "prefix": uid.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "\\_\\_%"
            },
        )
    ).all()
    if not rows:
        return 0
    await session.execute(text("SET LOCAL ROLE book_writer"))
    try:
        for schema, name in rows:
            schema_s, name_s = str(schema), str(name)
            if not re.fullmatch(r"inbound(_[a-z0-9_]+)?", schema_s) or '"' in name_s:
                raise RuntimeError(f"refusing to drop {schema_s}.{name_s}")
            await session.execute(text(f'DROP TABLE "{schema_s}"."{name_s}"'))  # noqa: S608
    finally:
        await session.execute(text("SET LOCAL ROLE service_role"))
    return len(rows)


async def clear_database(session: AsyncSession, tenant_id: int, uid: str) -> dict[str, int]:
    await assert_manifest_complete(session)
    counts = {
        table: int(
            (
                await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t"),  # noqa: S608
                    {"t": tenant_id},
                )
            ).scalar_one()
        )
        for table in sorted(CLEAR_TABLES)
    }
    remaining = sorted(CLEAR_TABLES)
    while remaining:
        failed: list[str] = []
        for table in remaining:
            savepoint = await session.begin_nested()
            try:
                await session.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id=:t"),  # noqa: S608
                    {"t": tenant_id},
                )
            except DBAPIError:
                await savepoint.rollback()
                failed.append(table)
            else:
                await savepoint.commit()
        if len(failed) == len(remaining):
            raise RuntimeError("tenant rows are still referenced in: " + ", ".join(failed))
        remaining = failed
    counts["auth.client_users"] = await _clear_client_identities(session, tenant_id)
    counts["inbound.tables"] = await _clear_inbound(session, uid)
    return counts


async def _table_columns(session: AsyncSession, table: str) -> set[str]:
    schema, name = table.split(".", 1)
    return set(
        (
            await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=:schema AND table_name=:name"
                ),
                {"schema": schema, "name": name},
            )
        )
        .scalars()
        .all()
    )


def _demo_predicates(
    table: str,
    columns: set[str],
    *,
    contacts: list[Any],
    accounts: list[Any],
    apps: list[Any],
    executions: list[Any],
    cases: list[Any],
) -> list[str]:
    """WHERE fragments that name only the previous demo book, not the rest of the firm."""
    predicates: list[str] = []
    for column in columns:
        if contacts and (column == "contact_id" or column.endswith("_contact_id")):
            predicates.append(f"{column} = ANY(:contacts)")
        elif accounts and (column == "account_id" or column.endswith("_account_id")):
            predicates.append(f"{column} = ANY(:accounts)")
        elif apps and column in {"application_id", "crm_application_id"}:
            predicates.append(f"{column} = ANY(:apps)")
        elif executions and column == "execution_id":
            predicates.append("execution_id = ANY(:executions)")
        elif cases and column == "case_id":
            predicates.append("case_id = ANY(:cases)")
    if table == "crm.contacts" and contacts:
        predicates.append("id = ANY(:contacts)")
    elif table == "platform.accounts" and accounts:
        predicates.append("id = ANY(:accounts)")
    elif table == "crm.applications" and apps:
        predicates.append("id = ANY(:apps)")
    elif table == "flows.executions" and executions:
        predicates.append("id = ANY(:executions)")
    elif table == "flows.cases" and cases:
        predicates.append("id = ANY(:cases)")
    elif table == "platform.links" and (accounts or contacts):
        if accounts:
            predicates.append("(entity_type='account' AND entity_id = ANY(:accounts))")
        if contacts:
            predicates.append("(entity_type='contact' AND entity_id = ANY(:contacts))")
    if "data" in columns:
        predicates.append("COALESCE(data->>'seeded_demo','') = 'true'")
    return predicates


async def clear_demo_book(session: AsyncSession, tenant_id: int) -> dict[str, int]:
    """Remove the previous seeded_demo clients and their book, leaving everyone else.

    Used by ``seed_book`` so a firm can refresh Amelia/Noah and the lake holdings
    without wiping documents, real clients, staff or authored flows.
    """
    contact_ids = list(
        (
            await session.execute(
                text(
                    "SELECT id FROM crm.contacts "
                    "WHERE tenant_id=:t AND COALESCE(data->>'seeded_demo','') = 'true'"
                ),
                {"t": tenant_id},
            )
        )
        .scalars()
        .all()
    )
    account_ids = list(
        {
            *(
                await session.execute(
                    text(
                        "SELECT id FROM platform.accounts WHERE tenant_id=:t "
                        "AND COALESCE(data->>'seeded_demo','') = 'true'"
                    ),
                    {"t": tenant_id},
                )
            )
            .scalars()
            .all(),
            *(
                (
                    await session.execute(
                        text(
                            "SELECT id FROM platform.accounts "
                            "WHERE tenant_id=:t AND contact_id = ANY(:contacts)"
                        ),
                        {"t": tenant_id, "contacts": contact_ids},
                    )
                )
                .scalars()
                .all()
                if contact_ids
                else []
            ),
        }
    )
    application_ids = (
        list(
            (
                await session.execute(
                    text(
                        "SELECT application_id FROM platform.accounts "
                        "WHERE id = ANY(:accounts) AND application_id IS NOT NULL"
                    ),
                    {"accounts": account_ids},
                )
            )
            .scalars()
            .all()
        )
        if account_ids
        else []
    )
    execution_ids = list(
        {
            *(
                await session.execute(
                    text(
                        "SELECT id FROM flows.executions WHERE tenant_id=:t "
                        "AND COALESCE(data->>'seeded_demo','') = 'true'"
                    ),
                    {"t": tenant_id},
                )
            )
            .scalars()
            .all(),
            *(
                (
                    await session.execute(
                        text(
                            "SELECT id FROM flows.executions "
                            "WHERE tenant_id=:t AND crm_application_id = ANY(:apps)"
                        ),
                        {"t": tenant_id, "apps": application_ids},
                    )
                )
                .scalars()
                .all()
                if application_ids
                else []
            ),
        }
    )
    case_ids = list(
        {
            *(
                (
                    await session.execute(
                        text(
                            "SELECT id FROM flows.cases "
                            "WHERE tenant_id=:t AND subject_contact_id = ANY(:contacts)"
                        ),
                        {"t": tenant_id, "contacts": contact_ids},
                    )
                )
                .scalars()
                .all()
                if contact_ids
                else []
            ),
            *(
                (
                    await session.execute(
                        text(
                            "SELECT id FROM flows.cases "
                            "WHERE tenant_id=:t AND execution_id = ANY(:executions)"
                        ),
                        {"t": tenant_id, "executions": execution_ids},
                    )
                )
                .scalars()
                .all()
                if execution_ids
                else []
            ),
        }
    )
    if not any((contact_ids, account_ids, application_ids, execution_ids, case_ids)):
        return {}

    params = {
        "t": tenant_id,
        "contacts": contact_ids,
        "accounts": account_ids,
        "apps": application_ids,
        "executions": execution_ids,
        "cases": case_ids,
    }
    column_cache: dict[str, set[str]] = {}
    remaining = sorted(CLEAR_TABLES)
    counts: dict[str, int] = {}
    while remaining:
        failed: list[str] = []
        progressed = False
        for table in remaining:
            columns = column_cache.get(table)
            if columns is None:
                columns = await _table_columns(session, table)
                column_cache[table] = columns
            predicates = _demo_predicates(
                table,
                columns,
                contacts=contact_ids,
                accounts=account_ids,
                apps=application_ids,
                executions=execution_ids,
                cases=case_ids,
            )
            if not predicates:
                continue
            savepoint = await session.begin_nested()
            try:
                result = await session.execute(
                    text(
                        f"DELETE FROM {table} WHERE tenant_id=:t AND ({' OR '.join(predicates)})"  # noqa: S608
                    ),
                    params,
                )
            except DBAPIError:
                await savepoint.rollback()
                failed.append(table)
            else:
                await savepoint.commit()
                counts[table] = int(getattr(result, "rowcount", 0) or 0)
                progressed = True
        remaining = failed
        if remaining and not progressed:
            raise RuntimeError("demo book rows are still referenced in: " + ", ".join(remaining))
    return counts


async def _set_job(
    session: AsyncSession,
    job_id: int,
    *,
    phase: str,
    result: dict[str, Any] | None = None,
    status: str | None = None,
    error: str | None = None,
) -> None:
    await session.execute(
        text(
            "UPDATE platform.tenant_data_jobs "
            "SET phase=:phase, result=result || CAST(:result AS jsonb), "
            "    status=COALESCE(:status,status), error=:error, updated_at=now(), "
            "    completed_at=CASE WHEN :status IN ('completed','failed') THEN now() "
            "                      ELSE completed_at END "
            "WHERE id=:id"
        ),
        {
            "id": job_id,
            "phase": phase,
            "result": json.dumps(result or {}),
            "status": status,
            "error": error,
        },
    )


async def claim(session: AsyncSession) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "UPDATE platform.tenant_data_jobs "
                    "SET status='processing', attempts=attempts+1, "
                    "    claimed_at=now(), updated_at=now() "
                    "WHERE id=("
                    " SELECT id FROM platform.tenant_data_jobs "
                    " WHERE status='queued' OR (status='processing' "
                    "   AND claimed_at < now() - interval '15 minutes') "
                    " ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
                    ") RETURNING *"
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def run_claimed(maker: async_sessionmaker[AsyncSession], job: dict[str, Any]) -> None:
    """Run one claimed job, resuming after its last committed phase."""
    job_id = int(job["id"])
    tenant_id = int(job["tenant_id"])
    progress = {
        "queued": 0,
        "database": 0,
        "database_done": 1,
        "object_storage": 1,
        "object_storage_done": 2,
        "lake_done": 3,
        "seed_done": 4,
    }.get(str(job["phase"]), 0)
    try:
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            tenant = await _tenant(session, tenant_id)
            if progress < 1:
                await _set_job(session, job_id, phase="database")
        if progress < 1:
            async with maker() as session, session.begin():
                await session.execute(text("SET LOCAL ROLE service_role"))
                if job["kind"] == "seed_book":
                    counts = await clear_demo_book(session, tenant_id)
                else:
                    counts = await clear_database(session, tenant_id, str(tenant["uid"]))
                await _set_job(session, job_id, phase="database_done", result={"database": counts})

        if job["kind"] != "seed_book":
            if progress < 2:
                async with maker() as session, session.begin():
                    await session.execute(text("SET LOCAL ROLE service_role"))
                    store = await tenant_storage.get_tenant_store(session, tenant_id)
                    await _set_job(session, job_id, phase="object_storage")
                objects = 0
                if store is not None:
                    objects = await storage.delete_objects_except(
                        store.bucket,
                        preserve_prefixes=("files/tenant/",),
                        endpoint=store.endpoint,
                        region=store.region,
                    )
                async with maker() as session, session.begin():
                    await session.execute(text("SET LOCAL ROLE service_role"))
                    await _set_job(
                        session,
                        job_id,
                        phase="object_storage_done",
                        result={"objects_deleted": objects},
                    )

            if progress < 3:
                await evict_tenant(str(tenant["uid"]))
                await datalake.reset_tenant_catalog(str(tenant["uid"]))
                async with maker() as session, session.begin():
                    await session.execute(text("SET LOCAL ROLE service_role"))
                    await _set_job(session, job_id, phase="lake_done")

        if job["kind"] in {"reset_and_seed", "seed_book"} and progress < 4:
            from app import demo_seed

            seeded = await demo_seed.seed(maker, tenant_id, str(tenant["uid"]))
            async with maker() as session, session.begin():
                await session.execute(text("SET LOCAL ROLE service_role"))
                await _set_job(session, job_id, phase="seed_done", result={"seed": seeded})

        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            await _set_job(session, job_id, phase="completed", status="completed")
    except Exception as exc:
        logger.exception("tenant data job %s failed", job_id)
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            await _set_job(
                session,
                job_id,
                phase="failed",
                status="failed",
                error=f"{type(exc).__name__}: {str(exc)[:1800]}",
            )


async def handle_once(maker: async_sessionmaker[AsyncSession]) -> bool:
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        job = await claim(session)
    if job is None:
        return False
    await run_claimed(maker, job)
    return True
