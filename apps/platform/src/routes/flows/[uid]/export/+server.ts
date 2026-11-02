import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders } from '$lib/api';
import { exportFilename } from '$lib/flow-bundle';
import type { RequestHandler } from './$types';

// A flow as a file. The API writes the bundle (`GET /flows/{uid}/export`); this same-origin proxy
// adds the download disposition, so the editor's Export link is a plain `<a download>` and the
// browser never sees the API's origin or the bearer token.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'flows:read')) error(403, 'Not permitted');
	const res = await fetch(`${apiUrl()}/flows/${params.uid}/export`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status === 404 ? 404 : 502, 'Flow unavailable');
	const bundle = (await res.json()) as { flow?: { code?: string } };
	const code = bundle.flow?.code || params.uid;
	return new Response(JSON.stringify(bundle, null, 2), {
		headers: {
			'content-type': 'application/json; charset=utf-8',
			'content-disposition': `attachment; filename="${exportFilename(code)}"`,
			'cache-control': 'private, no-store'
		}
	});
};
