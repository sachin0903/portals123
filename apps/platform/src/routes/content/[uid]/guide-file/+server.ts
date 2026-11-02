import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ locals, fetch, params }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'content:read')) error(403, 'Not permitted');
	const res = await fetch(`${apiUrl()}/content/items/${params.uid}/file`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	const body = await res.arrayBuffer();
	return new Response(body, {
		headers: {
			'content-type': res.headers.get('content-type') ?? 'application/pdf',
			'content-disposition': res.headers.get('content-disposition') ?? 'inline',
			'cache-control': 'private, no-store'
		}
	});
};
