import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Seed a tenant's notification rules from the shipped built-in defaults. The numeric tenant id is
 * resolved from the uid; the API enforces `tenants:write` and is idempotent (only fills gaps). */
export const POST: RequestHandler = async ({ locals, params, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(
		`${apiUrl()}/admin/tenants/${params.uid}/notification-rules/load-defaults`,
		{ method: 'POST', headers: jsonHeaders(locals.accessToken) }
	);
	if (!res.ok) return json({ error: 'Could not load the defaults.' }, { status: res.status });
	return json(await res.json());
};
