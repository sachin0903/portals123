import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { Catalogue } from '$lib/catalogue/types';
import type { PageServerLoad } from './$types';

export { catalogueActions as actions } from '$lib/catalogue/actions';

/** Terminology sits under Branding because it is the same job: how the firm's portal reads.
 * Colours and logos are one half, the words are the other.
 *
 * It reads the catalogue endpoint (which serves the merged terms) rather than a dedicated one —
 * the payload is small and the alternative is a second endpoint returning a subset of it. */
export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// The numeric id the `/admin/tenants/{tenant_id}` endpoints take. Resolved once by the
	// [uid] layout, so this costs no extra round trip.
	if (!can(locals.permissions, locals.role, 'platforms:read')) error(403, 'Not authorised');

	const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/catalogue`, {
		headers: authHeaders(locals.accessToken)
	});
	if (res.status === 404) error(404, 'Tenant not found.');
	if (!res.ok) error(res.status, await detail(res));
	const catalogue: Catalogue = await res.json();

	return {
		terms: catalogue.terms,
		canManage: can(locals.permissions, locals.role, 'platforms:write')
	};
};
