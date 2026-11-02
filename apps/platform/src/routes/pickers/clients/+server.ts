import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** Same-origin client typeahead. Token stays server-side; optional `?q=` is forwarded when present.
 *  Contacts, not `/documents/clients`: a profiles manager has `contacts:read` and may not hold
 *  `documents:write`. Capped so opening a picker is not a book dump. */
export const GET: RequestHandler = async ({ locals, fetch, url }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const qs = new URLSearchParams({ limit: '50' });
	const q = url.searchParams.get('q');
	if (q) qs.set('q', q);
	const res = await fetch(`${apiUrl()}/crm/contacts?${qs}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	const body: unknown = await res.json();
	const items = Array.isArray(body) ? body : ((body as { items?: unknown }).items ?? []);
	return json(items);
};
