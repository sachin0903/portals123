import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

/** Browser-callable per-conversation messages — MessagesInbox.svelte fetches this on selecting a
 * row and posts to it to reply, proxying the caller's own session token server-side. */
export const GET: RequestHandler = async ({ locals, fetch, params }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(`${apiUrl()}/messaging/conversations/${params.uid}/messages`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};

export const POST: RequestHandler = async ({ locals, fetch, params, request }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const body = await request.json();
	const res = await fetch(`${apiUrl()}/messaging/conversations/${params.uid}/messages`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(body)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
