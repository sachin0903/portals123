import { json, error } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

// Thin authenticated BFF for AddressLookup and the UK bank check. The browser calls this
// same-origin path; we forward to the API's /ref/postcoder/{action} with the user's bearer.
// The Postcoder key lives only on the API — never here, never in the browser.
export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	const action = params.action;
	if (action !== 'find' && action !== 'retrieve') error(404, 'Unknown Postcoder action');

	const res = await fetch(`${apiUrl()}/ref/postcoder/${action}?${url.searchParams}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};

export const POST: RequestHandler = async ({ params, request, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (params.action !== 'bank') error(404, 'Unknown Postcoder action');

	const res = await fetch(`${apiUrl()}/ref/postcoder/bank`, {
		method: 'POST',
		headers: { ...authHeaders(locals.accessToken), 'content-type': 'application/json' },
		body: await request.text()
	});
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};
