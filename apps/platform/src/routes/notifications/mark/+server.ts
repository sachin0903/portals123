import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Mark notifications read. Body `{ uid }` marks one; an empty body marks all the caller's unread.
 * RLS on the API scopes every write to the caller's own rows, so no ownership check is needed here. */
export const POST: RequestHandler = async ({ locals, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const body = (await request.json().catch(() => ({}))) as { uid?: string };
	const path = body.uid
		? `/notifications/${encodeURIComponent(body.uid)}/read`
		: '/notifications/read-all';
	const res = await fetch(`${apiUrl()}${path}`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken)
	});
	return json({ ok: res.ok }, { status: res.ok ? 200 : res.status });
};
