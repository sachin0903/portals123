import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Dismiss all of the caller's notifications (soft, on the API). RLS scopes the write to the
 * caller's own rows. */
export const POST: RequestHandler = async ({ locals, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(`${apiUrl()}/notifications/dismiss-all`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken)
	});
	return json({ ok: res.ok }, { status: res.ok ? 200 : res.status });
};
