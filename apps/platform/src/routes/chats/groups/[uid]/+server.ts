import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

/** Browser-callable group feed (merged across every member's own conversation) + send (fans one
 * message out into each member's own conversation) — mirrors chats/[id]/+server.ts. */
export const GET: RequestHandler = async ({ locals, fetch, params }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(`${apiUrl()}/messaging/groups/${params.uid}/messages`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};

export const POST: RequestHandler = async ({ locals, fetch, params, request }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const body = await request.json();
	const res = await fetch(`${apiUrl()}/messaging/groups/${params.uid}/messages`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(body)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
