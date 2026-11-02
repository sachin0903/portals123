import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders } from '$lib/api';

/** Re-word or tick one of the caller's own notes (`PATCH /me/notes/{uid}`). RLS on the API
 * scopes the write to the owner's rows: somebody else's uid is a 404 there, relayed as one. */
export const PATCH: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const patch = (await request.json().catch(() => ({}))) as { body?: unknown; done?: unknown };
	const res = await fetch(`${apiUrl()}/me/notes/${encodeURIComponent(params.uid!)}`, {
		method: 'PATCH',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({
			...(typeof patch.body === 'string' ? { body: patch.body } : {}),
			...(typeof patch.done === 'boolean' ? { done: patch.done } : {})
		})
	});
	const payload: unknown = await res.json().catch(() => ({}));
	return json(payload, { status: res.status });
};

/** Remove one of the caller's own notes (`DELETE /me/notes/{uid}`). */
export const DELETE: RequestHandler = async ({ locals, params, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(`${apiUrl()}/me/notes/${encodeURIComponent(params.uid!)}`, {
		method: 'DELETE',
		headers: authHeaders(locals.accessToken)
	});
	return new Response(null, { status: res.ok ? 204 : res.status });
};
