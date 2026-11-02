import { error, redirect } from '@sveltejs/kit';
import { PAGE_SIZE as LISTING_PAGE_SIZE } from '@graphene/ui/book/listing';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { AuditEvent } from '$lib/audit/types';
import { can } from '$lib/permissions';
import type { PageServerLoad } from './$types';

/**
 * This firm's slice of the record-change trail (`audit.log`) — the DML triggers' before/after
 * evidence, scoped to the section's firm by numeric `tenant_id` (from the layout's resolved
 * tenant). No firm filter: the firm is the URL; the global `/audit` page is the cross-firm view.
 *
 * Gated on `audit:read`, like the global surface — scoping to one firm is a filter, not a
 * privilege.
 */

/** Events per request; keyset-paged by `before_id` so "older" walks the whole log. The product-wide
 *  listing page size. */
const PAGE_SIZE = LISTING_PAGE_SIZE;

export const load: PageServerLoad = async ({ params, url, locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'audit:read')) error(403, 'Not authorised');

	// The numeric id the DML trail filters by comes from the section layout's resolved tenant.
	const { tenant } = await parent();

	// `actor` is an ARRAY: `getAll`, or a second value replaces the first.
	const filters = {
		actor: url.searchParams
			.getAll('actor')
			.map((v) => v.trim())
			.filter(Boolean),
		action: url.searchParams.get('action')?.trim() ?? '',
		since: url.searchParams.get('since')?.trim() ?? '',
		until: url.searchParams.get('until')?.trim() ?? ''
	};
	let events: AuditEvent[] = [];
	let apiError: string | null = null;

	const query = new URLSearchParams({ limit: String(PAGE_SIZE), tenant_id: String(tenant.id) });
	for (const actor of filters.actor) query.append('actor', actor);
	if (filters.action) query.set('action', filters.action);
	if (filters.since) query.set('since', filters.since);
	if (filters.until) query.set('until', `${filters.until}T23:59:59.999Z`);
	try {
		const res = await fetch(`${apiUrl()}/admin/audit?${query}`, {
			headers: authHeaders(locals.accessToken)
		});
		if (res.ok) {
			// The endpoint returns `{items, tenants}`; the scoped view ignores the firm lookup.
			events = ((await res.json()) as { items: AuditEvent[] }).items;
		} else if (res.status === 403) apiError = 'System role required to view the audit log.';
		else apiError = await detail(res);
	} catch {
		apiError = 'The service is unavailable.';
	}

	return {
		events,
		filters,
		// A full page back suggests there is more behind it (the shell appends via /audit/more).
		hasMore: events.length === PAGE_SIZE,
		apiError,
		tenantUid: params.uid,
		tenantId: tenant.id
	};
};
