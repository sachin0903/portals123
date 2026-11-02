import { json, type RequestHandler } from '@sveltejs/kit';
import { contactIdQuery } from '$lib/active-contact';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Dismiss all of the caller's notifications — optionally scoped to one linked contact, for a
 * login linked to more than one, so "dismiss all" while viewing one client does not also clear
 * the other's off the bell. Same "all, optionally scoped" shape as `mark/+server.ts`. */
export const POST: RequestHandler = async ({ locals, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const body = (await request.json().catch(() => ({}))) as { contactId?: unknown };
	const path = `/notifications/dismiss-all${contactIdQuery(body.contactId)}`;
	const res = await fetch(`${apiUrl()}${path}`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken)
	});
	return json({ ok: res.ok }, { status: res.ok ? 200 : res.status });
};
