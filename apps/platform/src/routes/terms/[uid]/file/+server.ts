import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** Keep the draft PDF on our own authenticated origin so the shared pdf.js viewer can fetch it.
 * The API's `draft=true` branch is staff-authorised and deliberately reads the mutable attachment;
 * the client-facing route without that flag remains pinned to the published snapshot. */
export const GET: RequestHandler = async ({ locals, fetch, params }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'terms:read')) error(403, 'Not permitted');
	const res = await fetch(`${apiUrl()}/terms/${params.uid}/file?draft=true`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	return new Response(await res.arrayBuffer(), {
		headers: {
			'content-type': res.headers.get('content-type') ?? 'application/pdf',
			'content-disposition': res.headers.get('content-disposition') ?? 'inline',
			'cache-control': 'private, no-store'
		}
	});
};
