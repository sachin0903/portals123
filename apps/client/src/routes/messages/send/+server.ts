import { json } from '@sveltejs/kit';
import { contactIdQuery } from '$lib/active-contact';
import { apiUrl, detail, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

/** Browser-callable text send for the client's own thread — mirrors the platform portal's
 * `/chats/[id]/+server.ts` POST. A plain endpoint rather than a form action: this is always
 * called from JS, never a real `<form>` submit, so it doesn't need the action-result envelope
 * (and doesn't inherit the "must be form-encoded" requirement that came with one). */
export const POST: RequestHandler = async ({ locals, fetch, request }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const { text, contactId } = await request.json();
	const value = String(text ?? '').trim();
	if (!value) return json({ error: 'Empty message.' }, { status: 422 });
	const cid = contactIdQuery(contactId);
	const res = await fetch(`${apiUrl()}/messaging/thread/messages${cid}`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({ text: value })
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
