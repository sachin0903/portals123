/**
 * Shared types for the record-change trail (`audit.log`) — the DML triggers' before/after
 * evidence, read by the global `/audit` page and the per-tenant `/tenants/[uid]/audit` view.
 *
 * These live in `$lib` rather than a route so both the pages and the shared audit-log components
 * (`audit-log.svelte` + its row/detail) depend on them without a route importing from another route.
 */

export type AuditEvent = {
	id: number;
	created_at: string;
	action: string;
	entity_type: string | null;
	entity_id: number | null;
	tenant_id: number | null;
	tenant_name: string | null;
	actor_email: string | null;
	actor_name: string | null;
	/** The before/after values — the substance of the evidence. */
	detail: Record<string, unknown> | null;
};

/** Filters the API applies server-side, so a search covers the whole trail, not one page of it. */
export type AuditFilters = {
	actor: string[];
	action: string;
	tenantId: string[];
	since: string;
	until: string;
};

/** A firm option for the audit-log firm filter — carried by the endpoint's own lookup, so the
 *  surface works for an operator holding `audit:read` without `tenants:read`. The audit trail
 *  filters by numeric `tenant_id`, so the option value is the id (not the uid). */
export type AuditTenant = { id: number; name: string };
