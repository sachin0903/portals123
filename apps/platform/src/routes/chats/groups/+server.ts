import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

/** Browser-callable groups listing (search) + create — MessagesInbox.svelte's "Groups" section
 * and its "+" dialog, proxying the caller's own session token server-side. */
export const GET: RequestHandler = async ({ locals, fetch, url }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const q = url.searchParams.get('q');
	const qs = q ? `?q=${encodeURIComponent(q)}` : '';
	const res = await fetch(`${apiUrl()}/messaging/groups${qs}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};

export const POST: RequestHandler = async ({ locals, fetch, request }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const body = await request.json();
	const res = await fetch(`${apiUrl()}/messaging/groups`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(body)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
