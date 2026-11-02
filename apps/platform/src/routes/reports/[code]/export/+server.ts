import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { RequestHandler } from './$types';

/** Proxy the API file. The query is run once, in the API, as this user. */
export const GET: RequestHandler = async ({ url, locals, fetch, params }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'reports:export')) error(403, 'Not authorised');

	const res = await fetch(
		`${apiUrl()}/reports/${encodeURIComponent(params.code)}/export?${url.searchParams}`,
		{ headers: authHeaders(locals.accessToken) }
	);
	if (!res.ok) error(res.status, await detail(res));
	return new Response(await res.arrayBuffer(), {
		headers: {
			'content-type': res.headers.get('content-type') ?? 'application/octet-stream',
			'content-disposition': res.headers.get('content-disposition') ?? 'attachment'
		}
	});
};
