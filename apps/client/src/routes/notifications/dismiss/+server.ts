import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Dismiss one notification (soft, on the API). RLS scopes the write to the caller's own row. */
export const POST: RequestHandler = async ({ locals, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const body = (await request.json().catch(() => ({}))) as { uid?: string };
	if (!body.uid) return json({ error: 'uid required' }, { status: 400 });
	const res = await fetch(`${apiUrl()}/notifications/${encodeURIComponent(body.uid)}/dismiss`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken)
	});
	return json({ ok: res.ok }, { status: res.ok ? 200 : res.status });
};
