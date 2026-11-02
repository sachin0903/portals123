import { error, redirect } from '@sveltejs/kit';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { parseListQuery, type AmlClientRow } from '$lib/aml';
import { can } from '$lib/permissions';
import { firmChoices, openWorkCount } from '$lib/server/aml';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'aml:read')) error(403, 'Not authorised');

	const query = parseListQuery(url.searchParams);
	const params = new URLSearchParams({ page: String(query.page) });
	if (query.tenant) params.set('tenant', query.tenant);
	if (query.q) params.set('q', query.q);
	if (query.sort) {
		params.set('sort', query.sort);
		params.set('dir', query.dir);
	}

	let rows: AmlClientRow[] = [];
	let total = 0;
	// The API states its own page size on every answer; this is only the value used when the call
	// fails, so an outage does not re-page the list.
	let pageSize: number = PAGE_SIZE;
	let loadError: string | null = null;
	try {
		const res = await fetch(`${apiUrl()}/aml/clients?${params}`, {
			headers: authHeaders(locals.accessToken)
		});
		if (res.ok) {
			const body = await res.json();
			rows = body.items ?? [];
			total = body.total ?? 0;
			pageSize = body.page_size ?? pageSize;
		} else {
			loadError = await detail(res);
		}
	} catch {
		loadError = 'The service is unavailable.';
	}

	const [firms, work] = await Promise.all([
		firmChoices(
			fetch,
			locals.accessToken,
			rows.map((row) => ({ uid: row.tenant_uid, name: row.tenant_name }))
		),
		openWorkCount(fetch, locals.accessToken, query.tenant)
	]);

	return { rows, total, pageSize, query, firms, work, loadError };
};
