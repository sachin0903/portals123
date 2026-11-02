import { error, json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

// The terms a client accepted, exactly as they read them, through a same-origin relay:
// browser -> this route -> API `/contacts/{uid}/term-acceptances/{acceptance}`.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'contacts:read')) error(403, 'Not authorised');
	let res: Response;
	try {
		res = await fetch(
			`${apiUrl()}/contacts/${encodeURIComponent(params.uid)}/term-acceptances/${encodeURIComponent(params.acceptance)}`,
			{ headers: authHeaders(locals.accessToken) }
		);
	} catch {
		error(503, 'The terms are unavailable.');
	}
	if (!res.ok) {
		if (res.status === 403 || res.status === 404) error(res.status, await detail(res));
		error(502, 'The terms are unavailable.');
	}
	return json(await res.json(), { headers: { 'cache-control': 'private, no-store' } });
};
