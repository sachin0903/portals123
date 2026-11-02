import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Persist a firm's notification config. The numeric tenant id is resolved from the uid; the API
 * enforces `tenants:write`, so a read-only operator's save honestly 403s here. */
export const POST: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const body = await request.json();
	const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/notification-config`, {
		method: 'PUT',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(body)
	});
	if (!res.ok)
		return json({ error: 'Could not save notification config.' }, { status: res.status });
	return json(await res.json());
};
