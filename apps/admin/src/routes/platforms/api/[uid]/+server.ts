import { error, json } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

// One call including its bodies. A separate fetch from the list because a single request body
// can be a whole CTC1 XML document or a 1,600-row register — sending fifty of them to render a
// table would make the page megabytes for data nobody reads until they open a row.
export const GET: RequestHandler = async ({ locals, fetch, params }) => {
	if (!locals.user) error(401, 'Not signed in');
	const res = await fetch(`${apiUrl()}/admin/platforms/api-calls/${params.uid}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, 'Could not read that API call');
	return json(await res.json());
};
