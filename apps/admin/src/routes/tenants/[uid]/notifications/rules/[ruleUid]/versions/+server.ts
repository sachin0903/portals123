import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';

/** A rule's change history for the admin surface. The numeric tenant id is resolved from the uid;
 * the API enforces `tenants:read` and scopes the history to that tenant. */
export const GET: RequestHandler = async ({ locals, params, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(
		`${apiUrl()}/admin/tenants/${params.uid}/notification-rules/${params.ruleUid}/versions`,
		{ headers: authHeaders(locals.accessToken) }
	);
	if (!res.ok) return json({ error: 'Could not load the history.' }, { status: res.status });
	return json(await res.json());
};
