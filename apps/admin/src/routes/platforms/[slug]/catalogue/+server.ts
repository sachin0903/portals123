// Models and lineage ask for column lineage, SQL and row counts after the overview is
// already on screen. The browser cannot call the API with the session's bearer token, so
// this same-origin route does.

import { error } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'platforms:read')) error(403, 'Not authorised');
	const provider = params.slug;
	if (!provider) error(404, 'Not a live platform');
	const upstream = await fetch(`${apiUrl()}/admin/platforms/${provider}/catalogue`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!upstream.ok) error(upstream.status, 'Platform catalogue is unavailable');
	return new Response(upstream.body, {
		headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
	});
};
