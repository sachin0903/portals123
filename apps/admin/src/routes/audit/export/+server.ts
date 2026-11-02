import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { csvDocument } from '$lib/csv';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';
import type { AuditEvent } from '../+page.server';

/** One API page; we walk back with `before_id` until the filter is exhausted or the cap is hit. */
const PAGE = 200;
/** A ceiling so a mis-specified export can't stream the whole trail. Narrow the dates instead. */
const MAX_ROWS = 20_000;

const COLUMNS = [
	'id',
	'created_at',
	'action',
	'entity_type',
	'entity_id',
	'tenant_id',
	'tenant_name',
	'actor_email',
	'actor_name',
	'detail'
] as const;

/**
 * The day after a `YYYY-MM-DD` date, so an inclusive end date becomes an exclusive bound.
 * Returns the input unchanged if it isn't a plain date (the API validates it and answers 422).
 */
export function _nextDay(date: string): string {
	if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return date;
	const next = new Date(`${date}T00:00:00.000Z`);
	next.setUTCDate(next.getUTCDate() + 1);
	return next.toISOString().slice(0, 10);
}

/**
 * Download the audit trail as CSV — the export a compliance request actually asks for.
 *
 * It honours the same filters as the page (actor / action / firm / date range) and pages the API
 * with `before_id` so the export is the whole matching set, not just what is on screen.
 */
export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'audit:read')) error(403, 'Not authorised');

	const base = new URLSearchParams({ limit: String(PAGE) });
	for (const key of ['action', 'since'] as const) {
		const value = url.searchParams.get(key)?.trim();
		if (value) base.set(key, value);
	}
	// `actor`/`tenant_id` are ARRAYS — each value carries forward as its own repeated param.
	for (const key of ['actor', 'tenant_id'] as const) {
		for (const value of url.searchParams.getAll(key)) {
			const trimmed = value.trim();
			if (trimmed) base.append(key, trimmed);
		}
	}
	const until = url.searchParams.get('until')?.trim();
	// The API's `until` is EXCLUSIVE, so the end of the day is the start of the next one. Stamping
	// 23:59:59.999 dropped the final millisecond — enough to lose an event from a compliance export,
	// which is the one file that must match the page it was exported from.
	if (until) base.set('until', `${_nextDay(until)}T00:00:00.000Z`);

	const rows: AuditEvent[] = [];
	let beforeId: number | null = null;
	while (rows.length < MAX_ROWS) {
		const query = new URLSearchParams(base);
		if (beforeId !== null) query.set('before_id', String(beforeId));
		const res = await fetch(`${apiUrl()}/admin/audit?${query}`, {
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) error(res.status, await detail(res));
		// The endpoint returns `{items, tenants}` (the tenants lookup feeds the page's firm filter);
		// the export only wants the rows.
		const page: AuditEvent[] = ((await res.json()) as { items: AuditEvent[] }).items;
		if (page.length === 0) break;
		rows.push(...page);
		beforeId = page[page.length - 1].id;
		if (page.length < PAGE) break;
	}

	const stamp = new Date().toISOString().slice(0, 10);
	return new Response(csvDocument(COLUMNS, rows), {
		headers: {
			'content-type': 'text/csv; charset=utf-8',
			'content-disposition': `attachment; filename="audit-${stamp}.csv"`,
			'cache-control': 'no-store'
		}
	});
};
