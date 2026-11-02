import { error, redirect } from '@sveltejs/kit';
import { PAGE_SIZE as LISTING_PAGE_SIZE } from '@graphene/ui/book/listing';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { AuditEvent, AuditFilters, AuditTenant } from '$lib/audit/types';
import type { PageServerLoad } from './$types';

/** The record-change trail (audit.log) — the DML triggers' before/after evidence. The action-event
 *  stream moved to its own page at /events; this page is now the audit log alone. */

// Types live in $lib/audit/types now (so the audit-log components + the per-tenant view share them);
// re-exported here for the callers that still import them from this route (the CSV export walker).
export type { AuditEvent, AuditFilters, AuditTenant };

/** Events per request; keyset-paged by `before_id` so "older" walks the whole log. The product-wide
 *  listing page size — the CSV export (`/audit/export`) keeps its own, much larger, walk page. */
const PAGE_SIZE = LISTING_PAGE_SIZE;

/** Params that only ever belonged to the old "Actions" tab — their presence means an old
 *  action-stream link, which now lives at /events. `actor`/`since`/`until` are shared by both
 *  views, so they alone don't trigger the redirect. */
const ACTIONS_ONLY_PARAMS = ['event_type', 'proxied', 'method', 'before', 'tenant'];

export const load: PageServerLoad = async ({ url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'audit:read')) error(403, 'Not authorised');

	// The Actions tab moved to /events; keep every old link working, filters and all. Only an
	// actions-shaped URL redirects (explicit view=actions, or an actions-only param); bare /audit
	// and the record-change filters stay here — /audit is the audit log now.
	const p = url.searchParams;
	if (p.get('view') === 'actions' || ACTIONS_ONLY_PARAMS.some((k) => p.has(k))) {
		const q = new URLSearchParams(p);
		q.delete('view');
		redirect(308, `/activity${q.size ? `?${q}` : ''}`);
	}

	// `actor`/`tenant_id` are ARRAYS: `getAll`, or a second value silently replaces the first.
	const filters: AuditFilters = {
		actor: url.searchParams
			.getAll('actor')
			.map((v) => v.trim())
			.filter(Boolean),
		action: url.searchParams.get('action')?.trim() ?? '',
		tenantId: url.searchParams
			.getAll('tenant_id')
			.map((v) => v.trim())
			.filter(Boolean),
		since: url.searchParams.get('since')?.trim() ?? '',
		until: url.searchParams.get('until')?.trim() ?? ''
	};
	let events: AuditEvent[] = [];
	let tenants: AuditTenant[] = [];
	let apiError: string | null = null;

	const query = new URLSearchParams({ limit: String(PAGE_SIZE) });
	for (const actor of filters.actor) query.append('actor', actor);
	if (filters.action) query.set('action', filters.action);
	for (const tenantId of filters.tenantId) query.append('tenant_id', tenantId);
	if (filters.since) query.set('since', filters.since);
	if (filters.until) query.set('until', `${filters.until}T23:59:59.999Z`);
	try {
		const res = await fetch(`${apiUrl()}/admin/audit?${query}`, {
			headers: authHeaders(locals.accessToken)
		});
		if (res.ok) {
			const page = (await res.json()) as { items: AuditEvent[]; tenants: AuditTenant[] };
			events = page.items;
			tenants = page.tenants;
		} else if (res.status === 403) apiError = 'System role required to view the audit log.';
		else apiError = await detail(res);
	} catch {
		apiError = 'The service is unavailable.';
	}

	return {
		events,
		tenants,
		filters,
		// A full page back suggests there is more behind it (the shell appends via /audit/more).
		hasMore: events.length === PAGE_SIZE,
		apiError
	};
};
