import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Create a notification rule for a firm. The numeric tenant id is resolved from the uid; the API
 * enforces `tenants:write`. */
export const POST: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/notification-rules`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(await request.json())
	});
	if (!res.ok) return json({ error: 'Could not create the rule.' }, { status: res.status });
	return json(await res.json());
};
