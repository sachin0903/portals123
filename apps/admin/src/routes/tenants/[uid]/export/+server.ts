import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

/**
 * Download a firm's offboarding archive as a JSON file.
 *
 * The retention step of offboarding: the firm's identity, roles, members and full audit trail
 * (plus row counts for its data), taken BEFORE deletion — `audit.log.tenant_id` cascades, so the
 * trail cannot be recovered afterwards. A plain GET so it is an ordinary browser download; the
 * API gates it on `tenants:write` because it is a bulk extraction of one firm's record.
 */
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// No parent() outside the load tree, so the uid is resolved here.
	if (!can(locals.permissions, locals.role, 'tenants:write')) error(403, 'Not authorised');

	const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/export`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	const archive = await res.json();
	const alias = archive?.tenant?.alias ?? params.uid;
	const stamp = new Date().toISOString().slice(0, 10);
	return new Response(JSON.stringify(archive, null, 2), {
		headers: {
			'content-type': 'application/json; charset=utf-8',
			'content-disposition': `attachment; filename="${alias}-archive-${stamp}.json"`,
			// A firm's record must never sit in a shared cache.
			'cache-control': 'no-store'
		}
	});
};
