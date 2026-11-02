import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';

/** A rule's change history (proxies to /firm/notification-rules/{uid}/versions; the API scopes it to
 * the caller's tenant and RLS to the firm's own rows). */
export const GET: RequestHandler = async ({ locals, params, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(`${apiUrl()}/firm/notification-rules/${params.ruleUid}/versions`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: 'Could not load the history.' }, { status: res.status });
	return json(await res.json());
};
