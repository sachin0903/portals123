import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** Browser-callable search over the tenant's whole book of conversations — a same-origin proxy
 * so MessagesInbox.svelte (a client component) can re-query as the operator types, without a
 * full page reload. */
export const GET: RequestHandler = async ({ locals, fetch, url }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const q = url.searchParams.get('q');
	const qs = q ? `?q=${encodeURIComponent(q)}` : '';
	const res = await fetch(`${apiUrl()}/messaging/conversations${qs}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
