import { error, json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, type AccountsTablePage } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

/**
 * The Accounts tab: a paginated listing of this integration's firm's accounts whose client is
 * already mapped to an Intelliflo client, one row per account. `?search=` narrows it (client name,
 * account name or number) and `?unlinked_only=true` hides the accounts that already have a plan.
 */
export const GET: RequestHandler = async ({ locals, params, url, fetch }) => {
	if (!locals.user) error(401, 'Not authenticated');
	if (!can(locals.permissions, locals.role, 'integrations:read')) error(403, 'Not authorised');

	const query = new URLSearchParams({
		integration_uid: params.uid,
		page: url.searchParams.get('page') ?? '1',
		page_size: url.searchParams.get('page_size') ?? '10'
	});
	// Omitted when blank, as on the Clients listing: an absent term is "everything".
	const search = url.searchParams.get('search')?.trim() ?? '';
	if (search) query.set('search', search);
	if (url.searchParams.get('unlinked_only') === 'true') query.set('unlinked_only', 'true');

	const res = await fetch(`${apiUrl()}/integrations/accounts-table?${query}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	return json((await res.json()) as AccountsTablePage);
};
