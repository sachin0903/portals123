import { json, type RequestHandler } from '@sveltejs/kit';
import { contactIdQuery } from '$lib/active-contact';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Mark notifications read. Body `{ uid }` marks one; empty (optionally `{ contactId }`) marks
 * all of ONE linked contact's, for a login linked to more than one — otherwise "mark all read"
 * while viewing one client would also clear the other's. `contactId` is meaningless for the
 * single-uid case (that row is already resolved) so it's only forwarded for "mark all". */
export const POST: RequestHandler = async ({ locals, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const body = (await request.json().catch(() => ({}))) as { uid?: string; contactId?: unknown };
	const path = body.uid
		? `/notifications/${encodeURIComponent(body.uid)}/read`
		: `/notifications/read-all${contactIdQuery(body.contactId)}`;
	const res = await fetch(`${apiUrl()}${path}`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken)
	});
	return json({ ok: res.ok }, { status: res.ok ? 200 : res.status });
};
