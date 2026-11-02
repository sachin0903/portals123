import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Save the client's notification preferences (proxies PUT /notifications/client-preferences; the
 * API keys them to the caller's own contacts under RLS). */
export const PUT: RequestHandler = async ({ locals, request, fetch }) => {
	if (!locals.accessToken) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(`${apiUrl()}/notifications/client-preferences`, {
		method: 'PUT',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(await request.json())
	});
	if (!res.ok) return json({ error: 'Could not save preferences.' }, { status: res.status });
	return json(await res.json());
};
