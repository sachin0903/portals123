import { json } from '@sveltejs/kit';
import { apiUrl, detail, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

/** Browser-callable "start/open a conversation with this client" — the "New" flow. Coexists
 * with this route's +page.server.ts (which only exports `load`); only POST is defined here so
 * there's no conflict with the page's own GET. */
export const POST: RequestHandler = async ({ locals, fetch, request }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const body = await request.json();
	const res = await fetch(`${apiUrl()}/messaging/conversations`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(body)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
