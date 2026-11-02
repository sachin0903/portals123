import { error, json } from '@sveltejs/kit';
import { apiUrl, jsonHeaders, detail, type IoClientUnlinkResult } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

/**
 * Unlink a client from its Intelliflo client: the API removes the client's link and nothing else.
 * It refuses (409, naming the accounts) while any of the client's accounts is still linked to an
 * Intelliflo plan; those are unlinked first, one at a time, on the Accounts tab.
 */
export const POST: RequestHandler = async ({ locals, params, fetch }) => {
	if (!locals.user) error(401, 'Not authenticated');
	if (!can(locals.permissions, locals.role, 'integrations:write')) error(403, 'Not authorised');

	const res = await fetch(
		`${apiUrl()}/integrations/clients/${encodeURIComponent(params.contactUid)}/io-unlink`,
		{ method: 'POST', headers: jsonHeaders(locals.accessToken), body: '{}' }
	);
	if (!res.ok) error(res.status, await detail(res));
	return json((await res.json()) as IoClientUnlinkResult);
};
