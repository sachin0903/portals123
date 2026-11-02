import { error, json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, type IoPlanSearchResult } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

/**
 * The IO Accounts dropdown: the just-picked Intelliflo client's plans under THIS integration's
 * provider. The integration is the route's `[uid]`, so the API knows which provider to filter by.
 */
export const GET: RequestHandler = async ({ locals, params, url, fetch }) => {
	if (!locals.user) error(401, 'Not authenticated');
	if (!can(locals.permissions, locals.role, 'integrations:read')) error(403, 'Not authorised');

	const clientIdIo = url.searchParams.get('client_id_io');
	if (!clientIdIo) error(400, 'client_id_io is required');

	const query = new URLSearchParams({ integration_uid: params.uid });
	const res = await fetch(`${apiUrl()}/integrations/io-clients/${clientIdIo}/accounts?${query}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	return json((await res.json()) as IoPlanSearchResult[]);
};
