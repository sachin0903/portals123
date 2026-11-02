import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Create a firm notification rule (proxies to /firm/notification-rules; firm_session resolves the
 * tenant server-side, the API enforces notifications:manage). */
export const POST: RequestHandler = async ({ locals, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(`${apiUrl()}/firm/notification-rules`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(await request.json())
	});
	if (!res.ok) return json({ error: 'Could not create the rule.' }, { status: res.status });
	return json(await res.json());
};
