import { error, json } from '@sveltejs/kit';
import { apiUrl, jsonHeaders, detail, type IoUnlinkResult } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

/**
 * Unlink ONE account from its Intelliflo plan: the API blanks the plan's policy number in
 * Intelliflo (when this link put our number there) and then removes the link. One account at a
 * time, by design: a client's Intelliflo client cannot be changed while accounts are still mapped.
 */
export const POST: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user) error(401, 'Not authenticated');
	if (!can(locals.permissions, locals.role, 'integrations:write')) error(403, 'Not authorised');

	const res = await fetch(
		`${apiUrl()}/integrations/accounts/${encodeURIComponent(params.accountUid)}/io-unlink`,
		{
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: (await request.text()) || '{}'
		}
	);
	if (!res.ok) error(res.status, await detail(res));
	return json((await res.json()) as IoUnlinkResult);
};
