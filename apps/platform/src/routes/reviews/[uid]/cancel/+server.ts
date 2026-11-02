import { json } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ locals, params, fetch }) => {
	if (!locals.user) return json({ error: 'You must be signed in.' }, { status: 401 });
	const response = await fetch(`${apiUrl()}/reviews/${params.uid}/cancel`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken)
	});
	const body = await response.json().catch(() => ({}));
	return json(response.ok ? body : { error: body.detail ?? 'Could not cancel the request.' }, {
		status: response.status
	});
};
