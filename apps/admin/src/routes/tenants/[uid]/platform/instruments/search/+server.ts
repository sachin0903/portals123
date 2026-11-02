// The allocation editor searches from the browser. The session token stays on the server,
// so this route asks the catalogue and returns the picker's shape.

import { error, json } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { can } from '$lib/permissions';
import type { Instrument } from '$lib/catalogue/types';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'platforms:read')) error(403, 'Not authorised');

	const q = url.searchParams.get('q')?.trim() ?? '';
	const search = new URLSearchParams({ limit: '25', offset: '0', sort: 'name', dir: 'asc' });
	if (q) search.set('q', q);

	const upstream = await fetch(
		`${apiUrl()}/admin/tenants/${params.uid}/catalogue/instruments?${search}`,
		{ headers: authHeaders(locals.accessToken) }
	);
	if (!upstream.ok) error(upstream.status, 'Instrument search is unavailable');
	const body: { items?: Instrument[] } = await upstream.json();

	return json(
		(body.items ?? []).map((instrument) => {
			const name = instrument.name?.trim() || instrument.isin || String(instrument.id);
			return {
				value: String(instrument.id),
				label: name,
				hint: instrument.isin && instrument.isin !== name ? instrument.isin : undefined
			};
		})
	);
};
