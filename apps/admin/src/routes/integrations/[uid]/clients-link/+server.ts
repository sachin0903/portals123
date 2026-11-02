import { error, json } from '@sveltejs/kit';
import { apiUrl, jsonHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

/**
 * The Clients tab's Link: map one client to an Intelliflo client. The API refuses (409) while any
 * of the client's accounts is still mapped, and its message says which, so it is passed through.
 */
export const POST: RequestHandler = async ({ locals, request, fetch }) => {
	if (!locals.user) error(401, 'Not authenticated');
	if (!can(locals.permissions, locals.role, 'integrations:write')) error(403, 'Not authorised');

	const res = await fetch(`${apiUrl()}/integrations/clients-link`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: await request.text()
	});
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};
