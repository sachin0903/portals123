// The platform Query console — proxies one read-only SELECT to the API's
// lake_reader console endpoint (POST /admin/platforms/{provider}/query) so the
// browser never talks to the API directly. Errors pass through as
// { detail } with the API's status (the console renders the engine message).

import { error, json } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ params, request, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	// A POST doesn't run any layout load, so the [slug] layout's gate never covers this proxy — it
	// needs its own. The console has its own slug (platforms:query, split out of platforms:read —
	// docs/api.md "six splits"); the API's console endpoint enforces the same permission, and this
	// keeps the SvelteKit surface from even forwarding an ungated operator's SQL.
	if (!can(locals.permissions, locals.role, 'platforms:query')) error(403, 'Not authorised');
	const provider = params.slug;
	if (!provider) error(404, 'Not a live platform');
	const body = (await request.json()) as { sql?: unknown };
	const res = await fetch(`${apiUrl()}/admin/platforms/${provider}/query`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({ sql: String(body.sql ?? ''), limit: 200 })
	});
	return json(await res.json(), { status: res.status });
};
