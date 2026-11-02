import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { Catalogue, ModelDetail } from '$lib/catalogue/types';
import type { LayoutServerLoad } from './$types';

/** The whole catalogue, loaded once for every page under /platform.
 *
 * A layout load rather than one per page: the pages cross-reference each other (a fee names a
 * product, a provider lists products, a model allocation names an instrument), so each would
 * otherwise refetch what its neighbours already have. SvelteKit reuses this across the section's
 * navigations, so moving between Products and Models is not a round trip. */
export const load: LayoutServerLoad = async ({ params, locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');
	// The numeric id the `/admin/tenants/{tenant_id}` endpoints take. Resolved once by the
	// [uid] layout, so this costs no extra round trip.
	if (!can(locals.permissions, locals.role, 'platforms:read')) error(403, 'Not authorised');

	const base = `${apiUrl()}/admin/tenants/${params.uid}/catalogue`;
	const res = await fetch(base, { headers: authHeaders(locals.accessToken) });
	if (res.status === 404) error(404, 'Tenant not found.');
	if (!res.ok) error(res.status, await detail(res));
	const catalogue: Catalogue = await res.json();

	// `?model=<uid>` opens the allocation history. Loaded server-side so a deep link renders it,
	// and so a stale uid 404s rather than showing an empty drawer.
	let model: ModelDetail | null = null;
	const modelId = url.searchParams.get('model');
	if (modelId) {
		const one = await fetch(`${base}/models/${modelId}`, {
			headers: authHeaders(locals.accessToken)
		});
		if (one.ok) model = await one.json();
		else if (one.status !== 404) error(one.status, await detail(one));
	}

	return { catalogue, model, canManage: can(locals.permissions, locals.role, 'platforms:write') };
};
