import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Add one of the caller's own notes (`POST /me/notes`). The API names the owner from the
 * session's claims, never from the body, and RLS admits the owner alone — so this is a relay. */
export const POST: RequestHandler = async ({ locals, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const body = (await request.json().catch(() => ({}))) as { body?: unknown };
	const res = await fetch(`${apiUrl()}/me/notes`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({ body: typeof body.body === 'string' ? body.body : '' })
	});
	const payload: unknown = await res.json().catch(() => ({}));
	return json(payload, { status: res.status });
};
