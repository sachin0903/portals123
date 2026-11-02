import { error, json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/**
 * Pickable records for one identifier field on an action form.
 *
 * A standalone handler rather than a load function because the picker fetches as the operator
 * types — and it proxies rather than letting the browser reach the API directly, for the reason
 * every other admin call does: the access token is a server-side secret.
 *
 * The provider comes from the slug map, not from layout data: a `+server.ts` cannot read a
 * layout (the same constraint that gave `$lib/api::tenantIdFor` its reason to exist).
 */
export const GET: RequestHandler = async ({ locals, params, url, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	const provider = params.slug;
	if (!provider) error(404, 'This platform has no lookups.');

	const search = new URLSearchParams();
	const q = url.searchParams.get('q');
	if (q) search.set('q', q);

	const res = await fetch(
		`${apiUrl()}/admin/platforms/${provider}/firms/${params.uid}/lookups/${params.field}?${search}`,
		{ headers: authHeaders(locals.accessToken) }
	);
	if (!res.ok) error(res.status, await detail(res));

	// Just the rows: the picker's contract is a list of options, and the envelope's `entity` /
	// `label` are already on the field contract the drawer rendered from.
	const body = (await res.json()) as { items: unknown[] };
	return json(body.items ?? []);
};
