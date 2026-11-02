import { error, json } from '@sveltejs/kit';
import { apiUrl, jsonHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

/**
 * The Accounts tab's Link: map one account to a plan of its client's Intelliflo client. The plan
 * is taken from that client's own plans on the API side, never from what is posted here, and must
 * sit under this integration's provider. The integration is the route's `[uid]`, never the body's.
 */
export const POST: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user) error(401, 'Not authenticated');
	if (!can(locals.permissions, locals.role, 'integrations:write')) error(403, 'Not authorised');

	const res = await fetch(`${apiUrl()}/integrations/accounts-link`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({ ...(await request.json()), integration_uid: params.uid })
	});
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};
