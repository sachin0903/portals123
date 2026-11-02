import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';

/** Same-origin proxy for the bell's badge poll (the access token is server-side). */
export const GET: RequestHandler = async ({ locals, fetch }) => {
	if (!locals.user) return json({ count: 0 });
	const res = await fetch(`${apiUrl()}/notifications/unread-count`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ count: 0 });
	return json(await res.json());
};
