import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** Same-origin models picker. Token stays server-side; the illustration drawer fetches this on open. */
export const GET: RequestHandler = async ({ locals, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(`${apiUrl()}/crm/models`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
