import { error } from '@sveltejs/kit';
import { authHeaders } from '$lib/api';
import { targetTenantUrl } from '$lib/target-tenant';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in.');
	const response = await fetch(
		targetTenantUrl(`/documents/${params.documentUid}/content`, params.uid),
		{ headers: authHeaders(locals.accessToken) }
	);
	if (!response.ok) error(response.status === 404 ? 404 : 502, 'Document unavailable.');
	return new Response(await response.arrayBuffer(), {
		headers: {
			'content-type': response.headers.get('content-type') ?? 'application/octet-stream',
			'content-disposition': response.headers.get('content-disposition') ?? 'inline',
			'cache-control': 'private, no-store'
		}
	});
};
