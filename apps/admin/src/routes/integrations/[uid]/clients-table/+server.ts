import { error, json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, type ClientsTablePage } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

/**
 * The Clients and Accounts tab's Client column: a paginated listing of this integration's own
 * firm's `crm.contacts` rows, each already carrying its existing Intelliflo mapping (if any).
 * `?search=` narrows it to clients whose name, or one of whose accounts' name or number, matches.
 */
export const GET: RequestHandler = async ({ locals, params, url, fetch }) => {
	if (!locals.user) error(401, 'Not authenticated');
	if (!can(locals.permissions, locals.role, 'integrations:read')) error(403, 'Not authorised');

	const page = url.searchParams.get('page') ?? '1';
	const pageSize = url.searchParams.get('page_size') ?? '10';
	const search = url.searchParams.get('search')?.trim() ?? '';
	const query = new URLSearchParams({
		integration_uid: params.uid,
		page,
		page_size: pageSize
	});
	// Omitted when blank: the API reads an absent term as "the whole book", and a bare `search=`
	// would be a second spelling of the same thing.
	if (search) query.set('search', search);
	const res = await fetch(`${apiUrl()}/integrations/clients-table?${query}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	return json((await res.json()) as ClientsTablePage);
};
