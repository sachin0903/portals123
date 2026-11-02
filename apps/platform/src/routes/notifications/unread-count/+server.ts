import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';

/** Same-origin proxy for the bell's badge poll. The browser can't call the API directly (the
 * access token is server-side), so the bell polls this and we forward with the session token. */
export const GET: RequestHandler = async ({ locals, fetch }) => {
	if (!locals.user) return json({ count: 0 });
	const res = await fetch(`${apiUrl()}/notifications/unread-count`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ count: 0 });
	return json(await res.json());
};
