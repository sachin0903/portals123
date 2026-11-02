import { json } from '@sveltejs/kit';
import { contactIdQuery } from '$lib/active-contact';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** Mark the client's own thread read — mirrors the platform portal's
 * `/chats/[id]/read/+server.ts`. An optional `{ contactId }` body selects which linked
 * contact's thread, for a login linked to more than one (a bare/empty body still works —
 * `contactId` is undefined, same as before this existed). */
export const POST: RequestHandler = async ({ locals, fetch, request }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const { contactId } = await request.json().catch(() => ({}) as { contactId?: unknown });
	const cid = contactIdQuery(contactId);
	const res = await fetch(`${apiUrl()}/messaging/thread/read${cid}`, {
		method: 'POST',
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
